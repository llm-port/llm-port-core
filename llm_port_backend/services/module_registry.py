"""Dynamic module registry for optional backend modules.

Replaces the hardcoded ``_MODULE_DEFS`` list.  Core modules self-register
during startup; EE plugins register additional modules when loaded via
``try/except ImportError``.

Two module types are supported:

* **container** – managed via Docker Compose profiles.  Status is
  determined by inspecting container state and probing a health URL.
* **plugin** – loaded in-process (e.g. from ``llm_port_ee``).  Status
  is determined by calling ``is_available_fn`` and ``health_fn``.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal

if TYPE_CHECKING:
    from llm_port_backend.services.system_settings import SystemSettingsService

logger = logging.getLogger(__name__)

# Type alias for the on_enable / on_disable callback signature.
# Receives the SystemSettingsService, an ``enabled`` flag, and the
# acting user ID.  Returns a (possibly empty) list of error strings.
SyncCallback = Callable[
    ["SystemSettingsService", bool, Any],
    Awaitable[list[str]],
]


@dataclass(slots=True)
class ModuleDef:
    """Descriptor for a single optional module."""

    # ── Identity ──────────────────────────────────────────────
    name: str
    display_name: str
    description: str
    module_type: Literal["container", "plugin"] = "container"
    enterprise: bool = False

    # ── Container-type fields ─────────────────────────────────
    settings_flag: str | None = None
    health_url_fn: Callable[[], str] | None = None
    compose_profile: str | None = None
    compose_services: list[str] = field(default_factory=list)
    container_names: list[str] = field(default_factory=list)

    # ── Plugin-type fields ────────────────────────────────────
    is_available_fn: Callable[[], bool] | None = None
    health_fn: Callable[[], Awaitable[str]] | None = None

    # ── Lifecycle callbacks (both types) ──────────────────────
    on_enable: SyncCallback | None = None
    on_disable: SyncCallback | None = None


class ModuleRegistry:
    """Ordered, thread-safe registry of :class:`ModuleDef` instances.

    Modules are returned in insertion order so the frontend renders
    them in a predictable sequence (RAG → PII → … → EE extras).
    """

    def __init__(self) -> None:
        self._modules: OrderedDict[str, ModuleDef] = OrderedDict()

    # ── Mutation ──────────────────────────────────────────────

    def register(self, module: ModuleDef) -> None:
        """Register a module.  Raises ``ValueError`` if the name is taken."""
        if module.name in self._modules:
            msg = f"Module '{module.name}' is already registered."
            raise ValueError(msg)
        self._modules[module.name] = module
        logger.info(
            "Registered module '%s' (type=%s, enterprise=%s)",
            module.name,
            module.module_type,
            module.enterprise,
        )

    def unregister(self, name: str) -> None:
        """Remove a module (useful in tests / hot-unload)."""
        self._modules.pop(name, None)

    # ── Queries ───────────────────────────────────────────────

    def list_modules(self) -> list[ModuleDef]:
        """Return all modules in registration order."""
        return list(self._modules.values())

    def get_module(self, name: str) -> ModuleDef | None:
        """Lookup by name, or ``None`` if not registered."""
        return self._modules.get(name)

    def __len__(self) -> int:
        return len(self._modules)

    def __contains__(self, name: str) -> bool:
        return name in self._modules


# Module-level singleton — imported by views, lifespan, and EE plugins.
module_registry = ModuleRegistry()
