"""HMAC-based command signature verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

_MAX_COMMAND_AGE_SEC = 300  # 5 minutes


def derive_signing_key(credential: str) -> bytes:
    """Derive an HMAC signing key from the agent credential."""
    return hmac.new(
        credential.encode("utf-8"),
        b"command-signing",
        hashlib.sha256,
    ).digest()


def _canonical_payload(command: dict[str, Any]) -> bytes:
    """Deterministic JSON serialization of command fields, excluding signature."""
    filtered = {k: v for k, v in sorted(command.items()) if k != "signature"}
    return json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_command_signature(command: dict[str, Any], signing_key: bytes) -> bool:
    """Verify the HMAC-SHA256 signature on a command envelope."""
    signature = command.get("signature")
    if not isinstance(signature, str) or not signature:
        return False
    expected = hmac.new(signing_key, _canonical_payload(command), hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def validate_command_age(command: dict[str, Any]) -> bool:
    """Reject commands whose issued_at is too old."""
    issued_at = command.get("issued_at")
    if not isinstance(issued_at, str):
        # If backend doesn't send issued_at, allow (don't break backward compat)
        return True
    try:
        issued = datetime.fromisoformat(issued_at)
    except (ValueError, TypeError):
        return False
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=UTC)
    age = (datetime.now(tz=UTC) - issued).total_seconds()
    return age <= _MAX_COMMAND_AGE_SEC
