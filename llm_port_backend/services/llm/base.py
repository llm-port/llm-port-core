"""Abstract base class for LLM provider adapters."""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import Any

from llm_port_backend.db.models.llm import LLMModel, LLMProvider, LLMRuntime, ModelArtifact


@dataclasses.dataclass(frozen=True)
class CompatResult:
    """Result of validating model/artifact compatibility with an engine."""

    compatible: bool
    reason: str = ""


@dataclasses.dataclass(frozen=True)
class HealthStatus:
    """Simplified health probe result."""

    healthy: bool
    detail: str = ""


@dataclasses.dataclass
class ContainerSpec:
    """
    Engine-agnostic container specification.

    Mirrors the parameters accepted by ``DockerService.create_container``.
    """

    image: str
    name: str | None = None
    cmd: list[str] | None = None
    env: list[str] | None = None
    ports: dict[str, list[dict[str, str]]] | None = None
    volumes: list[str] | None = None
    network: str | None = None
    gpu_devices: str | list[int] | None = None
    healthcheck: dict[str, Any] | None = None
    labels: dict[str, str] | None = None


class ProviderAdapter(ABC):
    """
    Interface that every LLM engine must implement.

    Subclasses translate the provider-agnostic ``generic_config`` stored on a
    ``Runtime`` into engine-specific container arguments.
    """

    @abstractmethod
    def validate_model(
        self,
        model: LLMModel,
        artifacts: list[ModelArtifact],
    ) -> CompatResult:
        """Check whether the engine can serve the given model artifacts."""
        ...

    @abstractmethod
    def build_container_spec(
        self,
        runtime: LLMRuntime,
        provider: LLMProvider,
        model: LLMModel,
        artifacts: list[ModelArtifact],
        model_store_root: str,
    ) -> ContainerSpec:
        """
        Translate a runtime configuration into a concrete container spec.

        The returned :class:`ContainerSpec` will be passed directly to
        :meth:`DockerService.create_container`.
        """
        ...

    @abstractmethod
    async def get_health(self, runtime: LLMRuntime) -> HealthStatus:
        """Probe the running engine and return its health."""
        ...

    def default_capabilities(self) -> dict[str, Any]:
        """Return static capability metadata for this engine type."""
        return {}

    def parse_log_line(self, raw: str) -> str:
        """Normalise a raw log line (default: pass-through)."""
        return raw
