"""In-memory price catalog cache for write-time cost estimation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_api.db.models.gateway import PriceCatalog

log = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class PriceCatalogEntry:
    """A cached active price row."""

    id: uuid.UUID
    provider: str
    model: str
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    currency: str


@dataclass(slots=True, frozen=True)
class CostEstimate:
    """Result of a cost calculation."""

    estimated_input_cost: Decimal | None
    estimated_output_cost: Decimal | None
    estimated_total_cost: Decimal | None
    currency: str
    price_catalog_id: uuid.UUID | None
    status: str  # "complete", "partial", "unavailable"


class PricingService:
    """Resolve model prices and compute per-request cost estimates.

    Prices are loaded from ``price_catalog`` into an in-memory dict on
    startup and refreshed when the catalog changes (30 s poll interval).
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], PriceCatalogEntry] = {}
        self._last_updated_at: datetime | None = None

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    async def load_cache(self, session: AsyncSession) -> None:
        """Load all active price catalog rows into memory."""
        result = await session.execute(
            select(PriceCatalog).where(PriceCatalog.active.is_(True)),
        )
        rows = result.scalars().all()
        new_cache: dict[tuple[str, str], PriceCatalogEntry] = {}
        latest: datetime | None = None
        for row in rows:
            key = (row.provider.lower(), row.model.lower())
            new_cache[key] = PriceCatalogEntry(
                id=row.id,
                provider=row.provider,
                model=row.model,
                input_price_per_1k=Decimal(str(row.input_price_per_1k)),
                output_price_per_1k=Decimal(str(row.output_price_per_1k)),
                currency=row.currency,
            )
            if latest is None or row.updated_at > latest:
                latest = row.updated_at
        self._cache = new_cache
        self._last_updated_at = latest
        log.info("Price catalog loaded: %d active entries", len(self._cache))

    async def check_invalidation(self, session: AsyncSession) -> None:
        """Reload cache if any price catalog row has been modified."""
        from sqlalchemy import func, select as sa_select  # noqa: PLC0415

        result = await session.execute(
            sa_select(func.max(PriceCatalog.updated_at)),
        )
        latest = result.scalar_one_or_none()
        if latest is not None and (
            self._last_updated_at is None or latest > self._last_updated_at
        ):
            await self.load_cache(session)

    # ------------------------------------------------------------------
    # Resolution & calculation
    # ------------------------------------------------------------------

    def resolve(self, provider: str, model: str) -> PriceCatalogEntry | None:
        """Look up the active price for a provider/model pair."""
        return self._cache.get((provider.lower(), model.lower()))

    def compute_cost(
        self,
        *,
        provider_name: str | None,
        model_alias: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cached_tokens: int | None = None,
    ) -> CostEstimate:
        """Compute estimated cost for a single request.

        Returns an ``unavailable`` estimate when provider/model or token
        counts are entirely missing, ``partial`` when only some token
        counts are present, and ``complete`` when all inputs are known.
        """
        if not provider_name or not model_alias:
            return CostEstimate(
                estimated_input_cost=None,
                estimated_output_cost=None,
                estimated_total_cost=None,
                currency="USD",
                price_catalog_id=None,
                status="unavailable",
            )

        entry = self.resolve(provider_name, model_alias)
        if entry is None:
            return CostEstimate(
                estimated_input_cost=None,
                estimated_output_cost=None,
                estimated_total_cost=None,
                currency="USD",
                price_catalog_id=None,
                status="unavailable",
            )

        has_prompt = prompt_tokens is not None
        has_completion = completion_tokens is not None

        if not has_prompt and not has_completion:
            return CostEstimate(
                estimated_input_cost=None,
                estimated_output_cost=None,
                estimated_total_cost=None,
                currency=entry.currency,
                price_catalog_id=entry.id,
                status="unavailable",
            )

        input_cost: Decimal | None = None
        output_cost: Decimal | None = None

        if has_prompt:
            effective_input = prompt_tokens
            if cached_tokens is not None and cached_tokens > 0:
                effective_input = max(prompt_tokens - cached_tokens, 0)
            input_cost = (
                Decimal(str(effective_input)) / Decimal("1000")
            ) * entry.input_price_per_1k

        if has_completion:
            output_cost = (
                Decimal(str(completion_tokens)) / Decimal("1000")
            ) * entry.output_price_per_1k

        total_cost = (input_cost or Decimal(0)) + (output_cost or Decimal(0))
        status = "complete" if has_prompt and has_completion else "partial"

        return CostEstimate(
            estimated_input_cost=input_cost,
            estimated_output_cost=output_cost,
            estimated_total_cost=total_cost,
            currency=entry.currency,
            price_catalog_id=entry.id,
            status=status,
        )
