"""DAO for pii_scan_events — insert + aggregate queries."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.pii import PIIScanEvent


class PIIEventDAO:
    """Read/write access to ``pii_scan_events``."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    # ── Write ─────────────────────────────────────────────────────────

    async def log_event(
        self,
        *,
        operation: str,
        mode: str | None = None,
        language: str = "en",
        score_threshold: float = 0.6,
        pii_detected: bool = False,
        entities_found: int = 0,
        entity_type_counts: dict[str, int] | None = None,
        source: str = "api",
        request_id: str | None = None,
    ) -> PIIScanEvent:
        """Insert a new scan event row."""
        event = PIIScanEvent(
            id=uuid.uuid4(),
            operation=operation,
            mode=mode,
            language=language,
            score_threshold=score_threshold,
            pii_detected=pii_detected,
            entities_found=entities_found,
            entity_type_counts=entity_type_counts,
            source=source,
            request_id=request_id,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    # ── Dashboard stats ───────────────────────────────────────────────

    async def get_stats(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """Return aggregate statistics for the dashboard.

        Response shape::

            {
                "total_scans": int,
                "total_with_pii": int,
                "total_entities": int,
                "detection_rate": float,         # 0.0 – 1.0
                "entity_type_breakdown": {str: int},
                "operation_breakdown": {str: int},
                "source_breakdown": {str: int},
                "daily_volume": [{"date": str, "count": int, "pii_count": int}],
            }
        """
        base = select(PIIScanEvent)
        if since:
            base = base.where(PIIScanEvent.created_at >= since)
        if until:
            base = base.where(PIIScanEvent.created_at <= until)

        # Totals
        totals_q = select(
            func.count().label("total"),
            func.sum(case((PIIScanEvent.pii_detected.is_(True), 1), else_=0)).label(
                "with_pii",
            ),
            func.coalesce(func.sum(PIIScanEvent.entities_found), 0).label(
                "total_entities",
            ),
        ).select_from(base.subquery())

        row = (await self.session.execute(totals_q)).one()
        total_scans = row.total or 0
        total_with_pii = int(row.with_pii or 0)
        total_entities = int(row.total_entities or 0)
        detection_rate = round(total_with_pii / total_scans, 4) if total_scans else 0.0

        # Operation breakdown
        op_q = (
            select(
                PIIScanEvent.operation,
                func.count().label("cnt"),
            )
            .where(
                PIIScanEvent.created_at >= since if since else True,
                PIIScanEvent.created_at <= until if until else True,
            )
            .group_by(PIIScanEvent.operation)
        )
        op_rows = (await self.session.execute(op_q)).all()
        operation_breakdown = {r.operation: r.cnt for r in op_rows}

        # Source breakdown
        src_q = (
            select(
                PIIScanEvent.source,
                func.count().label("cnt"),
            )
            .where(
                PIIScanEvent.created_at >= since if since else True,
                PIIScanEvent.created_at <= until if until else True,
            )
            .group_by(PIIScanEvent.source)
        )
        src_rows = (await self.session.execute(src_q)).all()
        source_breakdown = {r.source: r.cnt for r in src_rows}

        # Entity-type breakdown (aggregate JSON column)
        all_events_q = select(PIIScanEvent.entity_type_counts).where(
            PIIScanEvent.entity_type_counts.isnot(None),
            PIIScanEvent.created_at >= since if since else True,
            PIIScanEvent.created_at <= until if until else True,
        )
        et_rows = (await self.session.execute(all_events_q)).scalars().all()
        entity_counter: Counter[str] = Counter()
        for blob in et_rows:
            if isinstance(blob, dict):
                for etype, cnt in blob.items():
                    entity_counter[etype] += cnt

        # Daily volume (last 30 days if no range given)
        effective_since = since or (datetime.now(timezone.utc) - timedelta(days=30))
        daily_q = (
            select(
                func.date_trunc("day", PIIScanEvent.created_at).label("day"),
                func.count().label("cnt"),
                func.sum(
                    case((PIIScanEvent.pii_detected.is_(True), 1), else_=0),
                ).label("pii_cnt"),
            )
            .where(
                PIIScanEvent.created_at >= effective_since,
                PIIScanEvent.created_at <= until if until else True,
            )
            .group_by(text("1"))
            .order_by(text("1"))
        )
        daily_rows = (await self.session.execute(daily_q)).all()
        daily_volume = [
            {
                "date": r.day.strftime("%Y-%m-%d") if r.day else "",
                "count": r.cnt,
                "pii_count": int(r.pii_cnt or 0),
            }
            for r in daily_rows
        ]

        return {
            "total_scans": total_scans,
            "total_with_pii": total_with_pii,
            "total_entities": total_entities,
            "detection_rate": detection_rate,
            "entity_type_breakdown": dict(entity_counter),
            "operation_breakdown": operation_breakdown,
            "source_breakdown": source_breakdown,
            "daily_volume": daily_volume,
        }

    # ── Paginated event list ──────────────────────────────────────────

    async def list_events(
        self,
        *,
        operation: str | None = None,
        source: str | None = None,
        pii_only: bool = False,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PIIScanEvent]:
        """Return paginated event rows (newest first)."""
        q = select(PIIScanEvent)
        if operation:
            q = q.where(PIIScanEvent.operation == operation)
        if source:
            q = q.where(PIIScanEvent.source == source)
        if pii_only:
            q = q.where(PIIScanEvent.pii_detected.is_(True))
        if since:
            q = q.where(PIIScanEvent.created_at >= since)
        q = q.order_by(PIIScanEvent.created_at.desc()).limit(limit).offset(offset)
        rows = (await self.session.execute(q)).scalars().all()
        return list(rows)

    async def count_events(
        self,
        *,
        operation: str | None = None,
        source: str | None = None,
        pii_only: bool = False,
        since: datetime | None = None,
    ) -> int:
        """Return total count for the same filters."""
        q = select(func.count()).select_from(PIIScanEvent)
        if operation:
            q = q.where(PIIScanEvent.operation == operation)
        if source:
            q = q.where(PIIScanEvent.source == source)
        if pii_only:
            q = q.where(PIIScanEvent.pii_detected.is_(True))
        if since:
            q = q.where(PIIScanEvent.created_at >= since)
        return (await self.session.execute(q)).scalar() or 0
