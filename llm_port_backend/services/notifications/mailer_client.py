"""MailerClient — thin HTTP wrapper around the llm_port_mailer service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from llm_port_backend.settings import settings

log = logging.getLogger(__name__)


class MailerClient:
    """HTTP client for the mailer micro-service internal API."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    def _base_url(self) -> str:
        return settings.mailer_service_url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        token = settings.mailer_api_token
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    async def send_password_reset(self, payload: dict[str, Any]) -> None:
        """POST /internal/v1/messages/password-reset."""
        url = f"{self._base_url()}/internal/v1/messages/password-reset"
        resp = await self._http.post(
            url,
            json=payload,
            headers=self._auth_headers(),
            timeout=15.0,
        )
        resp.raise_for_status()

    async def send_admin_alert(self, payload: dict[str, Any]) -> None:
        """POST /internal/v1/messages/admin-alert."""
        url = f"{self._base_url()}/internal/v1/messages/admin-alert"
        resp = await self._http.post(
            url,
            json=payload,
            headers=self._auth_headers(),
            timeout=15.0,
        )
        resp.raise_for_status()
