"""NotificationDispatcher — polls the outbox and delivers via MailerClient."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_port_backend.db.models.notifications import NotificationOutbox
from llm_port_backend.services.notifications.mailer_client import MailerClient
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 10
_MAX_ATTEMPTS = 5


class NotificationDispatcher:
    """Background worker that drains the notification outbox.

    Polls for ``status='pending'`` rows whose ``next_attempt_at`` is past,
    then delivers them via :class:`MailerClient`.  Failures are retried
    with exponential back-off up to ``_MAX_ATTEMPTS``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        mailer_client: MailerClient,
    ) -> None:
        self._session_factory = session_factory
        self._mailer = mailer_client
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Launch the background polling loop."""
        if self._task is not None:
            return
        self._task = asyncio.get_event_loop().create_task(self._run(), name="notification-dispatcher")

    async def stop(self) -> None:
        """Gracefully cancel the polling task."""
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
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Notification dispatcher poll error.")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    async def _poll_once(self) -> None:
        # Skip delivery when mailer module is disabled — messages stay
        # queued and will be delivered once the module is turned on.
        if not settings.mailer_enabled:
            return

        now = datetime.now(tz=UTC)
        async with self._session_factory() as session:
            result = await session.execute(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.status == "pending",
                    NotificationOutbox.next_attempt_at <= now,
                )
                .order_by(NotificationOutbox.created_at)
                .limit(20),
            )
            rows = result.scalars().all()

        for row in rows:
            await self._deliver(row)

    async def _deliver(self, row: NotificationOutbox) -> None:
        try:
            if row.kind == "password_reset":
                await self._mailer.send_password_reset(row.payload_json)
            elif row.kind == "admin_alert":
                await self._mailer.send_admin_alert(row.payload_json)
            else:
                log.warning("Unknown notification kind: %s", row.kind)
                return

            async with self._session_factory() as session:
                await session.execute(
                    update(NotificationOutbox)
                    .where(NotificationOutbox.id == row.id)
                    .values(
                        status="sent",
                        sent_at=datetime.now(tz=UTC),
                    ),
                )
                await session.commit()
        except Exception as exc:
            log.warning("Delivery failed for %s (%s): %s", row.id, row.kind, exc)
            new_count = row.attempt_count + 1
            if new_count >= _MAX_ATTEMPTS:
                new_status = "failed"
                next_at = datetime.now(tz=UTC)
            else:
                new_status = "pending"
                next_at = datetime.now(tz=UTC) + timedelta(seconds=30 * (2 ** new_count))

            async with self._session_factory() as session:
                await session.execute(
                    update(NotificationOutbox)
                    .where(NotificationOutbox.id == row.id)
                    .values(
                        attempt_count=new_count,
                        status=new_status,
                        last_error=str(exc)[:2000],
                        next_attempt_at=next_at,
                    ),
                )
                await session.commit()
