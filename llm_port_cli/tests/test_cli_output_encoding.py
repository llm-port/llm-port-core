"""The CLI must be able to print its own output.

Windows consoles default to cp1252. This CLI writes arrows, box rules and
check marks in help text and progress output -- thousands of them -- and any
one reaching a cp1252 stream raises UnicodeEncodeError. `llmport dev up
--help` ended in a traceback where the help should have been, and redirecting
any command to a file did the same.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from llmport import cli as cli_module

_CLI_ROOT = Path(__file__).resolve().parents[1]


class _Cp1252Stream(io.TextIOWrapper):
    """A stream that behaves the way a Windows console does by default."""

    def __init__(self) -> None:
        super().__init__(io.BytesIO(), encoding="cp1252", errors="strict")
        self.reconfigured: dict[str, object] = {}

    def reconfigure(self, **kwargs: object) -> None:  # type: ignore[override]
        self.reconfigured = kwargs


def test_switches_stdout_and_stderr_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = _Cp1252Stream(), _Cp1252Stream()
    monkeypatch.setattr(cli_module.sys, "stdout", out)
    monkeypatch.setattr(cli_module.sys, "stderr", err)

    cli_module._use_utf8_output()

    for stream in (out, err):
        assert stream.reconfigured == {"encoding": "utf-8", "errors": "replace"}


def test_replaces_rather_than_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal that truly cannot show a glyph gets a degraded one.

    Never a stack trace where the output belongs.
    """
    out, err = _Cp1252Stream(), _Cp1252Stream()
    monkeypatch.setattr(cli_module.sys, "stdout", out)
    monkeypatch.setattr(cli_module.sys, "stderr", err)

    cli_module._use_utf8_output()

    assert out.reconfigured["errors"] == "replace"


def test_survives_a_stream_that_cannot_be_reconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pytest capture and some pipe wrappers have no reconfigure()."""
    monkeypatch.setattr(cli_module.sys, "stdout", io.StringIO())
    monkeypatch.setattr(cli_module.sys, "stderr", io.StringIO())

    cli_module._use_utf8_output()  # must not raise


@pytest.mark.parametrize("command", [["dev", "up"], ["dev"], []])
def test_help_prints_on_a_cp1252_console(command: list[str]) -> None:
    """The end-to-end case: help through a pipe, with cp1252 forced.

    PYTHONIOENCODING is how a subprocess is told what its console is; setting
    it to cp1252 reproduces a stock Windows terminal on any platform.
    """
    result = subprocess.run(
        [sys.executable, "-m", "llmport.cli", *command, "--help"],
        capture_output=True,
        text=True,
        cwd=_CLI_ROOT / "src",
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        check=False,
    )

    assert "UnicodeEncodeError" not in result.stderr
    assert result.returncode == 0
    assert "Usage:" in result.stdout
