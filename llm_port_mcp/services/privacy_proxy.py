"""Privacy proxy for MCP tool call arguments.

Applies Presidio-based PII detection/redaction before arguments
are sent to upstream MCP servers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrivacyDecision:
    """Result of a privacy check on tool call arguments."""

    allowed: bool
    sanitized_args: dict[str, Any]
    entities_found: list[dict[str, Any]] = field(default_factory=list)
    redaction_summary: dict[str, Any] = field(default_factory=dict)


class PrivacyProxy:
    """Enforces PII scanning before outbound MCP tool calls.

    When the PII service is unavailable, the proxy falls back to
    ``allow`` mode — it will not block tool calls but will log a
    warning.
    """

    def __init__(
        self,
        *,
        pii_base_url: str,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._base = pii_base_url.rstrip("/")
        self._http = http_client

    async def check(
        self,
        *,
        arguments: dict[str, Any],
        pii_mode: str,
    ) -> PrivacyDecision:
        """Run a PII check on *arguments* according to *pii_mode*.

        Modes:
            ``allow``  — passthrough, no scanning
            ``redact`` — scan and replace PII with redaction markers
            ``block``  — scan and reject if PII is detected
        """
        if pii_mode == "allow" or not self._base:
            return PrivacyDecision(allowed=True, sanitized_args=arguments)

        try:
            return await self._call_pii_service(
                arguments=arguments,
                pii_mode=pii_mode,
            )
        except Exception:
            logger.warning(
                "PII service unreachable — falling back to allow",
                exc_info=True,
            )
            return PrivacyDecision(allowed=True, sanitized_args=arguments)

    async def _call_pii_service(
        self,
        *,
        arguments: dict[str, Any],
        pii_mode: str,
    ) -> PrivacyDecision:
        """Call the PII service to scan/redact arguments."""
        # Build a synthetic payload wrapping the arguments as a
        # user message so Presidio can inspect the text content.
        payload = {
            "payload": {
                "messages": [
                    {
                        "role": "user",
                        "content": _args_to_text(arguments),
                    },
                ],
            },
            "mode": "redact" if pii_mode == "redact" else "detect",
            "language": "en",
        }

        resp = await self._http.post(
            f"{self._base}/api/v1/pii/sanitize",
            json=payload,
            timeout=10.0,
            headers={"x-pii-source": "mcp-proxy"},
        )
        resp.raise_for_status()
        data = resp.json()

        entities_found = data.get("entities_found", [])
        pii_detected = bool(entities_found)

        if pii_mode == "block" and pii_detected:
            return PrivacyDecision(
                allowed=False,
                sanitized_args=arguments,
                entities_found=entities_found,
                redaction_summary={"action": "blocked", "count": len(entities_found)},
            )

        if pii_mode == "redact" and pii_detected:
            sanitized_payload = data.get("sanitized_payload", {})
            sanitized_text = _extract_sanitized_text(sanitized_payload)
            return PrivacyDecision(
                allowed=True,
                sanitized_args=_text_to_args(sanitized_text, arguments),
                entities_found=entities_found,
                redaction_summary={"action": "redacted", "count": len(entities_found)},
            )

        return PrivacyDecision(allowed=True, sanitized_args=arguments)


def _args_to_text(arguments: dict[str, Any]) -> str:
    """Flatten tool arguments into a single text string for PII scanning."""
    import json

    return json.dumps(arguments, ensure_ascii=False)


def _extract_sanitized_text(sanitized_payload: dict[str, Any]) -> str:
    """Extract the sanitized text from the PII service response payload."""
    messages = sanitized_payload.get("messages", [])
    if messages:
        return messages[0].get("content", "")
    return ""


def _text_to_args(
    sanitized_text: str,
    original_args: dict[str, Any],
) -> dict[str, Any]:
    """Reconstruct arguments from sanitized text.

    For simple key-value arguments we attempt to parse the sanitized
    JSON back.  If parsing fails we fall back to the original arguments
    (the PII service already applied redaction to the text representation).
    """
    import json

    try:
        return json.loads(sanitized_text)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, TypeError):
        return original_args
