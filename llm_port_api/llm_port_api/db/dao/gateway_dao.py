import uuid
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.dependencies import get_db_session
from llm_port_api.db.models.gateway import (
    LLMGatewayRequestLog,
    LLMModelAlias,
    LLMPoolMembership,
    LLMToolCallLog,
    LLMProviderInstance,
    ProviderHealthStatus,
    ProviderType,
    TenantLLMPolicy,
    ChatSession,
    SessionClientCapability,
    SessionExecutionPolicy,
    SessionToolOverride,
    ExecutionMode,
    ToolRealm,
)


@dataclass(slots=True, frozen=True)
class RoutedInstance:
    """Resolved candidate instance for a model alias."""

    alias: str
    instance_id: uuid.UUID
    provider_type: ProviderType
    base_url: str
    weight: float
    max_concurrency: int
    api_key_encrypted: str | None = None
    litellm_provider: str | None = None
    litellm_model: str | None = None
    extra_params: dict | None = None
    node_id: uuid.UUID | None = None
    node_metadata: dict | None = None
    capacity_hints: dict | None = None


class GatewayDAO:
    """Data access layer for gateway control metadata."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def list_enabled_aliases_for_tenant(
        self,
        tenant_id: str,
    ) -> list[LLMModelAlias]:
        """List aliases visible for tenant that have at least one healthy provider."""
        policy = await self.get_tenant_policy(tenant_id)

        # Only return aliases backed by at least one healthy, enabled provider.
        healthy_provider = (
            exists()
            .where(
                LLMPoolMembership.model_alias == LLMModelAlias.alias,
                LLMPoolMembership.enabled.is_(True),
            )
            .where(
                LLMProviderInstance.id == LLMPoolMembership.provider_instance_id,
                LLMProviderInstance.enabled.is_(True),
                LLMProviderInstance.health_status == ProviderHealthStatus.HEALTHY,
            )
        )

        query = select(LLMModelAlias).where(
            LLMModelAlias.enabled.is_(True),
            healthy_provider,
        )
        if policy and policy.allowed_model_aliases:
            query = query.where(LLMModelAlias.alias.in_(policy.allowed_model_aliases))
        result = await self.session.execute(query.order_by(LLMModelAlias.alias.asc()))
        return list(result.scalars().all())

    async def get_tenant_policy(self, tenant_id: str) -> TenantLLMPolicy | None:
        """Fetch tenant policy."""
        result = await self.session.execute(
            select(TenantLLMPolicy).where(TenantLLMPolicy.tenant_id == tenant_id),
        )
        return result.scalar_one_or_none()

    async def resolve_candidates(
        self,
        *,
        alias: str,
        tenant_id: str,
    ) -> list[RoutedInstance]:
        """Resolve enabled and healthy candidates for the given alias/tenant."""
        policy = await self.get_tenant_policy(tenant_id)

        # Enforce tenant alias allowlist before running the heavier join.
        if (
            policy
            and policy.allowed_model_aliases
            and alias not in policy.allowed_model_aliases
        ):
            return []

        query = (
            select(LLMModelAlias, LLMPoolMembership, LLMProviderInstance)
            .join(
                LLMPoolMembership,
                LLMPoolMembership.model_alias == LLMModelAlias.alias,
            )
            .join(
                LLMProviderInstance,
                LLMProviderInstance.id == LLMPoolMembership.provider_instance_id,
            )
            .where(
                LLMModelAlias.alias == alias,
                LLMModelAlias.enabled.is_(True),
                LLMPoolMembership.enabled.is_(True),
                LLMProviderInstance.enabled.is_(True),
                LLMProviderInstance.health_status == ProviderHealthStatus.HEALTHY,
            )
        )
        if policy and policy.allowed_provider_types:
            query = query.where(
                LLMProviderInstance.type.in_(policy.allowed_provider_types),
            )

        rows = (await self.session.execute(query)).all()
        candidates: list[RoutedInstance] = []
        for _, membership, instance in rows:
            weight = (
                membership.weight_override
                if membership.weight_override is not None
                else instance.weight
            )
            candidates.append(
                RoutedInstance(
                    alias=alias,
                    instance_id=instance.id,
                    provider_type=instance.type,
                    base_url=instance.base_url.rstrip("/"),
                    weight=float(weight),
                    max_concurrency=max(instance.max_concurrency, 1),
                    api_key_encrypted=instance.api_key_encrypted,
                    litellm_provider=instance.litellm_provider,
                    litellm_model=instance.litellm_model,
                    extra_params=instance.extra_params,
                    node_id=instance.node_id,
                    node_metadata=instance.node_metadata,
                    capacity_hints=instance.capacity_hints,
                ),
            )
        return candidates

    async def insert_request_log(
        self,
        *,
        request_id: str,
        trace_id: str | None,
        tenant_id: str,
        user_id: str,
        model_alias: str | None,
        provider_instance_id: uuid.UUID | None,
        endpoint: str,
        status_code: int,
        latency_ms: int,
        ttft_ms: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        error_code: str | None,
        stream: bool | None = None,
        cached_tokens: int | None = None,
        estimated_input_cost: object | None = None,
        estimated_output_cost: object | None = None,
        estimated_total_cost: object | None = None,
        currency: str | None = None,
        price_catalog_id: uuid.UUID | None = None,
        cost_estimate_status: str | None = None,
        session_id: str | None = None,
        finish_reason: str | None = None,
        retry_count: int | None = None,
        skills_used: list[dict] | None = None,
        rag_context: dict | None = None,
        mcp_tool_call_count: int | None = None,
        mcp_tool_loop_iterations: int | None = None,
    ) -> LLMGatewayRequestLog:
        """Insert request audit log row."""
        row = LLMGatewayRequestLog(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            model_alias=model_alias,
            provider_instance_id=provider_instance_id,
            endpoint=endpoint,
            status_code=status_code,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            error_code=error_code,
            stream=stream,
            cached_tokens=cached_tokens,
            estimated_input_cost=estimated_input_cost,
            estimated_output_cost=estimated_output_cost,
            estimated_total_cost=estimated_total_cost,
            currency=currency,
            price_catalog_id=price_catalog_id,
            cost_estimate_status=cost_estimate_status,
            session_id=session_id,
            finish_reason=finish_reason,
            retry_count=retry_count,
            skills_used=skills_used,
            rag_context=rag_context,
            mcp_tool_call_count=mcp_tool_call_count,
            mcp_tool_loop_iterations=mcp_tool_loop_iterations,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def insert_tool_call_logs(
        self,
        *,
        request_log_id: uuid.UUID,
        request_id: str,
        tool_calls: list[dict],
    ) -> None:
        """Bulk-insert tool call telemetry rows."""
        for tc in tool_calls:
            row = LLMToolCallLog(
                request_log_id=request_log_id,
                request_id=request_id,
                iteration=tc.get("iteration", 0),
                tool_name=tc["tool_name"],
                mcp_server=tc.get("mcp_server"),
                latency_ms=tc.get("latency_ms"),
                is_error=tc.get("is_error", False),
                error_message=tc.get("error_message"),
            )
            self.session.add(row)
        await self.session.flush()

    # ── Session tool policy ──────────────────────────────────────────

    async def get_session_execution_mode(
        self,
        session_id: uuid.UUID,
    ) -> ExecutionMode:
        """Return the execution mode for a session (default: server_only)."""
        result = await self.session.execute(
            select(ChatSession.execution_mode).where(ChatSession.id == session_id),
        )
        mode = result.scalar_one_or_none()
        return mode if mode is not None else ExecutionMode.SERVER_ONLY

    async def get_session_execution_policy(
        self,
        session_id: uuid.UUID,
    ) -> SessionExecutionPolicy | None:
        """Fetch the full execution policy for a session."""
        result = await self.session.execute(
            select(SessionExecutionPolicy).where(
                SessionExecutionPolicy.session_id == session_id,
            ),
        )
        return result.scalar_one_or_none()

    async def get_session_tool_overrides(
        self,
        session_id: uuid.UUID,
    ) -> list[SessionToolOverride]:
        """Fetch all per-tool overrides for a session."""
        result = await self.session.execute(
            select(SessionToolOverride).where(
                SessionToolOverride.session_id == session_id,
            ),
        )
        return list(result.scalars().all())

    # ── Session tool policy writes ───────────────────────────────

    async def upsert_session_execution_policy(
        self,
        session_id: uuid.UUID,
        *,
        execution_mode: ExecutionMode | None = None,
        hybrid_preference: str | None = ...,  # type: ignore[assignment]
    ) -> SessionExecutionPolicy:
        """Create or update the execution policy for a session.

        Also updates ``ChatSession.execution_mode`` to keep it in sync.
        """
        policy = await self.get_session_execution_policy(session_id)
        if policy is None:
            mode = execution_mode or ExecutionMode.SERVER_ONLY
            policy = SessionExecutionPolicy(
                session_id=session_id,
                execution_mode=mode,
                hybrid_preference=hybrid_preference if hybrid_preference is not ... else None,
            )
            self.session.add(policy)
        else:
            if execution_mode is not None:
                policy.execution_mode = execution_mode
            if hybrid_preference is not ...:
                policy.hybrid_preference = hybrid_preference

        # Keep ChatSession.execution_mode in sync
        effective_mode = execution_mode or policy.execution_mode
        await self.session.execute(
            update(ChatSession)
            .where(ChatSession.id == session_id)
            .values(execution_mode=effective_mode),
        )
        await self.session.flush()
        return policy

    async def upsert_session_tool_overrides(
        self,
        session_id: uuid.UUID,
        overrides: list[tuple[str, bool]],
    ) -> list[SessionToolOverride]:
        """Upsert a batch of per-tool enable/disable overrides."""
        existing = await self.get_session_tool_overrides(session_id)
        by_tool: dict[str, SessionToolOverride] = {o.tool_id: o for o in existing}
        result: list[SessionToolOverride] = []
        for tool_id, enabled in overrides:
            if tool_id in by_tool:
                by_tool[tool_id].enabled = enabled
                result.append(by_tool[tool_id])
            else:
                row = SessionToolOverride(
                    session_id=session_id,
                    tool_id=tool_id,
                    enabled=enabled,
                )
                self.session.add(row)
                result.append(row)
        await self.session.flush()
        return result

    async def delete_session_tool_override(
        self,
        session_id: uuid.UUID,
        tool_id: str,
    ) -> bool:
        """Remove a single tool override. Returns True if a row was deleted."""
        result = await self.session.execute(
            delete(SessionToolOverride).where(
                SessionToolOverride.session_id == session_id,
                SessionToolOverride.tool_id == tool_id,
            ),
        )
        await self.session.flush()
        return result.rowcount > 0

    # ── Client capabilities ──────────────────────────────────────

    async def get_session_client_capabilities(
        self,
        session_id: uuid.UUID,
        client_id: str | None = None,
    ) -> list[SessionClientCapability]:
        """Fetch client-advertised tools for a session."""
        stmt = select(SessionClientCapability).where(
            SessionClientCapability.session_id == session_id,
        )
        if client_id is not None:
            stmt = stmt.where(SessionClientCapability.client_id == client_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def register_client_capabilities(
        self,
        session_id: uuid.UUID,
        client_id: str,
        tools: list[dict],
    ) -> list[SessionClientCapability]:
        """Register or replace all tools for a client within a session."""
        # Remove old entries for this client
        await self.session.execute(
            delete(SessionClientCapability).where(
                SessionClientCapability.session_id == session_id,
                SessionClientCapability.client_id == client_id,
            ),
        )
        rows: list[SessionClientCapability] = []
        for tool in tools:
            row = SessionClientCapability(
                session_id=session_id,
                client_id=client_id,
                tool_id=tool["tool_id"],
                realm=ToolRealm(tool.get("realm", "client_local")),
                schema_json=tool.get("schema"),
                available=tool.get("available", True),
            )
            self.session.add(row)
            rows.append(row)
        await self.session.flush()
        return rows

    async def revoke_client_capabilities(
        self,
        session_id: uuid.UUID,
        client_id: str,
    ) -> int:
        """Remove all tools for a client (e.g. on disconnect). Returns count."""
        result = await self.session.execute(
            delete(SessionClientCapability).where(
                SessionClientCapability.session_id == session_id,
                SessionClientCapability.client_id == client_id,
            ),
        )
        await self.session.flush()
        return result.rowcount

    async def mark_client_tools_unavailable(
        self,
        session_id: uuid.UUID,
        client_id: str,
    ) -> int:
        """Mark all tools for a client as unavailable (soft disconnect)."""
        result = await self.session.execute(
            update(SessionClientCapability)
            .where(
                SessionClientCapability.session_id == session_id,
                SessionClientCapability.client_id == client_id,
            )
            .values(available=False),
        )
        await self.session.flush()
        return result.rowcount
