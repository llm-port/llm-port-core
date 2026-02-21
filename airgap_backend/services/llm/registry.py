"""Provider adapter registry — maps engine type strings to adapter instances."""

from __future__ import annotations

from airgap_backend.db.models.llm import ProviderType
from airgap_backend.services.llm.base import ProviderAdapter

_registry: dict[ProviderType, type[ProviderAdapter]] = {}


def register_adapter(provider_type: ProviderType, cls: type[ProviderAdapter]) -> None:
    """Register an adapter class for a provider type."""
    _registry[provider_type] = cls


def get_adapter(provider_type: ProviderType | str) -> ProviderAdapter:
    """
    Return an instantiated adapter for the given provider type.

    :raises ValueError: if no adapter is registered for the type.
    """
    if isinstance(provider_type, str):
        provider_type = ProviderType(provider_type)
    cls = _registry.get(provider_type)
    if cls is None:
        raise ValueError(
            f"No adapter registered for provider type '{provider_type}'. "
            f"Available: {sorted(_registry.keys())}",
        )
    return cls()


def _auto_register() -> None:
    """Import adapters so they self-register on module load."""
    from airgap_backend.services.llm.adapters import llamacpp, ollama, tgi, vllm  # noqa: F401, PLC0415


_auto_register()
