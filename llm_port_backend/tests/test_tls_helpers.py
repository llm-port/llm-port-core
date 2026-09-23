"""Tests for connection-site TLS helpers."""

from __future__ import annotations

import ssl

import pytest

from llm_port_backend.services.tls import (
    SSLMode,
    build_asyncpg_ssl,
    build_httpx_verify,
    build_redis_ssl_kwargs,
    default_httpx_verify,
    rewrite_amqp_url_for_tls,
)


class TestBuildAsyncpgSsl:
    def test_disable_returns_false(self) -> None:
        assert build_asyncpg_ssl("disable") is False

    def test_prefer_returns_string(self) -> None:
        assert build_asyncpg_ssl("prefer") == "prefer"

    def test_require_returns_unverified_ctx(self) -> None:
        ctx = build_asyncpg_ssl("require")
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False

    def test_verify_ca_requires_cert_no_hostname(self) -> None:
        ctx = build_asyncpg_ssl("verify-ca")
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is False

    def test_verify_full_requires_cert_and_hostname(self) -> None:
        ctx = build_asyncpg_ssl(SSLMode.VERIFY_FULL)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_invalid_mode_raises(self) -> None:
        with pytest.raises(ValueError):
            build_asyncpg_ssl("bogus")


class TestBuildHttpxVerify:
    def test_disabled(self) -> None:
        assert build_httpx_verify(False, None) is False

    def test_default_trust_store_is_one_shared_context(self) -> None:
        # Built once: each build loads the CA bundle, ~400 ms on Windows,
        # on the event loop.
        ctx = build_httpx_verify(True, None)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx is default_httpx_verify() is build_httpx_verify(True, None)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.get_ca_certs(), "the default trust store is loaded"

    def test_custom_bundle_is_trusted_and_built_once(self) -> None:
        import certifi

        ctx = build_httpx_verify(True, certifi.where())
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx is build_httpx_verify(True, certifi.where())
        assert ctx is not default_httpx_verify()

    def test_a_missing_bundle_fails_loudly(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            build_httpx_verify(True, "/nonexistent/custom.pem")


class TestBuildRedisSslKwargs:
    def test_disabled_returns_empty(self) -> None:
        assert build_redis_ssl_kwargs(False, None) == {}

    def test_enabled_required_certs(self) -> None:
        kwargs = build_redis_ssl_kwargs(True, None)
        assert kwargs == {"ssl": True, "ssl_cert_reqs": "required"}

    def test_with_ca(self) -> None:
        kwargs = build_redis_ssl_kwargs(True, "/etc/ssl/ca.pem")
        assert kwargs["ssl_ca_certs"] == "/etc/ssl/ca.pem"


class TestRewriteAmqpUrlForTls:
    def test_disabled_passthrough(self) -> None:
        assert rewrite_amqp_url_for_tls("amqp://x:5672/", False) == "amqp://x:5672/"

    def test_upgrades_amqp_to_amqps(self) -> None:
        out = rewrite_amqp_url_for_tls("amqp://u:p@host:5672/v", True)
        assert out.startswith("amqps://")

    def test_amqps_unchanged(self) -> None:
        url = "amqps://u:p@host:5671/v"
        assert rewrite_amqp_url_for_tls(url, True) == url
