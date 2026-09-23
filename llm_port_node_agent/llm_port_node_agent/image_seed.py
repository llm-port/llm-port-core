"""Handing the runtime image to a cluster peer, machine to machine.

Every member used to pull the ~12 GB runtime image from the LLM.Port server,
over whatever link the server has -- on the DGX pair a 1 Gb/s management
network, shared by both machines at once, while the two sat next to each other
on a 200 Gb/s RoCE fabric. A machine that already holds the image can serve it
to its peer over that fabric in a fraction of the time.

The serving side is deliberately small. It binds only to the address the
backend names (the fabric address), answers only a request carrying the
one-off token the backend handed both ends over their authenticated command
streams, serves exactly one file -- an export of the pinned image -- supports
``Range`` so the fetching side can resume, and exits once the expected peers
have the whole file or its time is up. Nothing it serves is trusted blindly:
the fetching side loads the tar and then checks it against the same pin as any
other copy, so a wrong image from a peer is refused like one from anywhere.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from llm_port_node_agent.image_loader import (
    ImageLoaderError,
    _download_resumable,
    _file_chunks,
    _human_size,
)

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024


class SeedError(RuntimeError):
    """Serving the image to a peer did not work."""

    error_code = "image_seed_failed"


async def _export(image: str, destination: Path) -> None:
    """``docker save -o``: the file a peer downloads, with a length and offsets."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "save", "-o", str(destination), image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        destination.unlink(missing_ok=True)
        raise SeedError(
            f"could not export {image} for a peer: {err.decode('utf-8', 'replace').strip()[:300]}"
        )


def _parse_range(value: str | None, size: int) -> tuple[int, int] | None:
    """``bytes=N-`` or ``bytes=N-M`` -> inclusive (start, end); None if absent."""
    if not value or not value.startswith("bytes="):
        return None
    first, _, last = value[6:].partition("-")
    start = int(first) if first.strip().isdigit() else 0
    end = int(last) if last.strip().isdigit() else size - 1
    return start, min(end, size - 1)


class _SeedServer:
    """One file, one token, a few peers, then gone."""

    def __init__(self, *, path: Path, token: str, expected_peers: int) -> None:
        self._path = path
        self._token = token
        self._size = path.stat().st_size
        self._remaining = max(1, expected_peers)
        self.completed = 0
        self.bytes_sent = 0
        self.finished = asyncio.Event()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            writer.close()
            return
        lines = request.decode("latin-1").split("\r\n")
        method, path, *_ = (lines[0].split(" ") + ["", ""])[:3]
        headers = {}
        for line in lines[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()

        async def reply(status: str, extra: dict[str, str] | None = None, body: bytes = b"") -> None:
            head = [f"HTTP/1.1 {status}", f"Content-Length: {len(body)}", "Connection: close"]
            head += [f"{k}: {v}" for k, v in (extra or {}).items()]
            writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body)
            await writer.drain()
            writer.close()

        presented = headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(presented.encode(), self._token.encode()):
            await reply("401 Unauthorized")
            return
        if method != "GET" or path != "/image":
            await reply("404 Not Found")
            return

        span = _parse_range(headers.get("range"), self._size)
        start, end = span if span is not None else (0, self._size - 1)
        if start >= self._size:
            await reply("416 Range Not Satisfiable", {"Content-Range": f"bytes */{self._size}"})
            return

        length = end - start + 1
        status = "206 Partial Content" if span is not None else "200 OK"
        head = [
            f"HTTP/1.1 {status}",
            f"Content-Length: {length}",
            "Content-Type: application/x-tar",
            "Accept-Ranges: bytes",
            "Connection: close",
        ]
        if span is not None:
            head.append(f"Content-Range: bytes {start}-{end}/{self._size}")
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))

        sent = 0
        try:
            with self._path.open("rb") as handle:
                handle.seek(start)
                while sent < length:
                    chunk = await asyncio.to_thread(handle.read, min(_CHUNK, length - sent))
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    sent += len(chunk)
        except (ConnectionError, OSError):
            return  # the peer resumes from what it has
        finally:
            self.bytes_sent += sent
            writer.close()

        if end == self._size - 1 and sent == length:
            # This peer now has the tail of the file: count it as served.
            self.completed += 1
            if self.completed >= self._remaining:
                self.finished.set()


async def serve_image(
    *,
    image: str,
    bind_ip: str,
    port: int,
    token: str,
    expected_peers: int,
    timeout_sec: float,
    work_dir: Path,
    emit_progress: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Export *image* and serve it to ``expected_peers`` peers, then stop."""
    work_dir.mkdir(parents=True, exist_ok=True)
    tar = work_dir / (image.replace("/", "_").replace(":", "_") + ".seed.tar")
    if emit_progress is not None:
        await emit_progress({"phase": "seeding", "message": f"Preparing {image} for a peer"})
    await _export(image, tar)

    seed = _SeedServer(path=tar, token=token, expected_peers=expected_peers)
    server = await asyncio.start_server(seed.handle, host=bind_ip, port=port)
    log.info(
        "Serving %s (%s) to %d peer(s) on %s:%d",
        image, _human_size(tar.stat().st_size), expected_peers, bind_ip, port,
    )
    if emit_progress is not None:
        await emit_progress({
            "phase": "seeding",
            "message": f"Serving {image} to {expected_peers} peer(s) on {bind_ip}:{port}",
        })
    started = time.monotonic()
    try:
        await asyncio.wait_for(seed.finished.wait(), timeout=timeout_sec)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        server.close()
        await server.wait_closed()
        tar.unlink(missing_ok=True)

    result = {
        "served": seed.completed,
        "expected": expected_peers,
        "bytes_sent": seed.bytes_sent,
        "seconds": round(time.monotonic() - started, 1),
        "timed_out": timed_out,
    }
    log.info("Stopped serving %s: %s", image, result)
    return result


async def fetch_from_peer(
    *,
    url: str,
    token: str,
    image: str,
    runtime: Any,
    download_dir: Path,
    emit_progress: Callable[[dict[str, Any]], Any] | None = None,
) -> None:
    """Download the image from a peer (resumably) and load it.

    The peer may still be exporting when this starts, so a refused connection
    is expected at first and retried; ``_download_resumable`` backs off
    between attempts.
    """
    base, _, path = url.partition("//")[2].partition("/")
    base_url = url[: url.index(base) + len(base)]
    async with httpx.AsyncClient(base_url=base_url) as client:
        part = await _download_resumable(
            client=client,
            url="/" + path,
            params={},
            headers={"Authorization": f"Bearer {token}"},
            directory=download_dir,
            image=image,
            emit_progress=emit_progress,
            attempts=30,
        )
    if part is None:
        raise ImageLoaderError(f"no room in {download_dir} to receive {image} from a peer")
    try:
        if emit_progress is not None:
            await emit_progress({
                "phase": "loading_image",
                "message": f"Loading {image} received from a peer",
                "progress_pct": None,
            })
        await runtime.load_image_tar(_file_chunks(part))
    finally:
        part.unlink(missing_ok=True)
