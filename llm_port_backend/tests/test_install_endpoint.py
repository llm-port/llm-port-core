"""The installer the operator runs, and what it refuses to pretend.

The point of generating this server-side is that the human assembles nothing:
the address, the build and the digest are all filled in before the script
leaves the backend. The tests below pin the three things that would quietly
undo that -- a script that guesses a platform, a plan that claims a digest it
did not compute, and an install that says "verified" when nothing was checked.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from llm_port_backend.web.api import install as install_api

ORIGIN = "http://10.88.10.220:8000"


def _script() -> str:
    return install_api.render_installer(ORIGIN)


# ── what the script is, and is not ───────────────────────────────────────


class TestScript:
    def test_carries_this_backend_so_nobody_has_to_type_it(self) -> None:
        assert f'BACKEND="{ORIGIN}"' in _script()

    def test_is_never_piped_into_a_shell(self) -> None:
        """The whole security argument against the neighbours rests on this.

        The script lands on disk so it can be read before it is run.
        """
        script = _script()
        assert "| bash" not in script
        assert "| sh" not in script

    def test_verifies_what_it_downloads(self) -> None:
        script = _script()
        assert "sha256sum" in script
        assert 'ACTUAL" != "$SHA"' in script

    def test_says_so_when_there_is_nothing_to_verify(self) -> None:
        """A missing digest is not a passed check, and must not read like one."""
        script = _script()
        assert "published no digest" in script
        assert "nothing was verified" in script

    def test_does_not_ask_the_operator_to_pick_an_architecture(self) -> None:
        script = _script()
        assert "uname -m" in script
        assert "uname -s" in script

    @pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
    def test_is_valid_posix_shell(self, tmp_path: Path) -> None:
        path = tmp_path / "install.sh"
        path.write_text(_script(), encoding="utf-8", newline="\n")
        result = subprocess.run(
            ["sh", "-n", str(path)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr

    @pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX sh available")
    def test_rejects_an_option_it_does_not_understand(self, tmp_path: Path) -> None:
        path = tmp_path / "install.sh"
        path.write_text(_script(), encoding="utf-8", newline="\n")
        result = subprocess.run(
            ["sh", str(path), "--wat"], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 2
        assert "Unknown option" in result.stderr


# ── the plan: where the build comes from, and what it hashes to ──────────


@pytest.mark.anyio()
async def test_plan_points_at_the_release_when_there_is_no_local_copy(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(install_api, "local_binary", lambda _build: None)
    r = await client.get("/api/install/plan", params={"platform": "linux-aarch64"})
    assert r.status_code == 200
    body = r.json()
    assert body["platform"] == "linux-aarch64"
    assert body["url"].startswith("https://github.com/")
    assert body["source"] == "release"
    # We have not fetched it, so we do not have its digest -- and claiming one
    # we did not compute would be worse than admitting we have none.
    assert body["sha256"] is None


@pytest.mark.anyio()
async def test_plan_serves_the_local_copy_when_there_is_one(
    client: AsyncClient,
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The air-gapped case: the node never needs to reach the internet."""
    binary = tmp_path / "llmport-agent-linux-aarch64"
    binary.write_bytes(b"not really a binary, but it hashes")
    monkeypatch.setattr(install_api, "local_binary", lambda _build: binary)

    r = await client.get("/api/install/plan", params={"platform": "linux-aarch64"})
    body = r.json()
    assert body["source"] == "backend"
    assert "/api/install/binary/linux-aarch64" in body["url"]
    assert body["sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()


@pytest.mark.anyio()
async def test_arm64_and_aarch64_are_the_same_build(
    client: AsyncClient, fastapi_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``uname -m`` says aarch64; plenty of tooling says arm64."""
    monkeypatch.setattr(install_api, "local_binary", lambda _build: None)
    a = (await client.get("/api/install/plan", params={"platform": "linux-arm64"})).json()
    b = (await client.get("/api/install/plan", params={"platform": "linux-aarch64"})).json()
    assert a["url"] == b["url"]


@pytest.mark.anyio()
async def test_an_unpublished_platform_says_so_rather_than_guessing(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    r = await client.get("/api/install/plan", params={"platform": "linux-riscv64"})
    body = r.json()
    assert body["url"] is None
    assert "riscv64" in body["reason"]


@pytest.mark.anyio()
async def test_the_script_is_downloadable_without_a_login(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    """A machine that has never enrolled has no credential to present."""
    r = await client.get("/api/install/llmport-agent.sh")
    assert r.status_code == 200
    assert "llmport-agent.sh" in r.headers.get("content-disposition", "")
    assert "#!/bin/sh" in r.text


@pytest.mark.anyio()
async def test_binary_endpoint_404s_for_an_unknown_build(
    client: AsyncClient, fastapi_app: FastAPI
) -> None:
    r = await client.get("/api/install/binary/linux-vax")
    assert r.status_code == 404
