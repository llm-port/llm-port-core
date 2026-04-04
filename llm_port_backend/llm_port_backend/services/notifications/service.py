"""NotificationService — writes outbox rows and manages alert cooldown."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.notifications import (
    NotificationAlertState,
    NotificationOutbox,
)
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)


class NotificationService:
    """Per-request service that writes to the notification outbox.

    The caller owns the session and is responsible for calling
    ``session.commit()`` or ``session.flush()`` after enqueueing.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def maybe_enqueue_admin_alert(
        self,
        *,
        subject: str,
        severity: str,
        fingerprint: str,
        summary: str,
        details: str,
        source: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Enqueue an admin alert if the cooldown window has elapsed.

        Returns ``True`` if the alert was enqueued, ``False`` if suppressed.
        """
        now = datetime.now(tz=UTC)
        cooldown = timedelta(minutes=settings.mailer_alert_cooldown_minutes)

        result = await self._session.execute(
            select(NotificationAlertState).where(
                NotificationAlertState.fingerprint == fingerprint,
            ),
        )
        state = result.scalar_one_or_none()

        if state is not None and (now - state.last_sent_at) < cooldown:
            log.debug("Alert %s suppressed by cooldown.", fingerprint)
            return False

        # Resolve recipients.
        recipients = settings.mailer_admin_recipients
        if isinstance(recipients, str):
            recipients = [r.strip() for r in recipients.split(",") if r.strip()]
        if not recipients:
            log.warning("No admin recipients configured — alert '%s' dropped.", subject)
            return False

        payload = {
            "subject": subject,
            "severity": severity,
            "fingerprint": fingerprint,
            "summary": summary,
            "details": details,
            "source": source,
            "occurred_at": (occurred_at or now).isoformat(),
            "recipients": recipients,
        }

        self._session.add(
            NotificationOutbox(
                id=uuid.uuid4(),
                kind="admin_alert",
                payload_json=payload,
                status="pending",
            ),
        )

        # Update or create alert state.
        if state is not None:
            state.last_sent_at = now
            state.sent_count += 1
            state.last_payload_json = payload
        else:
            self._session.add(
                NotificationAlertState(
                    fingerprint=fingerprint,
                    last_sent_at=now,
                    sent_count=1,
                    last_payload_json=payload,
                ),
            )

        return True

    async def enqueue_password_reset(
        self,
        *,
        to_email: str,
        to_name: str | None,
        reset_url: str,
        request_id: str,
    ) -> None:
        """Enqueue a password-reset email for async delivery."""
        now = datetime.now(tz=UTC)
        payload = {
            "to_email": to_email,
            "to_name": to_name,
            "reset_url": reset_url,
            "requested_at": now.isoformat(),
            "request_id": request_id,
        }
        self._session.add(
            NotificationOutbox(
                id=uuid.uuid4(),
                kind="password_reset",
                payload_json=payload,
                status="pending",
            ),
        )
        await self._session.flush()
