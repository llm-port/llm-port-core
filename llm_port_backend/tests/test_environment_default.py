"""The backend must not run in dev mode unless asked to.

Dev mode seeds ``admin@localhost`` / ``admin`` and opens ``POST /auth/dev-login``,
which signs anyone in as that admin. Both must stay off by default.
"""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status

from llm_port_backend.settings import Settings, settings


def test_environment_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PORT_BACKEND_ENVIRONMENT", raising=False)
    assert Settings(_env_file=None).environment == "production"


@pytest.mark.anyio
async def test_dev_login_is_unavailable_outside_dev(
    fastapi_app: FastAPI,
    client: AsyncClient,
) -> None:
    assert settings.environment != "dev"  # the test suite runs as "pytest"
    resp = await client.post(fastapi_app.url_path_for("dev_login"))
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert "fapiauth" not in resp.cookies
