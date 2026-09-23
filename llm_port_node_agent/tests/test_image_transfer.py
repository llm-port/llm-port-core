"""Fetching the runtime image: resumable, visible, and told which build to want.

What this replaced streamed ``docker save`` straight into ``docker load``. The
first cluster start on a DGX node is a ~12 GB transfer, and it went wrong in
every way a long transfer can:

* a dropped connection restarted it from zero;
* progress was computed against the image's *unpacked* size and was not
  forwarded to anyone anyway, so the cluster page said "Preparing" for eleven
  minutes;
* the server shipped whatever its tag pointed at, and only after the last byte
  did the node discover it was the wrong build -- then the backend asked again,
  303 times.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from llm_port_node_agent import image_loader
from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.image_loader import ImageLoaderError, _describe, _download_resumable

IMAGE = "llmport/ray-vllm-gb10:ray2.58-nv26.08"
BLOB = bytes(range(256)) * 4096  # 1 MiB of recognisable bytes


def _server(*, cut_after: int | None = None, status_override: int | None = None):
    """A backend that serves BLOB with Range support, optionally dropping once."""
    calls: list[dict[str, Any]] = []
    state = {"dropped": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"range": request.headers.get("range"), "params": dict(request.url.params)})
        if status_override is not None:
            return httpx.Response(status_override, json={"detail": "the server holds a different build"})
        start = 0
        if request.headers.get("range"):
            start = int(request.headers["range"].split("=")[1].rstrip("-"))
            if start >= len(BLOB):
                return httpx.Response(416, headers={"content-range": f"bytes */{len(BLOB)}"})
        body = BLOB[start:]
        if cut_after is not None and not state["dropped"]:
            state["dropped"] = True

            async def broken():
                yield body[:cut_after]
                raise httpx.ReadError("connection reset by peer")

            return httpx.Response(
                206 if start else 200,
                headers={
                    "content-length": str(len(body)),
                    **({"content-range": f"bytes {start}-{len(BLOB) - 1}/{len(BLOB)}"} if start else {}),
                },
                content=broken(),
            )
        headers = {"content-length": str(len(body))}
        if start:
            headers["content-range"] = f"bytes {start}-{len(BLOB) - 1}/{len(BLOB)}"
        return httpx.Response(206 if start else 200, headers=headers, content=body)

    return handler, calls


async def _fetch(tmp_path: Path, handler, emit=None) -> Path | None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://backend"
    ) as client:
        return await _download_resumable(
            client=client,
            url="/api/node-files/images/save",
            params={"image": "x", "tag": "y"},
            headers={},
            directory=tmp_path,
            image=IMAGE,
            emit_progress=emit,
        )


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(image_loader.asyncio, "sleep", instant)


@pytest.fixture
def plenty_of_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Usage:
        free = 10**15

    monkeypatch.setattr(image_loader.shutil, "disk_usage", lambda _p: _Usage())


class TestResume:
    @pytest.mark.asyncio
    async def test_a_dropped_transfer_resumes_where_it_stopped(
        self, tmp_path: Path, plenty_of_disk: None
    ) -> None:
        handler, calls = _server(cut_after=300_000)

        part = await _fetch(tmp_path, handler)

        assert part is not None
        assert part.read_bytes() == BLOB, "the resumed file must be byte-identical"
        assert calls[0]["range"] is None
        assert calls[1]["range"] == "bytes=300000-", "the second request asks for the rest only"

    @pytest.mark.asyncio
    async def test_a_partial_left_by_a_previous_run_is_picked_up(
        self, tmp_path: Path, plenty_of_disk: None
    ) -> None:
        """Even across an agent restart, the bytes already here are not re-sent."""
        leftover = tmp_path / "llmport_ray-vllm-gb10_ray2.58-nv26.08.tar.part"
        leftover.write_bytes(BLOB[:500_000])
        handler, calls = _server()

        part = await _fetch(tmp_path, handler)

        assert part is not None and part.read_bytes() == BLOB
        assert calls[0]["range"] == "bytes=500000-"

    @pytest.mark.asyncio
    async def test_a_server_that_ignores_range_restarts_cleanly(
        self, tmp_path: Path, plenty_of_disk: None
    ) -> None:
        """A 200 to a ranged request is the whole file; appending would corrupt it."""
        leftover = tmp_path / "llmport_ray-vllm-gb10_ray2.58-nv26.08.tar.part"
        leftover.write_bytes(b"stale bytes from somewhere else")

        def whole_file_only(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-length": str(len(BLOB))}, content=BLOB)

        part = await _fetch(tmp_path, whole_file_only)

        assert part is not None and part.read_bytes() == BLOB

    @pytest.mark.asyncio
    async def test_an_already_complete_file_is_not_fetched_again(
        self, tmp_path: Path, plenty_of_disk: None
    ) -> None:
        leftover = tmp_path / "llmport_ray-vllm-gb10_ray2.58-nv26.08.tar.part"
        leftover.write_bytes(BLOB)
        handler, calls = _server()

        part = await _fetch(tmp_path, handler)

        assert part is not None and part.read_bytes() == BLOB
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_gives_up_eventually_and_keeps_what_it_has(
        self, tmp_path: Path, plenty_of_disk: None
    ) -> None:
        def always_drops(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(ImageLoaderError, match="interrupted attempts"):
            await _fetch(tmp_path, always_drops)


class TestTheWrongBuild:
    @pytest.mark.asyncio
    async def test_a_server_holding_a_different_build_says_so_before_sending(
        self, tmp_path: Path, plenty_of_disk: None
    ) -> None:
        handler, calls = _server(status_override=409)

        with pytest.raises(ImageLoaderError) as caught:
            await _fetch(tmp_path, handler)

        assert caught.value.error_code == "server_image_mismatch"
        assert "different build" in str(caught.value)
        assert len(calls) == 1, "a refusal is not something to retry"

    @pytest.mark.asyncio
    async def test_the_pin_travels_with_the_request(self, tmp_path: Path) -> None:
        seen: dict[str, str] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            seen.update(dict(request.url.params))
            return httpx.Response(409, json={"detail": "no"})

        class _Runtime:
            name = "docker"

            async def load_image_tar(self, stream: Any) -> str:
                raise AssertionError("must not load anything after a refusal")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(capture), base_url="http://backend"
        ) as client:
            with pytest.raises(ImageLoaderError):
                await image_loader.load_image_from_backend(
                    client=client,
                    credential="c",
                    image=IMAGE,
                    runtime=_Runtime(),  # type: ignore[arg-type]
                    expect_id="sha256:aaa",
                    expect_rootfs="sha256:bbb",
                    download_dir=tmp_path,
                )

        assert seen["expect_id"] == "sha256:aaa"
        assert seen["expect_rootfs"] == "sha256:bbb"


class TestProgress:
    @pytest.mark.asyncio
    async def test_progress_reaches_the_callback(
        self, tmp_path: Path, plenty_of_disk: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The loader used to be handed no callback, so nothing was reported."""
        monkeypatch.setattr(image_loader, "_PROGRESS_INTERVAL_SEC", 0)
        events: list[dict[str, Any]] = []

        async def emit(event: dict[str, Any]) -> None:
            events.append(event)

        handler, _calls = _server()
        await _fetch(tmp_path, handler, emit=emit)

        assert events, "no progress was emitted"
        assert all(e["phase"] == "loading_image" for e in events)
        assert any(e["progress_pct"] is not None for e in events)

    def test_the_message_says_how_much_how_fast_and_how_long(self) -> None:
        message, pct = _describe(IMAGE, 4 * 1024**3, 12 * 1024**3, 20 * 1024**2)

        assert pct == 33
        assert "4.0 GiB of 12.0 GiB" in message
        assert "/s" in message
        assert "min left" in message

    def test_never_claims_to_be_finished_before_it_is(self) -> None:
        _message, pct = _describe(IMAGE, 12 * 1024**3 - 1, 12 * 1024**3, 1.0)
        assert pct == 99


class TestNoRoom:
    @pytest.mark.asyncio
    async def test_falls_back_to_streaming_rather_than_filling_the_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Tiny:
            free = 1024

        monkeypatch.setattr(image_loader.shutil, "disk_usage", lambda _p: _Tiny())
        handler, _calls = _server()

        assert await _fetch(tmp_path, handler) is None
        assert not list(tmp_path.glob("*.part")), "nothing left behind"


class TestErrorCodes:
    @pytest.mark.asyncio
    async def test_an_exception_can_name_its_own_code(self) -> None:
        """"internal_error" for everything hid which failures are permanent."""

        class _State:
            state = type("S", (), {})()

            def get_command_result(self, _cid: str) -> None:
                return None

            def remember_command_result(self, *_a: Any, **_k: Any) -> None:
                return None

        class _Guard:
            def validate(self, **_k: Any) -> None:
                return None

        class _Events:
            def add(self, *_a: Any, **_k: Any) -> None:
                return None

            def __getattr__(self, _name: str) -> Any:
                return lambda *_a, **_k: None

        dispatcher = CommandDispatcher(
            state_store=_State(),  # type: ignore[arg-type]
            runtime_manager=None,  # type: ignore[arg-type]
            policy_guard=_Guard(),  # type: ignore[arg-type]
            events=_Events(),  # type: ignore[arg-type]
        )

        async def boom(**_k: Any) -> Any:
            raise ImageLoaderError("the server holds a different build", code="server_image_mismatch")

        dispatcher._guarded_execute = boom  # type: ignore[method-assign]

        async def emit(_e: dict[str, Any]) -> None:
            return None

        outcome = await dispatcher.handle(
            {"id": "c1", "command_type": "ensure_runtime_image", "payload": {}}, emit
        )

        assert outcome["success"] is False
        assert outcome["error_code"] == "server_image_mismatch"


class TestTheAgentsOwnLog:
    """Its terminal and journalctl showed nothing for the whole transfer."""

    @pytest.mark.asyncio
    async def test_logs_a_filling_bar_once_per_tenth(
        self, tmp_path: Path, plenty_of_disk: None, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO", logger="llm_port_node_agent.image_loader")

        async def many_chunks(request: httpx.Request) -> httpx.Response:
            async def body():
                step = len(BLOB) // 20
                for start in range(0, len(BLOB), step):
                    yield BLOB[start:start + step]

            return httpx.Response(200, headers={"content-length": str(len(BLOB))}, content=body())

        await _fetch(tmp_path, many_chunks)

        bars = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[")]
        assert 8 <= len(bars) <= 11, f"one line per tenth, got {len(bars)}"
        assert bars[-1].startswith("[" + "#" * 24 + "]") or "99%" in bars[-1]

    def test_the_bar_fills_in_proportion(self) -> None:
        assert image_loader._text_bar(0) == "[" + "." * 24 + "]"
        assert image_loader._text_bar(50) == "[" + "#" * 12 + "." * 12 + "]"
        assert image_loader._text_bar(100) == "[" + "#" * 24 + "]"
