"""Pydantic schemas for the observability admin API."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# ── Summary ───────────────────────────────────────────────────────


class ProviderBreakdownDTO(BaseModel):
    provider_instance_id: str
    total_requests: int = 0
    total_tokens: int = 0
    estimated_total_cost: Decimal | None = None


class ModelBreakdownDTO(BaseModel):
    model_alias: str
    total_requests: int = 0
    total_tokens: int = 0
    estimated_total_cost: Decimal | None = None


class TopUserDTO(BaseModel):
    user_id: str
    total_requests: int = 0
    total_tokens: int = 0
    estimated_total_cost: Decimal | None = None


class SummaryDTO(BaseModel):
    total_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    estimated_total_cost: Decimal | None = None
    error_count: int = 0
    avg_latency_ms: float | None = None
    by_provider: list[ProviderBreakdownDTO] = Field(default_factory=list)
    by_model: list[ModelBreakdownDTO] = Field(default_factory=list)
    top_users: list[TopUserDTO] = Field(default_factory=list)


# ── Timeseries ────────────────────────────────────────────────────


class TimeseriesBucketDTO(BaseModel):
    bucket: datetime
    total_requests: int = 0
    total_tokens: int = 0
    estimated_total_cost: Decimal | None = None
    error_count: int = 0
    avg_latency_ms: float | None = None


# ── Performance ───────────────────────────────────────────────────


class PerformanceDTO(BaseModel):
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    p99_latency_ms: float | None = None
    p50_ttft_ms: float | None = None
    p95_ttft_ms: float | None = None
    p99_ttft_ms: float | None = None
    avg_latency_ms: float | None = None
    total_requests: int = 0
    error_count: int = 0
    error_rate: float | None = None


# ── Request list / detail ─────────────────────────────────────────


class RequestLogDTO(BaseModel):
    id: str
    request_id: str
    trace_id: str | None = None
    tenant_id: str
    user_id: str
    model_alias: str | None = None
    provider_instance_id: str | None = None
    endpoint: str
    status_code: int
    latency_ms: int
    ttft_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error_code: str | None = None
    estimated_input_cost: Decimal | None = None
    estimated_output_cost: Decimal | None = None
    estimated_total_cost: Decimal | None = None
    currency: str | None = None
    cost_estimate_status: str | None = None
    cached_tokens: int | None = None
    stream: bool | None = None
    session_id: str | None = None
    finish_reason: str | None = None
    retry_count: int | None = None
    skills_used: list[dict] | None = None
    rag_context: dict | None = None
    mcp_tool_call_count: int | None = None
    mcp_tool_loop_iterations: int | None = None
    created_at: datetime


class ToolCallLogDTO(BaseModel):
    id: str
    request_id: str
    iteration: int
    tool_name: str
    mcp_server: str | None = None
    latency_ms: int
    is_error: bool = False
    error_message: str | None = None
    created_at: datetime


class PaginatedRequestsDTO(BaseModel):
    items: list[RequestLogDTO]
    total: int
    page: int
    limit: int


# ── Session cost ──────────────────────────────────────────────────


class SessionCostDTO(BaseModel):
    session_id: str
    total_requests: int = 0
    total_tokens: int = 0
    estimated_total_cost: Decimal | None = None


# ── Pricing ───────────────────────────────────────────────────────


class PricingEntryDTO(BaseModel):
    id: str
    provider: str
    model: str
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    currency: str = "USD"
    effective_from: datetime
    active: bool
    source: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class PricingCreateDTO(BaseModel):
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    input_price_per_1k: Decimal = Field(ge=0)
    output_price_per_1k: Decimal = Field(ge=0)
    currency: str = Field(default="USD", max_length=3)
    notes: str | None = None


class PricingUpdateDTO(BaseModel):
    input_price_per_1k: Decimal = Field(ge=0)
    output_price_per_1k: Decimal = Field(ge=0)
    currency: str | None = Field(default=None, max_length=3)
    notes: str | None = None
