"""Cloud / remote-endpoint provider adapter.

This adapter backs providers whose ``target`` is ``remote_endpoint``.
No local container is ever created; the gateway proxies requests
directly to the remote URL.  The adapter exists solely so that
``get_adapter()`` returns a valid object and ``default_capabilities``
can be queried.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

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

log = logging.getLogger(__name__)


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
        """Probe the remote endpoint to verify reachability.

        A lightweight GET to the base URL is used.  Any HTTP response
        (including 401/403) proves the server is alive; only connection
        errors or 5xx indicate a problem.
        """
        if not runtime.endpoint_url:
            return HealthStatus(healthy=False, detail="No endpoint URL configured")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(runtime.endpoint_url)
                if resp.status_code >= 500:
                    return HealthStatus(
                        healthy=False,
                        detail=f"Remote endpoint returned HTTP {resp.status_code}",
                    )
                return HealthStatus(healthy=True, detail=f"HTTP {resp.status_code}")
        except httpx.TimeoutException:
            return HealthStatus(healthy=False, detail="Remote endpoint timed out")
        except Exception as exc:
            log.debug("Cloud health probe failed for %s: %s", runtime.endpoint_url, exc)
            return HealthStatus(healthy=False, detail=str(exc))

    def default_capabilities(self) -> dict[str, Any]:
        return {
            "supports_chat": True,
            "supports_openai_compat": True,
            "supports_streaming": True,
        }


register_adapter(ProviderType.CLOUD, CloudAdapter)
