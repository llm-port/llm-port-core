"""CLI entrypoint for llm_port_node_agent."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.service import NodeAgentService

SERVICE_NAME = "llmport-agent"


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


async def _run() -> None:
    config = AgentConfig.from_env()
    _configure_logging(config.log_level)
    service = NodeAgentService(config)
    try:
        await service.run_forever()
    finally:
        await service.close()


# ── Helpers ───────────────────────────────────────────────────────


def _require_linux() -> None:
    if platform.system() != "Linux":
        print("ERROR: This subcommand requires Linux with systemd.", file=sys.stderr)
        sys.exit(1)
    if not shutil.which("systemctl"):
        print("ERROR: systemctl not found — systemd is required.", file=sys.stderr)
        sys.exit(1)


def _sudo_prefix() -> list[str]:
    return ["sudo"] if os.getuid() != 0 else []


def _run_cmd(cmd: list[str], *, check: bool = True) -> int:
    result = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    if result.returncode != 0 and check:
        print(f"ERROR: {' '.join(cmd)}", file=sys.stderr)
        if result.stderr:
            print(result.stderr.strip(), file=sys.stderr)
    return result.returncode


def _collect_env_lines() -> list[str]:
    """Collect current LLM_PORT_NODE_AGENT_* env vars as KEY=VALUE lines."""
    return [
        f"{k}={v}"
        for k, v in sorted(os.environ.items())
        if k.startswith("LLM_PORT_NODE_AGENT_")
    ]


def _find_service_template() -> Path | None:
    """Locate the bundled systemd service template."""
    candidates = [
        Path(__file__).resolve().parent.parent / "deploy" / "systemd" / f"{SERVICE_NAME}.service",
        Path(sys.prefix) / "share" / SERVICE_NAME / f"{SERVICE_NAME}.service",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _build_service_content(agent_bin: str) -> str:
    """Return systemd unit content, using template if available."""
    template = _find_service_template()
    if template:
        content = template.read_text(encoding="utf-8")
        return re.sub(
            r"^ExecStart=.*$",
            f"ExecStart={agent_bin}",
            content,
            flags=re.MULTILINE,
        )
    return (
        "[Unit]\n"
        "Description=llm-port node agent\n"
        "After=network-online.target docker.service\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"EnvironmentFile=-/etc/{SERVICE_NAME}.env\n"
        f"ExecStart={agent_bin}\n"
        "Restart=always\n"
        "RestartSec=5\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


# ── Subcommands ───────────────────────────────────────────────────


def cmd_start() -> None:
    """Install and start llmport-agent as a systemd service."""
    _require_linux()
    sudo = _sudo_prefix()

    env_lines = _collect_env_lines()
    if not any(line.startswith("LLM_PORT_NODE_AGENT_BACKEND_URL=") for line in env_lines):
        print("ERROR: LLM_PORT_NODE_AGENT_BACKEND_URL must be set.", file=sys.stderr)
        sys.exit(1)

    agent_bin = shutil.which("llmport-agent")
    if not agent_bin:
        print("ERROR: llmport-agent not found on PATH.", file=sys.stderr)
        sys.exit(1)

    service_content = _build_service_content(agent_bin)
    env_content = "\n".join(env_lines) + "\n"

    tmp_svc = tmp_env = None
    try:
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".service") as sf:
            sf.write(service_content)
            tmp_svc = sf.name
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".env") as ef:
            ef.write(env_content)
            tmp_env = ef.name

        if _run_cmd([*sudo, "install", "-m", "0644", tmp_svc, f"/etc/systemd/system/{SERVICE_NAME}.service"]) != 0:
            sys.exit(1)
        if _run_cmd([*sudo, "install", "-m", "0600", tmp_env, f"/etc/{SERVICE_NAME}.env"]) != 0:
            sys.exit(1)
        _run_cmd([*sudo, "mkdir", "-p", "/var/lib/llmport-agent"], check=False)
        if _run_cmd([*sudo, "systemctl", "daemon-reload"]) != 0:
            sys.exit(1)
        if _run_cmd([*sudo, "systemctl", "enable", "--now", SERVICE_NAME]) != 0:
            sys.exit(1)
    finally:
        if tmp_svc:
            os.unlink(tmp_svc)
        if tmp_env:
            os.unlink(tmp_env)

    print(f"{SERVICE_NAME} service installed and started.")
    print(f"  View logs:  journalctl -u {SERVICE_NAME} -f")
    print(f"  Stop:       llmport-agent stop")


def cmd_stop() -> None:
    """Stop and disable the llmport-agent systemd service."""
    _require_linux()
    sudo = _sudo_prefix()
    _run_cmd([*sudo, "systemctl", "disable", "--now", SERVICE_NAME], check=False)
    print(f"{SERVICE_NAME} service stopped and disabled.")


def cmd_status() -> None:
    """Show systemd service status."""
    _require_linux()
    # exec into systemctl so output goes straight to terminal
    os.execlp("systemctl", "systemctl", "status", SERVICE_NAME)


# ── Main ──────────────────────────────────────────────────────────


def main() -> None:
    """Process entrypoint."""
    parser = argparse.ArgumentParser(
        prog="llmport-agent",
        description="llm-port node agent — host-side execution bridge.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="Install and start as a systemd service")
    sub.add_parser("stop", help="Stop and disable the systemd service")
    sub.add_parser("status", help="Show systemd service status")

    args = parser.parse_args()

    if args.command is None:
        asyncio.run(_run())
    elif args.command == "start":
        cmd_start()
    elif args.command == "stop":
        cmd_stop()
    elif args.command == "status":
        cmd_status()


if __name__ == "__main__":
    main()
