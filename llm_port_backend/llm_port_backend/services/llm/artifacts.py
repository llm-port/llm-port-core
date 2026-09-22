"""Model artifact cache inspection, manifest synthesis, and payload building.

Extracted from web views so that both native and Ray Serve deployment paths,
as well as the inference ModelArtifactCoordinator, can inspect model cache
directories, build manifests, compute canonical digests, and synthesize sync
payloads without layering violations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from llm_port_backend.settings import settings

if TYPE_CHECKING:
    from llm_port_backend.db.models.llm import LLMModel

log = logging.getLogger(__name__)


def model_cache_dir(hf_repo_id: str) -> Path | None:
    """Return the ``models--org--name`` cache directory, or *None*."""
    cache_root = Path(settings.model_store_root)
    dir_name = f"models--{hf_repo_id.replace('/', '--')}"
    model_dir = cache_root / dir_name
    return model_dir if model_dir.is_dir() else None


def resolve_blob_hash(fpath: Path, blobs_dir_resolved: Path) -> str | None:
    """Determine which blob a snapshot file refers to.

    Handles both symlinks (standard Linux HF cache) and regular files
    that resolve into the ``blobs/`` directory (Windows / copied caches).
    """
    if fpath.is_symlink():
        return Path(os.readlink(fpath)).name
    try:
        resolved = fpath.resolve()
        resolved.relative_to(blobs_dir_resolved)
        return resolved.name
    except (ValueError, OSError):
        return None


def build_cache_manifest(model_dir: Path) -> dict[str, Any]:
    """Enumerate blobs, refs, and snapshot symlinks for an HF cache dir.

    Return structure::

        {
            "model_dir_name": "models--org--name",
            "blobs":     [{"hash": "<hex>", "size": N}, ...],
            "refs":      [{"name": "main", "commit": "<hex>"}, ...],
            "snapshots": [{"commit": "<hex>", "links": [{"path": "...", "blob_hash": "..."}]}, ...],
            "total_size": N,
        }
    """
    blobs_dir = model_dir / "blobs"
    refs_dir = model_dir / "refs"
    snapshots_dir = model_dir / "snapshots"

    # ── Blobs ────────────────────────────────────────────────
    blobs: list[dict[str, Any]] = []
    if blobs_dir.is_dir():
        for entry in sorted(blobs_dir.iterdir()):
            if entry.is_file():
                blobs.append({"hash": entry.name, "size": entry.stat().st_size})

    # ── Refs ─────────────────────────────────────────────────
    refs: list[dict[str, str]] = []
    if refs_dir.is_dir():
        for entry in sorted(refs_dir.iterdir()):
            if entry.is_file():
                refs.append({
                    "name": entry.name,
                    "commit": entry.read_text(encoding="utf-8").strip(),
                })

    # ── Snapshots (symlink tree) ─────────────────────────────
    snapshots: list[dict[str, Any]] = []
    if snapshots_dir.is_dir():
        blobs_resolved = blobs_dir.resolve()
        for commit_dir in sorted(snapshots_dir.iterdir()):
            if not commit_dir.is_dir():
                continue
            links: list[dict[str, str]] = []
            for dirpath, _dirs, fnames in os.walk(commit_dir):
                for fname in sorted(fnames):
                    fpath = Path(dirpath) / fname
                    rel = str(fpath.relative_to(commit_dir)).replace("\\", "/")
                    blob_hash = resolve_blob_hash(fpath, blobs_resolved)
                    if blob_hash:
                        links.append({"path": rel, "blob_hash": blob_hash})
                    else:
                        log.warning(
                            "Snapshot file %s cannot be mapped to a blob — skipping",
                            fpath,
                        )
            snapshots.append({"commit": commit_dir.name, "links": links})

    manifest = {
        "model_dir_name": model_dir.name,
        "blobs": blobs,
        "refs": refs,
        "snapshots": snapshots,
        "total_size": sum(b["size"] for b in blobs),
    }
    manifest["manifest_sha256"] = manifest_digest(manifest)
    return manifest


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Compute canonical SHA-256 digest over blobs, refs, and snapshots.

    Stable across dictionary key ordering; changes when any blob hash,
    ref commit, or snapshot link changes.
    """
    canonical = {
        "blobs": sorted(
            [{"hash": str(b.get("hash", "")), "size": int(b.get("size", 0))} for b in manifest.get("blobs", [])],
            key=lambda x: x["hash"],
        ),
        "refs": sorted(
            [{"commit": str(r.get("commit", "")), "name": str(r.get("name", ""))} for r in manifest.get("refs", [])],
            key=lambda x: x["name"],
        ),
        "snapshots": sorted(
            [
                {
                    "commit": str(s.get("commit", "")),
                    "links": sorted(
                        [
                            {"blob_hash": str(l.get("blob_hash", "")), "path": str(l.get("path", ""))}
                            for l in s.get("links", [])
                        ],
                        key=lambda x: x["path"],
                    ),
                }
                for s in manifest.get("snapshots", [])
            ],
            key=lambda x: x["commit"],
        ),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_model_sync_payload(
    model: LLMModel,
    *,
    source: str = "sync_from_server",
) -> dict[str, Any] | None:
    """Build the ``model_sync`` dict for node model synchronization.

    *source* controls how the model reaches the node:

    - ``"sync_from_server"`` — include the full blob manifest so the agent
      pulls from this backend's file server.
    - ``"download_from_hf"`` — include only the ``hf_repo_id`` so the agent
      can download directly from HuggingFace.
    """
    if not model.hf_repo_id:
        return None

    base: dict[str, Any] = {
        "model_id": str(model.id),
        "hf_repo_id": model.hf_repo_id,
        "source": source,
    }

    if source == "download_from_hf":
        return base

    from llm_port_backend.web.api.node_files import views as node_files_views

    # Support legacy tests monkeypatching node_files_views while honoring module patches
    views_cache_mod = getattr(getattr(node_files_views, "_model_cache_dir", None), "__module__", None)
    cache_dir_fn = node_files_views._model_cache_dir if views_cache_mod not in (None, __name__) else model_cache_dir

    views_manifest_mod = getattr(getattr(node_files_views, "_build_cache_manifest", None), "__module__", None)
    manifest_fn = node_files_views._build_cache_manifest if views_manifest_mod not in (None, __name__) else build_cache_manifest

    resolved_dir = cache_dir_fn(model.hf_repo_id)
    if resolved_dir is None:
        # No local copy at all.  The payload is returned rather than None so
        # callers can still tell "this model has a repo id" from "it does
        # not", but it carries no files -- see ``model_sync_carries_files``.
        return base

    model_dir = Path(resolved_dir)
    manifest = manifest_fn(model_dir)
    if not manifest or not manifest.get("blobs"):
        # The directory exists but is empty or unreadable.  This is the
        # hollow-cache case: a previous import created the folder and no
        # files, which looks present to anything that only checks for a path.
        return base

    return base | manifest


def model_sync_carries_files(payload: dict[str, Any] | None) -> bool:
    """Whether a ``sync_from_server`` payload actually has something to send.

    ``build_model_sync_payload`` returns a fileless payload when this server
    holds no copy of the model, and an agent correctly refuses it with
    "model_sync payload with files is required".  Dispatching one anyway
    records a *node* failure for a *server* problem, which sends the operator
    to inspect two perfectly healthy machines.
    """
    if not payload:
        return False
    if payload.get("source") == "download_from_hf":
        # The node fetches it itself; there is nothing for us to carry.
        return True
    return bool(payload.get("blobs"))

