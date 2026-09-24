"""Nightly backups, scheduled for the user who runs LLM.Port -- no root.

Two ways, whichever the machine has:

* a **systemd user timer** (``llmport-backup.timer``), preferred: every
  systemd distribution has one, and ``Persistent=true`` runs a backup missed
  while the server was off. It runs without anyone logged in only with
  *lingering* on, which is switched on for the user (as the node agent's
  installer does).
* a **crontab line** marked with :data:`MARK`, where there is cron but no
  usable systemd user manager.

Ubuntu 26.04 server ships no cron, which is why cron is not the only way.
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
#: The systemd user units this module owns.
UNIT = "llmport-backup"

#: Scheduled jobs run with a bare PATH; docker is usually in one of these.
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)  # noqa: S603


# ── which way ─────────────────────────────────────────────────────


def _systemd_user() -> bool:
    if sys.platform != "linux" or shutil.which("systemctl") is None:
        return False
    state = _run(["systemctl", "--user", "is-system-running"]).stdout.strip()
    # "degraded" still runs timers; "offline"/empty means no user manager.
    return state in {"running", "degraded", "starting", "initializing"}


def _cron() -> bool:
    return sys.platform != "win32" and shutil.which("crontab") is not None


def available() -> bool:
    """Whether backups can be scheduled on this machine at all."""
    return _systemd_user() or _cron()


# ── what runs ─────────────────────────────────────────────────────


def _cli() -> list[str]:
    """How a scheduled job should run this CLI: by absolute path, not a PATH lookup."""
    found = shutil.which("llmport")
    if found:
        return [str(Path(found).resolve())]
    return [sys.executable, "-m", "llmport"]


def _config() -> Path | None:
    value = os.environ.get("LLMPORT_CONFIG")
    return Path(value).expanduser().resolve() if value else None


# ── cron ──────────────────────────────────────────────────────────


def _read() -> list[str]:
    out = _run(["crontab", "-l"])
    # "no crontab for <user>" is exit 1 with nothing to keep.
    return out.stdout.splitlines() if out.returncode == 0 else []


def _write(lines: list[str]) -> None:
    text = "\n".join(lines).rstrip("\n") + "\n" if lines else ""
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)  # noqa: S607


def entry(*, hour: int, minute: int, retain: int, log: Path, config: Path | None) -> str:
    """The crontab line for a backup every day at *hour*:*minute*."""
    env = f"PATH={_PATH}"
    if config is not None:
        env += f" LLMPORT_CONFIG={shlex.quote(str(config))}"
    command = " ".join(shlex.quote(part) for part in [*_cli(), "backup", "-y", "--retain", str(retain)])
    return f"{minute} {hour} * * * {env} {command} >> {shlex.quote(str(log))} 2>&1 {MARK}"


# ── systemd ───────────────────────────────────────────────────────


def _unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user"


def units(*, hour: int, minute: int, retain: int, log: Path, config: Path | None) -> dict[str, str]:
    """The service and timer unit files for a backup every day at *hour*:*minute*."""
    env = [f"Environment=PATH={_PATH}"]
    if config is not None:
        env.append(f'Environment="LLMPORT_CONFIG={config}"')
    command = " ".join(shlex.quote(part) for part in [*_cli(), "backup", "-y", "--retain", str(retain)])
    service = "\n".join([
        "[Unit]",
        "Description=LLM.Port nightly backup",
        "",
        "[Service]",
        "Type=oneshot",
        *env,
        f"ExecStart={command}",
        f"StandardOutput=append:{log}",
        f"StandardError=append:{log}",
        "",
    ])
    timer = "\n".join([
        "[Unit]",
        "Description=LLM.Port nightly backup",
        "",
        "[Timer]",
        f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00",
        # A night the server was off is backed up when it next starts.
        "Persistent=true",
        "",
        "[Install]",
        "WantedBy=timers.target",
        "",
    ])
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def _linger() -> bool:
    """Lingering on for this user (so the timer runs with nobody logged in); whether it is."""
    user = os.environ.get("USER") or ""
    shown = _run(["loginctl", "show-user", user, "-p", "Linger"]).stdout.strip()
    if shown == "Linger=yes":
        return True
    _run(["loginctl", "enable-linger", user])
    return _run(["loginctl", "show-user", user, "-p", "Linger"]).stdout.strip() == "Linger=yes"


# ── public ────────────────────────────────────────────────────────


def current() -> str | None:
    """How the nightly backup is scheduled, if it is: the timer's time or the crontab line."""
    if _systemd_user() and _run(["systemctl", "--user", "is-enabled", f"{UNIT}.timer"]).stdout.strip() == "enabled":
        timer = _unit_dir() / f"{UNIT}.timer"
        calendar = next((line for line in timer.read_text().splitlines() if line.startswith("OnCalendar=")), "")
        return f"systemd timer {UNIT}.timer ({calendar.removeprefix('OnCalendar=')})"
    if _cron():
        return next((line for line in _read() if line.endswith(MARK)), None)
    return None


def install(*, hour: int, minute: int, retain: int, log: Path) -> tuple[str, list[str]]:
    """Schedule the backup, replacing an earlier schedule.

    Returns how it is scheduled, and warnings for the operator.
    """
    config = _config()
    warnings: list[str] = []
    remove()
    if _systemd_user():
        directory = _unit_dir()
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in units(hour=hour, minute=minute, retain=retain, log=log, config=config).items():
            (directory / name).write_text(text, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        enabled = _run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"])
        if enabled.returncode != 0:
            msg = f"systemctl --user enable {UNIT}.timer failed: {enabled.stderr.strip()}"
            raise RuntimeError(msg)
        if not _linger():
            warnings.append(
                "Lingering is off for this user, so the backup only runs while someone is logged in. "
                f"Turn it on once with: sudo loginctl enable-linger {os.environ.get('USER', '<user>')}"
            )
        return f"systemd timer {UNIT}.timer", warnings
    if _cron():
        line = entry(hour=hour, minute=minute, retain=retain, log=log, config=config)
        _write([*_read(), line])
        return "crontab", warnings
    msg = "Neither a systemd user manager nor cron is available."
    raise RuntimeError(msg)


def remove() -> bool:
    """Remove the scheduled backup, whichever way it was scheduled; whether there was one."""
    removed = False
    if _systemd_user():
        timer = _unit_dir() / f"{UNIT}.timer"
        if timer.exists():
            _run(["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"])
            for name in (f"{UNIT}.timer", f"{UNIT}.service"):
                (_unit_dir() / name).unlink(missing_ok=True)
            _run(["systemctl", "--user", "daemon-reload"])
            removed = True
    if _cron():
        lines = _read()
        kept = [line for line in lines if not line.endswith(MARK)]
        if len(kept) != len(lines):
            _write(kept)
            removed = True
    return removed
