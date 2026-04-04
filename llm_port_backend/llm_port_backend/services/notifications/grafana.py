"""Grafana webhook payload normalisation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedAlert:
    """A Grafana webhook payload normalised for the notification outbox."""

    subject: str
    severity: str
    fingerprint: str
    summary: str
    details: str
    source: str
    occurred_at: datetime | None


_SEVERITY_MAP = {
    "alerting": "critical",
    "no_data": "warning",
    "ok": "info",
    "pending": "warning",
}


def normalize_grafana_alert(payload: dict[str, Any]) -> NormalizedAlert:
    """Transform a raw Grafana webhook payload into a :class:`NormalizedAlert`.

    The function is intentionally tolerant of missing or unexpected keys
    so that partial / malformed webhooks do not crash the handler.
    """
    title = payload.get("title") or "Grafana Alert"
    state = str(payload.get("state") or "unknown").lower()
    severity = _SEVERITY_MAP.get(state, "warning")

    alerts: list[dict[str, Any]] = payload.get("alerts") or []
    alert_count = len(alerts)

    # Build a stable fingerprint from title + labels of the first alert.
    fp_parts = [title]
    if alerts:
        labels = alerts[0].get("labels") or {}
        for k in sorted(labels):
            fp_parts.append(f"{k}={labels[k]}")
    fingerprint = "grafana:" + hashlib.sha256(
        "|".join(fp_parts).encode()
    ).hexdigest()[:24]

    summary = f"{title} — state={state}, alerts={alert_count}"

    detail_lines: list[str] = []
    for i, alert in enumerate(alerts[:5]):
        labels = alert.get("labels") or {}
        annotations = alert.get("annotations") or {}
        desc = annotations.get("description") or annotations.get("summary") or ""
        detail_lines.append(
            f"  [{i + 1}] labels={labels!r}  description={desc!r}"
        )
    details = "\n".join(detail_lines) if detail_lines else "(no alert details)"

    occurred_at: datetime | None = None
    if alerts:
        starts_at = alerts[0].get("startsAt")
        if isinstance(starts_at, str):
            try:
                occurred_at = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
            except ValueError:
                occurred_at = datetime.now(tz=UTC)

    return NormalizedAlert(
        subject=summary,
        severity=severity,
        fingerprint=fingerprint,
        summary=summary,
        details=details,
        source="grafana-webhook",
        occurred_at=occurred_at,
    )
