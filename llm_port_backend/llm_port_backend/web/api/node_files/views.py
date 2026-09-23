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
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette import status

from llm_port_backend.db.dao.llm_dao import ModelDAO
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.inference import image_cache
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
# Helpers (delegated to services.llm.artifacts)
# ------------------------------------------------------------------

from llm_port_backend.services.llm.artifacts import (
    build_cache_manifest as _build_cache_manifest,
    model_cache_dir as _model_cache_dir,
    resolve_blob_hash as _resolve_blob_hash,
)


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.get("/whoami", name="node_whoami")
async def node_whoami(node: InfraNode = Depends(_authenticate_node)) -> dict[str, Any]:
    """Which machine this credential belongs to -- and that it still works.

    Lets an already-enrolled agent tell "I am a member here" from "I need to
    ask to join" without changing anything. Re-running the install line is
    how an operator upgrades, and it used to file a fresh join request for a
    machine that was already in the fleet.
    """
    return {"node_id": str(node.id), "agent_id": node.agent_id, "host": node.host}


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
    expect_id: str | None = None,
    expect_rootfs: str | None = None,
    _node: InfraNode = Depends(_authenticate_node),
) -> Response:
    """Hand a node the runtime image as a tar it can ``docker load``.

    Served from a one-time export when there is room for one, so the
    transfer has a real length and can be resumed with ``Range``; otherwise
    streamed live as before. ``expect_id``/``expect_rootfs`` are the pin the
    node was told to run: when this server holds a different build it says so
    with a 409 instead of sending twelve gigabytes the node will refuse.
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

    try:
        image_cache.verify_expected(info, expect_id=expect_id, expect_rootfs=expect_rootfs)
    except image_cache.ImageMismatch as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    image_id = str(info.get("Id") or "")
    unpacked_size = int(info.get("Size") or info.get("VirtualSize") or 0)
    if image_id:
        try:
            exported = await image_cache.ensure_export(docker, ref, image_id, unpacked_size)
        except Exception:  # never let the cache stand between a node and its image
            log.exception("Could not export %s; streaming it instead", ref)
            exported = None
        if exported is not None:
            return FileResponse(
                exported,
                media_type="application/x-tar",
                filename=ref.replace("/", "_").replace(":", "_") + ".tar",
                headers={"X-Image-Id": image_id},
            )

    # No export: stream live. Not resumable, and the only size known up front
    # is the unpacked one, which overstates the wire size about threefold.
    image_size = unpacked_size

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
