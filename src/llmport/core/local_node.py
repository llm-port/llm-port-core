"""Local node-agent provisioning helpers for dev/deploy workflows."""

from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from llmport.core.console import error, info, success, warning
from llmport.core.registry import repo_clone_url

_NODE_AGENT_REPO = "llm-port-node-agent"
_NODE_AGENT_DIR = "llm_port_node_agent"


def provision_local_node_agent(
    *,
    workspace: Path,
    branch: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    remote_host: str | None,
    use_sudo: bool,
    method: str = "https",
    github_token: str = "",
    workdir_override: str | None = None,
) -> bool:
    """Clone/update and install the node agent on local or remote host."""
    repo_url = repo_clone_url(_NODE_AGENT_REPO, method=method, token=github_token)
    branch = branch.strip() or "master"
    backend_url = backend_url.strip() or "http://127.0.0.1:8000"
    advertise_host = advertise_host.strip()
    enrollment_token = enrollment_token.strip()

    if remote_host:
        return _provision_remote(
            remote_host=remote_host,
            repo_url=repo_url,
            branch=branch,
            backend_url=backend_url,
            advertise_host=advertise_host,
            enrollment_token=enrollment_token,
            use_sudo=use_sudo,
            workdir_override=workdir_override,
        )
    return _provision_local(
        workspace=workspace,
        repo_url=repo_url,
        branch=branch,
        backend_url=backend_url,
        advertise_host=advertise_host,
        enrollment_token=enrollment_token,
        use_sudo=use_sudo,
    )


def _provision_local(
    *,
    workspace: Path,
    repo_url: str,
    branch: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
) -> bool:
    repo_dir = workspace / _NODE_AGENT_DIR
    info(f"Provisioning local node agent in {repo_dir}")
    if not _clone_or_update_repo(repo_dir=repo_dir, repo_url=repo_url, branch=branch):
        return False

    if not _install_agent_dependencies(repo_dir):
        return False

    if platform.system() != "Linux":
        warning("Systemd install is skipped on non-Linux hosts.")
        return True

    if not use_sudo:
        warning("Skipped systemd install (--local-node-no-sudo).")
        warning("Manual run: llm-port-node-agent")
        return True

    service_file = repo_dir / "deploy" / "systemd" / "llm-port-node-agent.service"
    if not service_file.exists():
        error(f"Systemd service file missing: {service_file}")
        return False

    env_lines = [f"LLM_PORT_NODE_AGENT_BACKEND_URL={backend_url}"]
    if advertise_host:
        env_lines.append(f"LLM_PORT_NODE_AGENT_ADVERTISE_HOST={advertise_host}")
    if enrollment_token:
        env_lines.append(f"LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN={enrollment_token}")
    else:
        warning("No enrollment token provided. Agent service will require later onboarding token.")

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(env_lines))
        handle.write("\n")
        temp_env = Path(handle.name)

    try:
        if _run(["sudo", "install", "-m", "0644", str(service_file), "/etc/systemd/system/llm-port-node-agent.service"]) != 0:
            return False
        if _run(["sudo", "install", "-m", "0644", str(temp_env), "/etc/llm-port-node-agent.env"]) != 0:
            return False
        if _run(["sudo", "systemctl", "daemon-reload"]) != 0:
            return False
        if _run(["sudo", "systemctl", "enable", "--now", "llm-port-node-agent"]) != 0:
            return False
    finally:
        try:
            temp_env.unlink(missing_ok=True)
        except Exception:
            pass

    success("Local node agent installed and started via systemd.")
    return True


def _provision_remote(
    *,
    remote_host: str,
    repo_url: str,
    branch: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
    workdir_override: str | None,
) -> bool:
    ssh = shutil.which("ssh")
    if not ssh:
        error("ssh is not installed or not on PATH.")
        return False

    workdir = (workdir_override or "").strip() or "/opt/llm_port_node_agent"
    script = _build_remote_script(
        repo_url=repo_url,
        branch=branch,
        workdir=workdir,
        backend_url=backend_url,
        advertise_host=advertise_host,
        enrollment_token=enrollment_token,
        use_sudo=use_sudo,
    )
    info(f"Provisioning node agent on remote host '{remote_host}'")
    proc = subprocess.run(  # noqa: S603
        [ssh, remote_host, "bash -s"],
        input=script,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        error("Remote node-agent provisioning failed.")
        if detail:
            error(detail)
        if use_sudo:
            warning(
                "Remote install used sudo. Ensure remote user has passwordless sudo for "
                "install/systemctl, or rerun with --local-node-no-sudo and a writable --local-node-workdir."
            )
        return False

    if proc.stdout.strip():
        info(proc.stdout.strip())
    success(f"Remote node agent provisioned on {remote_host}.")
    return True


def _build_remote_script(
    *,
    repo_url: str,
    branch: str,
    workdir: str,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
) -> str:
    q = shlex.quote
    use_sudo_int = "1" if use_sudo else "0"
    return f"""set -euo pipefail
REPO_URL={q(repo_url)}
BRANCH={q(branch)}
WORKDIR={q(workdir)}
BACKEND_URL={q(backend_url)}
ADVERTISE_HOST={q(advertise_host)}
ENROLLMENT_TOKEN={q(enrollment_token)}
USE_SUDO={use_sudo_int}

mkdir -p "$(dirname "$WORKDIR")"
if [ -d "$WORKDIR/.git" ]; then
  git -C "$WORKDIR" fetch --all --prune
else
  git clone "$REPO_URL" "$WORKDIR"
fi

if [ -n "$BRANCH" ]; then
  git -C "$WORKDIR" checkout "$BRANCH" || git -C "$WORKDIR" checkout -b "$BRANCH" "origin/$BRANCH" || true
  git -C "$WORKDIR" pull --ff-only origin "$BRANCH" || true
fi

if command -v uv >/dev/null 2>&1; then
  (cd "$WORKDIR" && uv sync)
else
  python3 -m pip install --user -e "$WORKDIR"
fi

if [ "$USE_SUDO" != "1" ]; then
  echo "Skipping systemd install (--local-node-no-sudo)."
  exit 0
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not available on remote host; skipping service install."
  exit 0
fi

SUDO_BIN="sudo"
if [ "$(id -u)" -eq 0 ]; then
  SUDO_BIN=""
fi

if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN -n true >/dev/null 2>&1 || {{
    echo "sudo permission check failed (passwordless sudo required for non-interactive install)."
    exit 2
  }}
fi

if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN install -m 0644 "$WORKDIR/deploy/systemd/llm-port-node-agent.service" /etc/systemd/system/llm-port-node-agent.service
else
  install -m 0644 "$WORKDIR/deploy/systemd/llm-port-node-agent.service" /etc/systemd/system/llm-port-node-agent.service
fi

TMP_ENV="$(mktemp)"
{{
  printf "LLM_PORT_NODE_AGENT_BACKEND_URL=%s\\n" "$BACKEND_URL"
  if [ -n "$ADVERTISE_HOST" ]; then
    printf "LLM_PORT_NODE_AGENT_ADVERTISE_HOST=%s\\n" "$ADVERTISE_HOST"
  fi
  if [ -n "$ENROLLMENT_TOKEN" ]; then
    printf "LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN=%s\\n" "$ENROLLMENT_TOKEN"
  fi
}} >"$TMP_ENV"

if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN install -m 0644 "$TMP_ENV" /etc/llm-port-node-agent.env
  $SUDO_BIN systemctl daemon-reload
  $SUDO_BIN systemctl enable --now llm-port-node-agent
else
  install -m 0644 "$TMP_ENV" /etc/llm-port-node-agent.env
  systemctl daemon-reload
  systemctl enable --now llm-port-node-agent
fi
rm -f "$TMP_ENV"

echo "Node agent service installed and started."
"""


def _clone_or_update_repo(*, repo_dir: Path, repo_url: str, branch: str) -> bool:
    git = shutil.which("git")
    if not git:
        error("git is not installed or not on PATH.")
        return False

    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if (repo_dir / ".git").exists():
        if _run([git, "-C", str(repo_dir), "fetch", "--all", "--prune"]) != 0:
            return False
    else:
        if _run([git, "clone", repo_url, str(repo_dir)]) != 0:
            return False

    if _run([git, "-C", str(repo_dir), "checkout", branch], check=False) != 0:
        _run([git, "-C", str(repo_dir), "checkout", "-b", branch, f"origin/{branch}"], check=False)
    _run([git, "-C", str(repo_dir), "pull", "--ff-only", "origin", branch], check=False)
    return True


def _install_agent_dependencies(repo_dir: Path) -> bool:
    uv = shutil.which("uv")
    if uv:
        if _run([uv, "sync"], cwd=repo_dir) == 0:
            success("Node agent dependencies installed via uv.")
            return True
        return False

    python = shutil.which("python3") or shutil.which("python")
    if not python:
        error("Neither uv nor python is available for node agent install.")
        return False
    if _run([python, "-m", "pip", "install", "-e", "."], cwd=repo_dir) != 0:
        return False
    success("Node agent installed via pip editable mode.")
    return True


def _run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        error(f"Command failed: {' '.join(cmd)}")
        if detail:
            error(detail)
    return proc.returncode
