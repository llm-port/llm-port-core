"""Stream a container image from the backend and load it via ``docker load``.

Used by air-gapped node agents that cannot pull images from a registry.
The backend exposes ``GET /api/node-files/images/save?image=...&tag=...``
which streams the output of ``docker save`` as a tar archive.  This
module pipes that stream directly into ``docker load`` on the node.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


class ImageLoaderError(RuntimeError):
    """Raised when an image transfer/load operation fails."""


async def load_image_from_backend(
    *,
    client: httpx.AsyncClient,
    credential: str,
    image: str,
) -> None:
    """Download image tarball from the backend and pipe into ``docker load``.

    Parameters
    ----------
    client:
        httpx AsyncClient with base_url pointing to the backend.
    credential:
        Node bearer credential for authentication.
    image:
        Full image reference (e.g. ``vllm/vllm-openai:latest``).
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

    # Start docker load process — it reads tar from stdin
    proc = await asyncio.create_subprocess_exec(
        "docker", "load",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None  # noqa: S101

    total_bytes = 0
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
            async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                proc.stdin.write(chunk)
                await proc.stdin.drain()
                total_bytes += len(chunk)
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
