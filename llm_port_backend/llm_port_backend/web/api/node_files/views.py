"""Serve model files to node agents for air-gapped deployments.

The manifest describes the full HuggingFace cache tree so the agent
can reconstruct the proper layout (blobs, refs, snapshot symlinks).

Endpoints authenticate via the same Bearer credential that agents
use for their WebSocket stream, so no additional secrets are needed.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette import status

from llm_port_backend.db.dao.llm_dao import ModelDAO
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.nodes import NodeControlService
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/node-files", tags=["node-files"])

_CHUNK_SIZE = 256 * 1024  # 256 KiB streaming chunks

# Blob filenames: hex hash, possibly with algo prefix separated by hyphen
_SAFE_BLOB_RE = re.compile(r"^[a-fA-F0-9][a-fA-F0-9_-]*$")


# ------------------------------------------------------------------
# Authentication dependency — validates node Bearer credential
# ------------------------------------------------------------------

async def _authenticate_node(request: Request) -> InfraNode:
    """Verify the caller is a registered node agent."""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database unavailable.",
        )
    async with session_factory() as session:
        dao = NodeControlDAO(session)
        service = NodeControlService(
            dao=dao,
            pepper=settings.settings_master_key,
            enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
            default_command_timeout_sec=settings.node_command_default_timeout_sec,
        )
        try:
            node, _credential = await service.authenticate_agent(
                authorization=request.headers.get("authorization"),
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid node credential.",
            ) from exc
        return node


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _model_cache_dir(hf_repo_id: str) -> Path | None:
    """Return the ``models--org--name`` cache directory, or *None*."""
    cache_root = Path(settings.model_store_root)
    dir_name = f"models--{hf_repo_id.replace('/', '--')}"
    model_dir = cache_root / dir_name
    return model_dir if model_dir.is_dir() else None


def _resolve_blob_hash(fpath: Path, blobs_dir_resolved: Path) -> str | None:
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


def _build_cache_manifest(model_dir: Path) -> dict[str, Any]:
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
                    blob_hash = _resolve_blob_hash(fpath, blobs_resolved)
                    if blob_hash:
                        links.append({"path": rel, "blob_hash": blob_hash})
                    else:
                        log.warning(
                            "Snapshot file %s cannot be mapped to a blob — skipping",
                            fpath,
                        )
            snapshots.append({"commit": commit_dir.name, "links": links})

    return {
        "model_dir_name": model_dir.name,
        "blobs": blobs,
        "refs": refs,
        "snapshots": snapshots,
        "total_size": sum(b["size"] for b in blobs),
    }


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get(
    "/models/{model_id}/manifest",
    name="node_model_manifest",
)
async def model_manifest(
    model_id: uuid.UUID,
    request: Request,
    _node: InfraNode = Depends(_authenticate_node),
) -> dict[str, Any]:
    """Return the HF-cache-aware manifest for a model.

    The manifest describes blobs (actual content), refs (branch →
    commit), and snapshot symlink trees so the node agent can
    faithfully reconstruct the HF cache layout.
    """
    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        dao = ModelDAO(session)
        model = await dao.get(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Model not found.")
        if not model.hf_repo_id:
            raise HTTPException(
                status_code=400,
                detail="Model has no HF repo ID — cannot serve files.",
            )

    model_dir = _model_cache_dir(model.hf_repo_id)
    if model_dir is None:
        raise HTTPException(
            status_code=404,
            detail=f"No cached model found for {model.hf_repo_id}.",
        )

    manifest = _build_cache_manifest(model_dir)
    manifest["hf_repo_id"] = model.hf_repo_id
    return manifest


@router.get(
    "/models/{model_id}/blob/{blob_hash}",
    name="node_model_blob",
)
async def model_blob(
    model_id: uuid.UUID,
    blob_hash: str,
    request: Request,
    _node: InfraNode = Depends(_authenticate_node),
) -> StreamingResponse:
    """Stream a single blob from the model's HF cache ``blobs/`` dir.

    ``blob_hash`` is the filename under ``blobs/`` (as returned
    by the ``/manifest`` endpoint).
    Supports ``Range`` header for resumable downloads.
    """
    if not _SAFE_BLOB_RE.match(blob_hash):
        raise HTTPException(status_code=400, detail="Invalid blob hash.")

    session_factory = request.app.state.db_session_factory
    async with session_factory() as session:
        dao = ModelDAO(session)
        model = await dao.get(model_id)
        if model is None:
            raise HTTPException(status_code=404, detail="Model not found.")
        if not model.hf_repo_id:
            raise HTTPException(status_code=400, detail="Model has no HF repo ID.")

    model_dir = _model_cache_dir(model.hf_repo_id)
    if model_dir is None:
        raise HTTPException(status_code=404, detail="Model cache not found.")

    # Resolve and enforce blobs/ containment
    blobs_dir = (model_dir / "blobs").resolve()
    target = (model_dir / "blobs" / blob_hash).resolve()
    if not str(target).startswith(str(blobs_dir)):
        raise HTTPException(status_code=400, detail="Invalid blob hash.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Blob not found.")

    file_size = target.stat().st_size

    # Parse Range header for resumable downloads
    range_header = request.headers.get("range")
    start = 0
    end = file_size - 1
    status_code = 200
    headers: dict[str, str] = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'attachment; filename="{blob_hash}"',
    }

    if range_header and range_header.startswith("bytes="):
        range_spec = range_header[6:]
        parts = range_spec.split("-", 1)
        if parts[0]:
            start = int(parts[0])
        if len(parts) > 1 and parts[1]:
            end = int(parts[1])
        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable.")
        end = min(end, file_size - 1)
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    content_length = end - start + 1
    headers["Content-Length"] = str(content_length)

    def _stream():
        with open(target, "rb") as fh:
            fh.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = fh.read(min(_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _stream(),
        status_code=status_code,
        media_type="application/octet-stream",
        headers=headers,
    )


# ------------------------------------------------------------------
# Image transfer — stream `docker save` output for air-gapped nodes
# ------------------------------------------------------------------

# Validate image references: allow registry/repo:tag but reject injection.
_SAFE_IMAGE_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*(?::[a-zA-Z0-9._-]+)?$"
)


@router.get(
    "/images/save",
    name="node_image_save",
)
async def image_save(
    request: Request,
    image: str,
    tag: str = "latest",
    _node: InfraNode = Depends(_authenticate_node),
) -> StreamingResponse:
    """Stream a ``docker save`` tarball so the agent can load it offline.

    The caller specifies the image name and tag.  This endpoint uses the
    Docker Engine API (via the mounted socket) to export the image as a
    tar archive and streams it back in chunked pieces.
    """
    ref = f"{image}:{tag}"
    if not _SAFE_IMAGE_RE.match(ref):
        raise HTTPException(status_code=400, detail="Invalid image reference.")

    docker: DockerService = request.app.state.docker

    # Verify the image exists locally before starting the stream.
    try:
        info = await docker.client.images.inspect(ref)
    except Exception:  # aiodocker raises DockerError(404, …)
        raise HTTPException(
            status_code=404,
            detail=f"Image {ref} not found on this host.",
        )

    # Expose image size so the node agent can report download progress.
    image_size = info.get("Size") or info.get("VirtualSize") or 0

    async def _stream_docker_save():
        async with docker.client.images.export_image(ref) as stream:
            while True:
                chunk = await stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk

    safe_filename = ref.replace("/", "_").replace(":", "_") + ".tar"
    resp_headers: dict[str, str] = {
        "Content-Disposition": f'attachment; filename="{safe_filename}"',
    }
    if image_size:
        resp_headers["X-Image-Size"] = str(image_size)
    return StreamingResponse(
        _stream_docker_save(),
        media_type="application/x-tar",
        headers=resp_headers,
    )
