"""The runtime image, exported once and served as a file.

A live ``docker save`` has no length and cannot be resumed, so a 12 GB
transfer that dropped near the end started again from nothing, and progress
measured against the image's unpacked size stopped near a third. And the
server shipped whatever its tag pointed at -- only for the node to refuse it,
after the last byte, as the wrong build.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from llm_port_backend.services.inference import image_cache
from llm_port_backend.services.inference.bundles import compute_rootfs_digest

LAYERS = ["sha256:" + "a" * 64, "sha256:" + "b" * 64]


def _info(image_id: str = "sha256:pinned", layers: list[str] | None = None) -> dict[str, Any]:
    return {"Id": image_id, "RootFS": {"Layers": LAYERS if layers is None else layers}}


class TestTheRightBuild:
    def test_passes_when_the_id_matches(self) -> None:
        image_cache.verify_expected(_info(), expect_id="sha256:pinned", expect_rootfs=None)

    def test_passes_on_content_even_under_a_rewritten_config(self) -> None:
        """A side-loaded copy keeps its layers but may get a new config id."""
        image_cache.verify_expected(
            _info(image_id="sha256:rewritten"),
            expect_id="sha256:pinned",
            expect_rootfs=compute_rootfs_digest(LAYERS),
        )

    def test_refuses_a_different_build_and_says_retrying_will_not_help(self) -> None:
        with pytest.raises(image_cache.ImageMismatch) as caught:
            image_cache.verify_expected(
                _info(image_id="sha256:old", layers=["sha256:" + "c" * 64]),
                expect_id="sha256:pinned",
                expect_rootfs=compute_rootfs_digest(LAYERS),
            )
        message = str(caught.value)
        assert "different build" in message
        assert "retrying will not change this" in message

    def test_nothing_to_compare_against_is_not_a_refusal(self) -> None:
        """Older agents send no pin; they keep working."""
        image_cache.verify_expected(_info(image_id="sha256:any"), expect_id=None, expect_rootfs=None)


class _Stream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _Export:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _Stream:
        return _Stream(self._chunks)

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _Docker:
    def __init__(self, chunks: list[bytes]) -> None:
        self.exports = 0
        chunks_ref = chunks
        outer = self

        class _Images:
            def export_image(self, _ref: str) -> _Export:
                outer.exports += 1
                return _Export(list(chunks_ref))

        class _Client:
            images = _Images()

        self.client = _Client()


@pytest.fixture
def cache_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(image_cache.settings, "image_cache_dir", str(tmp_path))

    class _Plenty:
        free = 10**15

    monkeypatch.setattr(image_cache.shutil, "disk_usage", lambda _p: _Plenty())
    return tmp_path


@pytest.mark.anyio()
async def test_exports_once_and_reuses_it(cache_in: Path) -> None:
    docker = _Docker([b"layer-one", b"layer-two"])

    first = await image_cache.ensure_export(docker, "img:tag", "sha256:abc", 10)
    second = await image_cache.ensure_export(docker, "img:tag", "sha256:abc", 10)

    assert first == second
    assert first is not None and first.read_bytes() == b"layer-onelayer-two"
    assert docker.exports == 1, "a finished export is reused, not rebuilt"


@pytest.mark.anyio()
async def test_no_room_means_no_export_rather_than_a_full_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image_cache.settings, "image_cache_dir", str(tmp_path))

    class _Tiny:
        free = 1024

    monkeypatch.setattr(image_cache.shutil, "disk_usage", lambda _p: _Tiny())

    assert await image_cache.ensure_export(_Docker([b"x"]), "img:tag", "sha256:abc", 10**9) is None
    assert not list(tmp_path.iterdir())


@pytest.mark.anyio()
async def test_a_failed_export_leaves_nothing_half_written(cache_in: Path) -> None:
    class _Broken(_Docker):
        def __init__(self) -> None:
            super().__init__([])

            class _Images:
                def export_image(self, _ref: str) -> Any:
                    raise RuntimeError("daemon went away")

            class _Client:
                images = _Images()

            self.client = _Client()

    with pytest.raises(RuntimeError):
        await image_cache.ensure_export(_Broken(), "img:tag", "sha256:abc", 10)
    assert not list(cache_in.iterdir())


class TestPruning:
    def test_keeps_the_current_export_and_removes_superseded_ones(self, cache_in: Path) -> None:
        (cache_in / "current.tar").write_bytes(b"x")
        (cache_in / "old-build.tar").write_bytes(b"x")

        removed = image_cache.prune({"sha256:current"})

        assert removed == 1
        assert (cache_in / "current.tar").exists()
        assert not (cache_in / "old-build.tar").exists()

    def test_never_deletes_an_export_being_written(self, cache_in: Path) -> None:
        in_flight = cache_in / "current.1234.partial"
        in_flight.write_bytes(b"x")

        image_cache.prune(set())

        assert in_flight.exists()

    def test_clears_an_export_abandoned_by_a_crash(self, cache_in: Path) -> None:
        stale = cache_in / "gone.5678.partial"
        stale.write_bytes(b"x")
        long_ago = time.time() - 7 * 3600
        os.utime(stale, (long_ago, long_ago))

        image_cache.prune(set())

        assert not stale.exists()
