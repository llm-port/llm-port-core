"""LiteLLM adapter — unified LLM completion / embedding interface.

Wraps ``litellm.acompletion`` and ``litellm.aembedding`` to provide a
provider-agnostic calling layer.  The gateway service delegates all
upstream calls through this adapter instead of the raw HTTP proxy.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import litellm

from llm_port_api.db.crypto import decrypt_value
from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.gateway.ssl_helpers import build_ssl_kwargs

logger = logging.getLogger(__name__)

# ── Provider type → LiteLLM provider prefix mapping ──────────────────
_PROVIDER_PREFIX: dict[str, str] = {
    ProviderType.REMOTE_OPENAI: "openai",
    ProviderType.REMOTE_ANTHROPIC: "anthropic",
    ProviderType.REMOTE_GOOGLE: "gemini",
    ProviderType.REMOTE_BEDROCK: "bedrock",
    ProviderType.REMOTE_AZURE: "azure",
    ProviderType.REMOTE_MISTRAL: "mistral",
    ProviderType.REMOTE_GROQ: "groq",
    ProviderType.REMOTE_DEEPSEEK: "deepseek",
    ProviderType.REMOTE_COHERE: "cohere",
    ProviderType.REMOTE_CUSTOM: "openai",
    # Local inference engines — all speak OpenAI protocol
    ProviderType.VLLM: "openai",
    ProviderType.LLAMACPP: "openai",
    ProviderType.TGI: "openai",
    ProviderType.OLLAMA: "ollama",
}


@dataclass(slots=True, frozen=True)
class CompletionResult:
    """Unified non-streaming completion result."""

    status_code: int
    payload: dict[str, Any]


def _build_litellm_model_name(
    *,
    provider_type: ProviderType,
    litellm_provider: str | None,
    litellm_model: str | None,
    requested_model: str,
) -> str:
    """Build the ``model`` string that LiteLLM expects.

    LiteLLM uses a ``provider/model`` naming convention.  If the user
    has configured an explicit ``litellm_provider`` and ``litellm_model``
    we use those.  Otherwise we derive them from the ``ProviderType``
    and the model alias sent in the request.
    """
    prefix = litellm_provider or _PROVIDER_PREFIX.get(provider_type, "openai")
    model = litellm_model or requested_model

    # Google's API returns model names with a "models/" prefix
    # (e.g. "models/gemini-2.0-flash-lite") — strip it for LiteLLM.
    if model.startswith("models/"):
        model = model[len("models/") :]

    # For local OpenAI-compatible engines (vLLM, TGI, llama.cpp) we
    # always route through LiteLLM's "openai" provider so it uses the
    # custom api_base.  The model name after the prefix is passed
    # verbatim to the engine.
    if provider_type in (
        ProviderType.VLLM,
        ProviderType.LLAMACPP,
        ProviderType.TGI,
    ):
        return f"openai/{model}"

    if provider_type == ProviderType.OLLAMA:
        return f"ollama/{model}"

    # Remote providers — use explicit prefix/model
    return f"{prefix}/{model}"


def _resolve_api_key(encrypted_key: str | None) -> str | None:
    """Decrypt the Fernet-encrypted API key stored in the DB."""
    if not encrypted_key:
        return None
    try:
        return decrypt_value(encrypted_key, purpose="provider-api-key")
    except Exception:
        logger.warning("Failed to decrypt provider API key; sending without auth")
        return None


def _upstream_kwargs(
    *,
    base_url: str | None,
    api_key: str | None,
    provider_type: ProviderType,
) -> dict[str, Any]:
    """Build the LiteLLM kwargs that select the upstream endpoint.

    Two concerns live here so every calling method (chat completion,
    streaming completion, embeddings) stays consistent:

    * **``api_base``** — the engine and proxied OpenAI-compatible
      servers whose Chat Completions / Embeddings API lives under a
      ``/v1`` prefix.  The DB stores a normalised base URL with any
      trailing ``/v1`` stripped (see the backend ``gateway_sync``
      normaliser, which assumed the legacy HTTP proxy would re-add it),
      so we restore it here for every type whose upstream expects the
      ``/v1`` path segment.  LiteLLM (via the OpenAI SDK) appends the
      endpoint path directly to ``api_base``, so we must not add a second
      ``/v1`` for cloud providers reached at their default base URL.
    * **``api_key``** — any endpoint the operator pointed us at
      explicitly (self-hosted OpenAI-compatible server, local inference
      engine, proxy, …) with no key configured for the provider.  The
      OpenAI SDK refuses to initialise without an api_key value even
      though the endpoint needs none, so we send the conventional
      ``"EMPTY"`` placeholder.  Cloud providers reached at their default
      base URL (no ``api_base`` set) never take this branch: they require
      a real key and will fail with a clear 401 instead.
    """
    kwargs: dict[str, Any] = {}
    if base_url and not base_url.startswith("litellm://"):
        effective_base = base_url.rstrip("/")
        # Local engines (vLLM/TGI) and proxied OpenAI-compatible servers
        # serve the Chat Completions / Embeddings API under a /v1 prefix;
        # restore the segment the backend normaliser stripped on save.
        # (Full rationale: see the docstring above.)
        v1_prefixed = (
            ProviderType.VLLM,
            ProviderType.TGI,
            ProviderType.REMOTE_OPENAI,
        )
        if provider_type in v1_prefixed and not effective_base.endswith("/v1"):
            effective_base += "/v1"
        kwargs["api_base"] = effective_base
    if api_key:
        kwargs["api_key"] = api_key
    elif kwargs.get("api_base"):
        kwargs["api_key"] = "EMPTY"
    return kwargs


class LLMAdapter:
    """Provider-agnostic adapter backed by LiteLLM."""

    async def completion(
        self,
        *,
        provider_type: ProviderType,
        base_url: str | None,
        api_key_encrypted: str | None,
        litellm_provider: str | None,
        litellm_model: str | None,
        extra_params: dict[str, Any] | None,
        payload: dict[str, Any],
        stream: bool = False,
        instance_id: uuid.UUID | None = None,
        ssl_verify_mode: str | None = None,
        ssl_ca_bundle_pem: str | None = None,
        ssl_client_cert_pem: str | None = None,
        ssl_client_key_pem: str | None = None,
    ) -> CompletionResult | AsyncIterator[Any]:
        """Run a chat completion (streaming or non-streaming).

        Returns ``CompletionResult`` for non-streaming, or an async
        iterator of ``ModelResponse`` chunks for streaming.
        """
        model_name = _build_litellm_model_name(
            provider_type=provider_type,
            litellm_provider=litellm_provider,
            litellm_model=litellm_model,
            requested_model=payload.get("model", ""),
        )
        api_key = _resolve_api_key(api_key_encrypted)

        # Build kwargs for litellm.acompletion
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": payload.get("messages", []),
            "stream": stream,
        }
        # Resolve the upstream endpoint (api_base + api_key) the same
        # way every other Litellm method does (see :func:`_upstream_kwargs`).
        kwargs.update(
            _upstream_kwargs(
                base_url=base_url,
                api_key=api_key,
                provider_type=provider_type,
            )
        )

        # Pass through supported OpenAI params
        for key in (
            "temperature",
            "top_p",
            "max_tokens",
            "stop",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "user",
            "tools",
            "tool_choice",
            "response_format",
            "seed",
            "n",
        ):
            if key in payload:
                kwargs[key] = payload[key]

        # Merge extra_params (custom headers, api_version, etc.)
        if extra_params:
            extra_headers = extra_params.pop("extra_headers", None)
            if extra_headers and isinstance(extra_headers, dict):
                kwargs["extra_headers"] = extra_headers
            # Remaining params go directly to litellm
            kwargs.update(extra_params)

        # Apply per-provider TLS overrides (CA bundle / mTLS).  When the
        # provider has nothing configured this is a no-op and LiteLLM
        # inherits the global default set in lifespan.
        if instance_id is not None:
            kwargs.update(
                build_ssl_kwargs(
                    instance_id=instance_id,
                    ssl_verify_mode=ssl_verify_mode,
                    ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                    ssl_client_cert_pem=ssl_client_cert_pem,
                    ssl_client_key_pem=ssl_client_key_pem,
                ),
            )

        if stream:
            # Request token usage in the final streaming chunk (OpenAI-compatible).
            kwargs["stream_options"] = {"include_usage": True}
            return self._stream_completion(**kwargs)

        return await self._non_stream_completion(**kwargs)

    async def _non_stream_completion(self, **kwargs: Any) -> CompletionResult:
        """Execute a non-streaming completion."""
        try:
            response = await litellm.acompletion(**kwargs)
            # LiteLLM returns a ModelResponse — convert to dict
            payload = response.model_dump()  # type: ignore[union-attr]
            return CompletionResult(status_code=200, payload=payload)
        except litellm.exceptions.AuthenticationError as exc:
            return CompletionResult(
                status_code=401,
                payload=_error_payload("authentication_error", str(exc)),
            )
        except litellm.exceptions.RateLimitError as exc:
            return CompletionResult(
                status_code=429,
                payload=_error_payload("rate_limit_error", str(exc)),
            )
        except litellm.exceptions.BadRequestError as exc:
            return CompletionResult(
                status_code=400,
                payload=_error_payload("invalid_request_error", str(exc)),
            )
        except Exception as exc:
            logger.exception("LiteLLM completion failed")
            return CompletionResult(
                status_code=502,
                payload=_error_payload("server_error", str(exc)),
            )

    async def _stream_completion(self, **kwargs: Any) -> AsyncIterator[bytes]:
        """Execute a streaming completion, yielding SSE-encoded bytes."""
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            # Pre-stream failure — yield the error as an SSE event so the
            # client receives a structured error instead of a broken stream.
            logger.exception("LiteLLM streaming failed (pre-stream)")
            error_data = _error_payload("server_error", str(exc))
            yield f"data: {json.dumps(error_data)}\n\n".encode()
            yield b"data: [DONE]\n\n"
            return

        try:
            async for chunk in response:  # type: ignore[union-attr]
                data = chunk.model_dump()  # type: ignore[union-attr]
                yield f"data: {json.dumps(data)}\n\n".encode()
            yield b"data: [DONE]\n\n"
        except Exception as exc:
            logger.exception("LiteLLM streaming failed (mid-stream)")
            error_data = _error_payload("server_error", str(exc))
            yield f"data: {json.dumps(error_data)}\n\n".encode()
            yield b"data: [DONE]\n\n"

    async def embedding(
        self,
        *,
        provider_type: ProviderType,
        base_url: str | None,
        api_key_encrypted: str | None,
        litellm_provider: str | None,
        litellm_model: str | None,
        extra_params: dict[str, Any] | None,
        payload: dict[str, Any],
        instance_id: uuid.UUID | None = None,
        ssl_verify_mode: str | None = None,
        ssl_ca_bundle_pem: str | None = None,
        ssl_client_cert_pem: str | None = None,
        ssl_client_key_pem: str | None = None,
    ) -> CompletionResult:
        """Run an embedding request."""
        model_name = _build_litellm_model_name(
            provider_type=provider_type,
            litellm_provider=litellm_provider,
            litellm_model=litellm_model,
            requested_model=payload.get("model", ""),
        )
        api_key = _resolve_api_key(api_key_encrypted)

        kwargs: dict[str, Any] = {
            "model": model_name,
            "input": payload.get("input", ""),
        }
        # Same upstream resolution as completion(): embeddings on a
        # proxied OpenAI-compatible server need the identical /v1 and
        # api_key handling as the chat path.
        kwargs.update(
            _upstream_kwargs(
                base_url=base_url,
                api_key=api_key,
                provider_type=provider_type,
            )
        )
        if extra_params:
            kwargs.update(extra_params)

        if instance_id is not None:
            kwargs.update(
                build_ssl_kwargs(
                    instance_id=instance_id,
                    ssl_verify_mode=ssl_verify_mode,
                    ssl_ca_bundle_pem=ssl_ca_bundle_pem,
                    ssl_client_cert_pem=ssl_client_cert_pem,
                    ssl_client_key_pem=ssl_client_key_pem,
                ),
            )

        try:
            response = await litellm.aembedding(**kwargs)
            return CompletionResult(
                status_code=200,
                payload=response.model_dump(),  # type: ignore[union-attr]
            )
        except Exception as exc:
            logger.exception("LiteLLM embedding failed")
            return CompletionResult(
                status_code=502,
                payload=_error_payload("server_error", str(exc)),
            )


def _error_payload(error_type: str, message: str) -> dict[str, Any]:
    return {
        "error": {
            "type": error_type,
            "message": message,
            "param": None,
            "code": error_type,
        },
    }
