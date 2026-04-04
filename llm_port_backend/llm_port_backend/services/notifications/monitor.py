"""GatewayAlertMonitor — 5xx error rate monitoring against the gateway DB."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_port_backend.services.notifications.service import NotificationService
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 60


class GatewayAlertMonitor:
    """Periodically checks the gateway request log for 5xx spikes.

    Uses the backend's secondary engine (``gateway_session_factory``) to
    query ``llm_gateway_request_log`` via raw SQL, then enqueues an admin
    alert through :class:`NotificationService` when the error rate
    exceeds the configured threshold.
    """

    def __init__(
        self,
        backend_session_factory: async_sessionmaker[AsyncSession],
        gateway_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._backend_sf = backend_session_factory
        self._gateway_sf = gateway_session_factory
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.get_event_loop().create_task(
            self._run(), name="gateway-alert-monitor",
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._check()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Gateway alert monitor check failed.")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _check(self) -> None:
        window = timedelta(minutes=settings.mailer_alert_5xx_window_minutes)
        threshold = settings.mailer_alert_5xx_threshold_percent
        cutoff = datetime.now(tz=UTC) - window

        async with self._gateway_sf() as gw_session:
            result = await gw_session.execute(
                text(
                    "SELECT "
                    "  count(*) AS total, "
                    "  count(*) FILTER (WHERE status_code >= 500) AS errors "
                    "FROM llm_gateway_request_log "
                    "WHERE created_at >= :cutoff"
                ),
                {"cutoff": cutoff},
            )
            row = result.one()
            total, errors = row[0], row[1]

        if total == 0:
            return

        error_pct = (errors / total) * 100
        if error_pct < threshold:
            return

        async with self._backend_sf() as session:
            service = NotificationService(session)
            queued = await service.maybe_enqueue_admin_alert(
                subject="Gateway 5xx error rate spike",
                severity="critical",
                fingerprint=f"gateway_5xx_rate:{settings.mailer_alert_5xx_window_minutes}m",
                summary=(
                    f"Gateway 5xx error rate is {error_pct:.1f}% "
                    f"({errors}/{total} requests in the last "
                    f"{settings.mailer_alert_5xx_window_minutes} minutes)."
                ),
                details=(
                    f"Threshold: {threshold}%. Window: "
                    f"{settings.mailer_alert_5xx_window_minutes} minutes."
                ),
                source="llm_port_backend.gateway_alert_monitor",
                occurred_at=datetime.now(tz=UTC),
            )
            if queued:
                await session.commit()
