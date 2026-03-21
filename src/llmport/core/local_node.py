"""Local node-agent provisioning helpers for dev/deploy workflows."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from llmport.core.console import error, info, success, warning
from llmport.core.registry import repo_clone_url

_NODE_AGENT_REPO = "llm-port-node-agent"
_NODE_AGENT_DIR = "llm_port_node_agent"


def create_enrollment_token(
    backend_url: str,
    api_token: str,
    *,
    note: str = "Auto-enrolled local node",
) -> str | None:
    """Create an enrollment token via the backend API.

    Returns the plaintext token string, or None on failure.
    """
    url = f"{backend_url.rstrip('/')}/api/admin/system/nodes/enrollment-tokens"
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(url, json={"note": note}, headers=headers, timeout=15)
        if resp.status_code in (200, 201):
            token = resp.json().get("token", "")
            if token:
                success("Enrollment token created for local node.")
                return token
            error("Enrollment token response missing 'token' field.")
            return None
        error(f"Failed to create enrollment token (HTTP {resp.status_code}): {resp.text}")
        return None
    except httpx.HTTPError as exc:
        error(f"Could not reach backend for enrollment token: {exc}")
        return None


def remove_local_node_agent(*, workspace: Path, use_sudo: bool = True) -> bool:
    """Stop the local node agent (systemd service and/or running process).

    The cloned source directory is intentionally left in place so that
    unpushed work is never destroyed by ``llmport down --all``.

    Returns True if cleanup succeeded (or nothing to clean up).
    """
    removed_anything = False

    # ── Stop and remove systemd service (Linux only) ──────────
    if platform.system() == "Linux" and use_sudo:
        systemctl = shutil.which("systemctl")
        if systemctl:
            # Check if the service unit exists before trying to stop it
            probe = subprocess.run(  # noqa: S603
                ["systemctl", "list-unit-files", "llmport-agent.service"],
                capture_output=True, text=True,
            )
            if "llmport-agent.service" in (probe.stdout or ""):
                info("Stopping llmport-agent systemd service…")
                _run(["sudo", "systemctl", "disable", "--now", "llmport-agent"], check=False)
                _run(["sudo", "rm", "-f", "/etc/systemd/system/llmport-agent.service"], check=False)
                _run(["sudo", "rm", "-f", "/etc/llmport-agent.env"], check=False)
                _run(["sudo", "systemctl", "daemon-reload"], check=False)
                removed_anything = True

    # ── Kill any running agent process (cross-platform) ───────
    if platform.system() == "Windows":
        # Best-effort: kill by process name
        kill_result = subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/IM", "llmport-agent.exe"],
            capture_output=True, text=True,
        )
        if kill_result.returncode == 0:
            removed_anything = True
    else:
        kill_result = subprocess.run(  # noqa: S603
            ["pkill", "-f", "llm.port.node.agent"],
            capture_output=True, text=True,
        )
        if kill_result.returncode == 0:
            removed_anything = True

    if removed_anything:
        success("Local node agent stopped.")
    else:
        info("No local node agent service found to remove.")
    return True


def _derive_loki_url(backend_url: str) -> str:
    """Derive Loki push URL from the backend URL (same host, port 3100)."""
    parsed = urlparse(backend_url)
    host = parsed.hostname or "127.0.0.1"
    scheme = parsed.scheme or "http"
    return f"{scheme}://{host}:3100"


def _build_env_lines(
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
) -> list[str]:
    """Build the common env lines for all provisioning paths."""
    loki_url = _derive_loki_url(backend_url)
    lines = [
        f"LLM_PORT_NODE_AGENT_BACKEND_URL={backend_url}",
        f"LLM_PORT_NODE_AGENT_LOKI_URL={loki_url}",
    ]
    if advertise_host:
        lines.append(f"LLM_PORT_NODE_AGENT_ADVERTISE_HOST={advertise_host}")
    if enrollment_token:
        lines.append(f"LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN={enrollment_token}")
    else:
        warning("No enrollment token provided. Agent will require later onboarding.")
    return lines


def _find_agent_binary(workspace: Path) -> Path | None:
    """Locate a pre-built node agent binary in the workspace."""
    candidates = [
        workspace / _NODE_AGENT_DIR / "dist" / "llmport-agent",
        workspace / _NODE_AGENT_DIR / "dist" / "llmport-agent.exe",
        workspace / "dist" / "llmport-agent",
        workspace / "dist" / "llmport-agent.exe",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


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

    # Try binary-first: if a pre-built executable exists, skip clone/venv.
    binary = _find_agent_binary(workspace)
    if binary:
        return _provision_local_binary(
            binary=binary,
            workspace=workspace,
            backend_url=backend_url,
            advertise_host=advertise_host,
            enrollment_token=enrollment_token,
            use_sudo=use_sudo,
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

    env_lines = _build_env_lines(backend_url, advertise_host, enrollment_token)

    if platform.system() != "Linux":
        return _start_agent_foreground(repo_dir, env_lines)

    if not use_sudo:
        info("No sudo — starting node agent as user background process.")
        return _start_agent_foreground(repo_dir, env_lines)

    service_file = repo_dir / "deploy" / "systemd" / "llmport-agent.service"
    if not service_file.exists():
        error(f"Systemd service file missing: {service_file}")
        return False

    # Pre-check: verify sudo access before attempting systemd install.
    if _run(["sudo", "-n", "true"], check=False) != 0:
        warning("sudo not available — falling back to user background process.")
        return _start_agent_foreground(repo_dir, env_lines)

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write("\n".join(env_lines))
        handle.write("\n")
        temp_env = Path(handle.name)

    try:
        if _run(["sudo", "install", "-m", "0644", str(service_file), "/etc/systemd/system/llmport-agent.service"]) != 0:
            return False
        if _run(["sudo", "install", "-m", "0644", str(temp_env), "/etc/llmport-agent.env"]) != 0:
            return False
        if _run(["sudo", "systemctl", "daemon-reload"]) != 0:
            return False
        if _run(["sudo", "systemctl", "enable", "--now", "llmport-agent"]) != 0:
            return False
    finally:
        try:
            temp_env.unlink(missing_ok=True)
        except Exception:
            pass

    success("Local node agent installed and started via systemd.")
    return True


def _provision_local_binary(
    *,
    binary: Path,
    workspace: Path,
    backend_url: str,
    advertise_host: str,
    enrollment_token: str,
    use_sudo: bool,
) -> bool:
    """Install a pre-built node agent binary (no Python/venv required)."""
    info(f"Found pre-built binary: {binary}")
    env_lines = _build_env_lines(backend_url, advertise_host, enrollment_token)

    is_linux = platform.system() == "Linux"
    install_path = Path("/usr/local/bin/llmport-agent") if is_linux else binary

    if is_linux and use_sudo:
        # Install binary to /usr/local/bin
        if _run(["sudo", "-n", "true"], check=False) != 0:
            warning("sudo not available — running binary in foreground.")
            return _start_binary_foreground(binary, env_lines)

        if _run(["sudo", "install", "-m", "0755", str(binary), str(install_path)]) != 0:
            return False
        info(f"Installed binary to {install_path}")

        # Write env file
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("\n".join(env_lines) + "\n")
            temp_env = Path(handle.name)

        # Install systemd service (use the template from workspace if available)
        service_src = workspace / _NODE_AGENT_DIR / "deploy" / "systemd" / "llmport-agent.service"
        if not service_src.exists():
            # Write a minimal service file inline
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".service") as sf:
                sf.write(
                    "[Unit]\n"
                    "Description=llm-port node agent\n"
                    "After=network-online.target docker.service\n"
                    "Wants=network-online.target\n\n"
                    "[Service]\n"
                    "Type=simple\n"
                    "EnvironmentFile=-/etc/llmport-agent.env\n"
                    f"ExecStart={install_path}\n"
                    "Restart=always\n"
                    "RestartSec=5\n\n"
                    "[Install]\n"
                    "WantedBy=multi-user.target\n"
                )
                service_src = Path(sf.name)

        try:
            if _run(["sudo", "install", "-m", "0644", str(service_src), "/etc/systemd/system/llmport-agent.service"]) != 0:
                return False
            if _run(["sudo", "install", "-m", "0644", str(temp_env), "/etc/llmport-agent.env"]) != 0:
                return False
            if _run(["sudo", "systemctl", "daemon-reload"]) != 0:
                return False
            if _run(["sudo", "systemctl", "enable", "--now", "llmport-agent"]) != 0:
                return False
        finally:
            try:
                temp_env.unlink(missing_ok=True)
            except Exception:
                pass

        success("Node agent binary installed and started via systemd.")
        return True

    return _start_binary_foreground(binary, env_lines)


def _start_binary_foreground(binary: Path, env_lines: list[str]) -> bool:
    """Start a pre-built binary as a detached background process."""
    proc_env = {**os.environ}
    for line in env_lines:
        if "=" in line:
            k, v = line.split("=", 1)
            proc_env[k] = v

    if platform.system() == "Windows":
        creationflags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(  # noqa: S603
            [str(binary)],
            env=proc_env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(  # noqa: S603
            [str(binary)],
            env=proc_env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    success(f"Node agent binary started (PID {proc.pid}).")
    return True


def _start_agent_foreground(repo_dir: Path, env_lines: list[str]) -> bool:
    """Write a .env file and start the node agent as a background process (non-Linux)."""
    # Write env file
    env_file = repo_dir / ".env"
    env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    info(f"Wrote agent env to {env_file}")

    # Resolve the Python interpreter from the uv venv
    if platform.system() == "Windows":
        venv_python = repo_dir / ".venv" / "Scripts" / "python.exe"
    else:
        venv_python = repo_dir / ".venv" / "bin" / "python"

    if not venv_python.exists():
        # Fallback to system python
        python = shutil.which("python3") or shutil.which("python")
        if not python:
            error("Cannot locate Python to start node agent.")
            return False
        venv_python = Path(python)

    # Build environment with the agent env vars
    proc_env = {**os.environ}
    for line in env_lines:
        if "=" in line:
            k, v = line.split("=", 1)
            proc_env[k] = v

    # When running as a non-root user, override the default state path
    # (/var/lib/...) to a writable location inside the repo dir.
    if "LLM_PORT_NODE_AGENT_STATE_PATH" not in proc_env:
        state_dir = repo_dir / ".agent-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        proc_env["LLM_PORT_NODE_AGENT_STATE_PATH"] = str(state_dir / "state.json")

    # Launch as detached background process
    cmd = [str(venv_python), "-m", "llm_port_node_agent"]
    if platform.system() == "Windows":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        creationflags = 0x00000008 | 0x00000200
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(repo_dir),
            env=proc_env,
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(repo_dir),
            env=proc_env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    success(f"Node agent started (PID {proc.pid}).")
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
elif [ -d "$WORKDIR" ] && [ -n "$(ls -A "$WORKDIR" 2>/dev/null)" ]; then
  # Directory exists but is not a git repo (e.g. rsynced without .git/).
  # Skip clone — use the existing source tree as-is.
  echo "Directory $WORKDIR exists without .git — using existing source."
else
  git clone "$REPO_URL" "$WORKDIR"
fi

if [ -d "$WORKDIR/.git" ] && [ -n "$BRANCH" ]; then
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
  $SUDO_BIN install -m 0644 "$WORKDIR/deploy/systemd/llmport-agent.service" /etc/systemd/system/llmport-agent.service
else
  install -m 0644 "$WORKDIR/deploy/systemd/llmport-agent.service" /etc/systemd/system/llmport-agent.service
fi

# Derive Loki URL from backend (same host, port 3100)
BACKEND_HOST=$(echo "$BACKEND_URL" | sed -E 's|^https?://([^:/]+).*|\1|')
BACKEND_SCHEME=$(echo "$BACKEND_URL" | sed -E 's|^(https?)://.*|\1|')
LOKI_URL="${{BACKEND_SCHEME}}://${{BACKEND_HOST}}:3100"

TMP_ENV="$(mktemp)"
{{
  printf "LLM_PORT_NODE_AGENT_BACKEND_URL=%s\\n" "$BACKEND_URL"
  printf "LLM_PORT_NODE_AGENT_LOKI_URL=%s\\n" "$LOKI_URL"
  if [ -n "$ADVERTISE_HOST" ]; then
    printf "LLM_PORT_NODE_AGENT_ADVERTISE_HOST=%s\\n" "$ADVERTISE_HOST"
  fi
  if [ -n "$ENROLLMENT_TOKEN" ]; then
    printf "LLM_PORT_NODE_AGENT_ENROLLMENT_TOKEN=%s\\n" "$ENROLLMENT_TOKEN"
  fi
}} >"$TMP_ENV"

if [ -n "$SUDO_BIN" ]; then
  $SUDO_BIN install -m 0644 "$TMP_ENV" /etc/llmport-agent.env
  $SUDO_BIN systemctl daemon-reload
  $SUDO_BIN systemctl enable --now llmport-agent
else
  install -m 0644 "$TMP_ENV" /etc/llmport-agent.env
  systemctl daemon-reload
  systemctl enable --now llmport-agent
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
        # Repo already cloned — try to fetch latest, but don't abort
        # if it fails (the existing code is sufficient to run).
        if _run([git, "-C", str(repo_dir), "fetch", "--all", "--prune"], check=False) != 0:
            warning("git fetch failed — continuing with existing source.")
    elif repo_dir.exists() and any(repo_dir.iterdir()):
        # Directory exists but is not a git repo (e.g. rsynced without .git/).
        # Use the existing source tree as-is — no clone needed.
        info("Directory exists without .git — using existing source.")
    else:
        if _run([git, "clone", repo_url, str(repo_dir)]) != 0:
            return False

    if (repo_dir / ".git").exists():
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
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env=env,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        error(f"Command failed: {' '.join(cmd)}")
        if detail:
            error(detail)
    return proc.returncode


def _has_uncommitted_changes(repo_dir: Path) -> bool:
    """Return True if *repo_dir* is a git repo with uncommitted changes."""
    git = shutil.which("git")
    if not git or not (repo_dir / ".git").exists():
        # Not a git repo — be safe and assume there are changes.
        return True
    proc = subprocess.run(  # noqa: S603
        [git, "status", "--porcelain"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
    )
    return bool(proc.stdout.strip())
