"""vLLM provider adapter — MVP engine."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from airgap_backend.db.models.llm import (
    ArtifactFormat,
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ModelArtifact,
    ProviderType,
)
from airgap_backend.services.llm.base import (
    CompatResult,
    ContainerSpec,
    HealthStatus,
    ProviderAdapter,
)
from airgap_backend.services.llm.registry import register_adapter
from airgap_backend.settings import settings

log = logging.getLogger(__name__)

# Nanosecond helpers for Docker healthcheck intervals
_SECOND_NS = 1_000_000_000


class VLLMAdapter(ProviderAdapter):
    """Maps generic runtime config to vLLM Docker container args."""

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_model(
        self,
        model: LLMModel,
        artifacts: list[ModelArtifact],
    ) -> CompatResult:
        """vLLM works best with safetensors; reject pure GGUF-only models."""
        if not artifacts:
            return CompatResult(compatible=False, reason="No artifacts found for model.")
        formats = {a.format for a in artifacts}
        if formats == {ArtifactFormat.GGUF}:
            return CompatResult(
                compatible=False,
                reason="vLLM does not support GGUF-only models. Use llama.cpp instead.",
            )
        return CompatResult(compatible=True)

    # ------------------------------------------------------------------
    # Container spec
    # ------------------------------------------------------------------

    def build_container_spec(
        self,
        runtime: LLMRuntime,
        provider: LLMProvider,
        model: LLMModel,
        artifacts: list[ModelArtifact],
        model_store_root: str,
    ) -> ContainerSpec:
        """
        Build a Docker container spec for vLLM.

        The model directory is mounted read-only into the container at
        ``/models/<hf_repo_id or model_id>``.
        """
        gc: dict[str, Any] = runtime.generic_config or {}
        pc: dict[str, Any] = runtime.provider_config or {}

        # Resolve the model path from the first artifact's directory
        model_dir = self._resolve_model_dir(model, artifacts, model_store_root)
        container_model_path = f"/models/{model.hf_repo_id or str(model.id)}"

        image = pc.get("image", settings.default_vllm_image)

        # Build vLLM CLI args
        cmd = ["--model", container_model_path]

        # Generic config → vLLM flags
        if max_model_len := gc.get("max_model_len"):
            cmd += ["--max-model-len", str(max_model_len)]
        if dtype := gc.get("dtype"):
            cmd += ["--dtype", dtype]
        if gpu_mem := gc.get("gpu_memory_utilization"):
            cmd += ["--gpu-memory-utilization", str(gpu_mem)]
        if tp := gc.get("tensor_parallel_size"):
            cmd += ["--tensor-parallel-size", str(tp)]
        if gc.get("enable_metrics"):
            cmd += ["--enable-metrics"]

        # Provider overlay → extra args
        if extra_args := pc.get("extra_args"):
            cmd += extra_args

        # Host port — pick from config or let Docker assign
        host_port = str(gc.get("host_port", "0"))
        ports = {
            "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": host_port}],
        }

        volumes = [f"{model_dir}:{container_model_path}:ro"]

        # GPU devices
        gpu_devices = gc.get("gpu_devices", "all")

        # Environment
        env: list[str] = []
        if gc.get("log_level"):
            env.append(f"VLLM_LOG_LEVEL={gc['log_level']}")

        # Healthcheck
        healthcheck = {
            "Test": ["CMD-SHELL", "curl -sf http://localhost:8000/health || exit 1"],
            "Interval": 30 * _SECOND_NS,
            "Timeout": 10 * _SECOND_NS,
            "Retries": 5,
            "StartPeriod": 120 * _SECOND_NS,  # vLLM takes time to load models
        }

        labels = {
            "llm-port.service": "llm-runtime",
            "llm-port.runtime_id": str(runtime.id),
            "llm-port.provider": "vllm",
        }

        return ContainerSpec(
            image=image,
            name=f"llm-port-vllm-{runtime.name}",
            cmd=cmd,
            env=env or None,
            ports=ports,
            volumes=volumes,
            gpu_devices=gpu_devices,
            healthcheck=healthcheck,
            labels=labels,
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def get_health(self, runtime: LLMRuntime) -> HealthStatus:
        """Hit the vLLM ``/health`` endpoint."""
        if not runtime.endpoint_url:
            return HealthStatus(healthy=False, detail="No endpoint URL configured")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{runtime.endpoint_url}/health")
                if resp.status_code == 200:
                    return HealthStatus(healthy=True)
                return HealthStatus(healthy=False, detail=f"HTTP {resp.status_code}")
        except Exception as exc:  # noqa: BLE001
            return HealthStatus(healthy=False, detail=str(exc))

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def default_capabilities(self) -> dict[str, Any]:
        return {
            "supports_gpu": True,
            "supports_openai_compat": True,
            "supports_quant": True,
            "artifact_formats": ["safetensors"],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_model_dir(
        model: LLMModel,
        artifacts: list[ModelArtifact],
        model_store_root: str,
    ) -> str:
        """Determine the host-side model directory from artifacts."""
        if not artifacts:
            # Fallback: construct from HF conventions
            if model.hf_repo_id:
                parts = model.hf_repo_id.replace("/", "/")
                rev = model.hf_revision or "main"
                return f"{model_store_root}/hf/{parts}/{rev}"
            return f"{model_store_root}/imports/{model.id}"

        # Use the parent directory of the first artifact path
        from pathlib import PurePosixPath  # noqa: PLC0415

        first_path = PurePosixPath(artifacts[0].path)
        return str(first_path.parent)


# Self-register on import
register_adapter(ProviderType.VLLM, VLLMAdapter)
