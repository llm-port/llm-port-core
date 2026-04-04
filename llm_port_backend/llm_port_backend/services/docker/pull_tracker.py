"""In-memory tracker for Docker image pull operations.

Provides:
- Background pull with progress tracking
- Deduplication: concurrent pulls of the same image reuse the same task
- SSE-compatible async progress iteration
- Status & active-pull queries surviving page refresh
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from llm_port_backend.services.docker.client import DockerService

logger = logging.getLogger(__name__)

# Keep completed/failed pulls visible for 5 min so the frontend can pick them up
# after a page refresh.
_COMPLETED_TTL_SEC = 300


class PullState(str, Enum):
    """Lifecycle states for a pull operation."""

    PULLING = "pulling"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class LayerProgress:
    """Progress for a single Docker image layer."""

    id: str
    status: str = ""
    current: int = 0
    total: int = 0


@dataclass
class PullJob:
    """Tracks one image pull."""

    pull_id: str
    image: str
    tag: str
    state: PullState = PullState.PULLING
    error: str | None = None
    layers: dict[str, LayerProgress] = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    last_update_at: float = field(default_factory=time.time)
    _event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _subscribers: int = field(default=0, repr=False)

    @property
    def image_ref(self) -> str:
        return f"{self.image}:{self.tag}"

    @property
    def overall_current(self) -> int:
        return sum(l.current for l in self.layers.values())

    @property
    def overall_total(self) -> int:
        return sum(l.total for l in self.layers.values())

    @property
    def percent(self) -> float:
        t = self.overall_total
        if t <= 0:
            return 0.0
        return min(round(self.overall_current / t * 100, 1), 100.0)


class PullTracker:
    """Singleton that manages concurrent image pulls with progress tracking."""

    def __init__(self) -> None:
        self._jobs: dict[str, PullJob] = {}  # pull_id → job
        self._active: dict[str, str] = {}  # image_ref → pull_id  (only while PULLING)
        self._lock = asyncio.Lock()

    # ── Public API ────────────────────────────────────────────────────

    async def start_pull(
        self,
        docker: DockerService,
        image: str,
        tag: str = "latest",
    ) -> tuple[PullJob, bool]:
        """Start a pull or return existing active pull.

        Returns ``(job, is_new)`` — *is_new* is ``False`` when deduplicated.
        """
        ref = f"{image}:{tag}"
        async with self._lock:
            # Deduplicate: return existing active pull
            existing_id = self._active.get(ref)
            if existing_id and existing_id in self._jobs:
                job = self._jobs[existing_id]
                if job.state == PullState.PULLING:
                    return job, False

            # Create new pull job
            pull_id = uuid.uuid4().hex[:12]
            job = PullJob(pull_id=pull_id, image=image, tag=tag)
            self._jobs[pull_id] = job
            self._active[ref] = pull_id

        # Launch background pull (not under the lock)
        asyncio.create_task(self._run_pull(docker, job))
        return job, True

    def get_job(self, pull_id: str) -> PullJob | None:
        return self._jobs.get(pull_id)

    def get_active_for(self, image: str, tag: str = "latest") -> PullJob | None:
        """Return active pull job for image:tag, or None."""
        ref = f"{image}:{tag}"
        pull_id = self._active.get(ref)
        if pull_id:
            job = self._jobs.get(pull_id)
            if job and job.state == PullState.PULLING:
                return job
        return None

    async def subscribe(self, job: PullJob) -> AsyncIterator[dict[str, Any]]:
        """Yield progress dicts until the pull completes or fails.

        Each dict is SSE-ready: ``{"event": "progress"|"complete"|"error", "data": ...}``.
        """
        job._subscribers += 1
        try:
            last_snapshot: tuple[float, int, int, int, int] | None = None
            last_emit_at = 0.0
            emit_interval_sec = 5.0
            while True:
                # Wait for a progress notification or check periodically
                try:
                    await asyncio.wait_for(
                        asyncio.shield(job._event.wait()),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    pass
                job._event.clear()

                if job.state == PullState.PULLING:
                    pct = job.percent
                    layers_done = sum(
                        1
                        for l in job.layers.values()
                        if l.status in ("Pull complete", "Already exists")
                    )
                    snapshot = (
                        pct,
                        job.overall_current,
                        job.overall_total,
                        layers_done,
                        len(job.layers),
                    )
                    now = time.time()
                    # Emit when metrics change OR as periodic heartbeat so the UI
                    # does not appear stalled while Docker reports static progress.
                    if snapshot != last_snapshot or (now - last_emit_at) >= emit_interval_sec:
                        last_snapshot = snapshot
                        last_emit_at = now
                        yield {
                            "event": "progress",
                            "data": {
                                "pull_id": job.pull_id,
                                "image": job.image_ref,
                                "state": job.state.value,
                                "percent": pct,
                                "current_bytes": job.overall_current,
                                "total_bytes": job.overall_total,
                                "layers_done": layers_done,
                                "layers_total": len(job.layers),
                            },
                        }
                elif job.state == PullState.COMPLETE:
                    yield {
                        "event": "complete",
                        "data": {
                            "pull_id": job.pull_id,
                            "image": job.image_ref,
                            "state": "complete",
                        },
                    }
                    return
                elif job.state == PullState.FAILED:
                    yield {
                        "event": "error",
                        "data": {
                            "pull_id": job.pull_id,
                            "image": job.image_ref,
                            "state": "failed",
                            "error": job.error or "Unknown error",
                        },
                    }
                    return
        finally:
            job._subscribers -= 1

    # ── Internal ──────────────────────────────────────────────────────

    async def _run_pull(self, docker: DockerService, job: PullJob) -> None:
        """Execute the pull with streaming progress updates."""
        try:
            logger.info("Image pull started for %s", job.image_ref)
            # aiodocker stream=True returns JSON chunks
            async for chunk in docker.client.images.pull(
                from_image=job.image,
                tag=job.tag,
                stream=True,
            ):
                layer_id = chunk.get("id", "")
                status = chunk.get("status", "")
                detail = chunk.get("progressDetail") or {}

                if layer_id:
                    if layer_id not in job.layers:
                        job.layers[layer_id] = LayerProgress(id=layer_id)
                    layer = job.layers[layer_id]
                    layer.status = status
                    layer.current = detail.get("current", layer.current)
                    layer.total = detail.get("total", layer.total)
                job.last_update_at = time.time()

                # Check for error in the chunk
                if "error" in chunk:
                    raise RuntimeError(chunk["error"])

                # Notify subscribers
                job._event.set()

            job.state = PullState.COMPLETE
            job.finished_at = time.time()
            logger.info(
                "Image pull completed for %s in %.1fs (layers=%d)",
                job.image_ref,
                job.finished_at - job.started_at,
                len(job.layers),
            )
        except Exception as exc:
            job.state = PullState.FAILED
            job.error = str(exc)
            job.finished_at = time.time()
            logger.warning("Image pull failed for %s: %s", job.image_ref, exc)
        finally:
            job._event.set()
            # Schedule cleanup
            asyncio.create_task(self._cleanup_after(job))

    async def _cleanup_after(self, job: PullJob) -> None:
        """Remove finished job from tracking after TTL."""
        await asyncio.sleep(_COMPLETED_TTL_SEC)
        async with self._lock:
            if self._active.get(job.image_ref) == job.pull_id:
                del self._active[job.image_ref]
            self._jobs.pop(job.pull_id, None)


# Module-level singleton
pull_tracker = PullTracker()
