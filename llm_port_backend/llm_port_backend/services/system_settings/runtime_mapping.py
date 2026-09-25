"""Shared runtime settings key mapping.

Centralizes DB setting-key -> ``settings`` attribute mapping so both startup
hydration and live apply paths use the same source of truth.
"""

from __future__ import annotations

from typing import Any

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
    "rag_lite.hybrid_search": "rag_lite_hybrid_search",
    "rag_lite.rerank_provider_id": "rag_lite_rerank_provider_id",
    "rag_lite.rerank_model": "rag_lite_rerank_model",
    "rag_lite.rerank_template": "rag_lite_rerank_template",
    "rag_lite.rerank_candidates": "rag_lite_rerank_candidates",
    "llm.residency.internal_networks": "residency_internal_networks",
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


async def refresh_runtime_values(session: Any, *, prefix: str) -> None:
    """Load the current values of the runtime keys under *prefix* into ``settings``.

    A live-reload setting is applied only in the process that handled the
    change. The taskiq worker -- and any other uvicorn worker -- kept the old
    value until restarted: after RAG Lite's embedding provider was changed,
    documents were still embedded with the old one while searches used the
    new one. Code that must see the current value asks here first.
    """
    from sqlalchemy import text  # noqa: PLC0415

    from llm_port_backend.settings import settings  # noqa: PLC0415

    keys = {k: a for k, a in get_runtime_value_key_map().items() if k.startswith(prefix)}
    if not keys:
        return
    rows = await session.execute(
        text("SELECT key, value_json FROM system_setting_value WHERE key = ANY(:keys)"),
        {"keys": list(keys)},
    )
    for row in rows.mappings():
        value = row["value_json"]
        if isinstance(value, dict):
            value = value.get("value", value)
        if isinstance(value, str) and not value.strip():
            continue  # blank keeps the code-level default, as at startup
        object.__setattr__(settings, keys[str(row["key"])], value)

