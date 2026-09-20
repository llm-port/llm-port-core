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

import json
import logging
import re
import uuid
from typing import Any, TYPE_CHECKING

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


def _map_provider_type(backend_type: str, *, is_remote: bool, litellm_provider: str | None = None) -> str:
    """Map a backend ``ProviderType`` to the gateway enum value."""
    if is_remote:
        # Use litellm_provider to pick the right gateway remote type
        _LITELLM_TO_GATEWAY: dict[str, str] = {
            "anthropic": "remote_anthropic",
            "gemini": "remote_google",
            "vertex_ai": "remote_google",
            "bedrock": "remote_bedrock",
            "azure": "remote_azure",
            "azure_ai": "remote_azure",
            "mistral": "remote_mistral",
            "groq": "remote_groq",
            "deepseek": "remote_deepseek",
            "cohere": "remote_cohere",
            "cohere_chat": "remote_cohere",
            "openai": "remote_openai",
            "openrouter": "remote_openai",
        }
        if litellm_provider:
            return _LITELLM_TO_GATEWAY.get(litellm_provider, "remote_custom")
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
        api_key_encrypted: str | None = None,
        litellm_provider: str | None = None,
        litellm_model: str | None = None,
        extra_params: dict[str, Any] | None = None,
        node_id: uuid.UUID | str | None = None,
        node_metadata: dict[str, Any] | None = None,
        capacity_hints: dict[str, Any] | None = None,
    ) -> None:
        """Create or update gateway routing records for a runtime.

        Upserts:
        1. ``llm_model_alias``      (alias → enabled)
        2. ``llm_provider_instance`` (id = runtime_id, base_url, type, health)
        3. ``llm_pool_membership``   (alias ↔ instance)
        """
        if not self.enabled:
            return
        gateway_type = _map_provider_type(
            backend_provider_type, is_remote=is_remote, litellm_provider=litellm_provider,
        )
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
                             health_status, api_key_encrypted, litellm_provider,
                             litellm_model, extra_params, node_id, node_metadata,
                             capacity_hints, source_kind, source_id, created_at, updated_at)
                        VALUES
                            (:id, :type, :base_url, TRUE, :weight, :max_concurrency,
                             :health, :api_key, :litellm_provider,
                             :litellm_model, :extra_params, :node_id, :node_metadata,
                             :capacity_hints, :source_kind, :source_id, NOW(), NOW())
                        ON CONFLICT (id) DO UPDATE
                            SET base_url        = EXCLUDED.base_url,
                                type            = EXCLUDED.type,
                                enabled         = TRUE,
                                weight          = EXCLUDED.weight,
                                max_concurrency = EXCLUDED.max_concurrency,
                                health_status   = EXCLUDED.health_status,
                                api_key_encrypted = EXCLUDED.api_key_encrypted,
                                litellm_provider  = EXCLUDED.litellm_provider,
                                litellm_model     = EXCLUDED.litellm_model,
                                extra_params      = EXCLUDED.extra_params,
                                node_id         = EXCLUDED.node_id,
                                node_metadata   = EXCLUDED.node_metadata,
                                capacity_hints  = EXCLUDED.capacity_hints,
                                source_kind     = EXCLUDED.source_kind,
                                source_id       = EXCLUDED.source_id,
                                updated_at      = NOW()
                    """),
                    {
                        "id": runtime_id,
                        "type": gateway_type,
                        "base_url": _normalize_base_url(base_url),
                        "weight": weight,
                        "max_concurrency": max_concurrency,
                        "health": health_status,
                        "api_key": api_key_encrypted,
                        "litellm_provider": litellm_provider,
                        "litellm_model": litellm_model,
                        "extra_params": json.dumps(extra_params) if extra_params else None,
                        "node_id": node_id,
                        "node_metadata": json.dumps(node_metadata) if node_metadata else None,
                        "capacity_hints": json.dumps(capacity_hints) if capacity_hints else None,
                        "source_kind": "remote_provider" if is_remote else "runtime",
                        "source_id": runtime_id,
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
                    "Gateway sync: published runtime %s as alias '%s' -> %s [%s]",
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
                    "Gateway sync: instance %s health -> %s",
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

    # ------------------------------------------------------------------
    # Generic Inference Source Publication (Phase 5)
    # ------------------------------------------------------------------

    async def publish_inference_endpoint(
        self,
        *,
        deployment_id: uuid.UUID,
        endpoint_id: uuid.UUID | None = None,
        base_url: str,
        alias: str | None = None,
        served_model_name: str | None = None,
        backend_provider_type: str = "vllm",
        health_status: str = "healthy",
        is_routable: bool = True,
        weight: float = 1.0,
        max_concurrency: int = 16,
        extra_params: dict[str, Any] | None = None,
        api_key_encrypted: str | None = None,
        capacity_hints: dict[str, Any] | None = None,
    ) -> uuid.UUID | None:
        """Create or update gateway routing records for an inference deployment.

        Ensures exactly one logical LLMProviderInstance exists for the deployment.
        """
        if not self.enabled:
            return None
        gateway_type = _map_provider_type(backend_provider_type, is_remote=False)
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                # 1. Resolve stable provider instance ID
                res = await session.execute(
                    text("""
                        SELECT id FROM llm_provider_instance
                        WHERE source_kind = 'inference_deployment' AND source_id = :dep_id
                        LIMIT 1
                    """),
                    {"dep_id": deployment_id},
                )
                existing_id = res.scalar()
                if existing_id is not None:
                    instance_id = existing_id if isinstance(existing_id, uuid.UUID) else uuid.UUID(str(existing_id))
                else:
                    instance_id = uuid.uuid5(uuid.NAMESPACE_DNS, f"inference_deployment:{deployment_id}")

                norm_base_url = _normalize_base_url(base_url)

                # 2. Upsert provider instance
                await session.execute(
                    text("""
                        INSERT INTO llm_provider_instance
                            (id, type, base_url, enabled, weight, max_concurrency,
                             health_status, api_key_encrypted, litellm_provider,
                             litellm_model, extra_params, node_id, node_metadata,
                             capacity_hints, source_kind, source_id, created_at, updated_at)
                        VALUES
                            (:id, :type, :base_url, :enabled, :weight, :max_concurrency,
                             :health, :api_key, NULL,
                             :litellm_model, :extra_params, NULL, NULL,
                             :capacity_hints, 'inference_deployment', :dep_id, NOW(), NOW())
                        ON CONFLICT (id) DO UPDATE
                            SET base_url        = EXCLUDED.base_url,
                                type            = EXCLUDED.type,
                                enabled         = EXCLUDED.enabled,
                                weight          = EXCLUDED.weight,
                                max_concurrency = EXCLUDED.max_concurrency,
                                health_status   = EXCLUDED.health_status,
                                api_key_encrypted = EXCLUDED.api_key_encrypted,
                                litellm_model   = EXCLUDED.litellm_model,
                                extra_params    = EXCLUDED.extra_params,
                                capacity_hints  = EXCLUDED.capacity_hints,
                                source_kind     = EXCLUDED.source_kind,
                                source_id       = EXCLUDED.source_id,
                                updated_at      = NOW()
                    """),
                    {
                        "id": instance_id,
                        "type": gateway_type,
                        "base_url": norm_base_url,
                        "enabled": is_routable,
                        "weight": weight,
                        "max_concurrency": max_concurrency,
                        "health": health_status,
                        "api_key": api_key_encrypted,
                        "litellm_model": served_model_name,
                        "extra_params": json.dumps(extra_params) if extra_params else None,
                        "capacity_hints": json.dumps(capacity_hints) if capacity_hints else None,
                        "dep_id": deployment_id,
                    },
                )

                # 3. Model alias and pool membership (if alias requested)
                if alias:
                    await session.execute(
                        text("""
                            INSERT INTO llm_model_alias (alias, description, enabled, created_at, updated_at)
                            VALUES (:alias, :desc, TRUE, NOW(), NOW())
                            ON CONFLICT (alias) DO UPDATE
                                SET enabled    = TRUE,
                                    updated_at = NOW()
                        """),
                        {"alias": alias, "desc": f"Auto-synced from deployment {deployment_id}"},
                    )

                    await session.execute(
                        text("""
                            INSERT INTO llm_pool_membership
                                (model_alias, provider_instance_id, enabled)
                            VALUES (:alias, :instance_id, :enabled)
                            ON CONFLICT (model_alias, provider_instance_id) DO UPDATE
                                SET enabled = EXCLUDED.enabled
                        """),
                        {
                            "alias": alias,
                            "instance_id": instance_id,
                            "enabled": is_routable,
                        },
                    )

                await session.commit()
                log.info(
                    "Gateway sync: published inference endpoint for deployment %s -> %s [%s] (routable=%s, health=%s)",
                    deployment_id,
                    norm_base_url,
                    gateway_type,
                    is_routable,
                    health_status,
                )
                return instance_id
        except Exception:
            log.exception("Gateway sync: failed to publish inference endpoint for deployment %s", deployment_id)
            return None

    async def set_source_health(
        self,
        *,
        source_kind: str,
        source_id: uuid.UUID,
        health_status: str,
        enabled: bool | None = None,
    ) -> None:
        """Update health and optional enabled state for a source."""
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                if enabled is not None:
                    await session.execute(
                        text("""
                            UPDATE llm_provider_instance
                            SET health_status = :status, enabled = :enabled, updated_at = NOW()
                            WHERE source_kind = :source_kind AND source_id = :source_id
                        """),
                        {
                            "source_kind": source_kind,
                            "source_id": source_id,
                            "status": health_status,
                            "enabled": enabled,
                        },
                    )
                else:
                    await session.execute(
                        text("""
                            UPDATE llm_provider_instance
                            SET health_status = :status, updated_at = NOW()
                            WHERE source_kind = :source_kind AND source_id = :source_id
                        """),
                        {
                            "source_kind": source_kind,
                            "source_id": source_id,
                            "status": health_status,
                        },
                    )
                await session.commit()
                log.debug(
                    "Gateway sync: source (%s, %s) health -> %s (enabled=%s)",
                    source_kind,
                    source_id,
                    health_status,
                    enabled,
                )
        except Exception:
            log.exception(
                "Gateway sync: failed to update health for source (%s, %s)",
                source_kind,
                source_id,
            )

    async def deactivate_source(
        self,
        *,
        source_kind: str,
        source_id: uuid.UUID,
    ) -> None:
        """Soft-deactivate a provider source without destroying aliases or memberships."""
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                await session.execute(
                    text("""
                        UPDATE llm_provider_instance
                        SET enabled = FALSE, updated_at = NOW()
                        WHERE source_kind = :source_kind AND source_id = :source_id
                    """),
                    {"source_kind": source_kind, "source_id": source_id},
                )
                await session.commit()
                log.info("Gateway sync: deactivated source (%s, %s)", source_kind, source_id)
        except Exception:
            log.exception("Gateway sync: failed to deactivate source (%s, %s)", source_kind, source_id)

    async def reactivate_source(
        self,
        *,
        source_kind: str,
        source_id: uuid.UUID,
        health_status: str = "healthy",
    ) -> None:
        """Reactivate a soft-deactivated provider source."""
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                await session.execute(
                    text("""
                        UPDATE llm_provider_instance
                        SET enabled = TRUE, health_status = :status, updated_at = NOW()
                        WHERE source_kind = :source_kind AND source_id = :source_id
                    """),
                    {
                        "source_kind": source_kind,
                        "source_id": source_id,
                        "status": health_status,
                    },
                )
                await session.commit()
                log.info("Gateway sync: reactivated source (%s, %s) with health %s", source_kind, source_id, health_status)
        except Exception:
            log.exception("Gateway sync: failed to reactivate source (%s, %s)", source_kind, source_id)

    async def retire_source(
        self,
        *,
        source_kind: str,
        source_id: uuid.UUID,
    ) -> None:
        """Mark a provider source retired (unhealthy and disabled)."""
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                await session.execute(
                    text("""
                        UPDATE llm_provider_instance
                        SET enabled = FALSE, health_status = 'unhealthy', updated_at = NOW()
                        WHERE source_kind = :source_kind AND source_id = :source_id
                    """),
                    {"source_kind": source_kind, "source_id": source_id},
                )
                await session.commit()
                log.info("Gateway sync: retired source (%s, %s)", source_kind, source_id)
        except Exception:
            log.exception("Gateway sync: failed to retire source (%s, %s)", source_kind, source_id)

    async def purge_source(
        self,
        *,
        source_kind: str,
        source_id: uuid.UUID,
    ) -> None:
        """Destructive cleanup of provider instances for a deleted source."""
        if not self.enabled:
            return
        try:
            async with self._sf() as session:  # type: ignore[union-attr]
                # Find matching instance IDs
                res = await session.execute(
                    text("""
                        SELECT id FROM llm_provider_instance
                        WHERE source_kind = :source_kind AND source_id = :source_id
                    """),
                    {"source_kind": source_kind, "source_id": source_id},
                )
                ids = [r[0] for r in res.fetchall()]
                for iid in ids:
                    await session.execute(
                        text("""
                            DELETE FROM llm_pool_membership
                            WHERE provider_instance_id = :iid
                        """),
                        {"iid": iid},
                    )
                    await session.execute(
                        text("""
                            DELETE FROM llm_provider_instance
                            WHERE id = :iid
                        """),
                        {"iid": iid},
                    )
                await session.commit()
                log.info("Gateway sync: purged source (%s, %s) (deleted %d instances)", source_kind, source_id, len(ids))
        except Exception:
            log.exception("Gateway sync: failed to purge source (%s, %s)", source_kind, source_id)
