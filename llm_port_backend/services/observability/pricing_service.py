"""CRUD operations on the ``price_catalog`` table.

All queries run against the gateway (``llm_api``) database via the
secondary ``llm_graph_trace_session_factory`` engine.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)


class PricingCRUDService:
    """Admin CRUD for price_catalog rows (raw SQL, no ORM coupling)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── List active ───────────────────────────────────────────────

    async def list_active(self) -> list[dict]:
        q = text("""
            SELECT id, provider, model, input_price_per_1k, output_price_per_1k,
                   currency, effective_from, active, source, notes,
                   created_at, updated_at
            FROM price_catalog
            WHERE active = TRUE
            ORDER BY provider, model
        """)
        result = await self._session.execute(q)
        rows = []
        for r in result.mappings().all():
            d = dict(r)
            d["id"] = str(d["id"])
            rows.append(d)
        return rows

    # ── Create ────────────────────────────────────────────────────

    async def create(
        self,
        provider: str,
        model: str,
        input_price_per_1k: Decimal,
        output_price_per_1k: Decimal,
        currency: str = "USD",
        notes: str | None = None,
    ) -> dict:
        new_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        q = text("""
            INSERT INTO price_catalog
                (id, provider, model, input_price_per_1k, output_price_per_1k,
                 currency, effective_from, active, source, notes, created_at, updated_at)
            VALUES
                (:id, :provider, :model, :input_price, :output_price,
                 :currency, :now, TRUE, 'admin', :notes, :now, :now)
            RETURNING id, provider, model, input_price_per_1k, output_price_per_1k,
                      currency, effective_from, active, source, notes, created_at, updated_at
        """)
        result = await self._session.execute(q, {
            "id": new_id,
            "provider": provider.strip().lower(),
            "model": model.strip().lower(),
            "input_price": input_price_per_1k,
            "output_price": output_price_per_1k,
            "currency": currency,
            "now": now,
            "notes": notes,
        })
        await self._session.commit()
        row = dict(result.mappings().one())
        row["id"] = str(row["id"])
        return row

    # ── Update (deactivate old → insert new) ─────────────────────

    async def update(
        self,
        entry_id: str,
        input_price_per_1k: Decimal,
        output_price_per_1k: Decimal,
        currency: str | None = None,
        notes: str | None = None,
    ) -> dict:
        """Deactivate the old entry and create a new one with updated prices."""
        # Fetch old entry
        fetch_q = text("""
            SELECT provider, model, currency, notes
            FROM price_catalog
            WHERE id = :id AND active = TRUE
        """)
        result = await self._session.execute(fetch_q, {"id": entry_id})
        old = result.mappings().first()
        if old is None:
            raise ValueError(f"Active price entry {entry_id} not found")
        old = dict(old)

        # Deactivate old
        now = datetime.now(timezone.utc)
        deactivate_q = text("""
            UPDATE price_catalog SET active = FALSE, updated_at = :now
            WHERE id = :id
        """)
        await self._session.execute(deactivate_q, {"id": entry_id, "now": now})

        # Insert new
        new_id = uuid.uuid4()
        insert_q = text("""
            INSERT INTO price_catalog
                (id, provider, model, input_price_per_1k, output_price_per_1k,
                 currency, effective_from, active, source, notes, created_at, updated_at)
            VALUES
                (:id, :provider, :model, :input_price, :output_price,
                 :currency, :now, TRUE, 'admin', :notes, :now, :now)
            RETURNING id, provider, model, input_price_per_1k, output_price_per_1k,
                      currency, effective_from, active, source, notes, created_at, updated_at
        """)
        result = await self._session.execute(insert_q, {
            "id": new_id,
            "provider": old["provider"],
            "model": old["model"],
            "input_price": input_price_per_1k,
            "output_price": output_price_per_1k,
            "currency": currency or old["currency"],
            "now": now,
            "notes": notes if notes is not None else old["notes"],
        })
        await self._session.commit()
        row = dict(result.mappings().one())
        row["id"] = str(row["id"])
        return row

    # ── Deactivate ────────────────────────────────────────────────

    async def deactivate(self, entry_id: str) -> None:
        now = datetime.now(timezone.utc)
        q = text("""
            UPDATE price_catalog SET active = FALSE, updated_at = :now
            WHERE id = :id AND active = TRUE
        """)
        result = await self._session.execute(q, {"id": entry_id, "now": now})
        await self._session.commit()
        if result.rowcount == 0:
            raise ValueError(f"Active price entry {entry_id} not found")

    # ── History ───────────────────────────────────────────────────

    async def get_history(self, provider: str, model: str) -> list[dict]:
        q = text("""
            SELECT id, provider, model, input_price_per_1k, output_price_per_1k,
                   currency, effective_from, active, source, notes,
                   created_at, updated_at
            FROM price_catalog
            WHERE LOWER(provider) = LOWER(:provider) AND LOWER(model) = LOWER(:model)
            ORDER BY effective_from DESC
            LIMIT 100
        """)
        result = await self._session.execute(q, {"provider": provider, "model": model})
        rows = []
        for r in result.mappings().all():
            d = dict(r)
            d["id"] = str(d["id"])
            rows.append(d)
        return rows
