from __future__ import annotations

import logging
import uuid

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.services.gateway.pricing import PricingService

logger = logging.getLogger(__name__)


class AuditService:
    """Writes gateway request logs."""

    def __init__(
        self,
        dao: GatewayDAO,
        pricing_service: PricingService | None = None,
    ) -> None:
        self.dao = dao
        self.pricing_service = pricing_service

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
        stream: bool | None = None,
        cached_tokens: int | None = None,
        provider_name: str | None = None,
        session_id: str | None = None,
        finish_reason: str | None = None,
        retry_count: int | None = None,
        skills_used: list[dict] | None = None,
        rag_context: dict | None = None,
        mcp_tool_call_count: int | None = None,
        mcp_tool_loop_iterations: int | None = None,
        tool_calls: list[dict] | None = None,
    ) -> None:
        """Persist one request log row with optional cost estimation."""
        parsed_provider_id: uuid.UUID | None = None
        if provider_instance_id:
            parsed_provider_id = uuid.UUID(provider_instance_id)

        # Compute cost estimate if pricing service is available
        estimated_input_cost = None
        estimated_output_cost = None
        estimated_total_cost = None
        currency: str | None = None
        price_catalog_id: uuid.UUID | None = None
        cost_estimate_status: str | None = None

        if self.pricing_service is not None:
            estimate = self.pricing_service.compute_cost(
                provider_name=provider_name,
                model_alias=model_alias,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
            )
            estimated_input_cost = estimate.estimated_input_cost
            estimated_output_cost = estimate.estimated_output_cost
            estimated_total_cost = estimate.estimated_total_cost
            currency = estimate.currency
            price_catalog_id = estimate.price_catalog_id
            cost_estimate_status = estimate.status

        log_row = await self.dao.insert_request_log(
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

        # Persist per-tool-call telemetry
        if tool_calls:
            try:
                await self.dao.insert_tool_call_logs(
                    request_log_id=log_row.id,
                    request_id=request_id,
                    tool_calls=tool_calls,
                )
            except Exception:
                logger.warning(
                    "Failed to persist tool call logs for %s", request_id,
                    exc_info=True,
                )
