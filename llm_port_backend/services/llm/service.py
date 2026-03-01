"""High-level LLM orchestration service.

Coordinates providers, models, runtimes, download jobs, and the Docker
service to implement the full LLM management workflow.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from llm_port_backend.db.dao.llm_dao import (
    ArtifactDAO,
    DownloadJobDAO,
    ModelDAO,
    ProviderDAO,
    RuntimeDAO,
)
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
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.llm.base import ContainerSpec
from llm_port_backend.services.llm.registry import get_adapter
from llm_port_backend.services.llm.scanner import scan_model_directory
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)


class LLMService:
    """Facade that ties together adapters, DAOs, and Docker."""

    def __init__(self, docker: DockerService) -> None:
        self.docker = docker

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
    ) -> LLMProvider:
        """Register a new LLM engine provider.

        For ``REMOTE_ENDPOINT`` providers, ``endpoint_url`` is required
        and points to an existing OpenAI-compatible API (vLLM, Ollama,
        TGI, NVIDIA NIM, etc.).  No Docker container is managed — the
        runtime simply proxies to that URL.
        """
        if target == ProviderTarget.REMOTE_ENDPOINT and not endpoint_url:
            raise ValueError("endpoint_url is required for remote providers")

        adapter = get_adapter(type_)
        capabilities = adapter.default_capabilities()
        if target == ProviderTarget.REMOTE_ENDPOINT:
            capabilities["remote"] = True

        return await provider_dao.create(
            name=name,
            type_=type_,
            target=target,
            capabilities=capabilities,
            endpoint_url=endpoint_url,
            api_key_encrypted=api_key,  # TODO: encrypt with SettingsCrypto
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

        # Build target directory
        org_repo = hf_repo_id.replace("/", "/")
        rev = hf_revision or "main"
        target_dir = f"{settings.model_store_root}/hf/{org_repo}/{rev}"

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
        artifacts = scan_model_directory(path)
        if artifacts:
            await artifact_dao.create_batch(model.id, artifacts)
        return model

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
    ) -> LLMRuntime:
        """Create a runtime, validate compatibility, and start the container."""
        # Fetch references
        provider = await provider_dao.get(provider_id)
        if provider is None:
            raise ValueError(f"Provider {provider_id} not found")
        model = await model_dao.get(model_id)
        if model is None:
            raise ValueError(f"Model {model_id} not found")
        if model.status != ModelStatus.AVAILABLE:
            raise ValueError(f"Model {model_id} is not available (status={model.status})")

        artifacts = await artifact_dao.list_by_model(model_id)
        adapter = get_adapter(provider.type)

        # Validate compatibility
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
                raise ValueError("Remote provider has no endpoint_url configured")
            await runtime_dao.set_container_ref(runtime.id, None, endpoint_url)
            await runtime_dao.set_status(runtime.id, RuntimeStatus.RUNNING)
            log.info(
                "Remote runtime %r → %s (no container)",
                runtime.name,
                endpoint_url,
            )
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

        # Create and start the container
        try:
            container_info = await self.docker.create_container(
                image=spec.image,
                name=spec.name,
                cmd=spec.cmd,
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
                auto_start=True,
            )
            container_id = container_info.get("Id", "")
            # Determine endpoint URL from port bindings
            endpoint_url = self._extract_endpoint(container_info)
            await runtime_dao.set_container_ref(runtime.id, container_id, endpoint_url)
            await runtime_dao.set_status(runtime.id, RuntimeStatus.STARTING)
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

        # Remote runtimes have no container — just mark as running
        if not runtime.container_ref:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.RUNNING)
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

        # Remote runtimes have no container — just mark as stopped
        if not runtime.container_ref:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.STOPPED)
            return runtime

        await runtime_dao.set_status(runtime_id, RuntimeStatus.STOPPING)
        await self.docker.stop(runtime.container_ref)
        await runtime_dao.set_status(runtime_id, RuntimeStatus.STOPPED)
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

        # Remote runtimes have no container — just toggle status
        if not runtime.container_ref:
            await runtime_dao.set_status(runtime_id, RuntimeStatus.RUNNING)
            return runtime

        await self.docker.restart(runtime.container_ref)
        await runtime_dao.set_status(runtime_id, RuntimeStatus.STARTING)
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
        if runtime.container_ref:
            try:
                await self.docker.stop(runtime.container_ref)
            except Exception:
                log.warning("Could not stop container %s during delete", runtime.container_ref)
            try:
                await self.docker.delete(runtime.container_ref, force=True)
            except Exception:
                log.warning("Could not delete container %s", runtime.container_ref)
        await runtime_dao.delete(runtime_id)

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
            elif docker_status in ("exited", "dead", "removing"):
                # Container has crashed / stopped unexpectedly
                exit_code = state.get("ExitCode", -1)
                await runtime_dao.set_status(runtime.id, RuntimeStatus.ERROR)
                log.warning(
                    "Runtime %r container %s is %s (exit=%s) — marking ERROR",
                    runtime.name,
                    runtime.container_ref,
                    docker_status,
                    exit_code,
                )
        except Exception:
            # Container doesn't exist at all — mark as error
            if runtime.status in (RuntimeStatus.CREATING, RuntimeStatus.STARTING):
                await runtime_dao.set_status(runtime.id, RuntimeStatus.ERROR)
                log.warning(
                    "Runtime %r container %s not found — marking ERROR",
                    runtime.name,
                    runtime.container_ref,
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
