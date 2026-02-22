from __future__ import annotations

from dataclasses import dataclass

from redis.asyncio import ConnectionPool, Redis

from llm_port_api.db.dao.gateway_dao import GatewayDAO, RoutedInstance
from llm_port_api.services.gateway.errors import GatewayError
from llm_port_api.services.gateway.lease import LeaseManager


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
        redis_pool: ConnectionPool,
        lease_manager: LeaseManager,
    ) -> None:
        self.dao = dao
        self.redis_pool = redis_pool
        self.lease_manager = lease_manager

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

        Falls back across candidates until one can acquire capacity.
        """
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
        raise GatewayError(
            status_code=503,
            message="No provider instance has free capacity for the requested model.",
            error_type="server_error",
            code="no_capacity",
        )

    async def release(self, decision: RoutingDecision) -> None:
        """Release lease associated with routing decision."""
        await self.lease_manager.release(
            instance_id=decision.candidate.instance_id,
            request_id=decision.request_id,
        )

    async def _active_counts(self, candidates: list[RoutedInstance]) -> dict[str, int]:
        keys = [self.lease_manager.active_key(c.instance_id) for c in candidates]
        if not keys:
            return {}
        async with Redis(connection_pool=self.redis_pool) as redis:
            raw = await redis.mget(keys)
        result: dict[str, int] = {}
        for candidate, value in zip(candidates, raw):
            try:
                result[str(candidate.instance_id)] = (
                    int(value) if value is not None else 0
                )
            except (TypeError, ValueError):
                result[str(candidate.instance_id)] = 0
        return result
