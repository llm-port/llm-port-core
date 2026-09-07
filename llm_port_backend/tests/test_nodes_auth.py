"""Unit tests for the node-control credential helpers (`services/nodes/auth`).

Pure functions over `secrets` / `hashlib` / `hmac` — no infrastructure.
"""

from __future__ import annotations

import base64
import re

from llm_port_backend.services.nodes.auth import (
    constant_time_equal,
    hash_with_pepper,
    random_secret,
)

_URLSAFE_RE = re.compile(r"^[A-Za-z0-9_-]*$")


def _urlsafe_len(n_bytes: int) -> int:
    """Exact length of ``secrets.token_urlsafe(n)``.

    That is urlsafe-base64 of ``n`` random bytes with the trailing ``=``
    padding stripped — so, e.g., 32 bytes → 43 chars, 16 bytes → 22 chars.
    """
    return len(base64.urlsafe_b64encode(b"\x00" * n_bytes).rstrip(b"="))


def test_random_secret_is_urlsafe_and_correct_length() -> None:
    secret = random_secret()  # default 32 bytes
    assert _URLSAFE_RE.fullmatch(secret) is not None
    assert len(secret) == _urlsafe_len(32)


def test_random_secret_respects_length_argument() -> None:
    assert len(random_secret(16)) == _urlsafe_len(16)
    assert len(random_secret(1)) == _urlsafe_len(1)


def test_random_secret_is_unpredictable() -> None:
    assert random_secret() != random_secret()


def test_hash_with_pepper_is_deterministic() -> None:
    a = hash_with_pepper("s0!secret", pepper="pep")
    b = hash_with_pepper("s0!secret", pepper="pep")
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{64}", a) is not None  # sha256 hex


def test_hash_with_pepper_depends_on_pepper() -> None:
    assert hash_with_pepper("s0!secret", pepper="pep-1") != hash_with_pepper("s0!secret", pepper="pep-2")


def test_hash_with_pepper_depends_on_value() -> None:
    assert hash_with_pepper("a", pepper="pep") != hash_with_pepper("b", pepper="pep")


def test_constant_time_equal_matches() -> None:
    assert constant_time_equal("abc", "abc") is True
    assert constant_time_equal("", "") is True


def test_constant_time_equal_rejects_mismatch_and_length() -> None:
    assert constant_time_equal("abc", "abd") is False
    assert constant_time_equal("abc", "abcd") is False
