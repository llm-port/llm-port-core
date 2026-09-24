"""The deployment files the CLI carries, for installs without a source checkout.

``pip install llmport-cli`` brings the compose file and everything it mounts
(packed from ``llm_port_shared`` at build time, see ``hatch_build.py``).
``llmport deploy`` unpacks them into an install directory and runs the
published images of the CLI's own version; ``llmport upgrade`` with a newer
CLI replaces them and moves the images to the new version.

An install made this way is marked with :data:`MARKER`. Without the marker
the install is a source checkout, which builds its own images.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from llmport import __version__

#: Written into an install made from the bundle: which release it runs.
MARKER = ".llmport-release"

#: Where ``llmport deploy`` puts an install when it is given no directory.
DEFAULT_INSTALL_DIR = Path.home() / "llm-port"

#: Files an install changes for itself. The bundle's copy is only a
#: starting point: an upgrade never replaces them once they exist.
_KEPT_ON_UPGRADE = {
    # Only ever a sample; deploy writes the real one from .env.
    "rabbitmq/definitions.json.example",
}

#: Files the bundle does not carry but the compose file mounts. They must
#: exist before the first start, or Docker creates a *directory* in their place.
_STARTING_CONTENT = {
    # The backend writes the Prometheus scrape targets here at run time.
    "prometheus/targets.json": "[]\n",
}


def bundled_files() -> Traversable | None:
    """The deployment files inside the installed CLI, or ``None`` when it has none.

    A CLI run from a source checkout (``pip install -e``) has none: the
    checkout's ``llm_port_shared`` is used instead.
    """
    root = resources.files("llmport") / "bundle" / "shared"
    return root if root.is_dir() and (root / "docker-compose.yaml").is_file() else None


def _walk(node: Traversable, prefix: str = "") -> list[tuple[str, Traversable]]:
    files: list[tuple[str, Traversable]] = []
    for child in node.iterdir():
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            files.extend(_walk(child, f"{rel}/"))
        else:
            files.append((rel, child))
    return files


def installed_release(install_dir: Path) -> str | None:
    """The release an install made from the bundle runs, or ``None`` for a source checkout."""
    marker = install_dir / MARKER
    if not marker.is_file():
        return None
    try:
        return str(json.loads(marker.read_text(encoding="utf-8"))["version"])
    except (ValueError, KeyError, OSError):
        return "unknown"


def unpack(install_dir: Path) -> list[str]:
    """Put this CLI's deployment files in *install_dir*; return what was written.

    Safe on an existing install: ``.env``, backups, credentials and what the
    services generated are not in the bundle and are never touched, and the
    few bundle files an install adapts are only written when missing.
    Anything else from the bundle is replaced by this release's copy.
    """
    root = bundled_files()
    if root is None:
        msg = "This llmport CLI carries no deployment files (it runs from a source checkout)."
        raise RuntimeError(msg)
    install_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for rel, source in _walk(root):
        target = install_dir / rel
        if rel in _KEPT_ON_UPGRADE and target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        if not target.is_file() or target.read_bytes() != data:
            target.write_bytes(data)
            written.append(rel)
    for rel, content in _STARTING_CONTENT.items():
        target = install_dir / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(rel)
    (install_dir / MARKER).write_text(
        json.dumps({"version": __version__, "unpacked_at": datetime.now(tz=UTC).isoformat(timespec="seconds")})
        + "\n",
        encoding="utf-8",
    )
    return written
