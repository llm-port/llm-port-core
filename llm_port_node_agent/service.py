"""Top-level node agent service orchestration."""

from __future__ import annotations

import asyncio
import logging

import psutil
from websockets.exceptions import InvalidStatusCode

from llm_port_node_agent.backend_client import BackendClient
from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.container_log_forwarder import ContainerLogForwarder
from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.log_collector import LogCollector
from llm_port_node_agent.loki_client import LokiClient
from llm_port_node_agent.image_loader import load_image_from_backend
from llm_port_node_agent.model_puller import pull_model
from llm_port_node_agent.policy_guard import PolicyGuard
from llm_port_node_agent.preflight import build_static_capabilities
from llm_port_node_agent.runtime_manager import RuntimeManager
from llm_port_node_agent.state_store import StateStore
from llm_port_node_agent.stream_client import StreamClient

log = logging.getLogger(__name__)


class NodeAgentService:
    """Bootstraps onboarding and runs persistent stream loop."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._state_store = StateStore(config.state_path)
        self._client = BackendClient(config)
        self._stream: StreamClient | None = None
        self._loki: LokiClient | None = None

    async def run_forever(self) -> None:
        """Run agent forever with reconnect and re-enrollment handling."""
        static_capabilities = await build_static_capabilities()
        log.info("Static capabilities: %s", static_capabilities)
        if not bool(static_capabilities.get("docker_available")):
            log.warning("Docker is not available; runtime commands will fail until daemon is reachable.")

        # Prime cpu_percent so first utilization report is non-zero
        psutil.cpu_percent()

        events = EventBuffer()

        async def _pull_model(*, model_sync: dict) -> None:
            """Pull model files from the backend using the node credential."""
            credential = self._state_store.state.credential
            if not credential:
                raise RuntimeError("No credential available for model pull.")
            await pull_model(
                client=self._client.http,
                credential=credential,
                model_sync=model_sync,
                model_store_root=self._config.model_store_root,
            )

        async def _load_image(*, image: str) -> None:
            """Stream image tarball from backend and load via docker."""
            credential = self._state_store.state.credential
            if not credential:
                raise RuntimeError("No credential available for image transfer.")
            await load_image_from_backend(
                client=self._client.http,
                credential=credential,
                image=image,
            )

        runtime_manager = RuntimeManager(
            state_store=self._state_store,
            events=events,
            advertise_host=self._config.advertise_host,
            advertise_scheme=self._config.advertise_scheme,
            model_store_root=self._config.model_store_root,
            model_puller=_pull_model,
            image_loader=_load_image,
        )
        stream = StreamClient(
            config=self._config,
            state_store=self._state_store,
            dispatcher=None,  # type: ignore[arg-type]  # set below
            static_capabilities=static_capabilities,
            events=events,
            backend_client=self._client,
        )
        dispatcher = CommandDispatcher(
            state_store=self._state_store,
            runtime_manager=runtime_manager,
            policy_guard=PolicyGuard(image_allowlist=self._config.image_allowlist),
            events=events,
            on_refresh_inventory=stream.trigger_inventory,
        )
        stream._dispatcher = dispatcher
        self._stream = stream

        # Start log collection → Loki push loop if configured
        log_task: asyncio.Task[None] | None = None
        if self._config.loki_url:
            loki_client = LokiClient(
                loki_url=self._config.loki_url,
                labels={"job": "node-agent", "host": self._config.host},
                verify_tls=self._config.verify_tls,
            )
            self._loki = loki_client
            collector = LogCollector(max_lines=self._config.log_batch_size)
            log_task = asyncio.create_task(
                self._log_push_loop(collector, loki_client),
                name="log_push",
            )
            # Container log forwarding — tails docker logs for tracked workloads
            container_log_task = asyncio.create_task(
                ContainerLogForwarder(
                    state_store=self._state_store,
                    loki=loki_client,
                    host=self._config.host,
                    interval_sec=self._config.log_flush_interval_sec,
                ).run_forever(),
                name="container_log_forwarder",
            )
            log.info("System log collection enabled → %s", self._config.loki_url)
        else:
            log.info("LLM_PORT_NODE_AGENT_LOKI_URL not set — system log collection disabled.")

        backoff = self._config.reconnect_min_sec
        while True:
            try:
                await self._ensure_credential(static_capabilities)
                credential = self._state_store.state.credential
                if not credential:
                    raise RuntimeError("Credential missing after enrollment flow.")
                await stream.run(credential=credential)
                backoff = self._config.reconnect_min_sec
            except InvalidStatusCode as exc:
                # 401/403 indicate credential no longer valid.
                if exc.status_code in {401, 403}:
                    log.warning("Stream auth rejected (%s), clearing credential.", exc.status_code)
                    self._state_store.state.credential = None
                    self._state_store.save()
                else:
                    log.warning("Stream rejected with status=%s", exc.status_code)
            except Exception:
                log.exception("Node stream loop failed.")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._config.reconnect_max_sec)

    async def close(self) -> None:
        """Close outbound HTTP resources and flush state."""
        self._state_store.flush_seq()
        await self._client.close()
        if self._loki:
            await self._loki.flush()
            await self._loki.close()

    async def _log_push_loop(
        self,
        collector: LogCollector,
        loki: LokiClient,
    ) -> None:
        """Collect system logs and push to Loki on a timer."""
        interval = max(self._config.log_flush_interval_sec, 2)
        while True:
            try:
                entries = await collector.collect()
                if entries:
                    loki.add_many(entries)
                await loki.flush()
            except Exception:
                log.warning("Log push cycle failed.", exc_info=True)
            await asyncio.sleep(interval)

    async def _ensure_credential(self, capabilities: dict) -> None:
        if self._state_store.state.credential:
            if self._config.enrollment_token:
                log.warning(
                    "Enrollment token is still configured after enrollment — consider removing it from env."
                )
            return
        token = self._config.enrollment_token
        if not token:
            raise RuntimeError("No credential in state and no enrollment token configured.")
        log.info("Enrolling node agent '%s' at backend.", self._config.agent_id)
        payload = await self._client.enroll(
            enrollment_token=token,
            agent_id=self._config.agent_id,
            host=self._config.host,
            capabilities=capabilities,
            version="0.1.0",
        )
        credential = payload.get("credential")
        node_id = payload.get("node_id")
        if not isinstance(credential, str) or not credential:
            raise RuntimeError("Enrollment succeeded but credential is missing.")
        self._state_store.state.credential = credential
        if isinstance(node_id, str) and node_id:
            self._state_store.state.node_id = node_id
        self._state_store.save()
        log.info("Enrollment complete. node_id=%s", self._state_store.state.node_id)
