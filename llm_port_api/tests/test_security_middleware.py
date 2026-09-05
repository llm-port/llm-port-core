"""Unit tests for security middleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_port_api.web.middleware.security import (
    HTTPSRedirectMiddleware,
    SecurityHeadersMiddleware,
)


def _make_app() -> FastAPI:
    app = FastAPI()

    @app.get("/ping")
    def _ping() -> dict:
        return {"ok": True}

    return app


def test_security_headers_default() -> None:
    app = _make_app()
    app.add_middleware(SecurityHeadersMiddleware)
    client = TestClient(app)
    r = client.get("/ping")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "strict-transport-security" not in r.headers  # http, no HSTS


def test_hsts_set_only_for_https() -> None:
    app = _make_app()
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_max_age=31536000,
        hsts_include_subdomains=True,
    )
    client = TestClient(app)
    r = client.get("/ping")
    # TestClient defaults to http scheme — HSTS must NOT be set.
    assert "strict-transport-security" not in r.headers


def test_https_redirect_middleware() -> None:
    app = _make_app()
    app.add_middleware(HTTPSRedirectMiddleware)
    client = TestClient(app)
    r = client.get("/ping", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"].startswith("https://")
