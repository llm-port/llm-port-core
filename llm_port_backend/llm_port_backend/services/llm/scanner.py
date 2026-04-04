"""Scan downloaded model files to detect format, size, and engine compatibility."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Extension → (ArtifactFormat value, compatible engines)
_FORMAT_MAP: dict[str, tuple[str, list[str]]] = {
    ".safetensors": ("safetensors", ["vllm", "tgi"]),
    ".gguf": ("gguf", ["llamacpp", "ollama"]),
}

# Files we always skip when scanning
_SKIP_NAMES = {
    ".gitattributes",
    "README.md",
    "LICENSE",
    "LICENSE.md",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "generation_config.json",
}


def scan_model_directory(
    directory: str | Path,
    *,
    compute_sha256: bool = False,
) -> list[dict[str, Any]]:
    """
    Walk *directory* and return a list of artifact dicts ready for
    :meth:`ArtifactDAO.create_batch`.

    Each dict has keys: ``path``, ``format``, ``size_bytes``,
    ``sha256`` (optional), ``engine_compat``.
    """
    root = Path(directory)
    if not root.is_dir():
        log.warning("scan_model_directory: %s is not a directory", root)
        return []

    artifacts: list[dict[str, Any]] = []
    for fpath in sorted(root.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.name in _SKIP_NAMES:
            continue

        suffix = fpath.suffix.lower()
        fmt_info = _FORMAT_MAP.get(suffix)
        if fmt_info is None:
            # Unknown extension — record as "other" with no engine compat
            fmt = "other"
            compat: list[str] = []
        else:
            fmt, compat = fmt_info

        entry: dict[str, Any] = {
            "path": str(fpath),
            "format": fmt,
            "size_bytes": fpath.stat().st_size,
            "engine_compat": compat,
        }

        if compute_sha256:
            entry["sha256"] = _sha256(fpath)

        artifacts.append(entry)

    log.info(
        "Scanned %s: found %d artifact(s) in %s",
        root,
        len(artifacts),
        directory,
    )
    return artifacts


def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
