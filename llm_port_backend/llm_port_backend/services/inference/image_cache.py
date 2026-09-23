"""Runtime images, exported once and served as files.

The node-facing image endpoint used to stream ``docker save`` live on every
request. That made two things impossible.

**Resuming.** A live export has no byte offsets to resume from, so a transfer
that dropped at 11 GB of 12 started again from nothing -- on the one step of
onboarding that is measured in tens of minutes, over whatever link the site
has.

**Knowing the size.** The only size available up front was the image's
*unpacked* size, about three times what actually crosses the wire, so progress
computed against it stopped near a third and stayed there.

Exporting once to a file fixes both: a file has a length, and a file can be
served with ``Range``. Files are keyed by the image's config id, which a
rebuild changes, so a stale export can never be served under a new build.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from llm_port_backend.services.inference.bundles import compute_rootfs_digest
from llm_port_backend.settings import TEMP_DIR, settings

log = logging.getLogger(__name__)

#: One export per image at a time; a second caller waits for the first.
_locks: dict[str, asyncio.Lock] = {}

#: Export only with this much room to spare beyond the image itself. Filling
#: the disk that holds the database to make a transfer resumable is not a
#: trade anyone would choose.
_HEADROOM_BYTES = 20 * 1024**3


class ImageMismatch(Exception):
    """This server holds a different build than the one asked for."""


def cache_dir() -> Path:
    """Where exported images live."""
    configured = getattr(settings, "image_cache_dir", "") or ""
    return Path(configured) if configured else TEMP_DIR / "llmport-image-cache"


def _key(image_id: str) -> str:
    return image_id.replace("sha256:", "").strip()


def cached_path(image_id: str) -> Path:
    """Where the export of *image_id* lives, whether or not it exists yet."""
    return cache_dir() / f"{_key(image_id)}.tar"


def cached(image_id: str) -> Path | None:
    """The finished export of *image_id*, if there is one."""
    path = cached_path(image_id)
    return path if path.is_file() else None


def verify_expected(
    info: dict[str, Any],
    *,
    expect_id: str | None,
    expect_rootfs: str | None,
) -> None:
    """Refuse to hand out a build other than the one the node was told to run.

    Without this the server shipped whatever its tag pointed at, the node
    spent eleven minutes receiving it, and only then did the node's own check
    discover it was the wrong build -- and the reconciler asked again, and
    again, 303 times. The answer never changes, so give it before the first
    byte rather than after the last.
    """
    if not (expect_id or expect_rootfs):
        return
    local_id = str(info.get("Id") or "")
    layers = [str(x) for x in ((info.get("RootFS") or {}).get("Layers") or [])]
    local_rootfs = compute_rootfs_digest(layers) if layers else None

    if expect_rootfs and local_rootfs == expect_rootfs:
        return
    if expect_id and local_id == expect_id:
        return
    raise ImageMismatch(
        f"This server holds a different build of the runtime image "
        f"(id {local_id or 'unknown'}) than the node was asked to run "
        f"(id {expect_id or 'unset'}). Rebuild the image, or import the pinned "
        f"build, on the LLM.Port server -- retrying will not change this."
    )


def _free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


async def ensure_export(docker: Any, ref: str, image_id: str, size_hint: int) -> Path | None:
    """Export *ref* to the cache if it is not there already.

    Returns the finished file, or ``None`` when there is not room to keep one,
    in which case the caller streams the export directly and the transfer
    simply is not resumable.
    """
    existing = cached(image_id)
    if existing is not None:
        return existing

    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if _free_bytes(directory) < size_hint + _HEADROOM_BYTES:
        log.warning(
            "Not caching %s: %s has too little free space for a resumable copy",
            ref,
            directory,
        )
        return None

    lock = _locks.setdefault(_key(image_id), asyncio.Lock())
    async with lock:
        existing = cached(image_id)
        if existing is not None:  # another caller finished it while we waited
            return existing

        partial = directory / f"{_key(image_id)}.{uuid.uuid4().hex}.partial"
        log.info("Exporting %s to %s", ref, directory)
        try:
            with partial.open("wb") as handle:
                async with docker.client.images.export_image(ref) as stream:
                    while True:
                        chunk = await stream.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
            os.replace(partial, cached_path(image_id))
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        log.info("Exported %s (%d bytes)", ref, cached_path(image_id).stat().st_size)
        return cached_path(image_id)


#: A partial export older than this is left over from a crash, not in flight.
_ABANDONED_AFTER_SEC = 6 * 3600


def prune(keep: set[str]) -> int:
    """Remove exports of images no longer referenced. Returns how many.

    Each rebuild changes the image id and so leaves the previous export
    behind -- twelve gigabytes a time, which adds up quickly on a server that
    also holds models. A partial export is only removed once it is clearly
    abandoned: one being written right now is not ours to delete.
    """
    import time

    directory = cache_dir()
    if not directory.is_dir():
        return 0
    wanted = {_key(image_id) for image_id in keep}
    now = time.time()
    removed = 0
    for path in directory.iterdir():
        stem = path.name.split(".", 1)[0]
        if path.name.endswith(".partial"):
            try:
                if now - path.stat().st_mtime < _ABANDONED_AFTER_SEC:
                    continue
            except OSError:
                continue
        elif stem in wanted and path.suffix == ".tar":
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
