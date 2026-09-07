"""Unit tests for the pure Loki/log-helper functions in ``web/api/logs/views``.

Covers URL building, label extraction + allowlisting, time parsing/formatting,
and structured-line detection.  All functions are pure except for reading the
global ``settings`` singleton, whose fields we monkeypatch per-test.  No LLM
upstream, Docker, or database is touched.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from llm_port_backend.settings import settings
from llm_port_backend.web.api.logs import views as logs_views

ONE_SEC_NS = 1_000_000_000


# ──────────────────────────────────────────────────────────────────────────────
# _loki_http_url / _loki_ws_url
# ──────────────────────────────────────────────────────────────────────────────


def test_loki_http_url_joins_path() -> None:
    assert isinstance(settings.loki_base_url, str)
    assert logs_views._loki_http_url("/loki/api/v1/query") == f"{settings.loki_base_url.rstrip('/')}/loki/api/v1/query"


def test_loki_http_url_strips_trailing_slash_from_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "loki_base_url", "http://loki:3100/")
    assert logs_views._loki_http_url("/x") == "http://loki:3100/x"


def test_loki_ws_url_http_base_uses_ws() -> None:
    assert isinstance(settings.loki_base_url, str)
    url = logs_views._loki_ws_url("/x", {"query": "{app=\"x\"}"})
    assert url.startswith("ws://")
    assert url.endswith("query=%7Bapp%3D%22x%22%7D")


def test_loki_ws_url_https_base_uses_wss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "loki_base_url", "https://loki.example:3100")
    url = logs_views._loki_ws_url("/x", {})
    assert url.startswith("wss://")
    assert url == "wss://loki.example:3100/x"


# ──────────────────────────────────────────────────────────────────────────────
# _extract_query_labels
# ──────────────────────────────────────────────────────────────────────────────


def test_extract_labels_single_brace() -> None:
    assert logs_views._extract_query_labels('{app="foo", pod="bar"}') == {"app", "pod"}


def test_extract_labels_ignores_bare_group_by() -> None:
    # labels inside braces only; the sum-by name is not a label selector
    assert logs_views._extract_query_labels('sum by (app) {app="x"}') == {"app"}


def test_extract_labels_matches_all_operators() -> None:
    assert logs_views._extract_query_labels('{foo!=1, bar=~"y", baz!~"z"}') == {"foo", "bar", "baz"}


def test_extract_labels_no_braces_empty() -> None:
    assert logs_views._extract_query_labels("no braces here") == set()


# ──────────────────────────────────────────────────────────────────────────────
# _enforce_query_allowlist / _enforce_label_name_allowed
# ──────────────────────────────────────────────────────────────────────────────


def test_allowlist_none_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", None)
    # No error even with arbitrary labels when no allowlist is configured.
    logs_views._enforce_query_allowlist('{anything="x"}')
    logs_views._enforce_label_name_allowed("anything")


def test_query_allowlist_allows_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", "app,pod")
    logs_views._enforce_query_allowlist('{app="x", pod="y"}')


def test_query_allowlist_rejects_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", "app,pod")
    with pytest.raises(HTTPException) as exc:
        logs_views._enforce_query_allowlist('{app="x", bad="y"}')
    assert exc.value.status_code == 400
    assert "bad" in str(exc.value.detail)


def test_query_allowlist_no_labels_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", "app")
    logs_views._enforce_query_allowlist('sum by (x)')  # no braces → no labels


@pytest.mark.parametrize("name,ok", [("app", True), ("APP", True), ("bad", False)])
def test_label_name_allowed_cases(monkeypatch: pytest.MonkeyPatch, name: str, ok: bool) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", "app")
    if ok:
        logs_views._enforce_label_name_allowed(name)
    else:
        with pytest.raises(HTTPException) as exc:
            logs_views._enforce_label_name_allowed(name)
        assert exc.value.status_code == 400
        assert "not allowed" in str(exc.value.detail)


# ──────────────────────────────────────────────────────────────────────────────
# _parse_time_to_ns / _time_param_ns
# ──────────────────────────────────────────────────────────────────────────────


def test_parse_time_numeric_ns_passthrough() -> None:
    assert logs_views._parse_time_to_ns("1700000000123456789") == 1700000000123456789


def test_parse_time_rfc3339_z() -> None:
    expected = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * ONE_SEC_NS)
    assert logs_views._parse_time_to_ns("2024-01-01T00:00:00Z") == expected


def test_parse_time_rfc3339_naive_assumed_utc() -> None:
    expected = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * ONE_SEC_NS)
    assert logs_views._parse_time_to_ns("2024-01-01T00:00:00") == expected


def test_parse_time_invalid_raises_400() -> None:
    with pytest.raises(HTTPException) as exc:
        logs_views._parse_time_to_ns("not-a-time")
    assert exc.value.status_code == 400
    assert "Invalid time value" in str(exc.value.detail)


def test_time_param_ns_returns_default_when_none() -> None:
    assert logs_views._time_param_ns(None, 42) == 42
    assert logs_views._time_param_ns("100", 42) == 100


# ──────────────────────────────────────────────────────────────────────────────
# _ns_to_rfc3339nano
# ──────────────────────────────────────────────────────────────────────────────


def test_ns_to_rfc3339nano_zero_nanos() -> None:
    assert logs_views._ns_to_rfc3339nano(str(1704067200_000000000)) == "2024-01-01T00:00:00.000000000Z"


def test_ns_to_rfc3339nano_preserves_nanoseconds() -> None:
    assert logs_views._ns_to_rfc3339nano(str(1704067200_123456789)) == "2024-01-01T00:00:00.123456789Z"


# ──────────────────────────────────────────────────────────────────────────────
# _maybe_parse_structured
# ──────────────────────────────────────────────────────────────────────────────


def test_maybe_parse_structured_object() -> None:
    assert logs_views._maybe_parse_structured(json.dumps({"a": 1})) == {"a": 1}


@pytest.mark.parametrize("line", ['[1, 2]', "123", '"str"', "not json", "{"])
def test_maybe_parse_structured_non_object_returns_none(line: str) -> None:
    assert logs_views._maybe_parse_structured(line) is None
