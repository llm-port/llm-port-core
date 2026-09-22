"""Unit tests for Ray log normalization (Phase 6, WI-2).

The boundary these defend: whatever a container runtime emits, the driver must
hand back :class:`LogPage`. The interesting cases are the ones that do not
parse -- a line an operator needs is often exactly the one with no timestamp.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from llm_port_backend.services.inference.drivers.ray.logs import (
    RayLogReader,
    _result_text,
    normalize_log_text,
    parse_log_line,
)
from llm_port_backend.services.inference.observability import LogSource


def test_parses_iso_timestamp_and_level() -> None:
    line = parse_log_line("2026-09-20T17:26:29.252000Z  WARNING  worker restarting")
    assert line.level == "WARNING"
    assert line.message == "worker restarting"
    assert line.ts is not None
    assert line.ts.year == 2026


def test_parses_bracketed_form() -> None:
    line = parse_log_line("[2026-09-20 17:26:29] [INFO] replica ready")
    assert line.level == "INFO"
    assert line.message == "replica ready"
    assert line.ts is not None


def test_parses_level_first_form() -> None:
    line = parse_log_line("ERROR 2026-09-20T17:26:29 engine failed to load")
    assert line.level == "ERROR"
    assert line.message == "engine failed to load"


def test_unparseable_line_keeps_its_full_text() -> None:
    """A traceback line has no timestamp; dropping it would hide the failure."""
    raw = "  File \"/opt/vllm/engine.py\", line 42, in load"
    line = parse_log_line(raw)
    assert line.ts is None
    assert line.level is None
    assert line.message == raw


def test_capitalised_first_word_is_not_treated_as_a_level() -> None:
    """``CUDA`` is not a log level; the line must survive intact."""
    line = parse_log_line("CUDA 2026-09-20T17:26:29 out of memory")
    assert line.level is None
    assert "CUDA" in line.message


def test_normalize_drops_blank_lines_and_marks_truncation() -> None:
    text = "\n".join(["line-1", "", "line-2", "   ", "line-3"])
    page = normalize_log_text(text, source=LogSource.RUNTIME_CONTAINER, tail=2)
    assert [entry.message for entry in page.lines] == ["line-2", "line-3"]
    assert page.truncated is True


def test_normalize_without_tail_is_not_truncated() -> None:
    page = normalize_log_text("a\nb", source=LogSource.SERVE_REPLICA)
    assert page.truncated is False
    assert len(page.lines) == 2


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"logs": "from-logs"}, "from-logs"),
        ({"output": "from-output"}, "from-output"),
        ({"stdout": ["a", "b"]}, "a\nb"),
        ({"error": "boom"}, ""),
    ],
)
def test_result_text_accepts_the_agent_s_key_variants(
    result: dict[str, Any], expected: str
) -> None:
    assert _result_text(result) == expected


class _FakeGateway:
    """Records issued commands and returns a canned terminal result."""

    def __init__(self, result: dict[str, Any] | None, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.issued: list[dict[str, Any]] = []

    async def issue(self, **kwargs: Any) -> Any:
        if self.fail:
            msg = "node offline"
            raise RuntimeError(msg)
        self.issued.append(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    async def wait(self, command_id: Any, *, budget_sec: float) -> Any:
        if self.result is None:
            return None
        return SimpleNamespace(result_json=self.result)


def _deployment() -> Any:
    return SimpleNamespace(id=uuid.uuid4())


@pytest.mark.anyio
async def test_reader_normalizes_agent_output() -> None:
    gateway = _FakeGateway({"logs": "2026-09-20T10:00:00Z INFO started\nplain line"})
    reader = RayLogReader(gateway)

    page = await reader.read(
        _deployment(),
        source=LogSource.RUNTIME_CONTAINER,
        head_node_id=uuid.uuid4(),
        app_name="llm-port-app",
        tail=100,
    )

    assert page.source == LogSource.RUNTIME_CONTAINER
    assert [entry.message for entry in page.lines] == ["started", "plain line"]
    assert gateway.issued[0]["payload"]["tail"] == 100


@pytest.mark.anyio
async def test_reader_clamps_an_absurd_tail() -> None:
    """Defence in depth: the route bounds ``tail``, and so does the reader."""
    gateway = _FakeGateway({"logs": "x"})
    reader = RayLogReader(gateway)

    await reader.read(
        _deployment(),
        source=LogSource.RUNTIME_CONTAINER,
        head_node_id=uuid.uuid4(),
        app_name="app",
        tail=10_000_000,
    )
    assert gateway.issued[0]["payload"]["tail"] == 5000


@pytest.mark.anyio
async def test_reader_never_replays_an_older_page() -> None:
    """Each read gets a fresh idempotency key, or the agent would return a cached page."""
    gateway = _FakeGateway({"logs": "x"})
    reader = RayLogReader(gateway)
    deployment = _deployment()
    head = uuid.uuid4()

    for _ in range(2):
        await reader.read(
            deployment,
            source=LogSource.RUNTIME_CONTAINER,
            head_node_id=head,
            app_name="app",
        )

    keys = [issued["idempotency_key"] for issued in gateway.issued]
    assert keys[0] != keys[1]


@pytest.mark.anyio
async def test_reader_targets_the_requested_node() -> None:
    gateway = _FakeGateway({"logs": "x"})
    reader = RayLogReader(gateway)
    worker = uuid.uuid4()

    page = await reader.read(
        _deployment(),
        source=LogSource.RUNTIME_CONTAINER,
        head_node_id=uuid.uuid4(),
        app_name="app",
        node_id=str(worker),
    )

    assert gateway.issued[0]["node_id"] == worker
    assert page.node_id == str(worker)


@pytest.mark.anyio
async def test_unreachable_node_yields_a_reason_not_an_exception() -> None:
    """A failed log read must not fail the request; it explains itself."""
    reader = RayLogReader(_FakeGateway(None, fail=True))

    page = await reader.read(
        _deployment(),
        source=LogSource.RUNTIME_CONTAINER,
        head_node_id=uuid.uuid4(),
        app_name="app",
    )

    assert page.lines == []
    assert page.detail is not None
    assert "node offline" in page.detail


@pytest.mark.anyio
async def test_timeout_is_reported_as_a_reason() -> None:
    reader = RayLogReader(_FakeGateway(None))

    page = await reader.read(
        _deployment(),
        source=LogSource.RUNTIME_CONTAINER,
        head_node_id=uuid.uuid4(),
        app_name="app",
    )

    assert page.lines == []
    assert page.detail is not None
    # The wording says what happens next, not just that nothing came back:
    # the fetch is still running (they take ~35s here, dominated by how long
    # a queued command waits to be delivered), so the next poll shows it.
    assert "has not answered" in page.detail
    assert "next refresh" in page.detail


@pytest.mark.anyio
async def test_empty_output_is_distinguished_from_a_failure() -> None:
    reader = RayLogReader(_FakeGateway({"logs": ""}))

    page = await reader.read(
        _deployment(),
        source=LogSource.RUNTIME_CONTAINER,
        head_node_id=uuid.uuid4(),
        app_name="app",
    )

    assert page.detail == "the node returned no log output"
