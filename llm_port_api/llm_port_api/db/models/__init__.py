"""Database model package for gateway service."""

from llm_port_api.db.models.gateway import (
    LLMGatewayRequestLog,
    LLMModelAlias,
    LLMPoolMembership,
    LLMProviderInstance,
    PriceCatalog,
    PrivacyMode,
    ProviderHealthStatus,
    ProviderType,
    TenantLLMPolicy,
)

__all__ = [
    "LLMGatewayRequestLog",
    "LLMModelAlias",
    "LLMPoolMembership",
    "LLMProviderInstance",
    "PriceCatalog",
    "PrivacyMode",
    "ProviderHealthStatus",
    "ProviderType",
    "TenantLLMPolicy",
]


def load_all_models() -> None:
    """Import all ORM models for migration discovery."""
