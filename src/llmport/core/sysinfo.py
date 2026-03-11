"""System resource detection and scalability tuning calculator.

Uses ``psutil`` to detect host hardware (CPU cores, RAM) and derives
optimal worker counts, DB pool sizes, and message-queue pool sizes
for all llm.port services.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import psutil

# ── Service classification ────────────────────────────────────────
# Heavy services handle high-volume request traffic or CPU-bound work.
# Light services are low-traffic or background-only.

HEAVY_SERVICES = ("backend", "api", "pii")
LIGHT_SERVICES: tuple[str, ...] = ()
ALL_SERVICES = HEAVY_SERVICES + LIGHT_SERVICES

# Services that use a PostgreSQL connection pool.
DB_SERVICES = ("backend", "api", "pii")

# Services that use a RabbitMQ connection pool.
RABBIT_SERVICES = ("backend", "api")


# ── System snapshot ───────────────────────────────────────────────


@dataclass(frozen=True)
class SystemInfo:
    """Point-in-time snapshot of host hardware resources."""

    physical_cores: int
    logical_cores: int
    total_ram_bytes: int

    @property
    def total_ram_gb(self) -> float:
        return round(self.total_ram_bytes / (1024**3), 1)


def detect_system() -> SystemInfo:
    """Return a :class:`SystemInfo` snapshot of the current host."""
    return SystemInfo(
        physical_cores=psutil.cpu_count(logical=False) or os.cpu_count() or 1,
        logical_cores=psutil.cpu_count(logical=True) or os.cpu_count() or 1,
        total_ram_bytes=psutil.virtual_memory().total,
    )


# ── Tune profile ─────────────────────────────────────────────────


@dataclass
class TuneProfile:
    """Computed scalability parameters for all services."""

    profile: str  # "dev" or "prod"
    system: SystemInfo

    # Per-service worker counts (service name → count).
    workers: dict[str, int] = field(default_factory=dict)

    # DB pool sizes (service name → pool_size).
    db_pool_size: dict[str, int] = field(default_factory=dict)
    db_max_overflow: dict[str, int] = field(default_factory=dict)

    # RabbitMQ pool sizes (service name → pool_size).
    rabbit_pool_size: dict[str, int] = field(default_factory=dict)
    rabbit_channel_pool_size: dict[str, int] = field(default_factory=dict)

    # ── Prefix map ────────────────────────────────────────────
    _ENV_PREFIX: dict[str, str] = field(
        default_factory=lambda: {
            "backend": "LLM_PORT_BACKEND_",
            "api": "LLM_PORT_API_",
            "pii": "LLM_PORT_PII_",
        },
        repr=False,
    )

    def to_env_dict(self) -> dict[str, str]:
        """Flatten into a dict of ``LLM_PORT_*`` env var → value."""
        env: dict[str, str] = {}

        for svc in ALL_SERVICES:
            pfx = self._ENV_PREFIX[svc]
            env[f"{pfx}WORKERS_COUNT"] = str(self.workers[svc])

        for svc in DB_SERVICES:
            pfx = self._ENV_PREFIX[svc]
            env[f"{pfx}DB_POOL_SIZE"] = str(self.db_pool_size[svc])
            env[f"{pfx}DB_MAX_OVERFLOW"] = str(self.db_max_overflow[svc])

        for svc in RABBIT_SERVICES:
            pfx = self._ENV_PREFIX[svc]
            env[f"{pfx}RABBIT_POOL_SIZE"] = str(self.rabbit_pool_size[svc])
            env[f"{pfx}RABBIT_CHANNEL_POOL_SIZE"] = str(
                self.rabbit_channel_pool_size[svc],
            )

        return env


# ── Calculator ────────────────────────────────────────────────────


def calculate_tune_profile(
    profile: str = "dev",
    system: SystemInfo | None = None,
) -> TuneProfile:
    """Calculate optimal scalability parameters for *profile*.

    Parameters
    ----------
    profile
        ``"dev"`` for conservative local defaults, ``"prod"`` for
        production-grade sizing.
    system
        Pre-detected system info.  If ``None``, :func:`detect_system`
        is called automatically.
    """
    sys = system or detect_system()
    cores = sys.physical_cores
    tp = TuneProfile(profile=profile, system=sys)

    if profile == "dev":
        # Dev: keep things light — 1-2 workers, small pools.
        for svc in HEAVY_SERVICES:
            tp.workers[svc] = min(cores, 2)
        for svc in LIGHT_SERVICES:
            tp.workers[svc] = 1
        for svc in DB_SERVICES:
            tp.db_pool_size[svc] = 5
            tp.db_max_overflow[svc] = 10
        for svc in RABBIT_SERVICES:
            tp.rabbit_pool_size[svc] = 2
            tp.rabbit_channel_pool_size[svc] = 10
    else:
        # Prod: scale with available CPU cores.
        for svc in HEAVY_SERVICES:
            tp.workers[svc] = min(2 * cores + 1, 17)
        for svc in LIGHT_SERVICES:
            tp.workers[svc] = min(cores + 1, 8)

        for svc in DB_SERVICES:
            w = tp.workers[svc]
            tp.db_pool_size[svc] = min(max(10, w * 5), 50)
            tp.db_max_overflow[svc] = tp.db_pool_size[svc]

        for svc in RABBIT_SERVICES:
            w = tp.workers[svc]
            tp.rabbit_pool_size[svc] = min(max(4, w * 2), 20)
            tp.rabbit_channel_pool_size[svc] = tp.rabbit_pool_size[svc] * 2

    return tp
