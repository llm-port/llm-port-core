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


async def _sync_mailer_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``llm_port_mailer.enabled`` as module lifecycle flag."""
    try:
        result = await service.update_value(
            key="llm_port_mailer.enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync llm_port_mailer.enabled")
        return [f"Failed to sync llm_port_mailer.enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply llm_port_mailer.enabled={enabled}: {details}"]
    return []


async def _sync_docling_enabled(
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync ``llm_port_backend.docling_enabled`` as module lifecycle flag."""
    try:
        result = await service.update_value(
            key="llm_port_backend.docling_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync llm_port_backend.docling_enabled")
        return [f"Failed to sync llm_port_backend.docling_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply llm_port_backend.docling_enabled={enabled}: {details}"]
    return []


# ── Registration ──────────────────────────────────────────────────────


def register_core_modules() -> None:
    """Register all built-in optional modules.

    Safe to call multiple times (e.g. in tests) — silently skips
    modules that are already registered.
    """
    _defs: list[ModuleDef] = [
        ModuleDef(
            name="rag",
            display_name="RAG Engine",
            description=(
                "Retrieval-Augmented Generation pipeline with document ingestion, "
                "chunking, embedding, and vector search."
            ),
            module_type="container",
            settings_flag="rag_enabled",
            health_url_fn=lambda: f"{settings.rag_base_url}/health",
            compose_profile="rag",
            compose_services=[
                "llm-port-rag",
                "llm-port-rag-worker",
                "llm-port-rag-scheduler",
                "llm-port-rag-migrator",
            ],
            container_names=[
                "llm-port-rag",
                "llm-port-rag-worker",
                "llm-port-rag-scheduler",
            ],
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
            name="pii-pro",
            display_name="PII Pro",
            description=(
                "Enterprise PII sidecar — adds reversible tokenization, "
                "per-tenant policies, audit logging, and detokenization. "
                "Requires a valid Enterprise license and the PII module."
            ),
            module_type="container",
            enterprise=True,
            settings_flag="pii_pro_enabled",
            health_url_fn=lambda: f"{settings.pii_pro_service_url.rstrip('/')}/api/health",
            compose_profile="pii-pro",
            compose_services=[
                "llm-port-pii-pro",
            ],
            container_names=[
                "llm-port-pii-pro",
            ],
        ),
        ModuleDef(
            name="mailer",
            display_name="Mailer",
            description=(
                "SMTP mail adapter used for password reset and system admin alerts."
            ),
            module_type="container",
            settings_flag="mailer_enabled",
            health_url_fn=lambda: f"{settings.mailer_service_url.rstrip('/')}/api/health",
            compose_profile="mailer",
            compose_services=[
                "llm-port-mailer",
            ],
            container_names=[
                "llm-port-mailer",
            ],
            on_enable=_sync_mailer_enabled,
            on_disable=_sync_mailer_enabled,
        ),
        ModuleDef(
            name="auth",
            display_name="External Auth",
            description=(
                "Enterprise SSO module providing OIDC and OAuth2 external "
                "authentication provider management."
            ),
            module_type="container",
            settings_flag="auth_enabled",
            health_url_fn=lambda: f"{settings.auth_service_url.rstrip('/')}/api/providers/health",
            compose_profile="auth",
            compose_services=[
                "llm-port-auth",
            ],
            container_names=[
                "llm-port-auth",
            ],
        ),
        ModuleDef(
            name="docling",
            display_name="Document Processor",
            description=(
                "Stateless document conversion service powered by IBM Docling. "
                "Provides layout-aware PDF/DOCX parsing, OCR, table extraction, "
                "and hierarchical chunking for RAG pipelines."
            ),
            module_type="container",
            settings_flag="docling_enabled",
            health_url_fn=lambda: f"{settings.docling_service_url.rstrip('/')}/api/v1/health",
            compose_profile="docling",
            compose_services=[
                "llm-port-docling",
            ],
            container_names=[
                "llm-port-docling",
            ],
            on_enable=_sync_docling_enabled,
            on_disable=_sync_docling_enabled,
        ),
        ModuleDef(
            name="observability-pro",
            display_name="Observability Pro",
            description=(
                "Enterprise observability sidecar — adds cost attribution, "
                "SSE trace streaming, alerting rules, Grafana webhook receiver, "
                "and full-content Langfuse tracing. "
                "Requires a valid Enterprise license."
            ),
            module_type="container",
            enterprise=True,
            settings_flag="observability_pro_enabled",
            health_url_fn=lambda: f"{settings.observability_pro_service_url.rstrip('/')}/api/health",
            compose_profile="observability-pro",
            compose_services=[
                "llm-port-observability-pro",
            ],
            container_names=[
                "llm-port-observability-pro",
            ],
        ),
    ]

    for mod in _defs:
        if mod.name not in module_registry:
            module_registry.register(mod)
