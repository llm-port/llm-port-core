"""Nightly backups, scheduled with the user's crontab.

cron rather than a systemd timer: it needs no root, and it runs whether or
not anybody is logged in (a systemd *user* timer only does with lingering
switched on). The entry is one line, marked with :data:`MARK`, so it can be
replaced or removed without touching the rest of the crontab.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

#: Marks the crontab line this module owns.
MARK = "# llmport-backup"

#: cron runs with a bare PATH; docker is usually in one of these.
_CRON_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def available() -> bool:
    """Whether this machine has a crontab to schedule in."""
    return sys.platform != "win32" and shutil.which("crontab") is not None


def _read() -> list[str]:
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)  # noqa: S607
    # "no crontab for <user>" is exit 1 with nothing to keep.
    return out.stdout.splitlines() if out.returncode == 0 else []


def _write(lines: list[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n" if lines else ""
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)  # noqa: S607


def current() -> str | None:
    """The scheduled backup line, if there is one."""
    return next((line for line in _read() if line.endswith(MARK)), None)


def _cli() -> str:
    """How cron should run this CLI: its absolute path, not a PATH lookup."""
    found = shutil.which("llmport")
    if found:
        return str(Path(found).resolve())
    return f"{shlex.quote(sys.executable)} -m llmport"


def entry(*, hour: int, minute: int, retain: int, log: Path, config: Path | None) -> str:
    """The crontab line for a backup every day at *hour*:*minute*."""
    env = f"PATH={_CRON_PATH}"
    if config is not None:
        env += f" LLMPORT_CONFIG={shlex.quote(str(config))}"
    command = f"{_cli()} backup -y --retain {retain} >> {shlex.quote(str(log))} 2>&1"
    return f"{minute} {hour} * * * {env} {command} {MARK}"


def install(*, hour: int, minute: int, retain: int, log: Path) -> str:
    """Schedule the backup, replacing an earlier schedule; return the line."""
    config = os.environ.get("LLMPORT_CONFIG")
    line = entry(hour=hour, minute=minute, retain=retain, log=log,
                 config=Path(config).expanduser().resolve() if config else None)
    _write([existing for existing in _read() if not existing.endswith(MARK)] + [line])
    return line


def remove() -> bool:
    """Remove the scheduled backup; whether there was one."""
    lines = _read()
    kept = [line for line in lines if not line.endswith(MARK)]
    if len(kept) == len(lines):
        return False
    _write(kept)
    return True
