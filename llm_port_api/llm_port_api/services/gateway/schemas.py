from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatCompletionRequest(BaseModel):
    """Minimal core contract for chat completions."""

    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False
    session_id: str | None = None

    model_config = ConfigDict(extra="allow")


class EmbeddingsRequest(BaseModel):
    """Minimal core contract for embeddings endpoint."""

    model: str = Field(min_length=1)
    input: Any

    model_config = ConfigDict(extra="allow")


class ModelObject(BaseModel):
    """OpenAI model object shape."""

    id: str
    object: str = "model"
    created: int
    owned_by: str


class ListModelsResponse(BaseModel):
    """OpenAI list models response shape."""

    object: str = "list"
    data: list[ModelObject]


# ---------------------------------------------------------------------------
# Tool Routing DTOs
# ---------------------------------------------------------------------------


class ExecutionModeEnum(StrEnum):
    """Execution mode values for API DTOs."""

    LOCAL_ONLY = "local_only"
    SERVER_ONLY = "server_only"
    HYBRID = "hybrid"


class ToolAvailabilityDTO(BaseModel):
    """Single tool entry in the effective tool catalog."""

    tool_id: str
    display_name: str | None = None
    description: str | None = None
    realm: str
    source: str
    effective_enabled: bool
    policy_allowed: bool
    user_enabled: bool
    available: bool
    availability_reason: str | None = None


class SessionToolOverrideDTO(BaseModel):
    """A single tool override in a policy patch request."""

    tool_id: str
    enabled: bool


class SessionToolPolicyDTO(BaseModel):
    """Session tool policy read/write shape."""

    session_id: str
    execution_mode: ExecutionModeEnum = ExecutionModeEnum.SERVER_ONLY
    hybrid_preference: str | None = None
    effective_catalog_version: int = 0


class SessionToolPolicyPatchDTO(BaseModel):
    """Payload for PATCH /v1/sessions/{session_id}/tool-policy."""

    execution_mode: ExecutionModeEnum | None = None
    hybrid_preference: str | None = None
    tool_overrides: list[SessionToolOverrideDTO] | None = None


class ToolAvailabilityResponse(BaseModel):
    """Response for GET /v1/tools/available."""

    session_id: str | None = None
    execution_mode: ExecutionModeEnum
    effective_catalog_version: int = 0
    tools: list[ToolAvailabilityDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tool Call Audit / Trace DTOs
# ---------------------------------------------------------------------------


class RouteDecisionDTO(BaseModel):
    """Captures one routing decision for tracing."""

    call_id: str
    tool_id: str
    realm: str
    executor: str
    session_id: str
    policy_decision: str  # "allow" | "deny" | "approval_required"
    denial_reason: str | None = None


class ToolCallTraceDTO(BaseModel):
    """A single tool call entry in an execution trace."""

    call_id: str
    tool_id: str
    realm: str
    executor: str
    is_error: bool
    latency_ms: int
    content_preview: str | None = None  # first 200 chars


class ToolExecutionTraceDTO(BaseModel):
    """Full trace of all tool calls within a single request."""

    request_id: str
    session_id: str
    execution_mode: str
    total_tool_calls: int = 0
    total_iterations: int = 0
    tool_calls: list[ToolCallTraceDTO] = Field(default_factory=list)
    route_decisions: list[RouteDecisionDTO] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Client Capability DTOs
# ---------------------------------------------------------------------------


class ClientCapabilityDTO(BaseModel):
    """A single client-advertised tool."""

    tool_id: str
    client_id: str
    realm: str
    available: bool
    schema_json: dict[str, Any] | None = None


class ClientHandshakeDTO(BaseModel):
    """Payload for client capability registration."""

    session_id: str
    client_id: str
    client_type: str | None = None
    client_version: str | None = None
    trust_level: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Approval DTOs
# ---------------------------------------------------------------------------


class ApprovalRequestDTO(BaseModel):
    """A pending tool call requiring user approval."""

    call_id: str
    tool_id: str
    realm: str
    sensitivity: str
    reason: str
    arguments_preview: dict[str, Any] = Field(default_factory=dict)


class ApprovalResponseDTO(BaseModel):
    """User response to an approval request."""

    call_id: str
    approved: bool
    reason: str | None = None
