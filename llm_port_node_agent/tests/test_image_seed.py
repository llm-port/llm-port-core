"""A machine hands the runtime image to its cluster peer.

Every member used to pull ~12 GB from the LLM.Port server over its management
link -- on the DGX pair, both machines sharing 1 Gb/s while a 200 Gb/s fabric
sat idle between them. These run the real seed server on loopback and fetch
from it with the real resumable client.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_port_node_agent import image_loader, image_seed

IMAGE = "llmport/ray-vllm-gb10:ray2.58-nv26.08"
BLOB = bytes(range(256)) * 8192  # 2 MiB


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(autouse=True)
def _fake_export(monkeypatch: pytest.MonkeyPatch) -> None:
    async def export(_image: str, destination: Path) -> None:
        destination.write_bytes(BLOB)

    monkeypatch.setattr(image_seed, "_export", export)

    class _Plenty:
        free = 10**15

    monkeypatch.setattr(image_loader.shutil, "disk_usage", lambda _p: _Plenty())


async def _serve(tmp_path: Path, port: int, **kw: Any) -> asyncio.Task:
    task = asyncio.create_task(
        image_seed.serve_image(
            image=IMAGE,
            bind_ip="127.0.0.1",
            port=port,
            token=kw.get("token", "s3cret"),
            expected_peers=kw.get("expected_peers", 1),
            timeout_sec=kw.get("timeout_sec", 30),
            work_dir=tmp_path / "seed",
        )
    )
    for _ in range(100):  # until it listens
        await asyncio.sleep(0.02)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return task
        except OSError:
            continue
    raise AssertionError("seed server never listened")


async def _download(tmp_path: Path, port: int, token: str = "s3cret") -> Path | None:
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        return await image_loader._download_resumable(
            client=client,
            url="/image",
            params={},
            headers={"Authorization": f"Bearer {token}"},
            directory=tmp_path / "recv",
            image=IMAGE,
            emit_progress=None,
            attempts=2,
        )


@pytest.mark.asyncio
async def test_a_peer_receives_the_exact_bytes_and_the_server_stops(tmp_path: Path) -> None:
    port = _free_port()
    serving = await _serve(tmp_path, port)

    part = await _download(tmp_path, port)
    result = await asyncio.wait_for(serving, timeout=10)

    assert part is not None and part.read_bytes() == BLOB
    assert result["served"] == 1
    assert result["timed_out"] is False
    assert not (tmp_path / "seed").exists() or not list((tmp_path / "seed").iterdir()), (
        "the export is removed once served"
    )


@pytest.mark.asyncio
async def test_a_request_without_the_token_gets_nothing(tmp_path: Path) -> None:
    port = _free_port()
    serving = await _serve(tmp_path, port, timeout_sec=2)

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        wrong = await client.get("/image", headers={"Authorization": "Bearer guess"})
        missing = await client.get("/image")

    assert wrong.status_code == 401
    assert missing.status_code == 401
    result = await serving
    assert result["served"] == 0 and result["bytes_sent"] == 0


@pytest.mark.asyncio
async def test_only_the_image_is_served(tmp_path: Path) -> None:
    port = _free_port()
    serving = await _serve(tmp_path, port, timeout_sec=2)

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
        other = await client.get("/etc/passwd", headers={"Authorization": "Bearer s3cret"})

    assert other.status_code == 404
    await serving


@pytest.mark.asyncio
async def test_a_partial_download_resumes_from_the_peer(tmp_path: Path) -> None:
    port = _free_port()
    serving = await _serve(tmp_path, port)
    recv = tmp_path / "recv"
    recv.mkdir(parents=True)
    (recv / "llmport_ray-vllm-gb10_ray2.58-nv26.08.tar.part").write_bytes(BLOB[:700_000])

    part = await _download(tmp_path, port)
    result = await asyncio.wait_for(serving, timeout=10)

    assert part is not None and part.read_bytes() == BLOB
    assert result["bytes_sent"] == len(BLOB) - 700_000, "only the rest was sent"


@pytest.mark.asyncio
async def test_waits_for_every_expected_peer(tmp_path: Path) -> None:
    port = _free_port()
    serving = await _serve(tmp_path, port, expected_peers=2)

    first = await _download(tmp_path / "a", port)
    await asyncio.sleep(0.1)
    assert not serving.done(), "one of two peers is not everyone"
    second = await _download(tmp_path / "b", port)
    result = await asyncio.wait_for(serving, timeout=10)

    assert first is not None and second is not None
    assert result["served"] == 2


@pytest.mark.asyncio
async def test_gives_up_after_its_time_and_cleans_up(tmp_path: Path) -> None:
    port = _free_port()
    serving = await _serve(tmp_path, port, timeout_sec=0.5)

    result = await asyncio.wait_for(serving, timeout=10)

    assert result["timed_out"] is True
    assert not list((tmp_path / "seed").glob("*.tar"))


def test_range_parsing() -> None:
    assert image_seed._parse_range(None, 100) is None
    assert image_seed._parse_range("bytes=10-", 100) == (10, 99)
    assert image_seed._parse_range("bytes=10-19", 100) == (10, 19)
    assert image_seed._parse_range("bytes=10-500", 100) == (10, 99)
