"""Core module registrations.

Registers all built-in optional modules into the :data:`module_registry`
singleton.  Called once during backend startup (from ``lifespan_setup``).

Each module owns its metadata **and** its lifecycle sync callback so
``views.py`` never hard-codes module names.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_port_backend.services.module_registry import ModuleDef, module_registry
from llm_port_backend.services.system_settings import SystemSettingsService
from llm_port_backend.settings import settings

logger = logging.getLogger(__name__)


# ── Sync callbacks ────────────────────────────────────────────────────
# Each function matches the SyncCallback signature:
#   (service, enabled, actor_id) -> list[str]


async def _sync_pii_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``llm_port_api.pii_enabled`` and trigger gateway apply flow."""
    try:
        result = await service.update_value(
            key="llm_port_api.pii_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync llm_port_api.pii_enabled")
        return [f"Failed to sync llm_port_api.pii_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply llm_port_api.pii_enabled={enabled}: {details}"]
    return []


async def _sync_rag_lite_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``rag_lite.enabled`` as module lifecycle flag."""
    try:
        result = await service.update_value(
            key="rag_lite.enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync rag_lite.enabled")
        return [f"Failed to sync rag_lite.enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply rag_lite.enabled={enabled}: {details}"]
    return []


async def _sync_sessions_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``llm_port_api.sessions_enabled`` as module lifecycle flag."""
    try:
        result = await service.update_value(
            key="llm_port_api.sessions_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync llm_port_api.sessions_enabled")
        return [f"Failed to sync llm_port_api.sessions_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply llm_port_api.sessions_enabled={enabled}: {details}"]
    return []


# ── Registration ──────────────────────────────────────────────────────


def register_core_modules() -> None:
    """Register all built-in optional modules.

    Safe to call multiple times (e.g. in tests) — silently skips
    modules that are already registered.
    """
    _defs: list[ModuleDef] = [
        ModuleDef(
            name="chat",
            display_name="Chat & Sessions",
            description=(
                "Session-aware chat with message history, rolling summaries, "
                "memory facts, and file attachments. Managed by the gateway."
            ),
            module_type="plugin",
            settings_flag="sessions_enabled",
            is_available_fn=lambda: settings.sessions_enabled,
            on_enable=_sync_sessions_enabled,
            on_disable=_sync_sessions_enabled,
        ),
        ModuleDef(
            name="rag_lite",
            display_name="RAG Lite",
            description=(
                "Embedded Retrieval-Augmented Generation with pgvector. "
                "Upload documents, chunk, embed, and search — no external "
                "RAG service required."
            ),
            module_type="plugin",
            settings_flag="rag_lite_enabled",
            is_available_fn=lambda: settings.rag_lite_enabled,
            on_enable=_sync_rag_lite_enabled,
            on_disable=_sync_rag_lite_enabled,
        ),
        ModuleDef(
            name="pii",
            display_name="PII Guard",
            description=(
                "Personally Identifiable Information detection and redaction "
                "service for request / response payloads."
            ),
            module_type="container",
            settings_flag="pii_enabled",
            health_url_fn=lambda: f"{settings.pii_service_url}/health",
            compose_profile="pii",
            compose_services=[
                "llm-port-pii",
                "llm-port-pii-worker",
                "llm-port-pii-migrator",
            ],
            container_names=[
                "llm-port-pii",
                "llm-port-pii-worker",
            ],
            on_enable=_sync_pii_enabled,
            on_disable=_sync_pii_enabled,
        ),
        ModuleDef(
            name="mcp",
            display_name="MCP Tool Registry",
            description=(
                "Model Context Protocol server registry — registers external "
                "MCP servers, discovers tools, and routes tool calls through "
                "a governed privacy proxy."
            ),
            module_type="container",
            settings_flag="mcp_enabled",
            health_url_fn=lambda: f"{settings.mcp_service_url}/api/health",
            compose_profile="mcp",
            compose_services=[
                "llm-port-mcp",
                "llm-port-mcp-migrator",
            ],
            container_names=[
                "llm-port-mcp",
            ],
        ),
    ]

    for mod in _defs:
        if mod.name not in module_registry:
            module_registry.register(mod)
