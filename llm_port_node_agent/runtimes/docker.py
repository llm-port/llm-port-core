"""Docker-backed implementation of :class:`ContainerRuntime`."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from typing import Any

from llm_port_node_agent.runtimes import ContainerRuntimeError

log = logging.getLogger(__name__)


class DockerRuntime:
    """Execute container operations via the Docker CLI.

    Implements the :class:`ContainerRuntime` protocol using
    ``docker`` subprocess calls — the same mechanism that was previously
    inlined in ``RuntimeManager._docker()``.
    """

    def __init__(self, *, socket_path: str | None = None) -> None:
        self._socket_path = socket_path

    # ── helpers ────────────────────────────────────────────────

    async def _exec(
        self,
        *args: str,
        timeout_sec: float = 30,
        raise_on_error: bool = True,
    ) -> tuple[int, str, str]:
        """Run ``docker <args>`` and return ``(returncode, stdout, stderr)``."""
        cmd: list[str] = ["docker"]
        if self._socket_path:
            cmd.extend(["-H", f"unix://{self._socket_path}"])
        cmd.extend(args)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec,
            )
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            if raise_on_error:
                raise ContainerRuntimeError(
                    f"docker {' '.join(args)} timed out after {timeout_sec}s"
                )
            return 124, "", "timeout"

        code = proc.returncode
        out = stdout.decode("utf-8", "replace")
        err = stderr.decode("utf-8", "replace")
        if raise_on_error and code != 0:
            raise ContainerRuntimeError(
                f"docker {' '.join(args)} failed: {err.strip() or out.strip()}"
            )
        return code, out, err

    # ── lifecycle ─────────────────────────────────────────────

    async def run(
        self,
        *,
        image: str,
        name: str,
        ports: list[str] | None = None,
        env: dict[str, str] | None = None,
        gpus: str | None = None,
        volumes: list[str] | None = None,
        command: list[str] | None = None,
        entrypoint: str | None = None,
        extra_args: list[str] | None = None,
        timeout_sec: float = 120,
    ) -> str:
        args: list[str] = ["run", "-d", "--name", name, "--restart", "unless-stopped"]

        for p in ports or []:
            args.extend(["-p", p])
        for k, v in (env or {}).items():
            args.extend(["-e", f"{k}={v}"])
        if gpus:
            args.extend(["--gpus", gpus])
        if entrypoint is not None:
            args.extend(["--entrypoint", entrypoint])
        for vol in volumes or []:
            args.extend(["-v", vol])
        args.extend(extra_args or [])
        args.append(image)
        args.extend(command or [])

        _, out, _ = await self._exec(*args, timeout_sec=timeout_sec)
        container_id = out.strip().splitlines()[0] if out.strip() else ""
        return container_id

    async def start(self, name: str, *, timeout_sec: float = 30) -> None:
        await self._exec("start", name, timeout_sec=timeout_sec)

    async def stop(self, name: str, *, timeout_sec: float = 30) -> None:
        await self._exec("stop", name, timeout_sec=timeout_sec)

    async def restart(self, name: str, *, timeout_sec: float = 45) -> None:
        await self._exec("restart", name, timeout_sec=timeout_sec)

    async def remove(self, name: str, *, force: bool = True, timeout_sec: float = 45) -> None:
        args = ["rm"]
        if force:
            args.append("-f")
        args.append(name)
        await self._exec(*args, timeout_sec=timeout_sec)

    # ── query ─────────────────────────────────────────────────

    async def inspect(
        self, name: str, *, format_: str | None = None, timeout_sec: float = 10,
    ) -> dict[str, Any]:
        args = ["inspect"]
        if format_:
            args.extend(["--format", format_])
        args.append(name)
        code, out, _ = await self._exec(*args, timeout_sec=timeout_sec, raise_on_error=False)
        if code != 0:
            return {"__missing": True, "__returncode": code}
        try:
            parsed = json.loads(out.strip())
        except (json.JSONDecodeError, ValueError):
            return {"__raw": out.strip()}
        if isinstance(parsed, list) and len(parsed) == 1:
            return parsed[0]
        return parsed if isinstance(parsed, dict) else {"__raw": parsed}

    async def exists(self, name: str) -> bool:
        code, _, _ = await self._exec(
            "inspect", name, timeout_sec=8, raise_on_error=False,
        )
        return code == 0

    async def port(
        self, name: str, container_port: str, *, timeout_sec: float = 10,
    ) -> str | None:
        code, out, _ = await self._exec(
            "port", name, f"{container_port}/tcp",
            timeout_sec=timeout_sec,
            raise_on_error=False,
        )
        if code != 0:
            return None
        lines = out.strip().splitlines()
        if not lines:
            return None
        host_port = lines[0].split(":")[-1].strip()
        return host_port or None

    async def logs(
        self,
        name: str,
        *,
        tail: str | None = None,
        since: str | None = None,
        timestamps: bool = False,
        timeout_sec: float = 15,
    ) -> tuple[int, str]:
        args: list[str] = ["logs"]
        if timestamps:
            args.append("--timestamps")
        if tail:
            args.extend(["--tail", tail])
        if since:
            args.extend(["--since", since])
        args.append(name)

        code, out, err = await self._exec(
            *args, timeout_sec=timeout_sec, raise_on_error=False,
        )
        return code, out + err

    async def ps(self, *, all_: bool = True, timeout_sec: float = 20) -> list[str]:
        args = ["ps"]
        if all_:
            args.append("-a")
        args.extend(["--format", "{{json .}}"])
        _, out, _ = await self._exec(*args, timeout_sec=timeout_sec)
        return [line for line in out.splitlines() if line.strip()]

    async def images(self, *, timeout_sec: float = 20) -> list[str]:
        _, out, _ = await self._exec(
            "images", "--format", "{{json .}}", timeout_sec=timeout_sec,
        )
        return [line for line in out.splitlines() if line.strip()]

    # ── image management ──────────────────────────────────────

    async def pull(self, image: str, *, timeout_sec: float = 1800) -> None:
        await self._exec("pull", image, timeout_sec=timeout_sec)

    async def load_image_tar(
        self,
        stream: Any,
        *,
        timeout_sec: float = 3600,
    ) -> str:
        """Pipe an async byte-stream into ``docker load``.

        *stream* must be an async iterable yielding ``bytes`` chunks
        (e.g. ``httpx.Response.aiter_bytes``).
        """
        cmd: list[str] = ["docker"]
        if self._socket_path:
            cmd.extend(["-H", f"unix://{self._socket_path}"])
        cmd.append("load")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None  # noqa: S101

        total_bytes = 0
        try:
            async for chunk in stream:
                proc.stdin.write(chunk)
                await proc.stdin.drain()
                total_bytes += len(chunk)
        finally:
            proc.stdin.close()
            await proc.stdin.wait_closed()

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec,
        )
        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", "replace").strip()
            raise ContainerRuntimeError(
                f"docker load failed (rc={proc.returncode}): {err_msg}"
            )

        result = stdout.decode("utf-8", "replace").strip()
        log.info("docker load: %d bytes → %s", total_bytes, result)
        return result

    # ── availability ──────────────────────────────────────────

    async def is_available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        code, _, _ = await self._exec(
            "version", "--format", "{{.Server.Version}}",
            timeout_sec=8,
            raise_on_error=False,
        )
        return code == 0

    @property
    def name(self) -> str:
        return "docker"
