from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from llm_port_api.db.dao.gateway_dao import GatewayDAO, RoutedInstance
from llm_port_api.services.gateway.errors import GatewayError
from llm_port_api.services.gateway.lease import LeaseManager

if TYPE_CHECKING:
    from llm_port_api.services.cache.protocol import CacheBackend


@dataclass(slots=True, frozen=True)
class RoutingDecision:
    """Chosen route target with acquired lease."""

    candidate: RoutedInstance
    request_id: str


class RouterService:
    """Resolve and lease a concrete provider instance."""

    def __init__(
        self,
        *,
        dao: GatewayDAO,
        cache: CacheBackend,
        lease_manager: LeaseManager,
        capacity_wait_sec: float = 0.0,
    ) -> None:
        self.dao = dao
        self.cache = cache
        self.lease_manager = lease_manager
        #: How long a request waits for a free slot before it is refused.
        self.capacity_wait_sec = capacity_wait_sec

    async def resolve_alias(
        self, *, alias: str, tenant_id: str,
    ) -> list[RoutedInstance]:
        """Fetch route candidates or raise 404 if alias not available."""
        candidates = await self.dao.resolve_candidates(alias=alias, tenant_id=tenant_id)
        if not candidates:
            raise GatewayError(
                status_code=404,
                message=f"Model alias '{alias}' is not available for this tenant.",
                code="model_not_found",
                param="model",
            )
        return candidates

    async def pick_and_lease(
        self,
        *,
        candidates: list[RoutedInstance],
        request_id: str,
    ) -> RoutingDecision:
        """
        Pick least-loaded candidate and acquire lease.

        Falls back across candidates until one can acquire capacity. When
        every one is full, waits for a slot -- up to ``capacity_wait_sec`` --
        rather than refusing at once: a burst of requests is the normal case
        for a model behind a gateway, and each one finishes in moments. A
        request is refused only after that wait.
        """
        deadline = time.monotonic() + max(self.capacity_wait_sec, 0.0)
        delay = 0.05
        while True:
            decision = await self._try_candidates(candidates, request_id)
            if decision is not None:
                return decision
            if time.monotonic() >= deadline:
                raise GatewayError(
                    status_code=503,
                    message="No provider instance has free capacity for the requested model.",
                    error_type="server_error",
                    code="no_capacity",
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.5)

    async def _try_candidates(
        self, candidates: list[RoutedInstance], request_id: str,
    ) -> RoutingDecision | None:
        active_counts = await self._active_counts(candidates)
        ordered = sorted(
            candidates,
            key=lambda c: (
                active_counts.get(str(c.instance_id), 0) / max(c.max_concurrency, 1),
                -c.weight,
                str(c.instance_id),
            ),
        )
        for candidate in ordered:
            acquired = await self.lease_manager.try_acquire(
                instance_id=candidate.instance_id,
                request_id=request_id,
                max_concurrency=candidate.max_concurrency,
            )
            if acquired:
                return RoutingDecision(candidate=candidate, request_id=request_id)
        return None

    async def release(self, decision: RoutingDecision) -> None:
        """Release lease associated with routing decision."""
        await self.lease_manager.release(
            instance_id=decision.candidate.instance_id,
            request_id=decision.request_id,
        )

    async def _active_counts(self, candidates: list[RoutedInstance]) -> dict[str, int]:
        return {
            str(c.instance_id): await self.lease_manager.in_flight(c.instance_id)
            for c in candidates
        }
