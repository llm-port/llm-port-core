"""Unit tests for ``llm_port_api.services.gateway.ssl_helpers``.

These tests don't touch litellm — they verify the kwargs we'd pass to it.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from llm_port_api.services.gateway import ssl_helpers as mod


@pytest.fixture(autouse=True)
def _isolate_cache(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(mod, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(mod, "_FILE_CACHE", {})
    yield


def test_default_mode_returns_empty() -> None:
    out = mod.build_ssl_kwargs(
        instance_id=uuid.uuid4(),
        ssl_verify_mode=None,
        ssl_ca_bundle_pem=None,
        ssl_client_cert_pem=None,
        ssl_client_key_pem=None,
    )
    assert out == {}


def test_verify_with_system_trust() -> None:
    out = mod.build_ssl_kwargs(
        instance_id=uuid.uuid4(),
        ssl_verify_mode="verify",
        ssl_ca_bundle_pem=None,
        ssl_client_cert_pem=None,
        ssl_client_key_pem=None,
    )
    assert out == {"ssl_verify": True}


def test_verify_custom_ca_materialises_pem() -> None:
    iid = uuid.uuid4()
    pem = "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n"
    out = mod.build_ssl_kwargs(
        instance_id=iid,
        ssl_verify_mode="verify_custom_ca",
        ssl_ca_bundle_pem=pem,
        ssl_client_cert_pem=None,
        ssl_client_key_pem=None,
    )
    p = Path(out["ssl_verify"])
    assert p.exists()
    assert p.read_text() == pem


def test_mtls_combines_cert_and_key() -> None:
    iid = uuid.uuid4()
    cert = "-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----\n"
    key = "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----\n"
    out = mod.build_ssl_kwargs(
        instance_id=iid,
        ssl_verify_mode="verify",
        ssl_ca_bundle_pem=None,
        ssl_client_cert_pem=cert,
        ssl_client_key_pem=key,
    )
    written = Path(out["ssl_certificate"]).read_text()
    assert "BEGIN CERTIFICATE" in written
    assert "BEGIN PRIVATE KEY" in written


def test_insecure_requires_global_opt_in(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(mod.settings, "tls_outbound_allow_insecure", False)
    out = mod.build_ssl_kwargs(
        instance_id=uuid.uuid4(),
        ssl_verify_mode="insecure",
        ssl_ca_bundle_pem=None,
        ssl_client_cert_pem=None,
        ssl_client_key_pem=None,
    )
    # Without global opt-in the insecure mode is silently downgraded.
    assert "ssl_verify" not in out or out.get("ssl_verify") is True


def test_insecure_with_opt_in(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(mod.settings, "tls_outbound_allow_insecure", True)
    out = mod.build_ssl_kwargs(
        instance_id=uuid.uuid4(),
        ssl_verify_mode="insecure",
        ssl_ca_bundle_pem=None,
        ssl_client_cert_pem=None,
        ssl_client_key_pem=None,
    )
    assert out == {"ssl_verify": False}
