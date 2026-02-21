"""llama.cpp provider adapter — future implementation."""

from __future__ import annotations

from typing import Any

from airgap_backend.db.models.llm import (
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


class LlamaCppAdapter(ProviderAdapter):
    """Stub adapter for llama.cpp — not yet implemented."""

    def validate_model(
        self,
        model: LLMModel,
        artifacts: list[ModelArtifact],
    ) -> CompatResult:
        raise NotImplementedError("llama.cpp adapter is not yet implemented")

    def build_container_spec(
        self,
        runtime: LLMRuntime,
        provider: LLMProvider,
        model: LLMModel,
        artifacts: list[ModelArtifact],
        model_store_root: str,
    ) -> ContainerSpec:
        raise NotImplementedError("llama.cpp adapter is not yet implemented")

    async def get_health(self, runtime: LLMRuntime) -> HealthStatus:
        raise NotImplementedError("llama.cpp adapter is not yet implemented")

    def default_capabilities(self) -> dict[str, Any]:
        return {
            "supports_gpu": True,
            "supports_openai_compat": True,
            "supports_quant": True,
            "artifact_formats": ["gguf"],
        }


register_adapter(ProviderType.LLAMACPP, LlamaCppAdapter)
