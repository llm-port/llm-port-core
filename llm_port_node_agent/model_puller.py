"""Pull model files from the backend and reconstruct proper HF cache layout.

Used by air-gapped node agents that cannot reach HuggingFace directly.
The backend exposes ``/api/node-files/models/{model_id}/manifest`` and
``/api/node-files/models/{model_id}/blob/{hash}`` to serve the
content-addressed blobs from its HF cache.

This module downloads blobs and reconstructs the full cache tree:

    models--org--name/
      blobs/{sha256-hash}          ← actual file data
      refs/main                    ← text file: commit hash
      snapshots/{commit}/
        config.json → ../../blobs/{hash}   ← symlink
        model.safetensors → ...            ← symlink
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ModelPullerError(RuntimeError):
    """Raised when a model pull operation fails."""


async def pull_model(
    *,
    client: httpx.AsyncClient,
    credential: str,
    model_sync: dict[str, Any],
    model_store_root: str,
    emit_progress: Callable[[dict[str, Any]], Any] | None = None,
) -> Path:
    """Download blobs and reconstruct the HF cache tree for a model.

    Parameters
    ----------
    client:
        httpx AsyncClient with base_url pointing to the backend.
    credential:
        Node bearer credential for authentication.
    model_sync:
        Manifest dict from the DEPLOY_WORKLOAD payload, containing
        ``model_id``, ``model_dir_name``, ``blobs``, ``refs``,
        ``snapshots``, and ``total_size``.
    model_store_root:
        Local root for model storage (e.g. ``/srv/llm-port/models``).
    emit_progress:
        Optional callback to report download progress.

    Returns
    -------
    Path to the local HF cache root (``model_store_root``).
    """
    model_id = model_sync["model_id"]
    model_dir_name = model_sync["model_dir_name"]
    blob_entries: list[dict[str, Any]] = model_sync.get("blobs", [])
    ref_entries: list[dict[str, str]] = model_sync.get("refs", [])
    snapshot_entries: list[dict[str, Any]] = model_sync.get("snapshots", [])
    total_size = model_sync.get("total_size", 0)

    hf_cache_root = Path(model_store_root)
    model_dir = hf_cache_root / model_dir_name
    blobs_dir = model_dir / "blobs"
    refs_dir = model_dir / "refs"
    snapshots_dir = model_dir / "snapshots"

    blobs_dir.mkdir(parents=True, exist_ok=True)
    refs_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {credential}"}
    downloaded = 0
    skipped = 0

    # ── 1. Download blobs ────────────────────────────────────
    for entry in blob_entries:
        blob_hash = entry["hash"]
        expected_size = entry.get("size", 0)
        target = blobs_dir / blob_hash

        # Skip blobs that already exist with the correct size
        if target.is_file() and target.stat().st_size == expected_size:
            skipped += expected_size
            log.debug("Skipping blob %s (cached, %d bytes)", blob_hash, expected_size)
            continue

        url = f"/api/node-files/models/{model_id}/blob/{blob_hash}"

        # Support resuming partial downloads
        start_byte = 0
        tmp_path = target.with_suffix(".part")
        if tmp_path.is_file():
            start_byte = tmp_path.stat().st_size
            log.info("Resuming blob %s from byte %d", blob_hash, start_byte)

        dl_headers = dict(headers)
        if start_byte > 0:
            dl_headers["Range"] = f"bytes={start_byte}-"

        try:
            async with client.stream("GET", url, headers=dl_headers) as resp:
                if resp.status_code not in (200, 206):
                    body = await resp.aread()
                    raise ModelPullerError(
                        f"Failed to download blob {blob_hash}: "
                        f"HTTP {resp.status_code} — {body[:200]}"
                    )

                mode = "ab" if start_byte > 0 and resp.status_code == 206 else "wb"
                with open(tmp_path, mode) as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                        fh.write(chunk)
                        downloaded += len(chunk)

        except httpx.HTTPError as exc:
            raise ModelPullerError(
                f"HTTP error downloading blob {blob_hash}: {exc}"
            ) from exc

        # Validate size
        actual = tmp_path.stat().st_size
        if expected_size and actual != expected_size:
            tmp_path.unlink(missing_ok=True)
            raise ModelPullerError(
                f"Size mismatch for blob {blob_hash}: "
                f"expected {expected_size}, got {actual}"
            )

        # Atomically move to final path
        os.replace(tmp_path, target)

        if emit_progress:
            pct = int(((downloaded + skipped) / max(total_size, 1)) * 100)
            try:
                await emit_progress({
                    "message": f"Model sync blob {blob_hash[:12]}… ({pct}%)",
                    "progress_pct": pct,
                })
            except Exception:
                pass  # progress reporting is best-effort

    # ── 2. Write refs ────────────────────────────────────────
    for ref in ref_entries:
        ref_file = refs_dir / ref["name"]
        ref_file.write_text(ref["commit"] + "\n", encoding="utf-8")
        log.debug("Wrote ref %s → %s", ref["name"], ref["commit"])

    # ── 3. Create snapshot symlink trees ─────────────────────
    for snap in snapshot_entries:
        commit = snap["commit"]
        commit_dir = snapshots_dir / commit
        commit_dir.mkdir(parents=True, exist_ok=True)

        for link in snap.get("links", []):
            rel_path = link["path"]
            blob_hash = link["blob_hash"]
            link_path = commit_dir / rel_path
            link_path.parent.mkdir(parents=True, exist_ok=True)

            # Compute relative symlink target: ../../blobs/{hash}
            depth = len(Path(rel_path).parts)
            up = os.sep.join([".."] * depth)
            symlink_target = os.path.join(up, "blobs", blob_hash)

            # Remove existing file/symlink before creating
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            os.symlink(symlink_target, link_path)

        log.debug("Created snapshot %s with %d links", commit, len(snap.get("links", [])))

    log.info(
        "Model sync complete: %s — downloaded %.1f MiB, skipped %.1f MiB, "
        "%d refs, %d snapshots",
        model_sync.get("hf_repo_id", model_id),
        downloaded / (1024 * 1024),
        skipped / (1024 * 1024),
        len(ref_entries),
        len(snapshot_entries),
    )
    return hf_cache_root
