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

import asyncio
import hashlib
import logging
import os
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)


class ModelPullerError(RuntimeError):
    """Raised when a model pull operation fails."""


_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")
_MODEL_PULL_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for_model_dir(model_dir: Path) -> asyncio.Lock:
    key = str(model_dir.resolve(strict=False))
    lock = _MODEL_PULL_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _MODEL_PULL_LOCKS[key] = lock
    return lock


def _safe_leaf_name(value: str, *, field: str) -> str:
    name = value.strip()
    p = Path(name)
    if (
        not name
        or p.is_absolute()
        or p.drive
        or len(p.parts) != 1
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
    ):
        raise ModelPullerError(f"Unsafe {field}: {value!r}")
    return name


def _safe_join(root: Path, relative: str, *, field: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or any(part == ".." for part in rel.parts):
        raise ModelPullerError(f"Unsafe {field}: {relative!r}")
    candidate = (root / rel).resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ModelPullerError(f"Unsafe {field}: {relative!r}")
    return candidate


def _expected_sha256(blob_hash: str) -> str | None:
    h = blob_hash.lower()
    if _SHA256_RE.fullmatch(h):
        return h
    if h.startswith("sha256-") and _SHA256_RE.fullmatch(h[7:]):
        return h[7:]
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_content_range(
    *,
    content_range: str | None,
    resume_start: int,
    expected_size: int,
    blob_hash: str,
) -> None:
    match = _CONTENT_RANGE_RE.match(content_range or "")
    if match is None:
        raise ModelPullerError(
            f"Invalid Content-Range for resumed blob {blob_hash}: {content_range!r}"
        )
    start = int(match.group(1))
    total = match.group(3)
    if start != resume_start:
        raise ModelPullerError(
            f"Unexpected resume offset for blob {blob_hash}: expected {resume_start}, got {start}"
        )
    if expected_size and total != "*" and int(total) != expected_size:
        raise ModelPullerError(
            f"Unexpected total size in Content-Range for blob {blob_hash}: {content_range!r}"
        )


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
    try:
        model_id = str(model_sync["model_id"])
        model_dir_name = _safe_leaf_name(
            str(model_sync["model_dir_name"]),
            field="model_dir_name",
        )
    except KeyError as exc:
        raise ModelPullerError(
            f"Missing required model_sync key: {exc.args[0]!r}"
        ) from exc
    blob_entries: list[dict[str, Any]] = model_sync.get("blobs", [])
    ref_entries: list[dict[str, str]] = model_sync.get("refs", [])
    snapshot_entries: list[dict[str, Any]] = model_sync.get("snapshots", [])
    total_size_raw = model_sync.get("total_size", 0)
    try:
        total_size = max(0, int(total_size_raw))
    except (TypeError, ValueError) as exc:
        raise ModelPullerError(
            f"Invalid total_size in model_sync payload: {total_size_raw!r}"
        ) from exc

    hf_cache_root = Path(model_store_root).resolve(strict=False)
    model_dir = _safe_join(
        hf_cache_root,
        model_dir_name,
        field="model_dir_name",
    )
    blobs_dir = model_dir / "blobs"
    refs_dir = model_dir / "refs"
    snapshots_dir = model_dir / "snapshots"

    async with _lock_for_model_dir(model_dir):
        blobs_dir.mkdir(parents=True, exist_ok=True)
        refs_dir.mkdir(parents=True, exist_ok=True)
        snapshots_dir.mkdir(parents=True, exist_ok=True)

        headers = {"Authorization": f"Bearer {credential}"}
        downloaded = 0
        skipped = 0
        completed = 0

        async def _emit_blob_progress(blob_hash: str) -> None:
            if emit_progress is None:
                return
            pct = int((completed / max(total_size, 1)) * 100)
            try:
                await emit_progress({
                    "message": f"Model sync blob {blob_hash[:12]}… ({pct}%)",
                    "progress_pct": pct,
                })
            except Exception:
                pass  # progress reporting is best-effort

        # ── 1. Download blobs ────────────────────────────────────
        for entry in blob_entries:
            if not isinstance(entry, dict):
                raise ModelPullerError(f"Invalid blob entry: {entry!r}")

            blob_hash = _safe_leaf_name(str(entry.get("hash", "")), field="blob hash")
            expected_size_raw = entry.get("size", 0)
            try:
                expected_size = max(0, int(expected_size_raw))
            except (TypeError, ValueError) as exc:
                raise ModelPullerError(
                    f"Invalid blob size for {blob_hash}: {expected_size_raw!r}"
                ) from exc

            expected_digest = _expected_sha256(blob_hash)
            target = _safe_join(blobs_dir, blob_hash, field="blob path")
            target_size = target.stat().st_size if target.is_file() else 0

            # Skip verified blobs already present in cache.
            if target.is_file() and (not expected_size or target_size == expected_size):
                if expected_digest:
                    actual_digest = _sha256_file(target)
                    if actual_digest == expected_digest:
                        skipped += target_size
                        completed += expected_size or target_size
                        log.debug(
                            "Skipping blob %s (cached, hash verified)",
                            blob_hash,
                        )
                        await _emit_blob_progress(blob_hash)
                        continue
                    log.warning(
                        "Cached blob %s failed hash check, re-downloading",
                        blob_hash,
                    )
                else:
                    skipped += target_size
                    completed += expected_size or target_size
                    log.debug(
                        "Skipping blob %s (cached, %d bytes)",
                        blob_hash,
                        target_size,
                    )
                    await _emit_blob_progress(blob_hash)
                    continue

            url = f"/api/node-files/models/{model_id}/blob/{blob_hash}"

            # Support resuming partial downloads with strict guards.
            start_byte = 0
            tmp_path = _safe_join(blobs_dir, f"{blob_hash}.part", field="partial blob path")
            if tmp_path.is_file():
                start_byte = tmp_path.stat().st_size
                if expected_size and start_byte > expected_size:
                    log.warning(
                        "Discarding oversized partial blob %s (%d > %d)",
                        blob_hash,
                        start_byte,
                        expected_size,
                    )
                    tmp_path.unlink(missing_ok=True)
                    start_byte = 0
                elif start_byte > 0:
                    log.info("Resuming blob %s from byte %d", blob_hash, start_byte)

                # A fully downloaded .part can happen after interruption right before replace().
                if start_byte > 0 and expected_size and start_byte == expected_size:
                    if expected_digest and _sha256_file(tmp_path) != expected_digest:
                        log.warning("Discarding invalid completed partial blob %s", blob_hash)
                        tmp_path.unlink(missing_ok=True)
                        start_byte = 0
                    else:
                        os.replace(tmp_path, target)
                        skipped += expected_size
                        completed += expected_size
                        await _emit_blob_progress(blob_hash)
                        continue

            dl_headers = dict(headers)
            if start_byte > 0:
                dl_headers["Range"] = f"bytes={start_byte}-"

            bytes_written = 0
            resumed = False
            try:
                async with client.stream("GET", url, headers=dl_headers) as resp:
                    if resp.status_code not in (200, 206):
                        body = await resp.aread()
                        raise ModelPullerError(
                            f"Failed to download blob {blob_hash}: "
                            f"HTTP {resp.status_code} — {body[:200]}"
                        )

                    mode = "wb"
                    if start_byte > 0 and resp.status_code == 206:
                        _validate_content_range(
                            content_range=resp.headers.get("Content-Range"),
                            resume_start=start_byte,
                            expected_size=expected_size,
                            blob_hash=blob_hash,
                        )
                        mode = "ab"
                        resumed = True
                    elif start_byte > 0 and resp.status_code == 200:
                        # Server ignored Range, so restart the blob from scratch.
                        log.warning(
                            "Backend ignored Range for blob %s; restarting download",
                            blob_hash,
                        )

                    with open(tmp_path, mode) as fh:
                        async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
                            fh.write(chunk)
                            bytes_written += len(chunk)

            except httpx.HTTPError as exc:
                raise ModelPullerError(
                    f"HTTP error downloading blob {blob_hash}: {exc}"
                ) from exc

            # Validate size and digest before publishing.
            actual = tmp_path.stat().st_size
            if expected_size and actual != expected_size:
                tmp_path.unlink(missing_ok=True)
                raise ModelPullerError(
                    f"Size mismatch for blob {blob_hash}: "
                    f"expected {expected_size}, got {actual}"
                )
            if expected_digest:
                actual_digest = _sha256_file(tmp_path)
                if actual_digest != expected_digest:
                    tmp_path.unlink(missing_ok=True)
                    raise ModelPullerError(
                        f"Hash mismatch for blob {blob_hash}: "
                        f"expected {expected_digest}, got {actual_digest}"
                    )

            # Atomically move to final path
            os.replace(tmp_path, target)
            if resumed:
                skipped += start_byte
            downloaded += bytes_written
            completed += expected_size or actual
            await _emit_blob_progress(blob_hash)

        # ── 2. Create snapshot symlink trees (publish commit dirs) ──
        for snap in snapshot_entries:
            if not isinstance(snap, dict):
                raise ModelPullerError(f"Invalid snapshot entry: {snap!r}")

            commit = _safe_leaf_name(str(snap.get("commit", "")), field="snapshot commit")
            commit_dir = _safe_join(snapshots_dir, commit, field="snapshot commit")
            temp_name = f".{commit}.tmp-{uuid.uuid4().hex}"
            tmp_commit_dir = _safe_join(snapshots_dir, temp_name, field="snapshot temp dir")
            tmp_commit_dir.mkdir(parents=True, exist_ok=False)

            try:
                for link in snap.get("links", []):
                    if not isinstance(link, dict):
                        raise ModelPullerError(f"Invalid snapshot link: {link!r}")
                    rel_path = str(link.get("path", ""))
                    blob_hash = _safe_leaf_name(
                        str(link.get("blob_hash", "")),
                        field="snapshot blob hash",
                    )
                    link_path = _safe_join(
                        tmp_commit_dir,
                        rel_path,
                        field="snapshot link path",
                    )
                    link_path.parent.mkdir(parents=True, exist_ok=True)

                    target_blob = _safe_join(blobs_dir, blob_hash, field="snapshot blob path")
                    if not target_blob.is_file():
                        raise ModelPullerError(
                            f"Snapshot references missing blob {blob_hash} ({rel_path})"
                        )
                    symlink_target = os.path.relpath(target_blob, start=link_path.parent)

                    if link_path.exists() or link_path.is_symlink():
                        link_path.unlink()
                    os.symlink(symlink_target, link_path)

                if commit_dir.exists() or commit_dir.is_symlink():
                    if commit_dir.is_dir() and not commit_dir.is_symlink():
                        shutil.rmtree(commit_dir)
                    else:
                        commit_dir.unlink()
                os.replace(tmp_commit_dir, commit_dir)
            except Exception:
                shutil.rmtree(tmp_commit_dir, ignore_errors=True)
                raise

            log.debug(
                "Created snapshot %s with %d links",
                commit,
                len(snap.get("links", [])),
            )

        # ── 3. Write refs last (publication step) ────────────────
        for ref in ref_entries:
            if not isinstance(ref, dict):
                raise ModelPullerError(f"Invalid ref entry: {ref!r}")
            ref_name = str(ref.get("name", ""))
            ref_commit = str(ref.get("commit", "")).strip()
            ref_file = _safe_join(refs_dir, ref_name, field="ref name")
            ref_file.parent.mkdir(parents=True, exist_ok=True)
            # Write commit hash WITHOUT trailing newline — huggingface_hub
            # snapshot_download reads refs with f.read() (no strip) so a
            # trailing newline produces a path that never matches the
            # snapshot directory, causing LocalEntryNotFoundError.
            ref_file.write_text(ref_commit, encoding="utf-8")
            log.debug("Wrote ref %s → %s", ref_name, ref_commit)

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
