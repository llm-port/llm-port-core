"""Stream a container image from the backend and load via the container runtime.

Used by air-gapped node agents that cannot pull images from a registry.
The backend exposes ``GET /api/node-files/images/save?image=...&tag=...``
which streams the output of ``docker save`` as a tar archive.  This
module pipes that stream directly into ``<runtime> load`` on the node.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

from llm_port_node_agent.runtimes import ContainerRuntime, ContainerRuntimeError

log = logging.getLogger(__name__)


class ImageLoaderError(RuntimeError):
    """Raised when an image transfer/load operation fails."""


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
) -> None:
    """Download image tarball from the backend and pipe into the container runtime.

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

    log.info("Streaming image %s:%s from backend via %s", image_name, image_tag, url)

    if runtime is not None:
        # ── New path: use the runtime abstraction ─────────────
        try:
            async with client.stream(
                "GET",
                url,
                params={"image": image_name, "tag": image_tag},
                headers=headers,
                timeout=httpx.Timeout(connect=30.0, read=3600.0, write=30.0, pool=30.0),
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    raise ImageLoaderError(
                        f"Backend returned {response.status_code}: {body.decode('utf-8', 'replace')[:500]}"
                    )
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
            params={"image": image_name, "tag": image_tag},
            headers=headers,
            timeout=httpx.Timeout(connect=30.0, read=3600.0, write=30.0, pool=30.0),
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                raise ImageLoaderError(
                    f"Backend returned {response.status_code}: {body.decode('utf-8', 'replace')[:500]}"
                )
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
