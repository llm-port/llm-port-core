"""Stream a container image from the backend and load via the container runtime.

Used by air-gapped node agents that cannot pull images from a registry.
The backend exposes ``GET /api/node-files/images/save?image=...&tag=...``
which streams the output of ``docker save`` as a tar archive.  This
module pipes that stream directly into ``<runtime> load`` on the node.
"""

from __future__ import annotations

import asyncio
import logging
import json
import shutil
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx

from llm_port_node_agent.runtimes import ContainerRuntime, ContainerRuntimeError

log = logging.getLogger(__name__)


class ImageLoaderError(RuntimeError):
    """Raised when an image transfer/load operation fails.

    ``error_code`` reaches the backend through the dispatcher, so it can tell
    a transfer worth retrying from a server that holds the wrong build.
    """

    def __init__(self, message: str, *, code: str = "image_transfer_failed") -> None:
        super().__init__(message)
        self.error_code = code


def _human_size(nbytes: int) -> str:
    """Return human-readable size string (e.g. ``1.23 GiB``)."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(nbytes) < 1024 or unit == "GiB":
            return f"{nbytes:.1f} {unit}" if unit != "B" else f"{nbytes} B"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} GiB"


_PROGRESS_INTERVAL_SEC = 5  # emit progress updates at most every 5 seconds


async def _progress_iter(
    stream: AsyncIterator[bytes],
    *,
    emit_progress: Callable[[dict[str, Any]], Any] | None,
    content_length: int | None,
    image: str,
) -> AsyncIterator[bytes]:
    """Wrap an async byte stream and emit periodic progress events."""
    total = 0
    last_emit = time.monotonic()
    async for chunk in stream:
        total += len(chunk)
        yield chunk
        now = time.monotonic()
        if emit_progress is not None and (now - last_emit) >= _PROGRESS_INTERVAL_SEC:
            last_emit = now
            if content_length and content_length > 0:
                pct = min(int(total / content_length * 100), 99)
                msg = f"Image {image}: {_human_size(total)} / {_human_size(content_length)} ({pct}%)"
            else:
                msg = f"Image {image}: {_human_size(total)} transferred"
            try:
                await emit_progress({"message": msg, "progress_pct": pct if content_length else None})
            except Exception:
                pass  # best-effort


async def load_image_from_backend(
    *,
    client: httpx.AsyncClient,
    credential: str,
    image: str,
    runtime: ContainerRuntime | None = None,
    emit_progress: Callable[[dict[str, Any]], Any] | None = None,
    expect_id: str | None = None,
    expect_rootfs: str | None = None,
    download_dir: Path | None = None,
) -> None:
    """Download image tarball from the backend and pipe into the container runtime.

    With a ``download_dir`` the tar lands on disk first and a dropped
    connection resumes from where it stopped; without one, or without room,
    it is piped straight into the runtime as before. ``expect_id`` and
    ``expect_rootfs`` are the pin, passed so a server holding a different
    build refuses up front instead of after the transfer.

    Parameters
    ----------
    client:
        httpx AsyncClient with base_url pointing to the backend.
    credential:
        Node bearer credential for authentication.
    image:
        Full image reference (e.g. ``vllm/vllm-openai:latest``).
    runtime:
        Container runtime to use for loading.  If ``None``, falls back
        to a direct ``docker load`` subprocess for backward compatibility.
    emit_progress:
        Optional callback to report transfer progress.
    """
    # Split image:tag for the query parameters
    last_colon = image.rfind(":")
    if last_colon > 0 and "/" not in image[last_colon:]:
        image_name = image[:last_colon]
        image_tag = image[last_colon + 1:]
    else:
        image_name = image
        image_tag = "latest"

    headers = {"Authorization": f"Bearer {credential}"}
    url = "/api/node-files/images/save"
    params = {"image": image_name, "tag": image_tag}
    if expect_id:
        params["expect_id"] = expect_id
    if expect_rootfs:
        params["expect_rootfs"] = expect_rootfs

    if runtime is not None and download_dir is not None:
        part = await _download_resumable(
            client=client,
            url=url,
            params=params,
            headers=headers,
            directory=download_dir,
            image=image,
            emit_progress=emit_progress,
        )
        if part is not None:
            try:
                if emit_progress is not None:
                    await _emit(emit_progress, f"Loading {image} into the container runtime", None)
                await runtime.load_image_tar(_file_chunks(part))
            finally:
                part.unlink(missing_ok=True)
            log.info("Image %s loaded from a resumable download", image)
            return
        # No room for a copy on disk: fall through to streaming.

    log.info("Streaming image %s:%s from backend via %s", image_name, image_tag, url)

    if runtime is not None:
        # ── New path: use the runtime abstraction ─────────────
        try:
            async with client.stream(
                "GET",
                url,
                params=params,
                headers=headers,
                timeout=httpx.Timeout(connect=30.0, read=3600.0, write=30.0, pool=30.0),
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise _backend_error(response.status_code, body)
                content_length = response.headers.get("content-length")
                cl = int(content_length) if content_length else None
                if not cl:
                    # Backend sends compressed image size via X-Image-Size
                    img_size = response.headers.get("x-image-size")
                    cl = int(img_size) if img_size else None
                stream = _progress_iter(
                    response.aiter_bytes(chunk_size=256 * 1024),
                    emit_progress=emit_progress,
                    content_length=cl,
                    image=image,
                )
                await runtime.load_image_tar(stream)
        except (ImageLoaderError, ContainerRuntimeError):
            raise
        except Exception as exc:
            raise ImageLoaderError(f"Failed to stream image from backend: {exc}") from exc

        log.info("Image %s loaded successfully via %s runtime", image, runtime.name)
        return

    # ── Legacy fallback: direct docker subprocess ─────────────
    proc = await asyncio.create_subprocess_exec(
        "docker", "load",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None  # noqa: S101

    total_bytes = 0
    last_emit = time.monotonic()
    try:
        async with client.stream(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout=httpx.Timeout(connect=30.0, read=3600.0, write=30.0, pool=30.0),
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise _backend_error(response.status_code, body)
            content_length = response.headers.get("content-length")
            cl = int(content_length) if content_length else None
            if not cl:
                img_size = response.headers.get("x-image-size")
                cl = int(img_size) if img_size else None
            async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                proc.stdin.write(chunk)
                await proc.stdin.drain()
                total_bytes += len(chunk)
                now = time.monotonic()
                if emit_progress is not None and (now - last_emit) >= _PROGRESS_INTERVAL_SEC:
                    last_emit = now
                    if cl and cl > 0:
                        pct = min(int(total_bytes / cl * 100), 99)
                        msg = f"Image {image}: {_human_size(total_bytes)} / {_human_size(cl)} ({pct}%)"
                    else:
                        msg = f"Image {image}: {_human_size(total_bytes)} transferred"
                    try:
                        await emit_progress({"message": msg, "progress_pct": pct if cl else None})
                    except Exception:
                        pass
    except ImageLoaderError:
        raise
    except Exception as exc:
        raise ImageLoaderError(f"Failed to stream image from backend: {exc}") from exc
    finally:
        proc.stdin.close()
        await proc.stdin.wait_closed()

    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", "replace").strip()
        raise ImageLoaderError(f"docker load failed (rc={proc.returncode}): {err_msg}")

    log.info(
        "Image %s loaded successfully (%d bytes transferred): %s",
        image,
        total_bytes,
        stdout.decode("utf-8", "replace").strip(),
    )


# -- resumable path -----------------------------------------------------------

#: How many times a dropped transfer is resumed before giving up.
_RESUME_ATTEMPTS = 8

#: Room to leave on the disk beyond the tar itself.
_HEADROOM_BYTES = 5 * 1024**3


def _backend_error(status_code: int, body: bytes) -> ImageLoaderError:
    text = body.decode("utf-8", "replace")
    try:
        detail = json.loads(text).get("detail") or text
    except (ValueError, AttributeError):
        detail = text
    if status_code == 409:
        # The server holds a different build than the pin. Retrying asks the
        # same server the same question; say that plainly.
        return ImageLoaderError(str(detail)[:600], code="server_image_mismatch")
    return ImageLoaderError(f"Backend returned {status_code}: {str(detail)[:500]}")


async def _emit(
    emit_progress: Callable[[dict[str, Any]], Any], message: str, pct: int | None
) -> None:
    try:
        await emit_progress({"phase": "loading_image", "message": message, "progress_pct": pct})
    except Exception:  # noqa: BLE001 - progress is best-effort
        pass


def _total_from(response: httpx.Response, have: int) -> int | None:
    """The full size of the tar, from whichever header carries it."""
    content_range = response.headers.get("content-range", "")
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    length = response.headers.get("content-length")
    if length and length.isdigit():
        return int(length) + (have if response.status_code == 206 else 0)
    return None


def _text_bar(pct: int, width: int = 24) -> str:
    """``[#########...............]`` -- the log's version of the console bar."""
    filled = max(0, min(width, round(width * pct / 100)))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _describe(image: str, have: int, total: int | None, rate: float) -> tuple[str, int | None]:
    """A progress line an operator can plan around: how much, how fast, how long."""
    if not total:
        return f"Receiving {image}: {_human_size(have)}", None
    pct = min(int(have / total * 100), 99)
    left = ""
    if rate > 0:
        minutes = (total - have) / rate / 60
        left = f", about {max(1, round(minutes))} min left" if minutes >= 1 else ", under a minute left"
    return (
        f"Receiving {image}: {_human_size(have)} of {_human_size(total)} ({pct}%) "
        f"at {_human_size(int(rate))}/s{left}",
        pct,
    )


async def _download_resumable(
    *,
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
    directory: Path,
    image: str,
    emit_progress: Callable[[dict[str, Any]], Any] | None,
    attempts: int = _RESUME_ATTEMPTS,
) -> Path | None:
    """Fetch the tar to disk, resuming after a dropped connection.

    Returns the finished file, or ``None`` when there is no room to keep a
    copy (the caller then streams, which cannot resume). The partial file is
    named after the image, so a later attempt -- even after an agent restart
    -- picks up where this one stopped instead of starting over.
    """
    directory.mkdir(parents=True, exist_ok=True)
    safe = image.replace("/", "_").replace(":", "_")
    part = directory / f"{safe}.tar.part"

    attempt = 0
    total: int | None = None
    while True:
        have = part.stat().st_size if part.exists() else 0
        request_headers = dict(headers)
        if have:
            request_headers["Range"] = f"bytes={have}-"
        try:
            async with client.stream(
                "GET",
                url,
                params=params,
                headers=request_headers,
                timeout=httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0),
            ) as response:
                if response.status_code == 416:
                    # Asked to start past the end: complete if the length is
                    # the one the server reports, otherwise start over.
                    whole = _total_from(response, 0)
                    if whole is not None and have == whole:
                        return part
                    part.unlink(missing_ok=True)
                    continue
                if response.status_code not in (200, 206):
                    raise _backend_error(response.status_code, await response.aread())
                if response.status_code == 200 and have:
                    # The server sent the whole thing rather than the rest.
                    have = 0
                total = _total_from(response, have)

                if total is not None and have == 0:
                    free = shutil.disk_usage(directory).free
                    if free < total + _HEADROOM_BYTES:
                        log.warning(
                            "Only %s free in %s for a %s image; streaming without resume",
                            _human_size(free), directory, _human_size(total),
                        )
                        part.unlink(missing_ok=True)
                        return None

                started = time.monotonic()
                received = 0
                last_emit = 0.0
                # The agent's own log -- its terminal, or journalctl -- showed
                # nothing at all for the eleven minutes a runtime image takes.
                # One line per tenth, so it reads as a bar filling up.
                logged_tenth = (have * 10 // total) if total else -1
                with part.open("ab" if have else "wb") as handle:
                    # Unchunked on purpose: re-chunking to a fixed size holds
                    # bytes back until a chunk fills, and a drop then loses
                    # them -- the resume would ask for data we had received.
                    async for chunk in response.aiter_bytes():
                        handle.write(chunk)
                        have += len(chunk)
                        received += len(chunk)
                        now = time.monotonic()
                        if total and have * 10 // total > logged_tenth:
                            logged_tenth = have * 10 // total
                            rate = received / max(now - started, 0.001)
                            message, pct = _describe(image, have, total, rate)
                            log.info("%s %s", _text_bar(pct or 0), message)
                        if emit_progress is not None and now - last_emit >= _PROGRESS_INTERVAL_SEC:
                            last_emit = now
                            rate = received / max(now - started, 0.001)
                            message, pct = _describe(image, have, total, rate)
                            await _emit(emit_progress, message, pct)

            if total is None or have >= total:
                return part
            raise httpx.ReadError(f"connection closed at {have} of {total} bytes")
        except ImageLoaderError:
            raise
        except (httpx.TransportError, httpx.StreamError) as exc:
            attempt += 1
            if attempt > attempts:
                raise ImageLoaderError(
                    f"Gave up on {image} after {attempts} interrupted attempts "
                    f"({_human_size(have)} received, kept for next time): {exc}"
                ) from exc
            delay = min(2**attempt, 30)
            log.warning(
                "Transfer of %s interrupted at %s (%s); resuming in %ss",
                image, _human_size(have), exc, delay,
            )
            if emit_progress is not None:
                await _emit(
                    emit_progress,
                    f"Connection dropped at {_human_size(have)}; resuming "
                    f"(attempt {attempt} of {attempts})",
                    None,
                )
            await asyncio.sleep(delay)


async def _file_chunks(path: Path, size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    """Read a file as an async byte stream without blocking the event loop."""
    with path.open("rb") as handle:
        while True:
            chunk = await asyncio.to_thread(handle.read, size)
            if not chunk:
                return
            yield chunk
