"""Unit tests for the code-defined settings registry.

Covers ``validate_value`` type rules, ``registry_by_key``, and the
``extend_registry`` EE-extension hook.  ``extend_registry`` appends to the
module-level ``SETTINGS_REGISTRY`` list in place, so each test that extends it
removes its own definitions afterwards to keep the registry pristine for other
tests (cross-test pollution guard).
"""

from __future__ import annotations

import pytest

from llm_port_backend.db.models.system_settings import SystemApplyScope
from llm_port_backend.services.system_settings import registry as reg
from llm_port_backend.services.system_settings.registry import (
    SETTINGS_REGISTRY,
    SettingDefinition,
    extend_registry,
    registry_by_key,
    validate_value,
)


def _defn(key: str, type_: str, **extra: object) -> SettingDefinition:
    base: dict[str, object] = {
        "key": key,
        "type": type_,  # type: ignore[arg-type]
        "category": "test",
        "group": "test",
        "label": "Test",
        "description": "desc",
        "is_secret": type_ == "secret",
        "default": None,
        "apply_scope": SystemApplyScope.LIVE_RELOAD,
        "service_targets": (),
    }
    base.update(extra)
    return SettingDefinition(**base)  # type: ignore[arg-type]


def test_known_keys_present_in_registry() -> None:
    keys = set(registry_by_key())
    for key in (
        "api.server.endpoint_url",
        "llm_port_api.jwt_secret",
        "llm_port_api.pii_enabled",
        "shared.redis.password",
        "rag_lite.enabled",
        "rag_lite.chunk_max_tokens",
    ):
        assert key in keys


def test_registry_by_key_maps_key_to_definition() -> None:
    by_key = registry_by_key()
    pii = by_key["llm_port_api.pii_enabled"]
    assert pii.type == "bool"
    assert pii.default is False


# ──────────────────────────────────────────────────────────────────────────────
# validate_value
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("type_", ["string", "secret"])
def test_validate_string_and_secret_accept_str(type_: str) -> None:
    assert validate_value(_defn("k", type_), "hello") == "hello"


@pytest.mark.parametrize("type_", ["string", "secret"])
def test_validate_string_and_secret_reject_non_str(type_: str) -> None:
    with pytest.raises(ValueError, match="expects string value"):
        validate_value(_defn("k", type_), 123)


def test_validate_int_accepts_int_and_rejects_str() -> None:
    assert validate_value(_defn("k", "int"), 42) == 42
    with pytest.raises(ValueError, match="expects integer value"):
        validate_value(_defn("k", "int"), "42")


def test_validate_int_accepts_bool_quirk() -> None:
    # bool is a subclass of int in Python, so True is accepted for int settings.
    assert validate_value(_defn("k", "int"), True) is True


def test_validate_bool_accepts_bool_and_rejects_int() -> None:
    assert validate_value(_defn("k", "bool"), False) is False
    with pytest.raises(ValueError, match="expects boolean value"):
        # isinstance(True, int) is True, but bool branch is checked for bool specifically.
        validate_value(_defn("k", "bool"), 1)


def test_validate_json_accepts_dict_and_list() -> None:
    assert validate_value(_defn("k", "json"), {"a": 1}) == {"a": 1}
    assert validate_value(_defn("k", "json"), [1, 2]) == [1, 2]
    with pytest.raises(ValueError, match="expects object or array value"):
        validate_value(_defn("k", "json"), "not-json")


def test_validate_enum_requires_known_option() -> None:
    enum_defn = _defn("k", "enum", enum_values=("a", "b"))
    assert validate_value(enum_defn, "a") == "a"
    with pytest.raises(ValueError, match="expects one of: a, b"):
        validate_value(enum_defn, "c")


def test_validate_enum_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        validate_value(_defn("k", "enum", enum_values=("a", "b")), 5)


def test_validate_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported setting type"):
        validate_value(_defn("k", "frobnicate"), "x")


# ──────────────────────────────────────────────────────────────────────────────
# extend_registry
# ──────────────────────────────────────────────────────────────────────────────


def test_extend_registry_appends_and_is_visible_in_lookup() -> None:
    ee_key = "ee.custom.feature"
    ee_defn = _defn(ee_key, "string")
    try:
        extend_registry(ee_defn)
        assert registry_by_key()[ee_key] is ee_defn
    finally:
        # Keep the module-level registry pristine for other tests.
        SETTINGS_REGISTRY.remove(ee_defn)

    assert ee_key not in registry_by_key()


def test_extend_registry_accepts_multiple_defs() -> None:
    a = _defn("ee.multi.one", "string")
    b = _defn("ee.multi.two", "int")
    try:
        extend_registry(a, b)
        by_key = registry_by_key()
        assert by_key["ee.multi.one"] is a
        assert by_key["ee.multi.two"] is b
    finally:
        SETTINGS_REGISTRY.remove(b)
        SETTINGS_REGISTRY.remove(a)
