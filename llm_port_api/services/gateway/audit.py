from __future__ import annotations

import uuid

from llm_port_api.db.dao.gateway_dao import GatewayDAO


class AuditService:
    """Writes gateway request logs."""

    def __init__(self, dao: GatewayDAO) -> None:
        self.dao = dao

    async def log(
        self,
        *,
        request_id: str,
        trace_id: str | None,
        tenant_id: str,
        user_id: str,
        model_alias: str | None,
        provider_instance_id: str | None,
        endpoint: str,
        status_code: int,
        latency_ms: int,
        ttft_ms: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        error_code: str | None,
    ) -> None:
        """Persist one request log row."""
        parsed_provider_id: uuid.UUID | None = None
        if provider_instance_id:
            parsed_provider_id = uuid.UUID(provider_instance_id)
        await self.dao.insert_request_log(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            model_alias=model_alias,
            provider_instance_id=parsed_provider_id,
            endpoint=endpoint,
            status_code=status_code,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            error_code=error_code,
        )
