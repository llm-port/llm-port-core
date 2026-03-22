"""Shared runtime settings key mapping.

Centralizes DB setting-key -> ``settings`` attribute mapping so both startup
hydration and live apply paths use the same source of truth.
"""

from __future__ import annotations

_RUNTIME_VALUE_KEY_MAP: dict[str, str] = {
    "llm_port_api.pii_enabled": "pii_enabled",
    "llm_port_api.pii_service_url": "pii_service_url",
    "llm_port_api.mcp_enabled": "mcp_enabled",
    "llm_port_api.mcp_service_url": "mcp_service_url",
    "llm_port_api.skills_enabled": "skills_enabled",
    "llm_port_api.skills_service_url": "skills_service_url",
    "llm_port_api.sessions_enabled": "sessions_enabled",
    "rag_lite.enabled": "rag_lite_enabled",
    "rag_lite.embedding_provider_id": "rag_lite_embedding_provider_id",
    "rag_lite.embedding_model": "rag_lite_embedding_model",
    "rag_lite.embedding_dim": "rag_lite_embedding_dim",
    "rag_lite.chunk_max_tokens": "rag_lite_chunk_max_tokens",
    "rag_lite.chunk_overlap_tokens": "rag_lite_chunk_overlap_tokens",
    "rag_lite.file_store_root": "rag_lite_file_store_root",
    "rag_lite.upload_max_file_mb": "rag_lite_upload_max_file_mb",
}

_RUNTIME_SECRET_KEY_MAP: dict[str, str] = {
    "llm_port_backend.users_secret": "users_secret",
    "llm_port_api.mcp_service_token": "mcp_service_token",
    "llm_port_api.skills_service_token": "skills_service_token",
}


def register_runtime_value_key(key: str, attr: str) -> None:
    """Register additional runtime value mapping (for EE/plugin extension)."""
    _RUNTIME_VALUE_KEY_MAP[key] = attr


def register_runtime_secret_key(key: str, attr: str) -> None:
    """Register additional runtime secret mapping (for EE/plugin extension)."""
    _RUNTIME_SECRET_KEY_MAP[key] = attr


def get_runtime_value_key_map() -> dict[str, str]:
    """Return mutable value-key map used by runtime hydration/apply paths."""
    return _RUNTIME_VALUE_KEY_MAP


def get_runtime_secret_key_map() -> dict[str, str]:
    """Return mutable secret-key map used by runtime hydration/apply paths."""
    return _RUNTIME_SECRET_KEY_MAP


def resolve_runtime_attr(key: str) -> str | None:
    """Resolve DB setting key to ``settings`` attribute name."""
    return _RUNTIME_VALUE_KEY_MAP.get(key) or _RUNTIME_SECRET_KEY_MAP.get(key)
