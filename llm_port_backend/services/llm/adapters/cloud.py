"""Cloud / remote-endpoint provider adapter.

This adapter backs providers whose ``target`` is ``remote_endpoint``.
No local container is ever created; the gateway proxies requests
directly to the remote URL.  The adapter exists solely so that
``get_adapter()`` returns a valid object and ``default_capabilities``
can be queried.
"""

from __future__ import annotations

from typing import Any

from llm_port_backend.db.models.llm import (
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ModelArtifact,
    ProviderType,
)
from llm_port_backend.services.llm.base import (
    CompatResult,
    ContainerSpec,
    HealthStatus,
    ProviderAdapter,
)
from llm_port_backend.services.llm.registry import register_adapter


class CloudAdapter(ProviderAdapter):
    """Lightweight adapter for cloud / remote-endpoint providers."""

    def validate_model(
        self,
        model: LLMModel,
        artifacts: list[ModelArtifact],
    ) -> CompatResult:
        # Remote models are always "compatible" — the engine lives elsewhere.
        return CompatResult(compatible=True)

    def build_container_spec(
        self,
        runtime: LLMRuntime,
        provider: LLMProvider,
        model: LLMModel,
        artifacts: list[ModelArtifact],
        model_store_root: str,
    ) -> ContainerSpec:
        raise NotImplementedError(
            "Cloud providers do not run local containers"
        )

    async def get_health(self, runtime: LLMRuntime) -> HealthStatus:
        # Health is determined by the gateway's upstream probe, not here.
        return HealthStatus(healthy=True, detail="remote endpoint")

    def default_capabilities(self) -> dict[str, Any]:
        return {
            "supports_chat": True,
            "supports_openai_compat": True,
            "supports_streaming": True,
        }


register_adapter(ProviderType.CLOUD, CloudAdapter)
