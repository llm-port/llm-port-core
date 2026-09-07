"""Unit tests for the DB-setting-key → ``settings`` attribute mapping.

``resolve_runtime_attr`` looks the key up in the value map first, falling back
to the secret map.  ``register_*`` mutate the module-level dicts; each
registering test removes its key afterwards to keep the maps pristine.
"""

from __future__ import annotations

import pytest

from llm_port_backend.services.system_settings import runtime_mapping as rm


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("llm_port_api.pii_enabled", "pii_enabled"),
        ("llm_port_api.pii_service_url", "pii_service_url"),
        ("rag_lite.enabled", "rag_lite_enabled"),
        ("rag_lite.chunk_overlap_tokens", "rag_lite_chunk_overlap_tokens"),
    ],
)
def test_resolve_runtime_attr_known_keys(key: str, expected: str) -> None:
    assert rm.resolve_runtime_attr(key) == expected


def test_resolve_runtime_attr_secret_keys_fall_back_to_secret_map() -> None:
    assert rm.resolve_runtime_attr("llm_port_backend.users_secret") == "users_secret"
    assert rm.resolve_runtime_attr("llm_port_api.mcp_service_token") == "mcp_service_token"
    assert rm.resolve_runtime_attr("llm_port_api.skills_service_token") == "skills_service_token"


def test_resolve_runtime_attr_unknown_key_is_none() -> None:
    assert rm.resolve_runtime_attr("does.not.exist") is None


def test_register_runtime_value_key() -> None:
    key = "ee.test.value_attr"
    try:
        rm.register_runtime_value_key(key, "ee_value_attr")
        assert rm.get_runtime_value_key_map()[key] == "ee_value_attr"
        assert rm.resolve_runtime_attr(key) == "ee_value_attr"
    finally:
        rm.get_runtime_value_key_map().pop(key, None)


def test_register_runtime_secret_key() -> None:
    key = "ee.test.secret_attr"
    try:
        rm.register_runtime_secret_key(key, "ee_secret_attr")
        assert rm.get_runtime_secret_key_map()[key] == "ee_secret_attr"
        assert rm.resolve_runtime_attr(key) == "ee_secret_attr"
    finally:
        rm.get_runtime_secret_key_map().pop(key, None)


def test_register_returns_live_mutations() -> None:
    value_map = rm.get_runtime_value_key_map()
    secret_map = rm.get_runtime_secret_key_map()
    # The returned references are the live module-level dicts.
    assert "llm_port_api.pii_enabled" in value_map
    assert "llm_port_backend.users_secret" in secret_map
