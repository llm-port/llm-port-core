"""Local host apply executor."""

from __future__ import annotations

import asyncio
from pathlib import Path

from airgap_backend.db.models.system_settings import SystemApplyScope
from airgap_backend.services.docker.client import DockerService
from airgap_backend.services.system_settings.executors.base import ApplyAction, ApplyExecutor


class LocalApplyExecutor(ApplyExecutor):
    """Executes apply operations on the current host."""

    def __init__(self, docker: DockerService, compose_file: str) -> None:
        self._docker = docker
        self._compose_file = compose_file

    async def execute(self, action: ApplyAction, target_host: str) -> list[str]:
        """Execute apply operation locally."""
        if target_host != "local":
            msg = "Local executor only supports target_host=local."
            raise ValueError(msg)

        if action.scope == SystemApplyScope.SERVICE_RESTART:
            return await self._restart_services(action.services)
        if action.scope == SystemApplyScope.STACK_RECREATE:
            return await self._recreate_services(action.services)
        return [f"Applied {len(action.changed_keys)} live-reload setting(s)."]

    async def _restart_services(self, services: tuple[str, ...]) -> list[str]:
        raw_containers = await self._docker.list_containers(all_=True)
        events: list[str] = []
        for service in services:
            match = next(
                (
                    item
                    for item in raw_containers
                    if any(name.lstrip("/") == service for name in item.get("Names", []))
                ),
                None,
            )
            if match is None:
                events.append(f"Skipped restart for {service}: container not found.")
                continue
            container_id = str(match.get("Id", ""))
            await self._docker.restart(container_id)
            events.append(f"Restarted service container: {service}.")
        return events

    async def _recreate_services(self, services: tuple[str, ...]) -> list[str]:
        compose = Path(self._compose_file)
        if not compose.exists():
            msg = f"Compose file not found: {compose}"
            raise FileNotFoundError(msg)
        command = [
            "docker",
            "compose",
            "-f",
            str(compose),
            "up",
            "-d",
            "--force-recreate",
        ]
        if services:
            command.extend(services)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8").strip() or stdout.decode("utf-8").strip()
            msg = f"Compose recreate failed: {message}"
            raise RuntimeError(msg)
        output = stdout.decode("utf-8").strip()
        return [output or "Compose recreate completed."]
