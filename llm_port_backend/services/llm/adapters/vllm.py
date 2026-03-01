"""vLLM provider adapter — MVP engine with multi-vendor GPU support."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from llm_port_backend.db.models.llm import (
    ArtifactFormat,
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ModelArtifact,
    ProviderType,
)
from llm_port_backend.services.gpu.detector import detect_gpus
from llm_port_backend.services.gpu.types import GpuVendor
from llm_port_backend.services.llm.base import (
    CompatResult,
    ContainerSpec,
    HealthStatus,
    ProviderAdapter,
)
from llm_port_backend.services.llm.registry import register_adapter
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)

# Nanosecond helpers for Docker healthcheck intervals
_SECOND_NS = 1_000_000_000

# ── Image selection per GPU vendor ────────────────────────────────────
# vLLM publishes separate container images for CUDA and ROCm.
# The correct image is chosen based on the detected GPU vendor.
_VLLM_IMAGES: dict[GpuVendor, str] = {
    GpuVendor.NVIDIA: settings.default_vllm_image,
    GpuVendor.AMD: settings.default_vllm_rocm_image,
    # Intel and Apple are not supported by vLLM — CPU fallback
}


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
        """VLLM works best with safetensors; reject pure GGUF-only models."""
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

        GPU vendor auto-detection selects the correct container image
        (CUDA vs ROCm) and Docker passthrough mechanism automatically.
        """
        gc: dict[str, Any] = runtime.generic_config or {}
        pc: dict[str, Any] = runtime.provider_config or {}

        # Resolve the model path from the first artifact's directory
        model_dir = self._resolve_model_dir(model, artifacts, model_store_root)
        container_model_path = f"/models/{model.hf_repo_id or str(model.id)}"

        # ── GPU vendor detection & image selection ────────────────────
        inventory = detect_gpus()
        gpu_vendor = inventory.primary_vendor
        compute_api = inventory.primary_compute_api

        # Allow explicit override via provider_config
        if vendor_override := pc.get("gpu_vendor"):
            gpu_vendor = GpuVendor(vendor_override)

        # Select the correct vLLM image for the GPU vendor
        default_image = _VLLM_IMAGES.get(gpu_vendor, settings.default_vllm_image)
        image = pc.get("image", default_image)

        log.info(
            "vLLM runtime %r: detected GPU vendor=%s, compute=%s, image=%s",
            runtime.name,
            gpu_vendor,
            compute_api,
            image,
        )

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

        # CPU-only mode when no supported GPU is found
        if gpu_vendor in (GpuVendor.UNKNOWN, GpuVendor.APPLE) or not inventory.has_gpu:
            cmd += ["--device", "cpu"]
            log.warning(
                "No supported GPU for vLLM (vendor=%s). Running in CPU-only mode.",
                gpu_vendor,
            )

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

        # ROCm-specific environment variables
        if gpu_vendor == GpuVendor.AMD:
            env.append("HSA_OVERRIDE_GFX_VERSION=11.0.0")  # Broad gfx compatibility
            if tp_val := gc.get("tensor_parallel_size"):
                # ROCm needs explicit visible device ordering for TP
                env.append(f"HIP_VISIBLE_DEVICES={','.join(str(i) for i in range(int(tp_val)))}")

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
            "llm-port.gpu_vendor": gpu_vendor.value,
        }

        return ContainerSpec(
            image=image,
            name=f"llm-port-vllm-{runtime.name}",
            cmd=cmd,
            env=env or None,
            ports=ports,
            volumes=volumes,
            gpu_devices=gpu_devices if inventory.has_gpu else None,
            gpu_vendor=gpu_vendor if inventory.has_gpu else None,
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
        except Exception as exc:
            return HealthStatus(healthy=False, detail=str(exc))

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def default_capabilities(self) -> dict[str, Any]:
        inventory = detect_gpus()
        return {
            "supports_gpu": True,
            "supports_openai_compat": True,
            "supports_quant": True,
            "artifact_formats": ["safetensors"],
            "gpu_vendor": inventory.primary_vendor.value,
            "gpu_compute_api": inventory.primary_compute_api.value,
            "gpu_count": inventory.device_count,
            "recommended_image": _VLLM_IMAGES.get(
                inventory.primary_vendor,
                settings.default_vllm_image,
            ),
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
