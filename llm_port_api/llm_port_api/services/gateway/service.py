from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.db.dao.session_dao import SessionDAO
from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.gateway.audit import AuditService
from llm_port_api.services.gateway.auth import AuthContext
from llm_port_api.services.gateway import attachment_search, knowledge
from llm_port_api.services.gateway.errors import GatewayError
from llm_port_api.services.gateway.llm_adapter import LLMAdapter
from llm_port_api.services.gateway.observability import (
    GatewayObservability,
)
from llm_port_api.services.gateway.mcp_client import MCPClient
from llm_port_api.services.gateway.mcp_tool_cache import MCP_TOOL_PREFIX, MCPToolCache
from llm_port_api.services.gateway.pii_client import PIIClient
from llm_port_api.services.gateway.pii_policy import PIIPolicy, parse_pii_policy
from llm_port_api.services.gateway.pii_restore import (
    redact_tokens,
    restore_payload,
    restore_sse,
    restore_text,
)
from llm_port_api.services.gateway.proxy import UpstreamProxy, UpstreamResult
from llm_port_api.services.gateway.rag_lite_client import RagLiteClient
from llm_port_api.services.gateway.ratelimit import RateLimiter
from llm_port_api.services.gateway.routing import RouterService, RoutingDecision
from llm_port_api.services.gateway.skills_client import ResolvedSkill, SkillsClient
from llm_port_api.services.gateway.stream import StreamStats, wrap_sse_stream
from llm_port_api.services.gateway.stream_buffer import StreamBuffer
from llm_port_api.services.gateway.tool_stream import DONE, ToolCalls, sse, sse_events
from llm_port_api.services.gateway.usage import (
    estimate_input_tokens,
    usage_from_payload,
)
from llm_port_api.services.gateway.file_store import FileStore
from llm_port_api.services.gateway.tool_router import ToolRouter
from llm_port_api.settings import settings

logger = logging.getLogger(__name__)


def _candidate_adapter_kwargs(candidate: Any) -> dict[str, Any]:
    """Return adapter kwargs derived from a routed provider candidate.

    Centralises the mapping of the per-instance provider/credential
    fields (including outbound TLS) into the kwargs accepted by
    :meth:`LLMAdapter.completion` and :meth:`LLMAdapter.embedding`.
    """
    return {
        "provider_type": candidate.provider_type,
        "base_url": candidate.base_url,
        "api_key_encrypted": candidate.api_key_encrypted,
        "litellm_provider": candidate.litellm_provider,
        "litellm_model": candidate.litellm_model,
        "extra_params": (
            dict(candidate.extra_params) if candidate.extra_params else None
        ),
        "instance_id": candidate.instance_id,
        "ssl_verify_mode": getattr(candidate, "ssl_verify_mode", None),
        "ssl_ca_bundle_pem": getattr(candidate, "ssl_ca_bundle_pem", None),
        "ssl_client_cert_pem": getattr(candidate, "ssl_client_cert_pem", None),
        "ssl_client_key_pem": getattr(candidate, "ssl_client_key_pem", None),
    }


# ── PII context system prompts ───────────────────────────────────────────────
_PII_REDACT_SYSTEM_PROMPT = (
    "IMPORTANT — Privacy notice: The user's message has been processed by an "
    "automated PII (Personally Identifiable Information) redaction system. "
    "Certain sensitive values have been replaced with placeholders such as "
    "<EMAIL_ADDRESS>, <PHONE_NUMBER>, <PERSON>, "
    "<CREDIT_CARD>, etc. These placeholders indicate where real data "
    "existed but was removed for privacy. "
    "When responding, preserve these placeholders exactly as they appear — do "
    "not attempt to guess the original values. If the user asks you to recall "
    "or fill in a redacted value, politely explain that the information was "
    "redacted for privacy."
)

_PII_TOKENIZE_SYSTEM_PROMPT = (
    "IMPORTANT — Privacy notice: The user's message has been processed by an "
    "automated PII (Personally Identifiable Information) tokenization system. "
    "Certain sensitive values have been replaced with surrogate tokens such as "
    "[PERSON_1], [EMAIL_ADDRESS_1], [PHONE_NUMBER_1], [LOCATION_1], etc. "
    "Each token represents a real value that will be restored after your response. "
    "CRITICAL RULES:\n"
    "1. Treat each token as if it were the real value it represents. For example, "
    "[PERSON_1] is a real person — refer to them naturally, use appropriate "
    "pronouns, and reason about them as you would a real name.\n"
    "2. Preserve every token exactly as written in your response — do not modify, "
    "remove, decode, or invent the underlying value.\n"
    "3. Place tokens in the same logical positions in your answer (e.g. if the "
    "user asks 'Who is [PERSON_1]?', your answer should reference [PERSON_1]).\n"
    "4. If multiple tokens of the same type appear (e.g. [PERSON_1] and "
    "[PERSON_2]), treat them as distinct individuals/values.\n"
    "5. Do not mention or explain the tokenization system to the user."
)


@dataclass(slots=True, frozen=True)
class GatewayResponse:
    """Structured non-streaming gateway output."""

    status_code: int
    payload: dict[str, Any]
    provider_instance_id: str
    latency_ms: int
    trace_id: str | None = None


@dataclass(slots=True, frozen=True)
class StreamingGatewayResponse:
    """Structured streaming gateway output."""

    stream: AsyncIterator[bytes]
    provider_instance_id: str
    latency_ms: int
    stats: StreamStats
    trace_id: str | None = None


#: What each endpoint can be sent to: the kinds a route declares
#: (``node_metadata["task"]``), plus routes that declare none.
_ENDPOINT_KINDS: dict[str, set[str | None]] = {
    "/v1/chat/completions": {"chat", None},
    "/v1/embeddings": {"embeddings", None},
    "/v1/rerank": {"scoring", None},
}

_KIND_ADVICE = {
    "chat": "is a chat model: send it to /v1/chat/completions",
    "embeddings": "is an embeddings model: send it to /v1/embeddings",
    "scoring": "is a scoring (rerank) model: send it to /v1/rerank",
}


def _as_input(rerank: dict[str, Any]) -> dict[str, Any]:
    """A rerank request as the pipeline knows texts: ``input``, query first.

    PII, the limits and the trace read ``messages`` or ``input`` -- a rerank
    request's ``query`` and ``documents`` would have gone out unscanned.
    """
    documents = [d if isinstance(d, str) else str(d.get("text", "")) for d in rerank["documents"]]
    return {"model": rerank["model"], "input": [rerank["query"], *documents]}


def _reranked(result: dict[str, Any], rerank: dict[str, Any], alias: str) -> dict[str, Any]:
    """The upstream's scores, with the client's own documents when it asked for them.

    The documents come from the request, not from upstream: they went out
    PII-scanned, and would have come back with its tokens.
    """
    results = []
    for item in result.get("results") or []:
        entry = {"index": item["index"], "relevance_score": item["relevance_score"]}
        if rerank.get("return_documents", True):
            document = rerank["documents"][item["index"]]
            entry["document"] = document if isinstance(document, dict) else {"text": document}
        results.append(entry)
    tokens = ((result.get("meta") or {}).get("billed_units") or {}).get("total_tokens") or 0
    return {
        "id": result.get("id"),
        "model": alias,
        "results": results,
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


#: Qwen3-Reranker's instruction format, as its model card and vLLM's example
#: give it. The server expects the client to apply it; raw text ranked a
#: sentence about bananas first for "When is the Aurora launch?".
_QWEN3_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the "
    'Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
)
_QWEN3_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_QWEN3_INSTRUCTION = "Given a question, retrieve passages that answer the question"


def _in_model_format(candidate: Any, alias: str, query: str, documents: list[str]) -> tuple[str, list[str]]:
    """*query* and *documents* as the route's reranker expects them.

    Qwen3-Reranker gets its instruction format -- unless the query has it
    already: a client that formats for the model is not wrapped twice.
    """
    name = f"{getattr(candidate, 'litellm_model', None) or ''} {alias}".lower()
    if "qwen3" not in name or "rerank" not in name or query.startswith("<|im_start|>"):
        return query, documents
    return (
        f"{_QWEN3_PREFIX}<Instruct>: {_QWEN3_INSTRUCTION}\n<Query>: {query}\n",
        [f"<Document>: {d}{_QWEN3_SUFFIX}" for d in documents],
    )


def _rerank_routes(alias: str, candidates: list[Any]) -> list[Any]:
    """The routes that can take a rerank request.

    A model LLM.Port serves on a cluster runs under Ray Serve, whose API has
    ``/v1/score`` and no ``/v1/rerank``.
    """
    routes = [c for c in candidates if getattr(c, "source_kind", None) != "inference_deployment"]
    if not routes:
        raise GatewayError(
            status_code=400,
            message=f"{alias} runs on a cluster (Ray Serve), which serves no rerank API.",
            error_type="invalid_request_error",
            code="rerank_not_supported",
            param="model",
        )
    return routes


def _check_kind(endpoint: str, alias: str, candidates: list[Any]) -> None:
    """Refuse a request no route behind *alias* can serve, before sending it anywhere.

    With models of several kinds behind one gateway, a chat request to an
    embedding model went all the way to vLLM and came back as LiteLLM's
    "does not support Chat Completions API". The route says what it is; the
    gateway can say so first, in plain words, and not spend a slot on it.
    """
    allowed = _ENDPOINT_KINDS.get(endpoint)
    if allowed is None:
        return
    kinds = {(getattr(c, "node_metadata", None) or {}).get("task") for c in candidates}
    if not kinds or kinds & allowed:
        return
    kind = next(k for k in kinds if k is not None)
    raise GatewayError(
        status_code=400,
        message=f"{alias} {_KIND_ADVICE.get(kind, f'serves {kind} requests, not this endpoint')}.",
        error_type="invalid_request_error",
        code="model_kind_mismatch",
        param="model",
    )


def _is_cloud(candidate: Any) -> bool:
    """Whether the route goes to a remote (cloud) provider."""
    return candidate.provider_type.value.startswith("remote_")


def _needs_scan(pii_policy: PIIPolicy | None, candidate: Any) -> bool:
    """Whether the PII policy scans what is sent to *candidate*."""
    if pii_policy is None:
        return False
    if _is_cloud(candidate):
        return pii_policy.egress.enabled_for_cloud
    return pii_policy.egress.enabled_for_local


#: Work started for after the response, kept referenced until it is done
#: (the event loop holds tasks only weakly).
_BACKGROUND: set[asyncio.Task[Any]] = set()


def _in_background(step: Awaitable[Any]) -> None:
    """Run *step* without waiting for it; a failure is logged, not raised."""

    async def run() -> None:
        try:
            await step
        except Exception:
            logger.warning("Background step failed", exc_info=True)

    task = asyncio.create_task(run())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def _together(*steps: Awaitable[Any]) -> list[Any]:
    """Await *steps* side by side; their results, in order.

    When one fails, the others are cancelled and its own exception is raised
    -- not an ExceptionGroup, which the routes do not handle.
    """
    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(_awaited(step)) for step in steps]
    except BaseExceptionGroup as failed:
        raise failed.exceptions[0] from None
    return [task.result() for task in tasks]


async def _awaited(step: Awaitable[Any]) -> Any:
    return await step


async def _value(value: Any) -> Any:
    """*value*, as a step that needs no waiting."""
    return value


def _scan_units(content: str) -> tuple[list[str], Callable[[list[str]], str]]:
    """A tool answer as the texts it is scanned as, and how to put it back together.

    A JSON answer is its string values, one text each, rebuilt in place: the
    structure is never handed to the scanner, which could break it. Any
    other answer is one text.
    """
    try:
        data = json.loads(content)
    except ValueError:
        return [content], lambda scanned: scanned[0]
    if not isinstance(data, dict | list):
        return [content], lambda scanned: scanned[0]
    places: list[tuple[Any, Any]] = []

    def collect(node: Any) -> None:
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for key, value in items:
            if isinstance(value, str):
                places.append((node, key))
            elif isinstance(value, dict | list):
                collect(value)

    collect(data)

    def rebuild(scanned: list[str]) -> str:
        for (node, key), text in zip(places, scanned, strict=True):
            node[key] = text
        return json.dumps(data)

    return [node[key] for node, key in places], rebuild


def _last_user_text(payload: dict[str, Any]) -> str:
    """The text of the last user message: what RAG and skills look up."""
    for msg in reversed(payload.get("messages") or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            return ""
    return ""


def _with_tools(payload: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """*payload* offering *tools* too; one of the same name is not added twice."""
    present = {(t.get("function") or {}).get("name") for t in payload.get("tools") or []}
    added = [t for t in tools if t["function"]["name"] not in present]
    out = {**payload, "tools": [*(payload.get("tools") or []), *added]}
    out.setdefault("tool_choice", "auto")
    return out


_TOOL_NAME = re.compile(r"^[\w.]+")


def _tool_name(call: dict[str, Any]) -> str:
    """A call's tool name, without what some models append to it."""
    raw = (call.get("function") or {}).get("name") or ""
    match = _TOOL_NAME.match(raw)
    return match.group(0) if match else raw


def _tool_arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = (call.get("function") or {}).get("arguments") or "{}"
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _restored(value: Any, mapping: dict[str, str]) -> Any:
    """*value* with PII tokens put back, in every string it holds."""
    if isinstance(value, str):
        return restore_text(value, mapping)
    if isinstance(value, list):
        return [_restored(v, mapping) for v in value]
    if isinstance(value, dict):
        return {k: _restored(v, mapping) for k, v in value.items()}
    return value


def _assistant_turn(content: str | None, calls: list[dict[str, Any]]) -> dict[str, Any]:
    """The model's tool-calling turn, as it goes back to the model with the results."""
    return {"role": "assistant", "content": content, "tool_calls": calls}


def _mcp_server(name: str) -> str | None:
    parts = name.split(".", 2)
    return parts[1] if len(parts) >= 2 else None


def _add_usage(total: dict[str, int], usage: Any) -> None:
    """Add one round's token usage to the answer's."""
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if isinstance(usage.get(key), int):
            total[key] = total.get(key, 0) + usage[key]


def _insert_after_system(payload: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """*payload* with *message* after its leading system messages."""
    messages = list(payload.get("messages") or [])
    at = 0
    while at < len(messages) and messages[at].get("role") == "system":
        at += 1
    messages.insert(at, message)
    return {**payload, "messages": messages}


def _skills_used(skills: list[ResolvedSkill]) -> list[dict[str, Any]] | None:
    """The skills a request used, for the audit log."""
    if not skills:
        return None
    return [
        {"skill_id": str(s.skill_id), "name": s.name, "slug": s.slug, "version": s.version}
        for s in skills
    ]


@dataclass(slots=True)
class _Prepared:
    """What a request has gathered on its way to the model.

    Filled step by step, so that wherever it fails, the route can release the
    slot it holds and write down how far the request got.
    """

    payload: dict[str, Any]
    egress_payload: dict[str, Any] | None = None
    token_mapping: dict[str, str] | None = None
    decision: RoutingDecision | None = None
    fallback_outcome: str = "not_used"
    pii_policy: PIIPolicy | None = None
    session_id: str | None = None
    skills: list[ResolvedSkill] = field(default_factory=list)
    rag_context: dict[str, Any] | None = None
    trace_context: Any = None
    released: bool = False
    #: Whether the knowledge tools were offered to the model.
    knowledge: bool = False
    #: Attached files too long to include, which the model can search.
    attachments: list[attachment_search.SearchableFile] = field(default_factory=list)
    #: The tools the gateway ran, for the audit log, and in how many rounds.
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_iterations: int = 0
    #: How a streamed answer ended.
    finish_reason: str | None = None

    @property
    def instance_id(self) -> str | None:
        return str(self.decision.candidate.instance_id) if self.decision is not None else None

    @property
    def trace_id(self) -> str | None:
        return self.trace_context.trace_id if self.trace_context is not None else None

    @property
    def fallback_error_code(self) -> str | None:
        if self.fallback_outcome == "fallback_to_local_succeeded":
            return "pii_fallback_to_local_succeeded"
        return None


class GatewayService:
    """Core shared pipeline for chat + embeddings + models."""

    def __init__(
        self,
        *,
        dao: GatewayDAO,
        router: RouterService,
        proxy: UpstreamProxy,
        adapter: LLMAdapter,
        limiter: RateLimiter,
        audit: AuditService,
        observability: GatewayObservability,
        pii_client: PIIClient | None = None,
        rag_lite_client: RagLiteClient | None = None,
        session_dao: SessionDAO | None = None,
        file_store: FileStore | None = None,
        mcp_client: MCPClient | None = None,
        mcp_tool_cache: MCPToolCache | None = None,
        skills_client: SkillsClient | None = None,
        tool_router: ToolRouter | None = None,
    ) -> None:
        self.dao = dao
        self.router = router
        self.proxy = proxy
        self.adapter = adapter
        self.limiter = limiter
        self.audit = audit
        self.observability = observability
        self.pii_client = pii_client
        self.rag_lite_client = rag_lite_client
        self.session_dao = session_dao
        self._file_store = file_store
        self.mcp_client = mcp_client
        self.mcp_tool_cache = mcp_tool_cache
        self.skills_client = skills_client
        self.tool_router = tool_router
        self.stream_buffer: StreamBuffer | None = None

    async def list_models(self, auth: AuthContext) -> dict[str, Any]:
        aliases = await self.dao.list_enabled_aliases_for_tenant(auth.tenant_id)
        # What each one is for, so a chat screen offers chat models and a
        # retrieval setting embedding ones. With models of several kinds behind
        # one gateway, a list that did not say offered an embedding model as a
        # chat model, and picking it failed.
        kinds = await self.dao.alias_kinds([a.alias for a in aliases])
        return {
            "object": "list",
            "data": [
                {
                    "id": alias.alias,
                    "alias": alias.alias,
                    "object": "model",
                    "created": int(alias.created_at.timestamp()),
                    "owned_by": "llm-port",
                    "description": alias.description,
                    "enabled": alias.enabled,
                    "kind": kinds.get(alias.alias),
                }
                for alias in aliases
            ],
        }

    async def route_non_stream(
        self,
        *,
        auth: AuthContext,
        endpoint: str,
        payload: dict[str, Any],
        request_id: str,
        session_id: str | None = None,
    ) -> GatewayResponse:
        started = time.perf_counter()
        rerank: dict[str, Any] | None = None
        if endpoint == "/v1/rerank":
            rerank, payload = payload, _as_input(payload)
        model_alias = _require_model(payload)
        policy = await self.dao.get_tenant_policy(auth.tenant_id)

        await _check_limits(
            limiter=self.limiter,
            tenant_id=auth.tenant_id,
            payload=payload,
            rpm_limit=policy.rpm_limit if policy else None,
            tpm_limit=policy.tpm_limit if policy else None,
        )

        candidates = await self.router.resolve_alias(
            alias=model_alias, tenant_id=auth.tenant_id,
        )
        _check_kind(endpoint, model_alias, candidates)
        if rerank is not None:
            candidates = _rerank_routes(model_alias, candidates)

        req = _Prepared(payload=payload)
        result: UpstreamResult | None = None
        error_code: str | None = None
        status_code = 500
        usage_prompt = None
        usage_completion = None
        usage_total = None
        # ── Observability tracking ──────────────────────────────────────
        retry_count = 0
        finish_reason: str | None = None
        try:
            await self._prepare(
                req,
                auth=auth,
                endpoint=endpoint,
                candidates=candidates,
                request_id=request_id,
                session_id=session_id,
                policy=policy,
                stream=False,
            )
            decision = req.decision
            assert decision is not None and req.egress_payload is not None  # noqa: S101

            for attempt in range(settings.retry_pre_first_token + 1):
                try:
                    if endpoint == "/v1/embeddings":
                        # Embeddings go to the embeddings API. They were sent
                        # through the chat path, which an embedding model
                        # refuses ("does not support Chat Completions API"),
                        # so /v1/embeddings could not reach one at all.
                        adapter_result = await self.adapter.embedding(
                            **_candidate_adapter_kwargs(decision.candidate),
                            payload=req.egress_payload,
                        )
                    elif rerank is not None:
                        texts = req.egress_payload["input"]
                        query, documents = _in_model_format(decision.candidate, model_alias, texts[0], texts[1:])
                        adapter_result = await self.adapter.rerank(
                            **_candidate_adapter_kwargs(decision.candidate),
                            requested_model=model_alias,
                            query=query,
                            documents=documents,
                            top_n=rerank.get("top_n"),
                        )
                    else:
                        adapter_result = await self.adapter.completion(
                            **_candidate_adapter_kwargs(decision.candidate),
                            payload=req.egress_payload,
                            stream=False,
                        )
                    from llm_port_api.services.gateway.llm_adapter import CompletionResult  # noqa: PLC0415
                    assert isinstance(adapter_result, CompletionResult)  # noqa: S101
                    result = UpstreamResult(
                        status_code=adapter_result.status_code,
                        payload=adapter_result.payload,
                        headers={},
                    )
                    status_code = result.status_code
                    retry_count = attempt
                    break
                except Exception as exc:
                    if attempt >= settings.retry_pre_first_token:
                        raise GatewayError(
                            status_code=502,
                            message=f"Upstream request failed: {exc}",
                            error_type="server_error",
                            code="upstream_request_failed",
                        ) from exc
            if result is None:
                raise GatewayError(
                    status_code=502,
                    message="Upstream returned no response.",
                    error_type="server_error",
                    code="upstream_request_failed",
                )
            # The tools the gateway runs: knowledge, attached files, MCP, the
            # tool router's.
            if endpoint == "/v1/chat/completions" and (
                req.knowledge or req.attachments or self.mcp_client or self.tool_router
            ):
                result = await self._tool_loop(req, result, auth=auth, request_id=request_id)

            # The model is done with this request: its slot is free for the
            # next one while the answer is saved and logged.
            await self._release(req)
            if rerank is not None and result.status_code == 200:
                result = UpstreamResult(
                    status_code=200, payload=_reranked(result.payload, rerank, model_alias), headers={},
                )

            # Extract finish_reason from the final result
            _choices = (result.payload or {}).get("choices") or []
            if _choices:
                finish_reason = _choices[0].get("finish_reason")

            usage = usage_from_payload(result.payload)
            usage_prompt = usage.prompt_tokens
            usage_completion = usage.completion_tokens
            usage_total = usage.total_tokens
            latency_ms = int((time.perf_counter() - started) * 1000)

            # The client gets the values tokenize mode took out; the trace
            # keeps the answer as the model gave it, with the tokens.
            response_payload = restore_payload(result.payload, req.token_mapping)

            if req.trace_context is not None:
                self.observability.record_success(
                    req.trace_context,
                    status_code=result.status_code,
                    latency_ms=latency_ms,
                    ttft_ms=None,
                    prompt_tokens=usage_prompt,
                    completion_tokens=usage_completion,
                    total_tokens=usage_total,
                    provider_instance_id=req.instance_id,
                    output_payload=result.payload,
                )
            # Persist assistant response in session
            await self._persist_assistant_response(
                session_id_str=req.session_id,
                response_payload=response_payload,
                model_alias=model_alias,
                provider_instance_id=req.instance_id,
                trace_id=req.trace_id,
            )

            # Best effort, and the client need not wait for it.
            _in_background(self._record_skills_usage(req.skills, auth, session_id=req.session_id))

            return GatewayResponse(
                status_code=result.status_code,
                payload=response_payload,
                provider_instance_id=str(decision.candidate.instance_id),
                latency_ms=latency_ms,
                trace_id=req.trace_id,
            )
        except GatewayError as exc:
            error_code = exc.code
            status_code = exc.status_code
            if req.trace_context is None:
                req.trace_context = self.observability.start_request_trace(
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    user_id=auth.user_id,
                    endpoint=endpoint,
                    model_alias=model_alias,
                    payload={"model": payload.get("model"), "_pii_mode": "pre_upstream_error"},
                    privacy_mode=policy.privacy_mode if policy else None,
                    stream=False,
                    routing_metadata={"pii_fallback_outcome": req.fallback_outcome},
                )
            self.observability.record_failure(
                req.trace_context,
                status_code=exc.status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                provider_instance_id=req.instance_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        finally:
            await self._release(req)
            await self.audit.log(
                request_id=request_id,
                trace_id=req.trace_id,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
                model_alias=model_alias,
                provider_instance_id=req.instance_id,
                endpoint=endpoint,
                status_code=status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                ttft_ms=None,
                prompt_tokens=usage_prompt,
                completion_tokens=usage_completion,
                total_tokens=usage_total,
                error_code=error_code or req.fallback_error_code,
                stream=False,
                provider_name=(
                    req.decision.candidate.litellm_provider
                    if req.decision is not None else None
                ),
                session_id=req.session_id or session_id,
                finish_reason=finish_reason,
                retry_count=retry_count,
                skills_used=_skills_used(req.skills),
                rag_context=req.rag_context,
                mcp_tool_call_count=len(req.tool_calls),
                mcp_tool_loop_iterations=req.tool_iterations,
                tool_calls=req.tool_calls,
            )

    async def route_stream_chat(
        self,
        *,
        auth: AuthContext,
        payload: dict[str, Any],
        request_id: str,
        session_id: str | None = None,
    ) -> StreamingGatewayResponse:
        started = time.perf_counter()
        endpoint = "/v1/chat/completions"
        model_alias = _require_model(payload)
        policy = await self.dao.get_tenant_policy(auth.tenant_id)
        await _check_limits(
            limiter=self.limiter,
            tenant_id=auth.tenant_id,
            payload=payload,
            rpm_limit=policy.rpm_limit if policy else None,
            tpm_limit=policy.tpm_limit if policy else None,
        )

        candidates = await self.router.resolve_alias(
            alias=model_alias, tenant_id=auth.tenant_id,
        )
        _check_kind("/v1/chat/completions", model_alias, candidates)

        req = _Prepared(payload=payload)
        stream_started = False
        stats: StreamStats | None = None
        pre_stream_status_code = 500
        pre_stream_error_code: str | None = None
        try:
            await self._prepare(
                req,
                auth=auth,
                endpoint=endpoint,
                candidates=candidates,
                request_id=request_id,
                session_id=session_id,
                policy=policy,
                stream=True,
            )
            decision = req.decision
            egress_payload = req.egress_payload
            assert decision is not None and egress_payload is not None  # noqa: S101
            mcp_tools_injected = any(
                (t.get("function", {}).get("name") or "").startswith(MCP_TOOL_PREFIX)
                for t in (egress_payload.get("tools") or [])
            )

            if req.knowledge or req.attachments or (mcp_tools_injected and (self.mcp_client or self.tool_router)):
                # Rounds: the answer's text streams as it comes, and the tools
                # run between rounds. With tools, a streamed chat used to be
                # answered whole and then replayed: nothing reached the client
                # until every tool had run and the answer was complete.
                wrapped_stream, stats = await wrap_sse_stream(
                    self._stream_tool_rounds(req, auth=auth, request_id=request_id),
                )
            else:
                raw_stream = self.adapter.completion(
                    **_candidate_adapter_kwargs(decision.candidate),
                    payload=egress_payload,
                    stream=True,
                )
                # raw_stream is a coroutine returning AsyncIterator[bytes]
                raw_stream = await raw_stream  # type: ignore[misc]
                # The tokens tokenize mode put in go back to the values as the
                # answer streams. They were not: the mapping was dropped here,
                # and the chat page -- which always streams -- showed
                # "Hello [PERSON_1]".
                wrapped_stream, stats = await wrap_sse_stream(
                    restore_sse(raw_stream, req.token_mapping),
                )
            stream_started = True

            # Start stream buffer for SSE reconnection
            _sbuf = self.stream_buffer
            _sbuf_sid = req.session_id
            if _sbuf and _sbuf_sid:
                _sbuf.start(_sbuf_sid)

            async def _stream_with_finalize() -> AsyncIterator[bytes]:
                stream_status_code = 200
                stream_error_code: str | None = None
                accumulated_content: list[str] = []
                try:
                    async for chunk in wrapped_stream:
                        # Collect assistant content for persistence
                        _accumulate_stream_content(chunk, accumulated_content)
                        # Push to reconnection buffer
                        if _sbuf and _sbuf_sid:
                            _sbuf.push(_sbuf_sid, chunk)
                        yield chunk
                except Exception as exc:
                    stream_status_code = 502
                    stream_error_code = "upstream_stream_failed"
                    del exc
                    # Response has already started; terminate stream gracefully.
                    yield b"data: [DONE]\n\n"
                finally:
                    # The model is done: its slot first, then what is saved.
                    # It was given back last, after the session write, the
                    # skills calls and the audit row.
                    await self._release(req)
                    # Persist the assistant response in the session
                    if req.session_id and accumulated_content:
                        try:
                            await self._persist_stream_assistant_response(
                                session_id_str=req.session_id,
                                content="".join(accumulated_content),
                                model_alias=model_alias,
                                provider_instance_id=req.instance_id,
                                trace_id=req.trace_id,
                                token_estimate=stats.usage.completion_tokens if stats is not None else None,
                            )
                        except Exception:
                            logger.warning("Failed to persist streamed assistant response", exc_info=True)
                    # Best effort, and nothing need wait for it.
                    _in_background(self._record_skills_usage(req.skills, auth, session_id=req.session_id))
                    final_error_code = stream_error_code or req.fallback_error_code
                    await self.audit.log(
                        request_id=request_id,
                        trace_id=req.trace_id,
                        tenant_id=auth.tenant_id,
                        user_id=auth.user_id,
                        model_alias=model_alias,
                        provider_instance_id=req.instance_id,
                        endpoint=endpoint,
                        status_code=stream_status_code,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        ttft_ms=stats.ttft_ms if stats is not None else None,
                        prompt_tokens=stats.usage.prompt_tokens if stats is not None else None,
                        completion_tokens=stats.usage.completion_tokens if stats is not None else None,
                        total_tokens=stats.usage.total_tokens if stats is not None else None,
                        error_code=final_error_code,
                        stream=True,
                        provider_name=(
                            req.decision.candidate.litellm_provider
                            if req.decision is not None else None
                        ),
                        session_id=req.session_id or session_id,
                        finish_reason=req.finish_reason,
                        retry_count=0,
                        skills_used=_skills_used(req.skills),
                        rag_context=req.rag_context,
                        mcp_tool_call_count=len(req.tool_calls),
                        mcp_tool_loop_iterations=req.tool_iterations,
                        tool_calls=req.tool_calls,
                    )
                    if req.trace_context is not None:
                        self.observability.finalize_stream(
                            req.trace_context,
                            status_code=stream_status_code,
                            latency_ms=int((time.perf_counter() - started) * 1000),
                            ttft_ms=stats.ttft_ms if stats is not None else None,
                            prompt_tokens=stats.usage.prompt_tokens if stats is not None else None,
                            completion_tokens=stats.usage.completion_tokens if stats is not None else None,
                            total_tokens=stats.usage.total_tokens if stats is not None else None,
                            provider_instance_id=req.instance_id,
                            error_code=final_error_code,
                        )
                    # Mark stream buffer as finished for reconnection
                    if _sbuf and _sbuf_sid:
                        _sbuf.finish(_sbuf_sid)

            return StreamingGatewayResponse(
                stream=_stream_with_finalize(),
                provider_instance_id=str(decision.candidate.instance_id),
                latency_ms=int((time.perf_counter() - started) * 1000),
                stats=stats,
                trace_id=req.trace_id,
            )
        except GatewayError as exc:
            pre_stream_status_code = exc.status_code
            pre_stream_error_code = exc.code
            if req.trace_context is None:
                req.trace_context = self.observability.start_request_trace(
                    request_id=request_id,
                    tenant_id=auth.tenant_id,
                    user_id=auth.user_id,
                    endpoint=endpoint,
                    model_alias=model_alias,
                    payload={"model": payload.get("model"), "_pii_mode": "pre_upstream_error"},
                    privacy_mode=policy.privacy_mode if policy else None,
                    stream=True,
                    routing_metadata={"pii_fallback_outcome": req.fallback_outcome},
                )
            self.observability.record_failure(
                req.trace_context,
                status_code=exc.status_code,
                latency_ms=int((time.perf_counter() - started) * 1000),
                provider_instance_id=req.instance_id,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise
        finally:
            if not stream_started:
                await self._release(req)
                await self.audit.log(
                    request_id=request_id,
                    trace_id=req.trace_id,
                    tenant_id=auth.tenant_id,
                    user_id=auth.user_id,
                    model_alias=model_alias,
                    provider_instance_id=req.instance_id,
                    endpoint=endpoint,
                    status_code=pre_stream_status_code,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    ttft_ms=None,
                    prompt_tokens=None,
                    completion_tokens=None,
                    total_tokens=None,
                    error_code=pre_stream_error_code or req.fallback_error_code,
                    stream=True,
                    provider_name=(
                        req.decision.candidate.litellm_provider
                        if req.decision is not None else None
                    ),
                    session_id=session_id,
                    finish_reason=None,
                    retry_count=0,
                    skills_used=_skills_used(req.skills),
                    rag_context=req.rag_context,
                    mcp_tool_call_count=0,
                    mcp_tool_loop_iterations=0,
                    tool_calls=[],
                )

    # ------------------------------------------------------------------
    # On the way to the model
    # ------------------------------------------------------------------

    async def _prepare(
        self,
        req: _Prepared,
        *,
        auth: AuthContext,
        endpoint: str,
        candidates: list[Any],
        request_id: str,
        session_id: str | None,
        policy: Any | None,
        stream: bool,
    ) -> None:
        """Gather the context, pass it through PII, then take a model slot.

        One path for streamed and whole answers alike; they had a copy each,
        and the streaming copy had dropped the PII token mapping.

        The slot is taken last. It used to be taken first and held while RAG,
        the session and the PII scans ran -- close to a second with PII on --
        during which the model could have been answering someone else.

        The session, skills and the MCP tool list do not depend on one
        another, so they are looked up side by side: the request waits for the
        slowest of them, not for their sum.
        """
        chat = endpoint == "/v1/chat/completions"
        payload = req.payload
        if "rag" in payload:
            # Retrieval before the model, on every turn that asked for it, is
            # gone: the model searches when it needs to (knowledge_search).
            raise GatewayError(
                status_code=400,
                message=(
                    "The rag field is no longer supported: models search the "
                    "knowledge base themselves, with the knowledge_search tool."
                ),
                error_type="invalid_request_error",
                code="unsupported_parameter",
                param="rag",
            )
        # Read before anything runs: the session step rewrites the messages.
        question = _last_user_text(payload)

        # A file too long to include is searched by the model, if every route
        # the request may take calls tools; told apart before the slot.
        searchable = chat and payload.get("tool_choice") != "none" and all(
            knowledge.calls_tools(c) for c in candidates
        )
        (payload, req.session_id), req.skills, mcp_tools = await _together(
            self._inject_session_context(payload, auth, session_id, req=req if searchable else None)
            if chat else _value((payload, None)),
            self._resolve_skills(question, auth, session_id) if chat else _value([]),
            self.mcp_tool_cache.get_tools(auth.tenant_id) if chat and self.mcp_tool_cache else _value(None),
        )
        for skill in req.skills:  # each after the last: they keep their order
            payload = _insert_after_system(payload, {
                "role": "system",
                "content": (
                    f"=== ACTIVE SKILL: {skill.name} (v{skill.version}) ===\n"
                    f"{skill.body_markdown}\n"
                    f"=== END SKILL ==="
                ),
            })
        req.payload = payload

        req.pii_policy = _resolve_pii_policy(
            policy, session_override=await self._session_pii_override(req.session_id, auth),
        )
        scanned, candidates = await self._scan_egress(req, candidates=candidates, request_id=request_id)
        telemetry_payload = None
        if req.pii_policy and self.pii_client and req.pii_policy.telemetry.enabled:
            if scanned is not None and req.pii_policy.telemetry.mode != "metrics_only":
                # One scan serves both what leaves and what the trace keeps:
                # the same text was scanned a second time for the trace, which
                # doubled what PII cost every request.
                telemetry_payload = redact_tokens(*scanned)
            else:
                telemetry_payload = await self._apply_telemetry_pii(
                    payload=payload,
                    pii_policy=req.pii_policy,
                    request_id=request_id,
                )

        # Everything that waits on another service is done: now the slot.
        req.decision = await self._lease(req, candidates=candidates, request_id=request_id)

        egress = payload
        if scanned is not None and _needs_scan(req.pii_policy, req.decision.candidate):
            egress, req.token_mapping = scanned
            egress = self._inject_pii_system_prompt(egress, req.pii_policy)  # type: ignore[arg-type]

        req.trace_context = self.observability.start_request_trace(
            request_id=request_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            endpoint=endpoint,
            model_alias=_require_model(payload),
            payload=telemetry_payload if telemetry_payload is not None else egress,
            privacy_mode=policy.privacy_mode if policy else None,
            stream=stream,
            routing_metadata={"pii_fallback_outcome": req.fallback_outcome},
        )

        if chat:
            # Inject current date/time so the model is aware of "today"
            egress = self._inject_datetime_context(egress)
            if mcp_tools:
                egress = self._merge_mcp_tools(egress, mcp_tools, auth.tenant_id)
            if self._offers_knowledge(req.decision.candidate, payload):
                egress = _with_tools(egress, knowledge.TOOLS)
                req.knowledge = True
            if req.attachments:
                egress = _with_tools(egress, attachment_search.TOOLS)
        # Apply skill-based tool constraints (after all tools are merged)
        if req.skills:
            egress = self._apply_skill_tool_constraints(egress, req.skills)
        req.egress_payload = egress

    async def _release(self, req: _Prepared) -> None:
        """Give the request's model slot back, once."""
        if req.decision is not None and not req.released:
            req.released = True
            await self.router.release(req.decision)

    async def _session_pii_override(self, session_id: str | None, auth: AuthContext) -> Any | None:
        """The PII settings a user chose for this chat session, if any."""
        if not session_id:
            return None
        try:
            sid = uuid.UUID(session_id)
        except (ValueError, TypeError):
            return None
        return await self.dao.get_session_pii_override(sid, auth.tenant_id, auth.user_id)

    async def _scan_egress(
        self,
        req: _Prepared,
        *,
        candidates: list[Any],
        request_id: str,
    ) -> tuple[tuple[dict[str, Any], dict[str, str] | None] | None, list[Any]]:
        """Scan what will leave, when any route it may take calls for it.

        Returns the scan (payload and token mapping), or ``None`` when there is
        none, and the candidates left: only the local ones when a failed scan
        falls back to them. The route is chosen after this -- by the slot -- so
        the scan is made if any candidate needs it, and a route that does not
        gets the payload as it was.
        """
        pii_policy = req.pii_policy
        if (
            pii_policy is None
            or self.pii_client is None
            or not any(_needs_scan(pii_policy, c) for c in candidates)
        ):
            return None, candidates
        try:
            return await self._sanitize(req.payload, pii_policy), candidates
        except Exception:
            fail_action = pii_policy.egress.fail_action
            if fail_action == "block":
                raise GatewayError(
                    status_code=502,
                    message="PII service unavailable and fail_action=block.",
                    error_type="server_error",
                    code="pii_service_unavailable",
                ) from None
            if fail_action == "fallback_to_local" and any(_is_cloud(c) for c in candidates):
                req.fallback_outcome = "fallback_to_local_attempted"
                local = [c for c in candidates if not _is_cloud(c)]
                if not local:
                    req.fallback_outcome = "fallback_to_local_failed"
                    raise GatewayError(
                        status_code=503,
                        message="PII fallback requested but no local provider candidate is available.",
                        error_type="server_error",
                        code="pii_fallback_no_local_provider",
                    ) from None
                if any(_needs_scan(pii_policy, c) for c in local):
                    try:
                        return await self._sanitize(req.payload, pii_policy), local
                    except Exception:
                        # Local is let through, as it was before.
                        logger.warning(
                            "PII egress scan failed again for %s; sending to local unscanned", request_id,
                        )
                return None, local
            logger.warning(
                "PII egress scan failed for %s; fail_action=%s, allowing through",
                request_id,
                fail_action,
            )
            return None, candidates

    async def _sanitize(
        self, payload: dict[str, Any], pii_policy: PIIPolicy,
    ) -> tuple[dict[str, Any], dict[str, str] | None]:
        assert self.pii_client is not None  # noqa: S101
        result = await self.pii_client.sanitize(
            payload=payload,
            policy=pii_policy,
            mode=pii_policy.egress.mode,
        )
        return result.sanitized_payload, result.token_mapping

    async def _lease(
        self, req: _Prepared, *, candidates: list[Any], request_id: str,
    ) -> RoutingDecision:
        """Take a model slot among *candidates*."""
        if req.fallback_outcome != "fallback_to_local_attempted":
            return await self.router.pick_and_lease(candidates=candidates, request_id=request_id)
        try:
            decision = await self.router.pick_and_lease(candidates=candidates, request_id=request_id)
        except GatewayError as exc:
            req.fallback_outcome = "fallback_to_local_failed"
            if exc.code == "no_capacity":
                raise GatewayError(
                    status_code=503,
                    message="PII fallback requested but no local provider has free capacity.",
                    error_type="server_error",
                    code="pii_fallback_no_local_capacity",
                ) from exc
            raise
        req.fallback_outcome = "fallback_to_local_succeeded"
        return decision

    # ------------------------------------------------------------------
    # PII helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mcp_pii_mode_override(
        pii_policy: PIIPolicy | None,
        decision: RoutingDecision | None,
    ) -> str | None:
        """Compute PII mode override for MCP tool calls.

        When the egress PII policy disables scanning for the current
        provider type (e.g. ``enabled_for_local=false`` and the provider
        is local), returns ``"allow"`` so the MCP proxy skips PII
        scanning.  Otherwise returns ``None`` (use server default).
        """
        if pii_policy is None or decision is None:
            return None
        is_cloud = decision.candidate.provider_type.value.startswith("remote_")
        should_scan = (
            (is_cloud and pii_policy.egress.enabled_for_cloud)
            or (not is_cloud and pii_policy.egress.enabled_for_local)
        )
        if not should_scan:
            return "allow"
        return None

    @staticmethod
    def _inject_datetime_context(
        egress_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Prepend a system message with the current date/time.

        Ensures the model knows "today's" date so it can reason about
        recency, scheduled events, etc.  Inserted as the very first
        system message so it doesn't displace user-provided prompts.
        """
        messages = egress_payload.get("messages")
        if not isinstance(messages, list):
            return egress_payload

        now = datetime.now(timezone.utc)
        # %-d (no zero-pad) is a GNU/POSIX extension that raises
        # ValueError on Windows; zero-padded %d works everywhere.
        date_str = now.strftime("%A, %B %d, %Y, %H:%M UTC")
        prompt = f"Current date and time: {date_str}."
        date_msg: dict[str, str] = {"role": "system", "content": prompt}

        new_messages = [date_msg, *messages]
        return {**egress_payload, "messages": new_messages}

    @staticmethod
    def _inject_pii_system_prompt(
        egress_payload: dict[str, Any],
        pii_policy: PIIPolicy,
    ) -> dict[str, Any]:
        """Prepend a system message explaining PII redaction/tokenization.

        Only modifies ``messages``-based payloads (chat completions).
        Returns a shallow-copied payload with the injected message.
        """
        messages = egress_payload.get("messages")
        if not isinstance(messages, list):
            return egress_payload

        prompt = (
            _PII_TOKENIZE_SYSTEM_PROMPT
            if pii_policy.egress.mode == "tokenize_reversible"
            else _PII_REDACT_SYSTEM_PROMPT
        )

        pii_system_msg: dict[str, str] = {"role": "system", "content": prompt}
        # Insert after any existing leading system messages so we don't
        # displace the user's own system prompt.
        insert_idx = 0
        for i, msg in enumerate(messages):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        new_messages = list(messages)
        new_messages.insert(insert_idx, pii_system_msg)
        return {**egress_payload, "messages": new_messages}

    async def _apply_telemetry_pii(
        self,
        *,
        payload: dict[str, Any],
        pii_policy: PIIPolicy,
        request_id: str,
    ) -> dict[str, Any]:
        """Produce a PII-clean version of *payload* for observability.

        If the telemetry mode is ``metrics_only`` we return a minimal
        stub so that Langfuse still gets token counts but no text.
        """
        assert self.pii_client is not None  # noqa: S101

        if pii_policy.telemetry.mode == "metrics_only":
            # Strip all text content; keep only model + metadata
            return {"model": payload.get("model"), "_pii_mode": "metrics_only"}

        try:
            result = await self.pii_client.sanitize(
                payload=payload,
                policy=pii_policy,
                mode="redact",  # always redact for telemetry
            )
            return result.sanitized_payload
        except Exception:
            logger.warning(
                "PII telemetry scan failed for %s; falling back to metadata-only",
                request_id,
            )
            return {"model": payload.get("model"), "_pii_mode": "fallback"}

    async def _inject_session_context(
        self,
        payload: dict[str, Any],
        auth: AuthContext,
        session_id_str: str | None,
        req: _Prepared | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        """Inject session history and memory into the payload.

        Returns ``(updated_payload, resolved_session_id_hex)``
        where the session id is ``None`` when sessions are disabled.
        With *req*, attached files too long to include are left for the model
        to search, and go on ``req.attachments``.
        """
        if not session_id_str or not self.session_dao:
            return payload, None

        import uuid as _uuid  # noqa: PLC0415

        from llm_port_api.services.gateway.context_assembler import ContextAssembler  # noqa: PLC0415

        try:
            sid = _uuid.UUID(session_id_str)
        except ValueError:
            return payload, None

        sess = await self.session_dao.get_session(
            session_id=sid, tenant_id=auth.tenant_id, user_id=auth.user_id,
        )
        if not sess:
            return payload, None

        # Resolve project if the session belongs to one
        project = None
        if sess.project_id:
            project = await self.session_dao.get_project(
                project_id=sess.project_id,
                tenant_id=auth.tenant_id,
                user_id=auth.user_id,
            )

        # Resolve file store for attachment context injection
        file_store = getattr(self, "_file_store", None)

        assembler = ContextAssembler(
            dao=self.session_dao,
            max_recent_messages=settings.session_max_recent_messages,
            token_budget=settings.session_token_budget,
            file_store=file_store,
            searchable=req is not None,
        )

        # Current request messages become the "tail" of the assembled context
        current_messages = payload.get("messages", [])

        # ── Dedup: detect retry / reload-retry ──────────────────
        # If the last persisted message already matches the incoming
        # user message, this is a retry.  Skip persistence and drop
        # the duplicate from current_messages so the assembler
        # (which already loads it from history) doesn't double it.
        if current_messages:
            last_msgs = await self.session_dao.get_recent_messages(
                session_id=sid, limit=1,
            )
            if last_msgs:
                last_db = last_msgs[-1]
                first_cur = current_messages[0]
                cur_content = first_cur.get("content", "")
                if isinstance(cur_content, list):
                    cur_content = " ".join(
                        p.get("text", "") for p in cur_content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ) or ""
                if (
                    last_db.role == first_cur.get("role")
                    and last_db.content == cur_content
                ):
                    current_messages = current_messages[1:]

        assembled = await assembler.assemble(
            session_id=sid,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            current_messages=current_messages,
            project=project,
        )

        payload["messages"] = assembled.messages
        if req is not None:
            req.attachments = [
                attachment_search.SearchableFile(id=str(a.id), filename=a.filename, text=a.extracted_text or "")
                for a in assembled.searchable
            ]

        # Persist only genuinely new user/system messages
        for msg in current_messages:
            if msg.get("role") in ("user", "system"):
                content = msg.get("content", "")
                # Handle multimodal content arrays
                content_parts_json = None
                if isinstance(content, list):
                    content_parts_json = content
                    content = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ) or ""
                await self.session_dao.append_message(
                    session_id=sid,
                    role=msg["role"],
                    content=content,
                    content_parts_json=content_parts_json,
                )

        # Commit user messages immediately so they survive if the
        # streaming response is interrupted (e.g. page reload).
        if current_messages:
            await self.session_dao.session.commit()

        return payload, str(sid)

    # ── Skills helpers ───────────────────────────────────────────────────────

    async def _resolve_skills(
        self,
        question: str,
        auth: AuthContext,
        session_id: str | None = None,
    ) -> list[ResolvedSkill]:
        """The skills that apply to *question*, for ``_prepare`` to put in.

        Resolved while the session loads, so the session id is the one the
        request named; the skills service does not use it to resolve.
        """
        if not self.skills_client:
            return []
        result = await self.skills_client.resolve_skills(
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            session_id=session_id,
            user_query=question or None,
        )
        return result.skills

    def _apply_skill_tool_constraints(
        self,
        payload: dict[str, Any],
        skills: list[ResolvedSkill],
    ) -> dict[str, Any]:
        """Filter the tools array based on skill constraints.

        If any resolved skill specifies ``forbidden_tools``, those tools
        are removed.  If any skill specifies ``allowed_tools``, only the
        union of allowed tools across all skills is kept.
        """
        if not skills:
            return payload

        tools = payload.get("tools")
        if not tools:
            return payload

        # Collect constraints across all resolved skills
        all_allowed: set[str] | None = None
        all_forbidden: set[str] = set()

        for skill in skills:
            if skill.forbidden_tools:
                all_forbidden.update(skill.forbidden_tools)
            if skill.allowed_tools:
                if all_allowed is None:
                    all_allowed = set()
                all_allowed.update(skill.allowed_tools)

        if all_allowed is None and not all_forbidden:
            return payload

        filtered: list[dict[str, Any]] = []
        for tool in tools:
            name = tool.get("function", {}).get("name", "")
            if name in all_forbidden:
                continue
            if all_allowed is not None and name not in all_allowed:
                continue
            filtered.append(tool)

        return {**payload, "tools": filtered}

    async def _record_skills_usage(
        self,
        skills: list[ResolvedSkill],
        auth: AuthContext,
        session_id: str | None = None,
    ) -> None:
        """Fire-and-forget usage telemetry for resolved skills."""
        if not self.skills_client or not skills:
            return
        for skill in skills:
            await self.skills_client.record_usage(
                tenant_id=auth.tenant_id,
                skill_id=skill.skill_id,
                version=skill.version,
                session_id=session_id,
                user_id=auth.user_id,
            )

    # ── MCP tool helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _merge_mcp_tools(
        payload: dict[str, Any],
        mcp_tools: list[dict[str, Any]],
        tenant_id: str,
    ) -> dict[str, Any]:
        """Merge MCP tools into the outgoing payload's ``tools`` array."""
        existing = list(payload.get("tools") or [])
        existing.extend(
            t["openai_tool"] if "openai_tool" in t else t for t in mcp_tools
        )
        payload = {**payload, "tools": existing}
        # Ensure tool_choice allows the model to call tools
        if "tool_choice" not in payload:
            payload["tool_choice"] = "auto"
        logger.info(
            "MCP tool injection: %d tool(s) merged for tenant %s: %s",
            len(mcp_tools),
            tenant_id,
            [
                (t.get("openai_tool", t).get("function", {}).get("name", "?") if isinstance(t, dict) else "?")
                for t in mcp_tools
            ],
        )
        return payload

    # ── Tools the gateway runs ───────────────────────────────────────────────

    def _offers_knowledge(self, candidate: Any, payload: dict[str, Any]) -> bool:
        """Whether the model gets the knowledge tools.

        When RAG Lite is on, the client has not ruled tools out, and the
        route's model answers with tool calls -- vLLM refuses a request that
        offers tools otherwise.
        """
        return (
            self.rag_lite_client is not None
            and settings.rag_lite_enabled
            and not settings.rag_enabled
            and payload.get("tool_choice") != "none"
            and knowledge.calls_tools(candidate)
        )

    def _router_session(self, session_id: str | None) -> uuid.UUID | None:
        """The session the tool router runs tools for, when it is in use."""
        if self.tool_router is None or not session_id:
            return None
        try:
            return uuid.UUID(session_id)
        except ValueError:
            return None

    async def _tool_loop(
        self, req: _Prepared, result: UpstreamResult, *, auth: AuthContext, request_id: str,
    ) -> UpstreamResult:
        """Run the gateway's tools until the model answers (a whole answer).

        Each round the model asks for tools, the gateway runs them and asks
        again, at most ``mcp_tool_loop_max_iterations`` times. A round that
        asks for a tool the gateway cannot run -- one the client defined --
        goes back to the client as it is.
        """
        payload = req.egress_payload
        assert payload is not None  # noqa: S101
        for iteration in range(settings.mcp_tool_loop_max_iterations):
            choices = (result.payload or {}).get("choices") or []
            message = choices[0].get("message") if choices else None
            if not message or not message.get("tool_calls"):
                break
            answers = await self._run_tool_calls(
                req, message["tool_calls"], iteration=iteration, auth=auth, request_id=request_id,
            )
            if answers is None:
                break
            req.tool_iterations = iteration + 1
            turn = _assistant_turn(message.get("content"), message["tool_calls"])
            payload = {**payload, "messages": [*payload["messages"], turn, *answers]}
            result = await self._complete(req, payload)
        return result

    async def _stream_tool_rounds(
        self, req: _Prepared, *, auth: AuthContext, request_id: str,
    ) -> AsyncIterator[bytes]:
        """A streamed answer, with the gateway's tools run between rounds.

        The answer's text reaches the client as it comes, with PII tokens put
        back. A round that ends in tool calls the gateway runs is not shown:
        they are run, and the next round streams on. Calls the client defined
        go to the client, as a streamed answer's tool calls do.
        """
        payload = req.egress_payload
        assert payload is not None and req.decision is not None  # noqa: S101
        usage: dict[str, int] = {}
        head: dict[str, Any] = {}
        for iteration in range(settings.mcp_tool_loop_max_iterations + 1):
            raw = await self.adapter.completion(
                **_candidate_adapter_kwargs(req.decision.candidate), payload=payload, stream=True,
            )
            calls = ToolCalls()
            said: list[str] = []
            ended: dict[str, Any] = {"finish": None, "failed": False}

            async def answer(raw: Any = raw, calls: ToolCalls = calls, said: list[str] = said,
                             ended: dict[str, Any] = ended) -> AsyncIterator[bytes]:
                async for event in sse_events(raw):  # type: ignore[arg-type]
                    if event == DONE:
                        continue
                    assert isinstance(event, dict)  # noqa: S101
                    if "error" in event and not event.get("choices"):
                        ended["failed"] = True
                        yield sse(event)
                        continue
                    own = {k: event[k] for k in ("id", "object", "created", "model") if k in event}
                    head.update(own)
                    _add_usage(usage, event.get("usage"))
                    for choice in event.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("tool_calls"):
                            calls.add(delta["tool_calls"])
                        if choice.get("finish_reason"):
                            ended["finish"] = choice["finish_reason"]
                        if delta.get("content"):
                            said.append(delta["content"])
                            yield sse({**own, "choices": [{
                                "index": choice.get("index", 0),
                                "delta": {"content": delta["content"]},
                                "finish_reason": None,
                            }]})

            async for chunk in restore_sse(answer(), req.token_mapping):
                yield chunk
            if ended["failed"]:
                yield sse(DONE)
                return

            if calls and iteration < settings.mcp_tool_loop_max_iterations:
                answers = await self._run_tool_calls(
                    req, calls.calls(), iteration=iteration, auth=auth, request_id=request_id,
                )
                if answers is not None:
                    req.tool_iterations = iteration + 1
                    turn = _assistant_turn("".join(said) or None, calls.calls())
                    payload = {**payload, "messages": [*payload["messages"], turn, *answers]}
                    continue
            if calls:
                # Tools the client defined: its to run, as in any streamed answer.
                yield sse({**head, "choices": [{
                    "index": 0, "delta": {"tool_calls": calls.as_deltas()}, "finish_reason": None,
                }]})
            req.finish_reason = ended["finish"] or ("tool_calls" if calls else "stop")
            last: dict[str, Any] = {
                **head, "choices": [{"index": 0, "delta": {}, "finish_reason": req.finish_reason}],
            }
            if usage:
                last["usage"] = usage
            yield sse(last)
            yield sse(DONE)
            return

    async def _complete(self, req: _Prepared, payload: dict[str, Any]) -> UpstreamResult:
        """One whole model call; an error from upstream is raised, not answered."""
        from llm_port_api.services.gateway.llm_adapter import CompletionResult  # noqa: PLC0415

        assert req.decision is not None  # noqa: S101
        adapter_result = await self.adapter.completion(
            **_candidate_adapter_kwargs(req.decision.candidate), payload=payload, stream=False,
        )
        assert isinstance(adapter_result, CompletionResult)  # noqa: S101
        if adapter_result.status_code >= 400:
            error = (adapter_result.payload or {}).get("error", {})
            raise GatewayError(
                status_code=adapter_result.status_code,
                message=error.get("message") or f"Upstream error {adapter_result.status_code}",
                error_type=error.get("type", "upstream_error"),
                code=error.get("code"),
            )
        return UpstreamResult(
            status_code=adapter_result.status_code, payload=adapter_result.payload, headers={},
        )

    async def _run_tool_calls(
        self,
        req: _Prepared,
        calls: list[dict[str, Any]],
        *,
        iteration: int,
        auth: AuthContext,
        request_id: str,
    ) -> list[dict[str, Any]] | None:
        """Run *calls*; the tool messages that answer them, PII-scanned.

        ``None`` when any of them is not the gateway's to run (a tool the
        client defined): the model's message then goes to the client as it
        is, rather than half-answered.
        """
        router_session = self._router_session(req.session_id)

        def ours(name: str) -> bool:
            if name in knowledge.NAMES:
                return req.knowledge
            if name == attachment_search.NAME:
                return bool(req.attachments)
            if router_session is not None:
                return name.startswith(("mcp.", "client.", "server."))
            return self.mcp_client is not None and name.startswith(MCP_TOOL_PREFIX)

        names = [_tool_name(call) for call in calls]
        if not all(ours(name) for name in names):
            return None

        async def run(call: dict[str, Any], name: str) -> tuple[dict[str, Any], dict[str, Any]]:
            arguments = _tool_arguments(call)
            started = time.perf_counter()
            row: dict[str, Any] = {"iteration": iteration, "tool_name": name, "mcp_server": None}
            if name in knowledge.NAMES:
                # Searched inside LLM.Port, like the session store: with the
                # values tokenize mode took out, or it would look for
                # "[PERSON_1]". A tool elsewhere (MCP) gets the tokens.
                if req.token_mapping:
                    arguments = _restored(arguments, req.token_mapping)
                assert self.rag_lite_client is not None  # noqa: S101
                tools = knowledge.KnowledgeTools(self.rag_lite_client, token=auth.token)
                ran = await tools.run(name, arguments)
                content, is_error = ran.content, ran.is_error
            elif name == attachment_search.NAME:
                # The session's own files, searched in memory, with the values
                # tokenize mode took out -- as the knowledge tools are.
                if req.token_mapping:
                    arguments = _restored(arguments, req.token_mapping)
                content, is_error = attachment_search.AttachmentSearch(req.attachments).run(arguments)
            elif router_session is not None:
                assert self.tool_router is not None  # noqa: S101
                routed = await self.tool_router.route(
                    tool_id=name, arguments=arguments, call_id=call.get("id", ""),
                    session_id=router_session, tenant_id=auth.tenant_id, request_id=request_id,
                )
                content, is_error = routed.content, routed.is_error
                row.update(mcp_server=_mcp_server(name), realm=routed.realm, executor=routed.executor)
            else:
                assert self.mcp_client is not None  # noqa: S101
                try:
                    called = await self.mcp_client.call_tool(
                        qualified_name=name, arguments=arguments, tenant_id=auth.tenant_id,
                        request_id=request_id,
                        pii_mode_override=self._mcp_pii_mode_override(req.pii_policy, req.decision),
                    )
                    content, is_error = called.content, called.is_error
                except Exception as exc:  # noqa: BLE001 - the model is told
                    content, is_error = f"Tool call failed: {exc}", True
                row["mcp_server"] = _mcp_server(name)
            row.update(
                latency_ms=int((time.perf_counter() - started) * 1000),
                is_error=is_error,
                error_message=content[:500] if is_error else None,
            )
            return row, {"role": "tool", "tool_call_id": call.get("id", ""), "content": content}

        # Calls the model makes together are independent -- it asked for them
        # before seeing any result -- so they run together: two searches take
        # as long as the slower one, not both. The tool router's run one at a
        # time, as they always have: some of them run on the user's machine.
        pairs = list(zip(calls, names, strict=True))
        if router_session is None:
            done = await _together(*(run(call, name) for call, name in pairs))
        else:
            done = [await run(call, name) for call, name in pairs]
        answers: list[dict[str, Any]] = []
        for row, answer in done:
            req.tool_calls.append(row)
            answers.append(answer)
        return await self._scan_tool_results(req, answers)

    async def _scan_tool_results(self, req: _Prepared, answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Tool results pass through PII like the rest of what leaves.

        In tokenize mode they continue the request's mapping, so a name in a
        search result is the token the question already used for it.
        """
        if (
            self.pii_client is None
            or req.decision is None
            or req.pii_policy is None
            or not _needs_scan(req.pii_policy, req.decision.candidate)
        ):
            return answers
        # Each text of each answer is scanned apart -- every passage of a
        # search, its query, its sources -- so the PII service's cache, which
        # keeps one analysis per text, knows a passage the next search returns
        # again. Scanned as one JSON blob per answer, every search was analysed
        # afresh: 400-900 ms a round for five passages.
        units: list[str] = []
        rebuilds: list[tuple[int, int, Callable[[list[str]], str]]] = []
        for answer in answers:
            parts, rebuild = _scan_units(str(answer.get("content") or ""))
            rebuilds.append((len(units), len(parts), rebuild))
            units.extend(parts)
        try:
            scanned = await self.pii_client.sanitize(
                payload={"messages": [{"role": "tool", "content": unit} for unit in units]},
                policy=req.pii_policy,
                mode=req.pii_policy.egress.mode,
                token_mapping=req.token_mapping,
            )
        except Exception:
            if req.pii_policy.egress.fail_action == "allow":
                logger.warning("PII scan of tool results failed; fail_action=allow, sending them as they are")
                return answers
            raise GatewayError(
                status_code=502,
                message="PII service unavailable: tool results were not sent to the model.",
                error_type="server_error",
                code="pii_service_unavailable",
            ) from None
        if scanned.token_mapping:
            req.token_mapping = scanned.token_mapping
        texts = [str(m.get("content") or "") for m in scanned.sanitized_payload.get("messages") or []]
        if len(texts) != len(units):
            raise GatewayError(
                status_code=502,
                message="PII scan of tool results came back incomplete: they were not sent to the model.",
                error_type="server_error",
                code="pii_scan_incomplete",
            )
        return [
            {**answer, "content": rebuild(texts[start:start + count])}
            for answer, (start, count, rebuild) in zip(answers, rebuilds, strict=True)
        ]

    async def _persist_assistant_response(
        self,
        *,
        session_id_str: str | None,
        response_payload: dict[str, Any],
        model_alias: str | None = None,
        provider_instance_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Store the assistant's response message in the session."""
        if not session_id_str or not self.session_dao:
            return

        import uuid as _uuid  # noqa: PLC0415

        try:
            sid = _uuid.UUID(session_id_str)
        except ValueError:
            return

        choices = response_payload.get("choices", [])
        if not choices:
            return

        msg_data = choices[0].get("message", {})
        content = msg_data.get("content", "")
        if not content:
            return

        # Handle multimodal assistant responses
        content_parts_json = None
        if isinstance(content, list):
            content_parts_json = content
            content = " ".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ) or ""

        usage = response_payload.get("usage", {})
        tokens = usage.get("completion_tokens")

        await self.session_dao.append_message(
            session_id=sid,
            role="assistant",
            content=content,
            content_parts_json=content_parts_json,
            model_alias=model_alias,
            provider_instance_id=(
                _uuid.UUID(provider_instance_id) if provider_instance_id else None
            ),
            token_estimate=tokens,
            trace_id=trace_id,
        )

    async def _persist_stream_assistant_response(
        self,
        *,
        session_id_str: str,
        content: str,
        model_alias: str | None = None,
        provider_instance_id: str | None = None,
        trace_id: str | None = None,
        token_estimate: int | None = None,
    ) -> None:
        """Store the accumulated streaming assistant response in the session.

        Uses a fresh, independent DB session so the commit is not tied
        to the request-scoped session (which may already be closed or
        rolled back if the client disconnected mid-stream).
        """
        if not self.session_dao or not content:
            return

        import uuid as _uuid  # noqa: PLC0415

        try:
            sid = _uuid.UUID(session_id_str)
        except ValueError:
            return

        # Obtain a fresh DB session from the factory stored on session_dao.
        # The request-scoped session may be unusable at this point (client
        # disconnect can close/rollback it), so we create an independent one.
        engine = self.session_dao.session.bind  # AsyncEngine
        from sqlalchemy.ext.asyncio import AsyncSession  # noqa: PLC0415
        from llm_port_api.db.models.gateway import ChatMessage  # noqa: PLC0415

        async with AsyncSession(engine, expire_on_commit=False) as fresh_session:
            msg = ChatMessage(
                session_id=sid,
                role="assistant",
                content=content,
                model_alias=model_alias,
                provider_instance_id=(
                    _uuid.UUID(provider_instance_id) if provider_instance_id else None
                ),
                token_estimate=token_estimate,
                trace_id=trace_id,
            )
            fresh_session.add(msg)
            await fresh_session.commit()


def _accumulate_stream_content(chunk: bytes, acc: list[str]) -> None:
    """Extract assistant content deltas from an SSE chunk and append to acc."""
    text = chunk.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        choice = parsed.get("choices", [{}])[0] if parsed.get("choices") else None
        if choice:
            delta_content = choice.get("delta", {}).get("content")
            if delta_content:
                acc.append(delta_content)


def _resolve_pii_policy(
    policy: Any | None,
    session_override: Any | None = None,
) -> PIIPolicy | None:
    """Resolve effective PII policy: tenant-specific → system default → None.

    When *session_override* (a ``SessionPIIOverrideRow``) is provided,
    ``clamp_and_merge`` is applied on top of the floor policy.
    """
    from llm_port_api.settings import settings as _settings

    raw = policy.pii_config if policy and getattr(policy, "pii_config", None) else None
    floor: PIIPolicy | None = None
    if raw:
        floor = parse_pii_policy(raw)
    if floor is None:
        # Fallback to the system-wide default policy loaded from system settings DB.
        default = getattr(_settings, "pii_default_policy", None)
        if default:
            floor = parse_pii_policy(default)
    if floor is None:
        return None

    if session_override is not None:
        from llm_port_api.services.gateway.pii_policy import (  # noqa: PLC0415
            SessionPIIOverride,
            clamp_and_merge,
        )

        override = SessionPIIOverride(
            pii_enabled=session_override.pii_enabled,
            egress_enabled_for_cloud=session_override.egress_enabled_for_cloud,
            egress_enabled_for_local=session_override.egress_enabled_for_local,
            egress_mode=session_override.egress_mode,
            egress_fail_action=session_override.egress_fail_action,
            telemetry_enabled=session_override.telemetry_enabled,
            telemetry_mode=session_override.telemetry_mode,
            presidio_threshold=session_override.presidio_threshold,
            presidio_entities_add=session_override.presidio_entities_add,
        )
        allow_mode = bool(getattr(policy, "allow_mode_override", False))
        return clamp_and_merge(floor, override, allow_mode_override=allow_mode)

    return floor


def _require_model(payload: dict[str, Any]) -> str:
    model = str(payload.get("model", "")).strip()
    if not model:
        raise GatewayError(
            status_code=400,
            message="Request must include a non-empty model.",
            code="missing_model",
            param="model",
        )
    return model


async def _check_limits(
    *,
    limiter: RateLimiter,
    tenant_id: str,
    payload: dict[str, Any],
    rpm_limit: int | None,
    tpm_limit: int | None,
) -> None:
    rpm = await limiter.check_rpm(tenant_id=tenant_id, limit=rpm_limit)
    if rpm and not rpm.allowed:
        raise GatewayError(
            status_code=429,
            message="Rate limit exceeded (RPM).",
            code="rate_limit_rpm",
        )
    estimated_tokens = estimate_input_tokens(
        payload.get("input") or payload.get("messages"),
    )
    tpm = await limiter.check_tpm(
        tenant_id=tenant_id, tokens=estimated_tokens, limit=tpm_limit,
    )
    if tpm and not tpm.allowed:
        raise GatewayError(
            status_code=429,
            message="Rate limit exceeded (TPM).",
            code="rate_limit_tpm",
        )
