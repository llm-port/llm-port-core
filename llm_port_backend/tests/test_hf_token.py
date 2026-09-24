"""The Hugging Face token: checked before it is kept, kept encrypted, never shown again."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.models.containers import AuditEvent
from llm_port_backend.db.models.system_settings import SystemSettingSecret
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.llm import hf_token
from llm_port_backend.services.system_settings.crypto import SettingsCrypto
from llm_port_backend.settings import settings

TOKEN = "hf_abcdefghijklmnopqrstuvwxyz0123456789"
URL = "/api/llm/settings/hf-token"


@pytest.fixture(autouse=True)
async def _isolated(monkeypatch: pytest.MonkeyPatch, fastapi_app: FastAPI, dbsession: AsyncSession) -> None:
    """No environment token, no remembered identities, an administrator signed in."""
    monkeypatch.delenv(hf_token.ENV_VAR, raising=False)
    monkeypatch.setattr(settings, "hf_token", None)
    # An installation's own key, whatever this environment defaults to.
    monkeypatch.setattr(settings, "settings_master_key", "test-installation-master-key-0123456789")
    hf_token._identity_cache.clear()
    admin = User(
        email=f"admin-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=True,
    )
    dbsession.add(admin)
    await dbsession.flush()
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin


def _hub_says(monkeypatch: pytest.MonkeyPatch, identity: hf_token.Identity) -> list[str]:
    asked: list[str] = []

    def whoami(token: str) -> hf_token.Identity:
        asked.append(token)
        return identity

    monkeypatch.setattr(hf_token, "_whoami_sync", whoami)
    return asked


async def _stored(dbsession: AsyncSession) -> SystemSettingSecret | None:
    return (await dbsession.execute(
        select(SystemSettingSecret).where(SystemSettingSecret.key == hf_token.HF_TOKEN_KEY),
    )).scalar_one_or_none()


@pytest.mark.anyio
async def test_a_good_token_is_kept_encrypted_and_never_answered_back(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hub_says(monkeypatch, hf_token.Identity(check="ok", username="sachith", token_name="llm-port", role="read"))

    response = await client.put(URL, json={"token": f"  {TOKEN}  "})
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["configured"] is True
    assert body["source"] == "database"
    assert body["username"] == "sachith"
    assert body["role"] == "read"
    assert TOKEN not in response.text

    row = await _stored(dbsession)
    assert row is not None
    assert TOKEN not in row.ciphertext
    assert SettingsCrypto(settings.settings_master_key).decrypt(row.ciphertext) == TOKEN

    again = await client.get(URL)
    assert again.json()["username"] == "sachith"
    assert TOKEN not in again.text

    events = (await dbsession.execute(
        select(AuditEvent).where(AuditEvent.action == "settings.hf_token.set"),
    )).scalars().all()
    assert len(events) == 1
    assert TOKEN not in (events[0].metadata_json or "")
    assert json.loads(events[0].metadata_json or "{}")["username"] == "sachith"


@pytest.mark.anyio
async def test_a_token_hugging_face_rejects_is_not_kept(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hub_says(monkeypatch, hf_token.Identity(check="invalid"))

    response = await client.put(URL, json={"token": TOKEN})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "does not accept" in response.json()["detail"]
    assert TOKEN not in response.text
    assert await _stored(dbsession) is None


@pytest.mark.anyio
async def test_offline_the_token_is_kept_and_marked_unchecked(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hub_says(monkeypatch, hf_token.Identity(check="offline"))

    response = await client.put(URL, json={"token": TOKEN})
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["check"] == "offline"
    assert await _stored(dbsession) is not None


@pytest.mark.anyio
async def test_a_hub_that_does_not_answer_is_not_asked_on_every_page(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settings page, the marketplace chip and every dialog open ask for
    the status; with the Hub unreachable each ask would wait out the timeout.
    """
    asked = _hub_says(monkeypatch, hf_token.Identity(check="offline"))
    await client.put(URL, json={"token": TOKEN})
    for _ in range(3):
        assert (await client.get(URL)).json()["check"] == "offline"
    assert len(asked) == 1


def test_whoami_reads_the_status_code_not_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 5xx whose request id happens to contain "401" is an outage, not a bad token."""
    import httpx

    def hub(status_code: int, body: str | dict) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            return httpx.Response(status_code, json=body) if isinstance(body, dict) else httpx.Response(status_code, text=body)

        monkeypatch.setattr(
            hf_token, "_hub_client",
            lambda: httpx.Client(base_url="https://hub.test", transport=httpx.MockTransport(handle)),
        )

    hub(503, "Service unavailable. Request ID: Root=1-401abc")
    assert hf_token._whoami_sync(TOKEN).check == "offline"
    hub(401, {"error": "Invalid user token"})
    assert hf_token._whoami_sync(TOKEN).check == "invalid"
    hub(200, {"name": "sachith", "auth": {"accessToken": {"displayName": "llm-port", "role": "read"}}})
    assert hf_token._whoami_sync(TOKEN) == hf_token.Identity(
        check="ok", username="sachith", token_name="llm-port", role="read",
    )


def test_whoami_gives_up_in_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    # The real client carries a deadline at all: HfApi.whoami has none.
    assert hf_token._hub_client().timeout.read == hf_token._WHOAMI_TIMEOUT

    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    monkeypatch.setattr(
        hf_token, "_hub_client",
        lambda: httpx.Client(base_url="https://hub.test", transport=httpx.MockTransport(hang)),
    )
    assert hf_token._whoami_sync(TOKEN).check == "offline"


@pytest.mark.anyio
async def test_an_overlong_token_is_refused_without_being_echoed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic's own length check answers with the offending input in the
    422 body; a token must not come back that way either.
    """
    asked = _hub_says(monkeypatch, hf_token.Identity(check="ok"))
    overlong = "hf_" + "x" * 600
    response = await client.put(URL, json={"token": overlong})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert "x" * 50 not in response.text
    assert asked == []


@pytest.mark.anyio
async def test_what_cannot_be_a_token_is_refused_without_asking(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _hub_says(monkeypatch, hf_token.Identity(check="ok"))

    response = await client.put(URL, json={"token": "hf_two words"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert asked == []


@pytest.mark.anyio
async def test_removing_it_falls_back_to_the_environment(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hub_says(monkeypatch, hf_token.Identity(check="ok", username="sachith"))
    await client.put(URL, json={"token": TOKEN})

    removed = await client.delete(URL)
    assert removed.status_code == status.HTTP_200_OK
    assert removed.json()["configured"] is False
    assert await _stored(dbsession) is None

    monkeypatch.setattr(settings, "hf_token", "hf_from_the_environment")
    now = (await client.get(URL)).json()
    assert now["configured"] is True
    assert now["source"] == "environment"
    assert await hf_token.resolve(dbsession) == ("hf_from_the_environment", "environment")


@pytest.mark.anyio
async def test_a_published_master_key_refuses_to_store(
    client: AsyncClient, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hub_says(monkeypatch, hf_token.Identity(check="ok"))
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "settings_master_key", "dev-settings-master-key-change-me")

    response = await client.put(URL, json={"token": TOKEN})
    assert response.status_code == status.HTTP_409_CONFLICT
    assert "master key" in response.json()["detail"]
    assert (await client.get(URL)).json()["storage_safe"] is False
    assert await _stored(dbsession) is None


@pytest.mark.anyio
async def test_a_token_under_another_master_key_reads_as_none(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dbsession.add(SystemSettingSecret(
        key=hf_token.HF_TOKEN_KEY,
        ciphertext=SettingsCrypto("some-other-installation-key").encrypt(TOKEN),
        nonce=None,
        kek_version="fernet-sha256",
    ))
    await dbsession.flush()
    assert await hf_token.resolve(dbsession) == (None, None)
