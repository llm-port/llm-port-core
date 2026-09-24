"""What an upgrade checks first, and what it clears after.

Met upgrading a 13 Sep install on a test VM: a Prometheus recreated by hand
with ``docker run`` stopped the restart after a quarter of an hour of
builds; and ClickHouse's own trace log had grown to 41 GB and filled the
disk.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from llmport.core import clickhouse, compose
from llmport.core.compose import ComposeContext


def _ctx() -> ComposeContext:
    return ComposeContext(compose_files=[Path("docker-compose.yaml")])


def _answers(monkeypatch: pytest.MonkeyPatch, answer: Any) -> list[list[str]]:
    ran: list[list[str]] = []

    def run(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        ran.append(cmd)
        code, out = answer(cmd)
        return subprocess.CompletedProcess(cmd, code, out, "")

    monkeypatch.setattr(compose, "_run", run)
    monkeypatch.setattr(clickhouse, "_run", run)
    monkeypatch.setattr(compose.shutil, "which", lambda name: name)
    return ran


def test_a_container_made_outside_this_project_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"name": "llm_port_shared", "services": {
        "prometheus": {"container_name": "llm-port-prometheus"},
        "postgres": {"container_name": "llm-port-postgres"},
        "backend": {},
    }}

    def answer(cmd: list[str]) -> tuple[int, str]:
        if "config" in cmd:
            return 0, json.dumps(config)
        return 0, (
            "llm-port-prometheus\t\n"            # docker run
            "llm-port-postgres\tllm_port_shared\n"  # ours
            "some-other-app\t\n"                  # not a name we use
        )

    _answers(monkeypatch, answer)
    assert compose.foreign_containers(_ctx()) == [("llm-port-prometheus", "")]


def test_nothing_is_reported_when_compose_cannot_be_read(monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, lambda cmd: (1, ""))
    assert compose.foreign_containers(_ctx()) == []


def test_the_logs_turned_off_and_their_renamed_copies_are_stale() -> None:
    names = ["trace_log", "trace_log_0", "text_log", "metric_log", "query_log", "query_log_1",
             "part_log", "error_log", "tables", "latency_log"]
    assert clickhouse.stale_log_tables(names) == [
        "trace_log", "trace_log_0", "text_log", "metric_log", "query_log_1", "latency_log",
    ]


def test_stale_logs_are_dropped_as_the_containers_user(monkeypatch: pytest.MonkeyPatch) -> None:
    def answer(cmd: list[str]) -> tuple[int, str]:
        return (0, "trace_log\nquery_log\nquery_log_0\n") if "SELECT" in cmd[-1] else (0, "")

    ran = _answers(monkeypatch, answer)
    assert clickhouse.drop_stale_logs(_ctx()) == ["trace_log", "query_log_0"]
    drops = [cmd[-1] for cmd in ran if cmd[-1].startswith("DROP")]
    assert drops == ["DROP TABLE IF EXISTS system.`trace_log` SYNC", "DROP TABLE IF EXISTS system.`query_log_0` SYNC"]
    assert all("-T" in cmd and '"$CLICKHOUSE_USER"' in cmd[-3] for cmd in ran), "no TTY; the container's credentials"


def test_clickhouse_that_cannot_be_asked_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _answers(monkeypatch, lambda cmd: (1, "service clickhouse is not running"))
    with pytest.raises(RuntimeError, match="not running"):
        clickhouse.drop_stale_logs(_ctx())


def test_the_shipped_clickhouse_config_turns_off_what_the_cli_drops() -> None:
    config = Path(__file__).parents[2] / "llm_port_shared" / "clickhouse" / "config.d" / "system-logs.xml"
    text = config.read_text(encoding="utf-8")
    for name in clickhouse.DISABLED_LOGS:
        assert f'<{name} remove="1"/>' in text
