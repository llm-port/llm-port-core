"""Gateway DB synchronisation service.

When a runtime is created, started, stopped, or deleted in the backend,
this service mirrors the relevant records into the *API gateway* database
(``llm_api``) so the gateway can route ``/v1/chat/completions`` requests
to the correct upstream endpoint.

The gateway database owns three core routing tables:

* ``llm_model_alias``      – logical model names exposed on ``/v1/models``
* ``llm_provider_instance`` – concrete upstream endpoints
* ``llm_pool_membership``   – maps aliases → provider instances

This module uses **raw SQL** (via ``sqlalchemy.text``) to avoid importing
gateway ORM models into the backend package.  All writes go through a
dedicated ``async_sessionmaker`` that targets the gateway database
(``llm_graph_trace_session_factory`` on ``app.state``).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend ProviderType  →  Gateway ProviderType mapping
# ---------------------------------------------------------------------------
# The gateway schema has an additional ``remote_openai`` type for proxied
# remote endpoints.  All backend types map 1:1 except remote providers
# which are always ``remote_openai`` on the gateway side.
_BACKEND_TO_GATEWAY_TYPE = {
    "vllm": "vllm",
    "llamacpp": "llamacpp",
    "tgi": "tgi",
    "ollama": "ollama",
}


def _map_provider_type(backend_type: str, *, is_remote: bool) -> str:
    """Map a backend ``ProviderType`` to the gateway enum value."""
    if is_remote:
        return "remote_openai"
    return _BACKEND_TO_GATEWAY_TYPE.get(backend_type, "vllm")


_V1_SUFFIX = re.compile(r"/v1/?$")


def _normalize_base_url(url: str) -> str:
    """Strip trailing ``/v1`` (or ``/v1/``) from a base URL.

    The gateway proxy concatenates ``base_url + /v1/chat/completions``.
    If the user registered ``http://host:8000/v1``, the request would hit
    ``/v1/v1/chat/completions``.  Stripping the suffix prevents this.
    """
    url = url.rstrip("/")
    return _V1_SUFFIX.sub("", url)


class GatewaySyncService:
    """Publish / unpublish backend runtimes in the API gateway database.

    The service is optional — when ``session_factory`` is ``None`` all
    methods silently no-op so the backend works in isolation too
    (e.g. during tests or when the gateway DB is unavailable).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None) -> None:
        self._sf = session_factory

    @property
    def enabled(self) -> bool:
        return self._sf is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def publish_runtime(
        self,
        *,
        runtime_id: uuid.UUID,
        alias: str,
        base_url: str,
        backend_provider_type: str,
        is_remote: bool,
        health_status: str = "healthy",
        weight: float = 1.0,
        max_concurrency: int = 10,
    ) -> None:
        """Create or update gateway routing records for a runtime.

        Upserts:
        1. ``llm_model_alias``      (alias → enabled)
        2. ``llm_provider_instance`` (id = runtime_id, base_url, type, health)
        3. ``llm_pool_membership``   (alias ↔ instance)
        """
        if not self.enabled:
            return
        gateway_type = _map_provider_type(backend_provider_type, is_remote=is_remote)
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                # 1 ── model alias ──────────────────────────────────────────
                await session.execute(
                    text("""
                        INSERT INTO llm_model_alias (alias, description, enabled, created_at, updated_at)
                        VALUES (:alias, :desc, TRUE, NOW(), NOW())
                        ON CONFLICT (alias) DO UPDATE
                            SET enabled    = TRUE,
                                updated_at = NOW()
                    """),
                    {"alias": alias, "desc": f"Auto-synced from runtime {runtime_id}"},
                )

                # 2 ── provider instance ────────────────────────────────────
                await session.execute(
                    text("""
                        INSERT INTO llm_provider_instance
                            (id, type, base_url, enabled, weight, max_concurrency,
                             health_status, created_at, updated_at)
                        VALUES
                            (:id, :type, :base_url, TRUE, :weight, :max_concurrency,
                             :health, NOW(), NOW())
                        ON CONFLICT (id) DO UPDATE
                            SET base_url       = EXCLUDED.base_url,
                                type           = EXCLUDED.type,
                                enabled        = TRUE,
                                weight         = EXCLUDED.weight,
                                max_concurrency= EXCLUDED.max_concurrency,
                                health_status  = EXCLUDED.health_status,
                                updated_at     = NOW()
                    """),
                    {
                        "id": runtime_id,
                        "type": gateway_type,
                        "base_url": _normalize_base_url(base_url),
                        "weight": weight,
                        "max_concurrency": max_concurrency,
                        "health": health_status,
                    },
                )

                # 3 ── pool membership ──────────────────────────────────────
                await session.execute(
                    text("""
                        INSERT INTO llm_pool_membership
                            (model_alias, provider_instance_id, enabled)
                        VALUES (:alias, :instance_id, TRUE)
                        ON CONFLICT (model_alias, provider_instance_id) DO UPDATE
                            SET enabled = TRUE
                    """),
                    {"alias": alias, "instance_id": runtime_id},
                )

                await session.commit()
                log.info(
                    "Gateway sync: published runtime %s as alias '%s' → %s [%s]",
                    runtime_id,
                    alias,
                    base_url,
                    gateway_type,
                )
        except Exception:
            log.exception("Gateway sync: failed to publish runtime %s", runtime_id)

    async def unpublish_runtime(
        self,
        *,
        runtime_id: uuid.UUID,
        alias: str,
    ) -> None:
        """Remove gateway routing records for a runtime.

        Deletes the pool membership and provider instance.  If no other
        memberships reference the alias, disables it (but does not delete
        the row to preserve history).
        """
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                # Remove pool membership
                await session.execute(
                    text("""
                        DELETE FROM llm_pool_membership
                        WHERE model_alias = :alias
                          AND provider_instance_id = :iid
                    """),
                    {"alias": alias, "iid": runtime_id},
                )

                # Remove provider instance
                await session.execute(
                    text("""
                        DELETE FROM llm_provider_instance
                        WHERE id = :iid
                    """),
                    {"iid": runtime_id},
                )

                # Disable alias if no remaining memberships
                remaining = await session.execute(
                    text("""
                        SELECT COUNT(*) FROM llm_pool_membership
                        WHERE model_alias = :alias AND enabled = TRUE
                    """),
                    {"alias": alias},
                )
                count = remaining.scalar() or 0
                if count == 0:
                    await session.execute(
                        text("""
                            UPDATE llm_model_alias
                            SET enabled = FALSE, updated_at = NOW()
                            WHERE alias = :alias
                        """),
                        {"alias": alias},
                    )

                await session.commit()
                log.info(
                    "Gateway sync: unpublished runtime %s (alias '%s')",
                    runtime_id,
                    alias,
                )
        except Exception:
            log.exception("Gateway sync: failed to unpublish runtime %s", runtime_id)

    async def set_instance_health(
        self,
        *,
        runtime_id: uuid.UUID,
        health_status: str,
    ) -> None:
        """Update the health status of a gateway provider instance.

        Called when a runtime transitions between running/stopped/error.
        """
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                await session.execute(
                    text("""
                        UPDATE llm_provider_instance
                        SET health_status = :status, updated_at = NOW()
                        WHERE id = :iid
                    """),
                    {"iid": runtime_id, "status": health_status},
                )
                await session.commit()
                log.debug(
                    "Gateway sync: instance %s health → %s",
                    runtime_id,
                    health_status,
                )
        except Exception:
            log.exception(
                "Gateway sync: failed to update health for %s", runtime_id,
            )

    async def set_instance_enabled(
        self,
        *,
        runtime_id: uuid.UUID,
        enabled: bool,
    ) -> None:
        """Enable or disable a gateway provider instance."""
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                await session.execute(
                    text("""
                        UPDATE llm_provider_instance
                        SET enabled = :enabled, updated_at = NOW()
                        WHERE id = :iid
                    """),
                    {"iid": runtime_id, "enabled": enabled},
                )
                await session.commit()
        except Exception:
            log.exception(
                "Gateway sync: failed to set enabled=%s for %s", enabled, runtime_id,
            )
