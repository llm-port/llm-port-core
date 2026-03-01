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

# vLLM >= 0.7 uses the V1 engine which only supports Flash Attention
# (requires compute capability >= 8.0).  For older NVIDIA GPUs
# (Turing CC 7.5, Volta CC 7.0) we fall back to the legacy image
# that uses the V0 engine with XFormers.
_VLLM_LEGACY_IMAGE = settings.default_vllm_legacy_image


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

        The host model store (which doubles as the HuggingFace cache) is
        mounted into the container at ``/root/.cache/huggingface/hub`` so
        vLLM can locate models by their repository ID, matching the
        approach shown in the official vLLM Docker documentation::

            docker run --gpus all \\
                -v ~/.cache/huggingface:/root/.cache/huggingface \\
                --ipc=host -p 8000:8000 \\
                vllm/vllm-openai:latest --model org/model-name

        GPU vendor auto-detection selects the correct container image
        (CUDA vs ROCm) and Docker passthrough mechanism automatically.
        """
        gc: dict[str, Any] = runtime.generic_config or {}
        pc: dict[str, Any] = runtime.provider_config or {}

        # ── GPU vendor detection & image selection ────────────────────
        inventory = detect_gpus()
        gpu_vendor = inventory.primary_vendor
        compute_api = inventory.primary_compute_api

        # Allow explicit override via provider_config
        if vendor_override := pc.get("gpu_vendor"):
            gpu_vendor = GpuVendor(vendor_override)

        # Select the correct vLLM image for the GPU vendor.
        # enforce_eager signals an older GPU (CC < 8.0) that needs the
        # V0 legacy image to avoid the FA2 crash in V1 engine.
        enforce_eager = gc.get("enforce_eager", True)
        if enforce_eager and gpu_vendor == GpuVendor.NVIDIA:
            default_image = _VLLM_LEGACY_IMAGE
        else:
            default_image = _VLLM_IMAGES.get(gpu_vendor, settings.default_vllm_image)
        image = pc.get("image", default_image)

        log.info(
            "vLLM runtime %r: detected GPU vendor=%s, compute=%s, image=%s",
            runtime.name,
            gpu_vendor,
            compute_api,
            image,
        )

        # ── Model argument — pass the HF repo ID directly ────────────
        model_name = model.hf_repo_id or str(model.id)
        cmd = ["--model", model_name]

        # Generic config → vLLM flags
        if max_model_len := gc.get("max_model_len"):
            cmd += ["--max-model-len", str(max_model_len)]
        if dtype := gc.get("dtype"):
            cmd += ["--dtype", dtype]
        if gpu_mem := gc.get("gpu_memory_utilization"):
            cmd += ["--gpu-memory-utilization", str(gpu_mem)]
        if tp := gc.get("tensor_parallel_size"):
            cmd += ["--tensor-parallel-size", str(tp)]
        if (swap_space := gc.get("swap_space")) is not None:
            cmd += ["--swap-space", str(swap_space)]
        if gc.get("enable_metrics"):
            cmd += ["--enable-metrics"]

        # CPU-only mode when no supported GPU is found
        if gpu_vendor in (GpuVendor.UNKNOWN, GpuVendor.APPLE) or not inventory.has_gpu:
            cmd += ["--device", "cpu"]
            log.warning(
                "No supported GPU for vLLM (vendor=%s). Running in CPU-only mode.",
                gpu_vendor,
            )

        # ── Enforce eager mode ────────────────────────────────────────
        # enforce_eager is resolved earlier (before image selection).
        # Add the CLI flag here; legacy image handles the rest.
        if enforce_eager:
            cmd += ["--enforce-eager"]

        # Provider overlay → extra args
        if extra_args := pc.get("extra_args"):
            cmd += extra_args

        # Host port — pick from config or let Docker assign
        host_port = str(gc.get("host_port", "0"))
        ports = {
            "8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": host_port}],
        }

        # ── Volume mount — expose the HF cache to the container ──────
        # The download task uses ``cache_dir=model_store_root/hf``
        # which creates the standard HF cache layout under
        # model_store_root/hf (models--org--model/snapshots/...).
        # We mount that directory into the container and explicitly set
        # HF_HUB_CACHE to point to it, so vLLM's snapshot_download()
        # resolves models there regardless of default cache paths.
        hf_cache_mount = "/data/hf-cache"
        volumes = [f"{model_store_root}/hf:{hf_cache_mount}"]

        # GPU devices
        gpu_devices = gc.get("gpu_devices", "all")

        # ── Environment ───────────────────────────────────────────────
        env: list[str] = [
            # Prevent vLLM from attempting downloads inside the container
            "HF_HUB_OFFLINE=1",
            "TRANSFORMERS_OFFLINE=1",
            # Point HuggingFace at our mounted cache directory
            f"HF_HUB_CACHE={hf_cache_mount}",
        ]
        if settings.hf_token:
            env.append(f"HF_TOKEN={settings.hf_token}")
        if gc.get("log_level"):
            env.append(f"VLLM_LOG_LEVEL={gc['log_level']}")

        # Keep XFORMERS hint for the V0 fallback path (if V1 is
        # disabled externally or by a future vLLM version).
        if enforce_eager:
            env.append("VLLM_ATTENTION_BACKEND=XFORMERS")

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
            ipc_mode="host",
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
        """Determine the host-side model directory for imported (non-HF) models."""
        if artifacts:
            from pathlib import Path  # noqa: PLC0415

            first_path = Path(artifacts[0].path)
            parent = str(first_path.parent)
            if parent and parent != ".":
                return parent

        return f"{model_store_root}/imports/{model.id}"


# Self-register on import
register_adapter(ProviderType.VLLM, VLLMAdapter)
