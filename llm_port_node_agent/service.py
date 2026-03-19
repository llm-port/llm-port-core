"""Top-level node agent service orchestration."""

from __future__ import annotations

import asyncio
import logging

from websockets.exceptions import InvalidStatusCode

from llm_port_node_agent.backend_client import BackendClient
from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.event_buffer import EventBuffer
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

    async def run_forever(self) -> None:
        """Run agent forever with reconnect and re-enrollment handling."""
        static_capabilities = await build_static_capabilities()
        log.info("Static capabilities: %s", static_capabilities)
        if not bool(static_capabilities.get("docker_available")):
            log.warning("Docker is not available; runtime commands will fail until daemon is reachable.")

        events = EventBuffer()
        runtime_manager = RuntimeManager(
            state_store=self._state_store,
            events=events,
            advertise_host=self._config.advertise_host,
            advertise_scheme=self._config.advertise_scheme,
        )
        dispatcher = CommandDispatcher(
            state_store=self._state_store,
            runtime_manager=runtime_manager,
            policy_guard=PolicyGuard(),
            events=events,
        )
        stream = StreamClient(
            config=self._config,
            state_store=self._state_store,
            dispatcher=dispatcher,
            static_capabilities=static_capabilities,
            events=events,
        )

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
        """Close outbound HTTP resources."""
        await self._client.close()

    async def _ensure_credential(self, capabilities: dict) -> None:
        if self._state_store.state.credential:
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
