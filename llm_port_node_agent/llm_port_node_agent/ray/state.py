"""Optional Ray State API diagnostics — Tier B (spec sections 2 & 5).

The ``ray.util.state`` SDK (``list_actors``, ``list_jobs``/``list_tasks``,
``list_placement_groups``, ``list_workload``) is the *only* Ray surface in
this refactor that requires the Dashboard component: its queries are served
by the Dashboard's State API server.  Ray 2.58 verified fact: with
``--include-dashboard=false`` the State API is **unavailable** —
``ray.util.state`` calls raise.

Therefore this module is strictly an **enhanced-diagnostics** tier:

* It is imported lazily and only when explicitly requested
  (``include_state=True`` on the status probe).
* It never participates in cluster health decisions — callers must map any
  failure to ``RayStateCapability(available=False)`` while leaving
  ``RayEnvironmentStatus.alive`` untouched.
* If/when a future Ray release makes the state surface Dashboard-independent,
  this module is the single place to re-tier it.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_port_node_agent.ray import models
from llm_port_node_agent.ray.core import RayCoreClient

log = logging.getLogger(__name__)


def _one_line(text: str, limit: int = 300) -> str:
    """Collapse multi-line exception text into one trimmed line for ``detail``."""
    cleaned = " ".join(str(text).split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


class RayStateDiagnostics:
    """Optional, Dashboard-dependent state queries (Tier B)."""

    def __init__(self, *, core: RayCoreClient | None = None) -> None:
        self._core = core or RayCoreClient()

    def _state_module(self) -> Any:
        # The public State API in Ray 2.58 is ``ray.util.state``.  (Top-level
        # ``from ray import state`` resolves to the private ``ray._private.state``
        # accessor, which does NOT expose ``list_actors``/``list_tasks`` — that
        # would make every availability probe report "unavailable" even when the
        # Dashboard's State API server is running.)
        try:
            from ray.util import state  # noqa: PLC0415

            return state
        except Exception:
            return None

    def availability(self) -> models.RayStateCapability:
        """Report whether the State API is *actually* reachable.

        The distinction that matters for Tier B: the ``ray.util.state``
        module always imports (it ships in the wheel), but its queries are
        served by the Dashboard's State API server — which is absent under
        ``--include-dashboard=false``.  So importability alone is a false
        signal; we probe with a single bounded query.  This is opt-in
        (``include_state=True``), so the extra round-trip is paid only when a
        consumer explicitly asks for state diagnostics.

        Never raises: any failure folds into ``available=False`` + ``detail``
        and leaves cluster health untouched.
        """
        state = self._state_module()
        if state is None:
            return models.RayStateCapability(
                available=False,
                detail="ray.util.state not importable in this Ray build",
            )
        try:
            self._core.ensure_attached()
            # Bounded probe: connect to the State API server and fetch a
            # trivial page.  With the Dashboard disabled this fails fast.
            state.list_actors(limit=1)
        except Exception as exc:  # noqa: BLE001 - any failure => unavailable
            return models.RayStateCapability(
                available=False,
                detail=_one_line(f"State API unreachable (Dashboard component required): {exc}"),
            )
        return models.RayStateCapability(available=True, detail="State API reachable")


    def actors(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List actor records (requires Dashboard component)."""
        state = self._state_module()
        if state is None:
            raise RuntimeError("ray.util.state not importable")
        self._core.ensure_attached()
        records = state.list_actors(limit=limit)
        return [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in records]

    def tasks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List task records (requires Dashboard component)."""
        state = self._state_module()
        if state is None:
            raise RuntimeError("ray.util.state not importable")
        self._core.ensure_attached()
        records = state.list_tasks(limit=limit)
        return [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in records]

    def jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List job records (requires Dashboard component)."""
        state = self._state_module()
        if state is None:
            raise RuntimeError("ray.util.state not importable")
        self._core.ensure_attached()
        records = state.list_jobs(limit=limit)
        return [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in records]
