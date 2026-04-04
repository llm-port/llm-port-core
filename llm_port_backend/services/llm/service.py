"""High-level LLM orchestration service.

Coordinates providers, models, runtimes, download jobs, and the Docker
service to implement the full LLM management workflow.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from huggingface_hub import scan_cache_dir

from llm_port_backend.db.dao.llm_dao import (
    ArtifactDAO,
    DownloadJobDAO,
    ModelDAO,
    ProviderDAO,
    RuntimeDAO,
)
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.llm import (
    DownloadJob,
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ModelSource,
    ModelStatus,
    ProviderTarget,
    ProviderType,
    RuntimeStatus,
)
from llm_port_backend.db.models.node_control import InfraNode, NodeCommandType
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.llm.base import ContainerSpec
from llm_port_backend.services.llm.gateway_sync import GatewaySyncService, _normalize_base_url
from llm_port_backend.services.llm.registry import get_adapter
from llm_port_backend.services.llm.scanner import scan_model_directory
from llm_port_backend.services.nodes import NodeControlService
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)


class LLMService:
    """Facade that ties together adapters, DAOs, and Docker."""

    def __init__(
        self,
        docker: DockerService,
        *,
        gateway_sync: GatewaySyncService | None = None,
        _caps: dict[str, int | None] | None = None,
    ) -> None:
        self.docker = docker
        self.gateway_sync = gateway_sync or GatewaySyncService(None)
        self._caps: dict[str, int | None] = _caps if _caps is not None else {}

    @staticmethod
    def _node_container_name(runtime_name: str) -> str:
        """Deterministic Docker container name for node-deployed runtimes."""
        slug = runtime_name.replace("_", "-").replace("/", "-").replace(" ", "-").lower()
        slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-").strip("-")
        return f"llm-port-{slug[:48]}" if slug else "llm-port-runtime"

    async def _build_node_deploy_payload(
        self,
        runtime: LLMRuntime,
        session: Any,
    ) -> dict[str, Any]:
        """Build a full deploy-compatible payload for a node command.

        Used by start/restart so the node agent can fall back to a fresh
        ``deploy_workload`` when the container doesn't exist yet.
        """
        provider_dao = ProviderDAO(session)
        model_dao = ModelDAO(session)
        provider = await provider_dao.get(runtime.provider_id)
        model = await model_dao.get(runtime.model_id)
        prov_config = runtime.provider_config or {}
        gpu_request = prov_config.get("gpu_request", "")
        if not gpu_request and provider and provider.type.value in ("vllm", "tgi"):
            gpu_request = "all"
        ipc_mode = prov_config.get("ipc_mode", "")
        if not ipc_mode and gpu_request and provider and provider.type.value in ("vllm", "tgi"):
            ipc_mode = "host"
        model_source = prov_config.get("model_source") or "sync_from_server"
        image_source = prov_config.get("image_source") or "pull_from_registry"
        return {
            "runtime_id": str(runtime.id),
            "runtime_name": runtime.name,
            "container_name": self._node_container_name(runtime.name),
            "provider_id": str(runtime.provider_id),
            "provider_type": provider.type.value if provider else "vllm",
            "model_id": str(runtime.model_id),
            "generic_config": runtime.generic_config or {},
            "provider_config": prov_config,
            "openai_compat": runtime.openai_compat,
            "model_sync": self._build_model_sync_payload(
                model, source=model_source,
            ) if model else {},
            "image_source": image_source,
            "gpu_request": gpu_request,
            "ipc_mode": ipc_mode,
            "shm_size": prov_config.get("shm_size", ""),
            "memory_limit": prov_config.get("memory_limit", ""),
            "cpu_limit": prov_config.get("cpu_limit", ""),
            "container_port": prov_config.get("container_port", ""),
        }

    # ------------------------------------------------------------------
    # Providers
    # ------------------------------------------------------------------

    async def create_provider(
        self,
        provider_dao: ProviderDAO,
        *,
        name: str,
        type_: ProviderType,
        target: ProviderTarget = ProviderTarget.LOCAL_DOCKER,
        endpoint_url: str | None = None,
        api_key: str | None = None,
        remote_model: str | None = None,
        litellm_provider: str | None = None,
        litellm_model: str | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> LLMProvider:
        """Register a new LLM engine provider.

        For ``REMOTE_ENDPOINT`` providers, ``endpoint_url`` is required
        and points to an existing OpenAI-compatible API (vLLM, Ollama,
        TGI, NVIDIA NIM, etc.).  No Docker container is managed — the
        runtime simply proxies to that URL.
        """
        if (
            target == ProviderTarget.REMOTE_ENDPOINT
            and not endpoint_url
            and not litellm_provider
        ):
            raise ValueError(
                "endpoint_url or litellm_provider is required for remote providers",
            )

        # ── capacity enforcement ─────────────────────────────
        _lim = self._caps.get("remote_providers")
        if _lim is not None and target == ProviderTarget.REMOTE_ENDPOINT:
            if await provider_dao.count_by_target(ProviderTarget.REMOTE_ENDPOINT) >= _lim:
                from fastapi import HTTPException  # noqa: PLC0415

                raise HTTPException(
                    status_code=402,
                    detail=(
                        "Resource capacity reached for this deployment. "
                        "Please upgrade your plan to add more providers."
                    ),
                )

        # Normalise the URL so the gateway proxy does not duplicate /v1.
        if endpoint_url:
            endpoint_url = _normalize_base_url(endpoint_url)

        adapter = get_adapter(type_)
        capabilities = adapter.default_capabilities()
        if target == ProviderTarget.REMOTE_ENDPOINT:
            capabilities["remote"] = True
            if remote_model and remote_model.strip():
                capabilities["remote_model"] = remote_model.strip()

        return await provider_dao.create(
            name=name,
            type_=type_,
            target=target,
            capabilities=capabilities,
            endpoint_url=endpoint_url,
            api_key_encrypted=api_key,  # TODO: encrypt with SettingsCrypto
            litellm_provider=litellm_provider,
            litellm_model=litellm_model,
            extra_params=extra_params,
        )

    # ------------------------------------------------------------------
    # Model download
    # ------------------------------------------------------------------

    async def start_download(
        self,
        model_dao: ModelDAO,
        job_dao: DownloadJobDAO,
        *,
        hf_repo_id: str,
        hf_revision: str | None = None,
        display_name: str | None = None,
        tags: list[str] | None = None,
    ) -> tuple[LLMModel, DownloadJob]:
        """Create a model record + download job and dispatch the Taskiq task.

        Returns (model, job). If dispatch fails, job.error_message is set
        to the dispatch error string (model & job are still persisted).
        """
        model = await model_dao.create(
            display_name=display_name or hf_repo_id,
            source=ModelSource.HUGGINGFACE,
            hf_repo_id=hf_repo_id,
            hf_revision=hf_revision,
            tags=tags,
            status=ModelStatus.DOWNLOADING,
        )
        job = await job_dao.create(model.id)

        # Files land in the standard HF cache layout directly under
        # model_store_root so vLLM can resolve models by repo ID.
        target_dir = settings.model_store_root

        # Dispatch async task — non-fatal if RabbitMQ is temporarily
        # unreachable; the user can retry from the Jobs page.
        from llm_port_backend.services.llm.tasks import download_model_task  # noqa: PLC0415

        try:
            await download_model_task.kiq(
                model_id=str(model.id),
                job_id=str(job.id),
                hf_repo_id=hf_repo_id,
                hf_revision=hf_revision,
                target_dir=target_dir,
            )
            log.info("Dispatched download task for %s (job=%s)", hf_repo_id, job.id)
        except Exception as exc:
            log.warning(
                "Failed to dispatch download task for %s (job=%s): %s",
                hf_repo_id,
                job.id,
                exc,
                exc_info=True,
            )
            # Store the dispatch error on the job so callers can surface it
            job.error_message = f"Dispatch failed: {exc}"

        return model, job

    # ------------------------------------------------------------------
    # Model registration (local path / import)
    # ------------------------------------------------------------------

    async def register_local_model(
        self,
        model_dao: ModelDAO,
        artifact_dao: ArtifactDAO,
        *,
        display_name: str,
        path: str,
        tags: list[str] | None = None,
    ) -> LLMModel:
        """Register a model already present on disk."""
        # Guard against path traversal — ensure the resolved path is
        # within the configured model store root.
        resolved = Path(path).resolve()
        store_root = Path(settings.model_store_root).resolve()
        if not str(resolved).startswith(str(store_root)):
            msg = f"Path '{path}' resolves outside the model store root '{settings.model_store_root}'."
            raise ValueError(msg)

        model = await model_dao.create(
            display_name=display_name,
            source=ModelSource.LOCAL_PATH,
            tags=tags,
            status=ModelStatus.AVAILABLE,
        )
        artifacts = await asyncio.to_thread(scan_model_directory, path)
        if artifacts:
            await artifact_dao.create_batch(model.id, artifacts)
        return model

    # ------------------------------------------------------------------
    # HF cache auto-discovery
    # ------------------------------------------------------------------

    async def auto_import_hf_cache(
        self,
        model_dao: ModelDAO,
        artifact_dao: ArtifactDAO,
    ) -> list[LLMModel]:
        """Scan HF cache directories and auto-import any new models.

        Scans the app-managed cache (``model_store_root``), the
        default HuggingFace cache (``~/.cache/huggingface/hub``), and
        the host-mounted HF cache (``host_hf_cache_dir``) if configured.
        Models whose ``hf_repo_id`` is already registered are skipped.
        Newly imported models are tagged ``["local"]`` and set to
        AVAILABLE.

        Returns the list of newly created model records.
        """
        existing_models = await model_dao.list_all()
        registered_repos: set[str] = {
            m.hf_repo_id for m in existing_models if m.hf_repo_id
        }

        # Directories to scan -- deduplicated by resolved path
        cache_dirs: list[Path] = []
        seen_resolved: set[Path] = set()
        app_cache = Path(settings.model_store_root)
        default_cache = Path.home() / ".cache" / "huggingface" / "hub"

        candidates = [app_cache, default_cache]
        if settings.host_hf_cache_dir:
            candidates.append(Path(settings.host_hf_cache_dir))

        for d in candidates:
            resolved = d.resolve()
            if resolved.is_dir() and resolved not in seen_resolved:
                cache_dirs.append(d)
                seen_resolved.add(resolved)

        if not cache_dirs:
            return []

        imported: list[LLMModel] = []

        for cache_dir in cache_dirs:
            try:
                cache_info = scan_cache_dir(cache_dir)
            except Exception:
                log.debug("Could not scan HF cache at %s", cache_dir, exc_info=True)
                continue

            for repo in cache_info.repos:
                if repo.repo_type != "model":
                    continue
                if repo.repo_id in registered_repos:
                    continue

                # Pick the latest revision snapshot
                revisions = sorted(
                    repo.revisions,
                    key=lambda r: r.last_modified,
                    reverse=True,
                )
                if not revisions:
                    continue
                snapshot_path = revisions[0].snapshot_path
                if not snapshot_path.is_dir():
                    continue

                model = await model_dao.create(
                    display_name=repo.repo_id,
                    source=ModelSource.HUGGINGFACE,
                    hf_repo_id=repo.repo_id,
                    tags=["local"],
                    status=ModelStatus.AVAILABLE,
                )
                artifacts = await asyncio.to_thread(scan_model_directory, str(snapshot_path))
                if artifacts:
                    await artifact_dao.create_batch(model.id, artifacts)

                imported.append(model)
                registered_repos.add(repo.repo_id)
                log.info("Auto-imported HF cache model: %s", repo.repo_id)

        return imported

    # ------------------------------------------------------------------
    # Runtimes
    # ------------------------------------------------------------------

    async def create_runtime(
        self,
        runtime_dao: RuntimeDAO,
        provider_dao: ProviderDAO,
        model_dao: ModelDAO,
        artifact_dao: ArtifactDAO,
        *,
        name: str,
        provider_id: uuid.UUID,
        model_id: uuid.UUID,
        generic_config: dict[str, Any] | None = None,
        provider_config: dict[str, Any] | None = None,
        openai_compat: bool = True,
        target_node_id: uuid.UUID | None = None,
        placement_hints: dict[str, Any] | None = None,
        model_source: str | None = None,
        image_source: str | None = None,
    ) -> LLMRuntime:
        """Create a runtime, validate compatibility, and start the container."""
        # Fetch references
        provider = await provider_dao.get(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")
        model = await model_dao.get(model_id)
        if model is None:
            raise ValueError(f"Model {model_id} not found")

        # When the remote node will download from HuggingFace directly,
        # the model record may still be in "downloading" state on the
        # server — that's fine because the node fetches it independently.
        node_downloads_model = model_source == "download_from_hf"
        if not node_downloads_model and model.status != ModelStatus.AVAILABLE:
            raise ValueError(f"Model {model_id} is not available (status={model.status})")

        artifacts = await artifact_dao.list_by_model(model_id)
        adapter = get_adapter(provider.type)

        # Validate compatibility — skip for remote providers (no local
        # artifacts, model lives on the remote endpoint) and for
        # node-side HF downloads (artifacts don't exist locally yet).
        if provider.target != ProviderTarget.REMOTE_ENDPOINT and not node_downloads_model:
            compat = adapter.validate_model(model, artifacts)
            if not compat.compatible:
                raise ValueError(f"Model not compatible with {provider.type}: {compat.reason}")

        # Create DB record
        runtime = await runtime_dao.create(
            name=name,
            provider_id=provider_id,
            model_id=model_id,
            generic_config=generic_config,
            provider_config=provider_config,
            openai_compat=openai_compat,
        )

        # ── Remote endpoint providers — no container needed ──────────
        if provider.target == ProviderTarget.REMOTE_ENDPOINT:
            endpoint_url = provider.endpoint_url
            if not endpoint_url:
                if provider.litellm_provider:
                    # LiteLLM handles routing internally; use a sentinel so
                    # the gateway sync still has a non-null base_url.
                    endpoint_url = f"litellm://{provider.litellm_provider}"
                else:
                    raise ValueError("Remote provider has no endpoint_url configured")
            await runtime_dao.set_container_ref(runtime.id, None, endpoint_url)
            await runtime_dao.set_status(runtime.id, RuntimeStatus.RUNNING)
            log.info(
                "Remote runtime %r → %s (no container)",
                runtime.name,
                endpoint_url,
            )
            # Publish to gateway — remote runtimes are immediately routable
            await self.gateway_sync.publish_runtime(
                runtime_id=runtime.id,
                alias=runtime.name,
                base_url=endpoint_url,
                backend_provider_type=provider.type.value,
                is_remote=True,
                health_status="healthy",
                api_key_encrypted=provider.api_key_encrypted,
                litellm_provider=provider.litellm_provider,
                litellm_model=provider.litellm_model,
                extra_params=provider.extra_params,
            )
            return runtime

        # ── Node cluster mode (backend scheduler + node commands) ─────────
        if settings.node_cluster_enabled or target_node_id:
            node_service = self._build_node_control_service(runtime_dao.session)
            selected_node, explain = await self._select_node_for_runtime(
                node_service=node_service,
                provider=provider,
                runtime=runtime,
                target_node_id=target_node_id,
            )
            runtime.execution_target = "node"
            runtime.assigned_node_id = selected_node.id
            runtime.desired_state = "running"
            runtime.placement_explain_json = explain | {"placement_hints": placement_hints or {}}
            await node_service.upsert_workload_assignment(
                runtime_id=runtime.id,
                node_id=selected_node.id,
                desired_state=runtime.desired_state,
                actual_state="pending",
            )
            # Derive GPU request — vLLM and TGI always need GPU access
            gpu_request = (provider_config or {}).get("gpu_request", "")
            if not gpu_request and provider.type.value in ("vllm", "tgi"):
                gpu_request = "all"
            ipc_mode = (provider_config or {}).get("ipc_mode", "")
            if not ipc_mode and gpu_request and provider.type.value in ("vllm", "tgi"):
                ipc_mode = "host"

            command = await node_service.issue_command(
                node_id=selected_node.id,
                command_type=NodeCommandType.DEPLOY_WORKLOAD.value,
                payload={
                    "runtime_id": str(runtime.id),
                    "runtime_name": runtime.name,
                    "container_name": self._node_container_name(runtime.name),
                    "provider_id": str(provider.id),
                    "provider_type": provider.type.value,
                    "model_id": str(model.id),
                    "generic_config": generic_config or {},
                    "provider_config": provider_config or {},
                    "openai_compat": openai_compat,
                    "placement_hints": placement_hints or {},
                    "model_sync": self._build_model_sync_payload(
                        model, source=model_source or "sync_from_server",
                    ),
                    "image_source": image_source,
                    "gpu_request": gpu_request,
                    "ipc_mode": ipc_mode,
                    "shm_size": (provider_config or {}).get("shm_size", ""),
                    "memory_limit": (provider_config or {}).get("memory_limit", ""),
                    "cpu_limit": (provider_config or {}).get("cpu_limit", ""),
                    "container_port": (provider_config or {}).get("container_port", ""),
                },
                issued_by=None,
                correlation_id=str(runtime.id),
                timeout_sec=settings.node_command_default_timeout_sec,
                idempotency_key=f"runtime:{runtime.id}:deploy",
            )
            runtime.last_command_id = command.id
            await runtime_dao.set_status(runtime.id, RuntimeStatus.STARTING)
            return runtime

        # ── Local Docker providers — build and start container ───────
        # Build container spec
        spec: ContainerSpec = adapter.build_container_spec(
            runtime=runtime,
            provider=provider,
            model=model,
            artifacts=artifacts,
            model_store_root=settings.model_store_root,
        )

        # Commit the runtime record *before* starting slow Docker
        # operations so the DB connection is not held idle in a
        # transaction (avoids asyncpg command_timeout).
        await runtime_dao.session.commit()

        # Create and start the container
        try:
            container_info = await self.docker.create_container(
                image=spec.image,
                name=spec.name,
                cmd=spec.cmd,
                entrypoint=spec.entrypoint,
                env=spec.env,
                ports=spec.ports,
                volumes=spec.volumes,
                gpu_devices=spec.gpu_devices,
                gpu_vendor=spec.gpu_vendor,
                devices=spec.devices,
                security_opt=spec.security_opt,
                group_add=spec.group_add,
                healthcheck=spec.healthcheck,
                labels=spec.labels,
                ipc_mode=spec.ipc_mode,
                auto_start=True,
            )
            container_id = container_info.get("Id", "")
            # Determine endpoint URL from port bindings
            endpoint_url = self._extract_endpoint(container_info)
            await runtime_dao.set_container_ref(runtime.id, container_id, endpoint_url)
            await runtime_dao.set_status(runtime.id, RuntimeStatus.STARTING)
            # Publish to gateway with unknown health — will be promoted
            # to healthy once reconciliation detects the container is up.
            if endpoint_url:
                await self.gateway_sync.publish_runtime(
                    runtime_id=runtime.id,
                    alias=runtime.name,
                    base_url=endpoint_url,
                    backend_provider_type=provider.type.value,
                    is_remote=False,
                    health_status="unknown",
                    litellm_model=model.hf_repo_id or None,
                )
        except Exception as exc:
            log.exception("Failed to start runtime container: %s", exc)
            # Roll back: remove the runtime DB record so the provider
            # can be cleaned up without a dangling reference.
            await runtime_dao.delete(runtime.id)
            raise

        return runtime

    async def start_runtime(
        self,
        runtime_dao: RuntimeDAO,
        runtime_id: uuid.UUID,
    ) -> LLMRuntime:
        """Start an existing stopped runtime."""
        runtime = await runtime_dao.get(runtime_id)
        if runtime is None:
            raise ValueError(f"Runtime {runtime_id} not found")

        if runtime.execution_target == "node" and runtime.assigned_node_id:
            node_service = self._build_node_control_service(runtime_dao.session)
            payload = await self._build_node_deploy_payload(runtime, runtime_dao.session)
            command = await node_service.issue_command(
                node_id=runtime.assigned_node_id,
                command_type=NodeCommandType.START_WORKLOAD.value,
                payload=payload,
                issued_by=None,
                correlation_id=str(runtime.id),
                timeout_sec=settings.node_command_default_timeout_sec,
                idempotency_key=f"runtime:{runtime.id}:start",
            )
            runtime.last_command_id = command.id
            await runtime_dao.set_status(runtime_id, RuntimeStatus.STARTING)
            return runtime

        # Remote runtimes have no container — just mark as running
        if not runtime.container_ref:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.RUNNING)
            await self.gateway_sync.set_instance_health(
                runtime_id=runtime_id, health_status="healthy",
            )
            return runtime

        await self.docker.start(runtime.container_ref)
        await runtime_dao.set_status(runtime_id, RuntimeStatus.STARTING)
        return runtime

    async def stop_runtime(
        self,
        runtime_dao: RuntimeDAO,
        runtime_id: uuid.UUID,
    ) -> LLMRuntime:
        """Stop a running runtime."""
        runtime = await runtime_dao.get(runtime_id)
        if runtime is None:
            raise ValueError(f"Runtime {runtime_id} not found")

        if runtime.execution_target == "node" and runtime.assigned_node_id:
            node_service = self._build_node_control_service(runtime_dao.session)
            command = await node_service.issue_command(
                node_id=runtime.assigned_node_id,
                command_type=NodeCommandType.STOP_WORKLOAD.value,
                payload={
                    "runtime_id": str(runtime.id),
                    "runtime_name": runtime.name,
                    "container_name": self._node_container_name(runtime.name),
                },
                issued_by=None,
                correlation_id=str(runtime.id),
                timeout_sec=settings.node_command_default_timeout_sec,
                idempotency_key=f"runtime:{runtime.id}:stop",
            )
            runtime.last_command_id = command.id
            await runtime_dao.set_status(runtime_id, RuntimeStatus.STOPPING)
            return runtime

        # Remote runtimes have no container — just mark as stopped
        if not runtime.container_ref:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.STOPPED)
            await self.gateway_sync.set_instance_health(
                runtime_id=runtime_id, health_status="unhealthy",
            )
            return runtime

        await runtime_dao.set_status(runtime_id, RuntimeStatus.STOPPING)
        await runtime_dao.session.commit()
        await self.docker.stop(runtime.container_ref)
        await runtime_dao.set_status(runtime_id, RuntimeStatus.STOPPED)
        await self.gateway_sync.set_instance_health(
            runtime_id=runtime_id, health_status="unhealthy",
        )
        return runtime

    async def restart_runtime(
        self,
        runtime_dao: RuntimeDAO,
        runtime_id: uuid.UUID,
    ) -> LLMRuntime:
        """Restart a runtime container."""
        runtime = await runtime_dao.get(runtime_id)
        if runtime is None:
            raise ValueError(f"Runtime {runtime_id} not found")

        if runtime.execution_target == "node" and runtime.assigned_node_id:
            node_service = self._build_node_control_service(runtime_dao.session)
            payload = await self._build_node_deploy_payload(runtime, runtime_dao.session)
            command = await node_service.issue_command(
                node_id=runtime.assigned_node_id,
                command_type=NodeCommandType.RESTART_WORKLOAD.value,
                payload=payload,
                issued_by=None,
                correlation_id=str(runtime.id),
                timeout_sec=settings.node_command_default_timeout_sec,
                idempotency_key=f"runtime:{runtime.id}:restart",
            )
            runtime.last_command_id = command.id
            await runtime_dao.set_status(runtime_id, RuntimeStatus.STARTING)
            return runtime

        # Remote runtimes have no container — just toggle status
        if not runtime.container_ref:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.RUNNING)
            return runtime

        await runtime_dao.session.commit()
        await self.docker.restart(runtime.container_ref)
        await runtime_dao.set_status(runtime_id, RuntimeStatus.STARTING)
        return runtime

    async def update_and_restart_runtime(
        self,
        runtime_dao: RuntimeDAO,
        provider_dao: ProviderDAO,
        model_dao: ModelDAO,
        artifact_dao: ArtifactDAO,
        runtime_id: uuid.UUID,
        *,
        name: str | None = None,
        generic_config: dict | None = ...,
        provider_config: dict | None = ...,
        openai_compat: bool | None = None,
        target_node_id: uuid.UUID | None = None,
        placement_hints: dict[str, Any] | None = None,
    ) -> LLMRuntime:
        """Update runtime config, tear down the old container, and start a new one."""
        runtime = await runtime_dao.get(runtime_id)
        if runtime is None:
            raise ValueError(f"Runtime {runtime_id} not found")

        # Update DB fields
        runtime = await runtime_dao.update_config(
            runtime_id,
            name=name,
            generic_config=generic_config,
            provider_config=provider_config,
            openai_compat=openai_compat,
        )
        if runtime is None:
            raise ValueError(f"Runtime {runtime_id} not found")

        if runtime.execution_target == "node":
            node_service = self._build_node_control_service(runtime_dao.session)
            provider = await provider_dao.get(runtime.provider_id)
            if provider is None:
                raise ValueError(f"Provider {runtime.provider_id} not found")
            model = await model_dao.get(runtime.model_id)
            if target_node_id is not None:
                runtime.assigned_node_id = target_node_id
            if runtime.assigned_node_id is None:
                selected_node, explain = await self._select_node_for_runtime(
                    node_service=node_service,
                    provider=provider,
                    runtime=runtime,
                    target_node_id=target_node_id,
                )
                runtime.assigned_node_id = selected_node.id
                runtime.placement_explain_json = explain | {"placement_hints": placement_hints or {}}
            prov_config = runtime.provider_config or {}
            gpu_request = prov_config.get("gpu_request", "")
            if not gpu_request and provider.type.value in ("vllm", "tgi"):
                gpu_request = "all"
            ipc_mode = prov_config.get("ipc_mode", "")
            if not ipc_mode and gpu_request and provider.type.value in ("vllm", "tgi"):
                ipc_mode = "host"
            model_source = prov_config.get("model_source") or "sync_from_server"
            image_source = prov_config.get("image_source") or "pull_from_registry"
            command = await node_service.issue_command(
                node_id=runtime.assigned_node_id,
                command_type=NodeCommandType.UPDATE_WORKLOAD.value,
                payload={
                    "runtime_id": str(runtime.id),
                    "runtime_name": runtime.name,
                    "container_name": self._node_container_name(runtime.name),
                    "provider_id": str(provider.id),
                    "provider_type": provider.type.value,
                    "model_id": str(runtime.model_id),
                    "generic_config": runtime.generic_config or {},
                    "provider_config": prov_config,
                    "openai_compat": runtime.openai_compat,
                    "placement_hints": placement_hints or {},
                    "model_sync": self._build_model_sync_payload(
                        model, source=model_source,
                    ) if model else {},
                    "image_source": image_source,
                    "gpu_request": gpu_request,
                    "ipc_mode": ipc_mode,
                    "shm_size": prov_config.get("shm_size", ""),
                    "memory_limit": prov_config.get("memory_limit", ""),
                    "cpu_limit": prov_config.get("cpu_limit", ""),
                    "container_port": prov_config.get("container_port", ""),
                },
                issued_by=None,
                correlation_id=str(runtime.id),
                timeout_sec=settings.node_command_default_timeout_sec,
                idempotency_key=f"runtime:{runtime.id}:update",
            )
            runtime.last_command_id = command.id
            await runtime_dao.set_status(runtime.id, RuntimeStatus.STARTING)
            return runtime

        # Remote runtimes — just mark running, nothing else to do
        provider = await provider_dao.get(runtime.provider_id)
        if provider and provider.target == ProviderTarget.REMOTE_ENDPOINT:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.RUNNING)
            return runtime

        # Prepare everything we need from the DB before Docker work
        model = await model_dao.get(runtime.model_id)
        if model is None:
            raise ValueError(f"Model {runtime.model_id} not found")
        artifacts = await artifact_dao.list_by_model(runtime.model_id)
        adapter = get_adapter(provider.type)

        spec: ContainerSpec = adapter.build_container_spec(
            runtime=runtime,
            provider=provider,
            model=model,
            artifacts=artifacts,
            model_store_root=settings.model_store_root,
        )

        # Commit DB changes *before* starting slow Docker operations so
        # the connection is not held idle in a transaction (which triggers
        # asyncpg command_timeout / idle_in_transaction_session_timeout).
        await runtime_dao.session.commit()

        # Tear down old container (best-effort)
        await self._teardown_runtime(runtime)

        # Rebuild with updated config
        container_info = await self.docker.create_container(
            image=spec.image,
            name=spec.name,
            cmd=spec.cmd,
            entrypoint=spec.entrypoint,
            env=spec.env,
            ports=spec.ports,
            volumes=spec.volumes,
            gpu_devices=spec.gpu_devices,
            gpu_vendor=spec.gpu_vendor,
            devices=spec.devices,
            security_opt=spec.security_opt,
            group_add=spec.group_add,
            healthcheck=spec.healthcheck,
            labels=spec.labels,
            ipc_mode=spec.ipc_mode,
            auto_start=True,
        )
        container_id = container_info.get("Id", "")
        endpoint_url = self._extract_endpoint(container_info)
        await runtime_dao.set_container_ref(runtime.id, container_id, endpoint_url)
        await runtime_dao.set_status(runtime.id, RuntimeStatus.STARTING)
        return runtime

    async def delete_runtime(
        self,
        runtime_dao: RuntimeDAO,
        runtime_id: uuid.UUID,
    ) -> None:
        """Stop, remove the container, and delete the runtime record."""
        runtime = await runtime_dao.get(runtime_id)
        if runtime is None:
            raise ValueError(f"Runtime {runtime_id} not found")
        if runtime.execution_target == "node" and runtime.assigned_node_id:
            node_service = self._build_node_control_service(runtime_dao.session)
            await node_service.issue_command(
                node_id=runtime.assigned_node_id,
                command_type=NodeCommandType.REMOVE_WORKLOAD.value,
                payload={
                    "runtime_id": str(runtime.id),
                    "runtime_name": runtime.name,
                    "container_name": self._node_container_name(runtime.name),
                },
                issued_by=None,
                correlation_id=str(runtime.id),
                timeout_sec=settings.node_command_default_timeout_sec,
                idempotency_key=f"runtime:{runtime.id}:remove",
            )
            await self.gateway_sync.unpublish_runtime(runtime_id=runtime_id, alias=runtime.name)
            await runtime_dao.delete(runtime_id)
            return
        # Flush pending work and release the idle transaction before
        # slow Docker teardown.
        await runtime_dao.session.commit()
        await self._teardown_runtime(runtime)
        # Remove gateway routing records before deleting the runtime
        await self.gateway_sync.unpublish_runtime(
            runtime_id=runtime_id, alias=runtime.name,
        )
        await runtime_dao.delete(runtime_id)

    async def delete_provider(
        self,
        provider_dao: ProviderDAO,
        runtime_dao: RuntimeDAO,
        provider_id: uuid.UUID,
        *,
        model_dao: ModelDAO | None = None,
    ) -> None:
        """Cascade-delete: stop & remove runtimes, then delete the provider.

        Auto-provisioned placeholder models that become orphaned (no
        remaining runtimes reference them) are cleaned up as well.
        """
        provider = await provider_dao.get(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")

        # Tear down every runtime that belongs to this provider.
        runtimes = await runtime_dao.list_by_provider(provider_id)
        # Collect model_ids so we can check for orphans after removal.
        orphan_candidate_ids: set[uuid.UUID] = {rt.model_id for rt in runtimes}
        # Flush pending work before slow Docker teardown.
        await runtime_dao.session.commit()
        for rt in runtimes:
            await self._teardown_runtime(rt)
            # Remove gateway routing records
            await self.gateway_sync.unpublish_runtime(
                runtime_id=rt.id, alias=rt.name,
            )
            await runtime_dao.delete(rt.id)

        await provider_dao.delete(provider_id)

        # ── Clean up orphaned auto-provisioned models ─────────────
        if model_dao is not None:
            for mid in orphan_candidate_ids:
                model = await model_dao.get(mid)
                if model is None:
                    continue
                # Only auto-clean models that were machine-created, not
                # user-managed models (downloaded from HF, registered, etc.).
                if not (model.tags and "auto-provisioned" in model.tags):
                    continue
                if await model_dao.is_used_by_any_runtime(mid):
                    continue
                log.info(
                    "Cleaning up orphaned auto-provisioned model %s (%s)",
                    mid, model.display_name,
                )
                await model_dao.delete(mid)

    async def _teardown_runtime(self, runtime: LLMRuntime) -> None:
        """Stop and remove the Docker container for a runtime (best-effort)."""
        if not runtime.container_ref:
            return
        # Node runtimes are cleaned up via REMOVE_WORKLOAD command, not
        # local Docker — skip here to avoid bogus warnings.
        if runtime.execution_target == "node":
            return
        try:
            await self.docker.stop(runtime.container_ref)
        except Exception:
            log.warning("Could not stop container %s during delete", runtime.container_ref)
        try:
            await self.docker.delete(runtime.container_ref, force=True)
        except Exception:
            log.warning("Could not delete container %s", runtime.container_ref)

    async def reconcile_runtime_status(
        self,
        runtime_dao: RuntimeDAO,
        runtime: LLMRuntime,
    ) -> LLMRuntime:
        """Check actual Docker container state and reconcile the DB status.

        Handles the following transitions:
        - STARTING → RUNNING  when container is healthy
        - STARTING/RUNNING → ERROR  when container is dead/exited
        - CREATING → ERROR  when container never started
        """
        # Remote runtimes (no container) — nothing to reconcile
        if not runtime.container_ref:
            return runtime

        # Node-deployed runtimes are managed by the remote agent, not
        # the local Docker daemon — skip local container inspection.
        if runtime.execution_target == "node":
            return runtime

        # Only reconcile transient statuses
        if runtime.status not in (
            RuntimeStatus.CREATING,
            RuntimeStatus.STARTING,
            RuntimeStatus.RUNNING,
        ):
            return runtime

        try:
            info = await self.docker.inspect_container(runtime.container_ref)
            state = info.get("State", {})
            docker_status = state.get("Status", "").lower()

            if docker_status == "running":
                # Container is up — promote STARTING/CREATING → RUNNING
                if runtime.status in (RuntimeStatus.CREATING, RuntimeStatus.STARTING):
                    await runtime_dao.set_status(runtime.id, RuntimeStatus.RUNNING)
                    log.info(
                        "Runtime %r reconciled %s → running (container alive)",
                        runtime.name,
                        runtime.status.value,
                    )
                    # Promote gateway instance to healthy now that container is up
                    await self.gateway_sync.set_instance_health(
                        runtime_id=runtime.id, health_status="healthy",
                    )
            elif docker_status in ("exited", "dead", "removing"):
                # Container has crashed / stopped unexpectedly
                exit_code = state.get("ExitCode", -1)
                runtime.status_message = f"Container {docker_status} (exit code {exit_code})"
                await runtime_dao.set_status(runtime.id, RuntimeStatus.ERROR)
                log.warning(
                    "Runtime %r container %s is %s (exit=%s) — marking ERROR",
                    runtime.name,
                    runtime.container_ref,
                    docker_status,
                    exit_code,
                )
                await self.gateway_sync.set_instance_health(
                    runtime_id=runtime.id, health_status="unhealthy",
                )
        except Exception:
            # Container doesn't exist at all — mark as error
            if runtime.status in (RuntimeStatus.CREATING, RuntimeStatus.STARTING):
                runtime.status_message = f"Container {runtime.container_ref} not found"
                await runtime_dao.set_status(runtime.id, RuntimeStatus.ERROR)
                log.warning(
                    "Runtime %r container %s not found — marking ERROR",
                    runtime.name,
                    runtime.container_ref,
                )
                await self.gateway_sync.set_instance_health(
                    runtime_id=runtime.id, health_status="unhealthy",
                )

        # Re-fetch to return the updated object
        updated = await runtime_dao.get(runtime.id)
        return updated or runtime

    async def reconcile_all_runtimes(
        self,
        runtime_dao: RuntimeDAO,
    ) -> list[LLMRuntime]:
        """Reconcile status for all runtimes and return the updated list."""
        runtimes = await runtime_dao.list_all()
        result: list[LLMRuntime] = []
        for rt in runtimes:
            reconciled = await self.reconcile_runtime_status(runtime_dao, rt)
            result.append(reconciled)
        return result

    async def get_runtime_health(
        self,
        runtime_dao: RuntimeDAO,
        provider_dao: ProviderDAO,
        runtime_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Probe runtime health via its adapter.

        Also reconciles the DB status with actual container state:
        promotes STARTING → RUNNING when healthy, or marks ERROR when
        the container has died.
        """
        runtime = await runtime_dao.get(runtime_id)
        if runtime is None:
            raise ValueError(f"Runtime {runtime_id} not found")
        provider = await provider_dao.get(runtime.provider_id)
        if provider is None:
            raise ValueError(f"Provider {runtime.provider_id} not found")

        # Reconcile container state first
        runtime = await self.reconcile_runtime_status(runtime_dao, runtime)

        adapter = get_adapter(provider.type)
        health = await adapter.get_health(runtime)

        # Promote STARTING → RUNNING when the health probe passes
        if health.healthy and runtime.status == RuntimeStatus.STARTING:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.RUNNING)
            log.info("Runtime %r is healthy — promoted to RUNNING", runtime.name)

        return {"healthy": health.healthy, "detail": health.detail}

    # ------------------------------------------------------------------
    # Delete model (safety check)
    # ------------------------------------------------------------------

    async def delete_model(
        self,
        model_dao: ModelDAO,
        model_id: uuid.UUID,
        *,
        job_dao: DownloadJobDAO | None = None,
        artifact_dao: ArtifactDAO | None = None,
    ) -> None:
        """Delete a model, its jobs, and artifacts.

        Running / queued jobs are automatically cancelled before deletion.
        Raises ValueError if the model is used by a currently-running runtime.
        """
        if await model_dao.is_used_by_running_runtime(model_id):
            raise ValueError("Cannot delete model that is used by a running runtime")

        # Cancel any active download jobs first
        if job_dao is not None:
            cancelled = await job_dao.cancel_active_for_model(model_id)
            if cancelled:
                log.info("Cancelled %d active jobs for model %s", cancelled, model_id)
            await job_dao.delete_by_model(model_id)

        # Delete artifacts
        if artifact_dao is not None:
            await artifact_dao.delete_by_model(model_id)

        # Delete the model itself
        deleted = await model_dao.delete(model_id)
        if not deleted:
            raise ValueError(f"Model {model_id} not found")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_node_control_service(self, session) -> NodeControlService:
        """Construct node control service from the current request session."""
        dao = NodeControlDAO(session)
        return NodeControlService(
            dao=dao,
            pepper=settings.settings_master_key,
            enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
            default_command_timeout_sec=settings.node_command_default_timeout_sec,
            gateway_sync=self.gateway_sync,
        )

    async def _select_node_for_runtime(
        self,
        *,
        node_service: NodeControlService,
        provider: LLMProvider,
        runtime: LLMRuntime,
        target_node_id: uuid.UUID | None,
    ) -> tuple[InfraNode, dict[str, Any]]:
        """Select node by pinning or backend scheduler."""
        if target_node_id is not None:
            node = await node_service.get_node_record(node_id=target_node_id)
            if node is None:
                raise ValueError(f"Target node {target_node_id} not found")
            return node, {
                "selected_node_id": str(node.id),
                "selection_mode": "pinned",
                "runtime_name": runtime.name,
                "ts": datetime.now(tz=UTC).isoformat(),
            }
        node, explain = await node_service.schedule_node_for_runtime(
            provider=provider,
            runtime_name=runtime.name,
        )
        explain["selection_mode"] = "scheduler"
        return node, explain

    @staticmethod
    def _extract_endpoint(container_info: dict[str, Any]) -> str | None:
        """Extract the host endpoint URL from container inspect data."""
        try:
            network_settings = container_info.get("NetworkSettings", {})
            port_map = network_settings.get("Ports", {})
            bindings = port_map.get("8000/tcp")
            if bindings and isinstance(bindings, list) and bindings[0]:
                host_ip = bindings[0].get("HostIp", "127.0.0.1")
                host_port = bindings[0].get("HostPort", "8000")
                if host_ip in ("", "0.0.0.0"):
                    host_ip = "127.0.0.1"
                return f"http://{host_ip}:{host_port}"
        except Exception:
            log.warning("Could not extract endpoint from container info")
        return None

    @staticmethod
    def _build_model_sync_payload(
        model: LLMModel,
        *,
        source: str = "sync_from_server",
    ) -> dict[str, Any] | None:
        """Build the ``model_sync`` dict for remote node deployment.

        *source* controls how the model reaches the node:

        - ``"sync_from_server"`` — include the full blob manifest so
          the agent pulls from this backend's file server.
        - ``"download_from_hf"`` — include only the ``hf_repo_id`` so
          the agent's container can download directly from HuggingFace.
        """
        if not model.hf_repo_id:
            return None

        base: dict[str, Any] = {
            "model_id": str(model.id),
            "hf_repo_id": model.hf_repo_id,
            "source": source,
        }

        if source == "download_from_hf":
            # The node has internet — just tell the agent which repo to fetch.
            return base

        # sync_from_server — include full cache manifest
        from llm_port_backend.web.api.node_files.views import (
            _build_cache_manifest,
            _model_cache_dir,
        )

        model_dir = _model_cache_dir(model.hf_repo_id)
        if model_dir is None:
            return None

        manifest = _build_cache_manifest(model_dir)
        if not manifest["blobs"]:
            return None

        return base | manifest
