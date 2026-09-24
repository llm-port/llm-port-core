"""Nightly backups: one crontab line, owned and replaced by `llmport backup schedule`.

There were backups with retention, but nothing ran them: an install was
only as safe as the operator's memory.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from llmport.commands.backup import backup_cmd
from llmport.core import schedule


@pytest.fixture()
def crontab(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines = ["MAILTO=ops@example.com", "15 * * * * /usr/bin/something-else"]
    monkeypatch.setattr(schedule, "available", lambda: True)
    monkeypatch.setattr(schedule, "_read", lambda: list(lines))

    def write(new: list[str]) -> None:
        lines[:] = new

    monkeypatch.setattr(schedule, "_write", write)
    monkeypatch.setattr(schedule.shutil, "which", lambda name: "/home/ops/.local/bin/llmport")
    return lines


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    install = tmp_path / "llm-port"
    install.mkdir()
    config = tmp_path / "llmport.yaml"
    config.write_text(f"version: 1\ninstall_dir: {install.as_posix()}\n", encoding="utf-8")
    monkeypatch.setenv("LLMPORT_CONFIG", str(config))
    return install


def test_scheduling_adds_one_line_and_keeps_the_rest(
    crontab: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = _configure(tmp_path, monkeypatch)
    result = CliRunner().invoke(backup_cmd, ["schedule", "--at", "02:30", "--retain", "10"])
    assert result.exit_code == 0, result.output

    assert crontab[:2] == ["MAILTO=ops@example.com", "15 * * * * /usr/bin/something-else"]
    line = crontab[2]
    assert line.startswith("30 2 * * * PATH=")
    assert "backup -y --retain 10" in line
    assert "LLMPORT_CONFIG=" in line, "cron must find the same install"
    assert str(install / "backups" / "backup.log").replace("\\", "/") in line.replace("\\", "/")
    assert line.endswith(schedule.MARK)

    # Scheduling again replaces it; --off removes only it.
    CliRunner().invoke(backup_cmd, ["schedule", "--at", "04:00"])
    assert len([x for x in crontab if x.endswith(schedule.MARK)]) == 1
    assert crontab[-1].startswith("0 4 * * * ")
    CliRunner().invoke(backup_cmd, ["schedule", "--off"])
    assert crontab == ["MAILTO=ops@example.com", "15 * * * * /usr/bin/something-else"]


@pytest.mark.parametrize("bad", ["25:00", "3pm", "03:61"])
def test_a_bad_time_is_refused(crontab: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    _configure(tmp_path, monkeypatch)
    result = CliRunner().invoke(backup_cmd, ["schedule", "--at", bad])
    assert result.exit_code == 2
    assert not any(x.endswith(schedule.MARK) for x in crontab)


def test_backup_without_a_subcommand_still_backs_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from llmport.commands import backup as backup_module

    _configure(tmp_path, monkeypatch)
    ran: list[Path] = []

    class _Result:
        ok = True
        errors: list[str] = []  # noqa: RUF012
        backup_dir = tmp_path / "b"
        databases = ["llm_port_backend"]
        env_snapshot = None
        volumes: list[str] = []  # noqa: RUF012
        manifest_path = None

    monkeypatch.setattr(backup_module, "build_context_from_config", lambda cfg: None)
    monkeypatch.setattr(backup_module, "create_backup", lambda ctx, **k: ran.append(k["output_dir"]) or _Result())
    result = CliRunner().invoke(backup_cmd, ["-y", "--retain", "3"])
    assert result.exit_code == 0, result.output
    assert len(ran) == 1
