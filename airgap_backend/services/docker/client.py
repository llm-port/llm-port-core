"""Async Docker Engine client wrapper built on top of aiodocker."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from typing import Any

from aiodocker import Docker

_DEFAULT_DOCKER_URL = "http://localhost:2375" if sys.platform == "win32" else "unix:///var/run/docker.sock"


class DockerService:
    """
    Thin async wrapper around aiodocker.

    One instance should be shared for the lifetime of the application;
    call :meth:`close` on shutdown.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or _DEFAULT_DOCKER_URL
        self._client: Docker | None = None

    @property
    def client(self) -> Docker:
        """Return (lazily created) aiodocker client."""
        if self._client is None:
            self._client = Docker(url=self._url)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._client:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------
    # Containers
    # ------------------------------------------------------------------

    async def list_containers(self, all_: bool = True) -> list[dict[str, Any]]:
        """Return a list of container summary dicts (similar to `docker ps -a`)."""
        containers = await self.client.containers.list(all=all_)
        result = []
        for c in containers:
            info: dict[str, Any] = dict(c._container)  # noqa: SLF001
            result.append(info)
        return result

    async def inspect_container(self, container_id: str) -> dict[str, Any]:
        """Return full inspection data for a container."""
        c = await self.client.containers.get(container_id)
        await c.show()
        return dict(c._container)  # noqa: SLF001

    async def start(self, container_id: str) -> None:
        """Start a stopped container."""
        c = await self.client.containers.get(container_id)
        await c.start()

    async def stop(self, container_id: str, timeout: int = 10) -> None:
        """Stop a running container."""
        c = await self.client.containers.get(container_id)
        await c.stop(timeout=timeout)

    async def restart(self, container_id: str, timeout: int = 10) -> None:
        """Restart a container."""
        c = await self.client.containers.get(container_id)
        await c.restart(timeout=timeout)

    async def pause(self, container_id: str) -> None:
        """Pause a running container."""
        c = await self.client.containers.get(container_id)
        await c.pause()

    async def unpause(self, container_id: str) -> None:
        """Unpause a paused container."""
        c = await self.client.containers.get(container_id)
        await c.unpause()

    async def create_container(
        self,
        image: str,
        name: str | None = None,
        cmd: list[str] | None = None,
        env: list[str] | None = None,
        ports: dict[str, list[dict[str, str]]] | None = None,
        volumes: list[str] | None = None,
        network: str | None = None,
        auto_start: bool = False,
        gpu_devices: str | list[int] | None = None,
        healthcheck: dict[str, Any] | None = None,
        labels: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Create (and optionally start) a container.

        :param image: Image name, e.g. ``nginx:latest``.
        :param name: Optional container name.
        :param cmd: Override command, e.g. ``["/bin/sh", "-c", "sleep 100"]``.
        :param env: Environment variables, e.g. ``["KEY=value", "FOO=bar"]``.
        :param ports: Port bindings in Docker format, e.g.
            ``{"80/tcp": [{"HostIp": "", "HostPort": "8080"}]}``.
        :param volumes: Host-path binds, e.g. ``["/host/path:/container/path"]``.
        :param network: Name/ID of network to attach on creation.
        :param auto_start: If ``True``, start the container immediately.
        :param gpu_devices: GPU passthrough — ``"all"`` for all GPUs, or a list
            of device indices (e.g. ``[0, 1]``).  Requires NVIDIA Container
            Toolkit on the host.
        :param healthcheck: Docker healthcheck config dict with keys:
            ``Test`` (list[str]), ``Interval`` (int, ns), ``Timeout`` (int, ns),
            ``Retries`` (int).  Example::

                {"Test": ["CMD-SHELL", "curl -f http://localhost:8000/health"],
                 "Interval": 30_000_000_000, "Timeout": 5_000_000_000, "Retries": 3}
        :param labels: Optional container labels, e.g. ``{"app": "vllm"}``.
        :return: Container inspect dict.
        """
        config: dict[str, Any] = {"Image": image}
        if cmd:
            config["Cmd"] = cmd
        if env:
            config["Env"] = env
        if labels:
            config["Labels"] = labels
        if healthcheck:
            config["Healthcheck"] = healthcheck
        host_config: dict[str, Any] = {}
        if ports:
            host_config["PortBindings"] = ports
            config["ExposedPorts"] = {p: {} for p in ports}
        if volumes:
            host_config["Binds"] = volumes
        if network:
            host_config["NetworkMode"] = network
        if gpu_devices is not None:
            device_ids: list[str]
            if gpu_devices == "all":
                device_ids = ["all"]
            else:
                device_ids = [str(d) for d in gpu_devices]
            host_config["DeviceRequests"] = [
                {
                    "Driver": "nvidia",
                    "DeviceIDs": device_ids,
                    "Capabilities": [["gpu"]],
                },
            ]
        config["HostConfig"] = host_config

        create_kwargs: dict[str, Any] = {"config": config}
        if name:
            create_kwargs["name"] = name

        c = await self.client.containers.create(**create_kwargs)
        if auto_start:
            await c.start()
        await c.show()
        return dict(c._container)  # noqa: SLF001

    async def delete(self, container_id: str, force: bool = False) -> None:
        """Delete a container."""
        c = await self.client.containers.get(container_id)
        await c.delete(force=force)

    async def logs(
        self,
        container_id: str,
        tail: int | str = 100,
        follow: bool = False,
        stdout: bool = True,
        stderr: bool = True,
    ) -> AsyncIterator[str]:
        """
        Yield log lines from a container.

        :param tail: number of log lines to tail ("all" or int).
        :param follow: if True keep yielding new lines.
        """
        c = await self.client.containers.get(container_id)
        if follow:
            # follow=True returns an async iterator
            async for chunk in c.log(
                stdout=stdout,
                stderr=stderr,
                follow=True,
                tail=str(tail),
            ):
                yield chunk
        else:
            # follow=False returns a coroutine that resolves to a list of strings
            lines = await c.log(
                stdout=stdout,
                stderr=stderr,
                follow=False,
                tail=str(tail),
            )
            for line in lines:
                yield line

    async def create_exec(
        self,
        container_id: str,
        cmd: list[str],
        workdir: str = "/",
        env: list[str] | None = None,
    ) -> str:
        """Create an exec instance and return its ID."""
        c = await self.client.containers.get(container_id)
        exec_id = await c.exec(
            cmd=cmd,
            workdir=workdir,
            environment=env or [],
            stdin=True,
            stdout=True,
            stderr=True,
            tty=True,
        )
        return exec_id._id  # noqa: SLF001

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    async def list_images(self) -> list[dict[str, Any]]:
        """Return a list of local image summary dicts."""
        images = await self.client.images.list()
        return [dict(img) for img in images]

    async def pull_image(self, from_image: str, tag: str = "latest") -> None:
        """Pull an image from a registry."""
        await self.client.images.pull(from_image=from_image, tag=tag)

    async def prune_images(self) -> dict[str, Any]:
        """Prune dangling images and return prune report."""
        return await self.client.images.prune()  # type: ignore[return-value]

    async def prune_images_dry_run(self) -> list[dict[str, Any]]:
        """Return images that would be pruned without removing them."""
        images = await self.list_images()
        return [img for img in images if not img.get("RepoTags")]

    # ------------------------------------------------------------------
    # Networks
    # ------------------------------------------------------------------

    async def list_networks(self) -> list[dict[str, Any]]:
        """Return all Docker networks."""
        nets = await self.client.networks.list()
        return [dict(n) for n in nets]

    async def inspect_network(self, network_id: str) -> dict[str, Any]:
        """Return full details for a single network."""
        n = await self.client.networks.get(network_id)
        return await n.show()

    async def create_network(
        self,
        name: str,
        driver: str = "bridge",
        internal: bool = False,
        labels: dict[str, str] | None = None,
        subnet: str | None = None,
        gateway: str | None = None,
    ) -> dict[str, Any]:
        """Create a Docker network and return its info."""
        config: dict[str, Any] = {
            "Name": name,
            "Driver": driver,
            "Internal": internal,
            "Labels": labels or {},
        }
        if subnet or gateway:
            ipam_config: dict[str, Any] = {}
            if subnet:
                ipam_config["Subnet"] = subnet
            if gateway:
                ipam_config["Gateway"] = gateway
            config["IPAM"] = {"Config": [ipam_config]}
        n = await self.client.networks.create(config)
        return await n.show()

    async def delete_network(self, network_id: str) -> None:
        """Delete a Docker network."""
        n = await self.client.networks.get(network_id)
        await n.delete()
