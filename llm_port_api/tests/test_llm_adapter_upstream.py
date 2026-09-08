"""Regression tests for :func:`_upstream_kwargs` (api_base + api_key resolution).

These pin the exact endpoint-resolution behaviour that was root-caused from
live 500s during the remote OpenAI-compatible provider work:

* a ``remote_openai`` (and vLLM/TGI) instance whose DB base URL had its
  trailing ``/v1`` stripped by the backend normaliser must have ``/v1``
  restored here (otherwise LiteLLM calls ``/chat/completions`` at the root
  and gets a 404);
* an explicitly-configured endpoint (``api_base`` set) with no API key must
  fall back to the conventional ``"EMPTY"`` placeholder, because the OpenAI
  SDK refuses to initialise without an api_key value even when the endpoint
  needs none;
* cloud providers reached at their default base URL (no ``api_base``) must
  NOT get ``api_base`` or the placeholder key — they need a real key and
  will fail with a clear 401 rather than an opaque init/404 error.
"""

from __future__ import annotations

import pytest

from llm_port_api.db.models.gateway import ProviderType
from llm_port_api.services.gateway.llm_adapter import _upstream_kwargs


@pytest.mark.parametrize(
    ("provider_type", "base_url"),
    [
        (ProviderType.VLLM, "http://upstream.local:8000"),
        (ProviderType.VLLM, "http://upstream.local:8000/"),
        (ProviderType.TGI, "http://tgi.local:8000"),
        # The bug fixed this session: DB stores /v1 stripped, restore it.
        (ProviderType.REMOTE_OPENAI, "http://10.88.10.49:8000"),
        (ProviderType.REMOTE_OPENAI, "http://10.88.10.49:8000/"),
    ],
)
def test_v1_prefix_restored_for_openai_compatible(
    provider_type: ProviderType,
    base_url: str,
) -> None:
    """/v1 is appended for engines + remote OpenAI servers serving under /v1."""
    kwargs = _upstream_kwargs(
        base_url=base_url, api_key=None, provider_type=provider_type
    )
    assert kwargs["api_base"].endswith("/v1")


def test_v1_prefix_not_doubled_when_already_present() -> None:
    """/v1 must not be appended twice (idempotent for already-qualified base)."""
    kwargs = _upstream_kwargs(
        base_url="http://host:8000/v1",
        api_key=None,
        provider_type=ProviderType.REMOTE_OPENAI,
    )
    assert kwargs["api_base"] == "http://host:8000/v1"


def test_no_api_base_for_provider_without_explicit_url() -> None:
    """No base_url → LiteLLM uses the provider default (no api_base key)."""
    kwargs = _upstream_kwargs(
        base_url=None, api_key=None, provider_type=ProviderType.REMOTE_OPENAI
    )
    assert "api_base" not in kwargs
    assert "api_key" not in kwargs


def test_litellm_scheme_url_is_untouched() -> None:
    """litellm:// URLs are LiteLLM-native configs, not custom api_base."""
    kwargs = _upstream_kwargs(
        base_url="litellm://some-internal-ref",
        api_key=None,
        provider_type=ProviderType.REMOTE_OPENAI,
    )
    assert "api_base" not in kwargs


@pytest.mark.parametrize(
    "provider_type",
    [
        ProviderType.VLLM,
        ProviderType.TGI,
        ProviderType.LLAMACPP,
        ProviderType.REMOTE_OPENAI,
        ProviderType.REMOTE_CUSTOM,
        ProviderType.OLLAMA,
    ],
)
def test_empty_api_key_placeholder_when_endpoint_set(
    provider_type: ProviderType,
) -> None:
    """Any explicit endpoint with no key → the 'EMPTY' placeholder."""
    kwargs = _upstream_kwargs(
        base_url="http://selfhosted.local:9000",
        api_key=None,
        provider_type=provider_type,
    )
    assert kwargs["api_base"] is not None
    assert kwargs["api_key"] == "EMPTY"


def test_real_api_key_wins_over_placeholder() -> None:
    """A configured key is sent verbatim and never overridden by 'EMPTY'."""
    kwargs = _upstream_kwargs(
        base_url="http://host:8000",
        api_key="sk-secret",
        provider_type=ProviderType.REMOTE_OPENAI,
    )
    assert kwargs["api_key"] == "sk-secret"


def test_no_placeholder_key_for_cloud_default_base() -> None:
    """Cloud provider at default base (no api_base) → no api_key injected.

    This is the guard against silently sending 'EMPTY' to a real cloud
    provider: it only becomes an api_key when an explicit endpoint is set.
    """
    kwargs = _upstream_kwargs(
        base_url=None, api_key=None, provider_type=ProviderType.REMOTE_OPENAI
    )
    assert kwargs == {}
