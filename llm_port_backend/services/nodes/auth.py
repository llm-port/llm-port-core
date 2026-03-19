"""Credential and token helpers for node control."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def random_secret(length_bytes: int = 32) -> str:
    """Return a URL-safe random secret."""
    return secrets.token_urlsafe(length_bytes)


def hash_with_pepper(value: str, *, pepper: str) -> str:
    """Return SHA256 hash bound to a deployment-local pepper."""
    payload = f"{pepper}:{value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    """Compare digests in constant time."""
    return hmac.compare_digest(a, b)
