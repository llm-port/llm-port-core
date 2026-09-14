"""Docker endpoint selection — Docker Desktop / Rancher Desktop fallback.

On Windows the docker CLI talks to a container runtime over a named pipe.
Docker Desktop and Rancher Desktop install their own endpoints and often
point the default docker context (or ``DOCKER_HOST``) at the *other*
runtime's pipe.  When that pipe is down — e.g. Docker Desktop is not
running but Rancher Desktop is — every docker call fails with::

    failed to connect to the docker API at
    npipe:////./pipe/dockerDesktopLinuxEngine: The system cannot find
    the file specified.

This module centralises the resolution logic:

* :func:`rancher_endpoints` — known Rancher Desktop daemon endpoints per
  platform.  On Windows Rancher Desktop's ``wsl-helper`` proxy listens
  on the classic ``//./pipe/docker_engine`` pipe (see
  ``serve_windows.go`` in the rancher-desktop source:
  ``DefaultEndpoint = "npipe:////./pipe/docker_engine"``).
* :func:`resolve_docker_host` — probe the current endpoint first, then
  each Rancher Desktop fallback; return the first ``docker info`` that
  answers, or ``None``.
* :func:`ensure_docker_host` — one-shot guard: if the current endpoint
  is dead and a Rancher Desktop endpoint answers, export
  ``DOCKER_HOST`` (cached in a module global) so *every* docker
  subprocess the CLI spawns (detect, compose, logs, …) reaches the live
  daemon.  No side effects when the active endpoint already works.

Only ``os`` and ``subprocess`` are used — no Docker SDK, consistent with
the rest of the CLI.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path


def is_windows() -> bool:
    """True when the daemon endpoints are Windows named pipes."""
    return platform.system() == "Windows"


def rancher_endpoints() -> list[str]:
    """Known rancher-desktop daemon endpoints for this platform.

    * Windows — the WSL2/Hyper-V backend's host-side proxy pipe
      (``rancher-desktop/run/docker.sock`` inside the VM).
    * macOS / Linux — the socket the app publishes on the host,
      optionally forwarded to the conventional ``/var/run/docker.sock``
      location when ``application.adminAccess`` is enabled.
    """
    if is_windows():
        return ["npipe:////./pipe/docker_engine"]
    app_dir = Path.home() / "Library" / "Application Support" / "rancher-desktop"
    return [
        f"unix://{app_dir / 'docker.sock'}",
        "unix:///var/run/docker.sock",
    ]


def _probe(endpoint: str, timeout: int = 10) -> bool:
    """Return True if ``docker info`` answers at *endpoint*.

    ``endpoint`` may be the docker CLI's special value ``"default"``
    (leave the configuration untouched and let the CLI use its current
    docker context).
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return False
    env = dict(os.environ)
    if endpoint == "default":
        env.pop("DOCKER_HOST", None)
    else:
        env["DOCKER_HOST"] = endpoint
    try:
        return (
            subprocess.run(  # noqa: S603
                [docker_bin, "info"],
                env=env,
                capture_output=True,
                check=False,
                timeout=timeout,
            ).returncode
            == 0
        )
    except Exception:  # a probe must never raise
        return False


def resolve_docker_host() -> str | None:
    """Return the first reachable docker endpoint, or ``None``.

    Order: the endpoint the docker CLI is currently configured for
    (``"default"`` when ``DOCKER_HOST`` is unset, i.e. its active
    docker context, or the ``DOCKER_HOST`` value itself), then the
    Rancher Desktop fallback endpoints.
    """
    current = os.environ.get("DOCKER_HOST") or "default"
    if _probe(current):
        return current
    for candidate in rancher_endpoints():
        if candidate != current and _probe(candidate):
            return candidate
    return None


def ensure_docker_host(verbose: bool = False, quiet: bool = False) -> str | None:
    """Make every docker subprocess talk to a *running* daemon.

    Called once from the CLI root callback, before any command runs:

    * current endpoint already works  → no-op;
    * current endpoint dead but a Rancher Desktop endpoint answers →
      export ``DOCKER_HOST`` and print a short notice, so detect /
      compose / logs all hit the live daemon;
    * nothing answers → no change (the usual "Docker daemon is not
      running" errors from ``detect_docker`` stay in effect).

    Safe on every platform: on a Linux host without any container
    daemon the probes simply fail fast.  Returns the selected endpoint
    or ``None``.
    """
    if shutil.which("docker") is None:
        return None

    current = os.environ.get("DOCKER_HOST") or "default"
    host = resolve_docker_host()
    if host is None:
        if verbose:
            from llmport.core.console import warning  # noqa: PLC0415 — late import avoids cycle
            warning("No reachable Docker endpoint (Docker Desktop or Rancher Desktop).")
        return None

    if host != current:
        os.environ["DOCKER_HOST"] = host
        if not quiet:
            from llmport.core.console import info  # noqa: PLC0415 — late import avoids cycle
            if is_windows() and host == "npipe:////./pipe/docker_engine":
                info("Docker Desktop endpoint not found — using Rancher Desktop.")
            else:
                info(f"Docker endpoint → {host}")
    return host
