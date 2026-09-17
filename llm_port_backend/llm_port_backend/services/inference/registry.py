"""String-keyed driver registry for the neutral inference domain.

Mirrors the spirit of the existing provider adapter registry: drivers are
registered under a stable string key (e.g. ``registry.register("ray",
RayDriver)``) and resolved by key.  No database enum is used for backends,
so adding a new driver never requires a schema migration.

Phase 1 registers **no** drivers; the registry exists so that later phases
can plug in ``RayDriver`` and its companion orchestrator/environment
managers without touching core code paths.
"""

from __future__ import annotations


class DriverRegistry:
    """Maps driver keys to driver classes."""

    def __init__(self) -> None:
        self._drivers: dict[str, type] = {}

    def register(self, key: str, driver_cls: type) -> None:
        """
        Register a driver class under a stable key.

        :param key: the driver key (e.g. ``"ray"``).
        :param driver_cls: a class conforming to ``InferenceDriver``.
        :raises ValueError: if the key is already registered to a different class.
        """
        existing = self._drivers.get(key)
        if existing is not None and existing is not driver_cls:
            msg = f"driver key {key!r} is already registered to {existing.__name__}"
            raise ValueError(msg)
        self._drivers[key] = driver_cls

    def get(self, key: str) -> type | None:
        """
        Resolve a driver class by key.

        :param key: the driver key.
        :return: the registered class, or ``None`` if unknown.
        """
        return self._drivers.get(key)

    def contains(self, key: str) -> bool:
        """
        Check whether a driver key is registered.

        :param key: the driver key.
        :return: True when the key resolves to a registered driver.
        """
        return key in self._drivers

    def keys(self) -> list[str]:
        """
        List the registered driver keys.

        :return: a sorted list of driver keys.
        """
        return sorted(self._drivers)


#: Process-wide registry instance.  Phase 1 leaves this empty; Phase 2
#: registers the Ray driver here at import/lifespan time.
registry = DriverRegistry()

# Register the Ray driver
from llm_port_backend.services.inference.drivers.ray.driver import RayDriver
registry.register(RayDriver.key, RayDriver)
