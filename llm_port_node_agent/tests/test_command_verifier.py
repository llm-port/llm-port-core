"""Tests for HMAC command signature verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from llm_port_node_agent.command_verifier import (
    _canonical_payload,
    derive_signing_key,
    validate_command_age,
    verify_command_signature,
)


def _sign(command: dict, key: bytes) -> str:
    import hashlib
    import hmac
    import json

    filtered = {k: v for k, v in sorted(command.items()) if k != "signature"}
    canon = json.dumps(filtered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, canon, hashlib.sha256).hexdigest()


def test_derive_signing_key_deterministic() -> None:
    key1 = derive_signing_key("cred-abc")
    key2 = derive_signing_key("cred-abc")
    assert key1 == key2
    assert len(key1) == 32  # SHA-256 digest


def test_derive_signing_key_differs_per_credential() -> None:
    assert derive_signing_key("cred-a") != derive_signing_key("cred-b")


def test_canonical_payload_excludes_signature() -> None:
    cmd = {"id": "1", "type": "cmd", "signature": "abc"}
    canon = _canonical_payload(cmd)
    assert b"signature" not in canon


def test_verify_valid_signature() -> None:
    key = derive_signing_key("test-cred")
    cmd = {"id": "cmd-1", "command_type": "deploy_workload", "payload": {}}
    cmd["signature"] = _sign(cmd, key)
    assert verify_command_signature(cmd, key) is True


def test_verify_tampered_payload() -> None:
    key = derive_signing_key("test-cred")
    cmd = {"id": "cmd-1", "command_type": "deploy_workload", "payload": {}}
    cmd["signature"] = _sign(cmd, key)
    cmd["payload"] = {"injected": True}
    assert verify_command_signature(cmd, key) is False


def test_verify_missing_signature() -> None:
    key = derive_signing_key("test-cred")
    cmd = {"id": "cmd-1", "command_type": "deploy_workload"}
    assert verify_command_signature(cmd, key) is False


def test_verify_wrong_key() -> None:
    key1 = derive_signing_key("cred-a")
    key2 = derive_signing_key("cred-b")
    cmd = {"id": "cmd-1", "command_type": "deploy_workload"}
    cmd["signature"] = _sign(cmd, key1)
    assert verify_command_signature(cmd, key2) is False


def test_validate_command_age_recent() -> None:
    cmd = {"issued_at": datetime.now(tz=UTC).isoformat()}
    assert validate_command_age(cmd) is True


def test_validate_command_age_expired() -> None:
    old = datetime.now(tz=UTC) - timedelta(minutes=10)
    cmd = {"issued_at": old.isoformat()}
    assert validate_command_age(cmd) is False


def test_validate_command_age_missing_field() -> None:
    # Missing issued_at should not reject (backward compat)
    assert validate_command_age({}) is True


def test_validate_command_age_invalid_format() -> None:
    cmd = {"issued_at": "not-a-date"}
    assert validate_command_age(cmd) is False
