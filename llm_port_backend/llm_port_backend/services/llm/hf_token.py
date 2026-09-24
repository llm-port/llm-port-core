"""This server's Hugging Face token: kept encrypted, used here, never shown again.

The token is stored in ``system_setting_secret`` encrypted with the settings
master key. No API answers with it: callers learn only whether one is set,
where it comes from, and whom Hugging Face says it belongs to. It is used by
the server itself -- Hub search, model details, downloads -- and handed to a
legacy container only when that container is allowed onto the network, where
it stays until that container is stopped and started again. Cluster machines
never receive it: they get models from this server.

``LLM_PORT_BACKEND_HF_TOKEN`` still works as a fallback for installations that
set it before the setting existed; a stored token takes precedence.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

log = logging.getLogger(__name__)

HF_TOKEN_KEY = "llm_backend.hf_token"
ENV_VAR = "LLM_PORT_BACKEND_HF_TOKEN"
MAX_TOKEN_LENGTH = 512
_DEV_MASTER_KEY = "dev-settings-master-key-change-me"
_IDENTITY_TTL = 600.0
#: An outage is remembered too, briefly. Otherwise every page that shows the
#: token's status asks the Hub again and, on a network that drops packets,
#: waits out the whole timeout below each time.
_OFFLINE_TTL = 60.0
#: How long one whoami may take. ``HfApi.whoami`` has no timeout at all: on a
#: firewall that drops packets it waits for the operating system to give up --
#: about two minutes on Linux -- with the request's database transaction open.
_WHOAMI_TIMEOUT = 10.0


class TokenRejected(ValueError):
    """Hugging Face does not accept this token."""


class TokenStoreUnsafe(RuntimeError):
    """This server cannot store a secret safely (its master key is the published default)."""


@dataclass
class Identity:
    """Whom a token belongs to, as Hugging Face reports it."""

    check: str  # "ok" | "invalid" | "offline"
    username: str | None = None
    token_name: str | None = None
    # "read", "write" or "fineGrained": read access is all this server needs.
    role: str | None = None


_identity_cache: dict[str, tuple[float, Identity]] = {}


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _crypto() -> Any:
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    return SettingsCrypto(settings.settings_master_key)


def _env_token() -> str | None:
    """The token set in the environment, read fresh so a stored one never shadows it here."""
    from llm_port_backend.settings import settings  # noqa: PLC0415

    value = os.environ.get(ENV_VAR) or settings.hf_token
    return value.strip() if isinstance(value, str) and value.strip() else None


def normalize(token: str) -> str:
    """The token as typed, trimmed; refuses what cannot be a token."""
    value = (token or "").strip()
    if not value or len(value) > MAX_TOKEN_LENGTH or any(c.isspace() for c in value):
        raise TokenRejected("That does not look like a Hugging Face token.")
    return value


def storage_is_safe() -> bool:
    """Whether the master key that encrypts the token is this installation's own."""
    from llm_port_backend.settings import settings  # noqa: PLC0415

    key = settings.settings_master_key or ""
    if settings.environment.lower() in {"dev", "development", "test", "pytest"}:
        return bool(key)
    return bool(key) and key != _DEV_MASTER_KEY and len(key) >= 16


async def stored_token(session: Any) -> str | None:
    """The stored token, decrypted, or None."""
    from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO  # noqa: PLC0415

    secret = await SystemSettingsDAO(session).get_secret(HF_TOKEN_KEY)
    if secret is None or not secret.ciphertext:
        return None
    try:
        return _crypto().decrypt(secret.ciphertext) or None
    except Exception:  # noqa: BLE001 - a changed master key must not break browsing
        log.warning("The stored Hugging Face token cannot be decrypted with this master key.")
        return None


async def resolve(session: Any) -> tuple[str | None, str | None]:
    """The token this server uses, and where it comes from: ``database``, ``environment`` or None."""
    try:
        token = await stored_token(session)
    except Exception:  # noqa: BLE001
        log.warning("Could not read the stored Hugging Face token.", exc_info=True)
        token = None
    if token:
        return token, "database"
    env = _env_token()
    return (env, "environment") if env else (None, None)


def _hub_client() -> Any:
    """An HTTP client for the Hub with a deadline. Replaced in tests."""
    import httpx  # noqa: PLC0415
    from huggingface_hub.constants import ENDPOINT  # noqa: PLC0415

    from llm_port_backend.services.tls import default_httpx_verify  # noqa: PLC0415

    return httpx.Client(base_url=ENDPOINT, timeout=_WHOAMI_TIMEOUT, verify=default_httpx_verify())


def _whoami_sync(token: str) -> Identity:
    """Ask the Hub whom *token* belongs to: ``HfApi.whoami``, with a deadline and read by status code."""
    import httpx  # noqa: PLC0415

    try:
        with _hub_client() as client:
            response = client.get("/api/whoami-v2", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:  # not reached, or not in time
        log.info("Hugging Face whoami did not answer: %s", type(exc).__name__)
        return Identity(check="offline")
    # Only the status says whether the token was refused: a 5xx's body or
    # request id can contain "401" and must not read as a bad token.
    if response.status_code in {401, 403}:
        return Identity(check="invalid")
    if response.status_code != 200:
        log.info("Hugging Face whoami answered %s", response.status_code)
        return Identity(check="offline")
    try:
        info = response.json()
    except ValueError:
        info = None
    if not isinstance(info, dict):
        return Identity(check="offline")
    access = (info.get("auth") or {}).get("accessToken") or {}
    return Identity(
        check="ok",
        username=info.get("name"),
        token_name=access.get("displayName"),
        role=access.get("role"),
    )


async def identify(token: str, *, fresh: bool = False) -> Identity:
    """Ask Hugging Face whom *token* belongs to; remembered for a few minutes, an outage for one."""
    key = _fingerprint(token)
    cached = _identity_cache.get(key)
    if cached and not fresh:
        ttl = _OFFLINE_TTL if cached[1].check == "offline" else _IDENTITY_TTL
        if time.monotonic() - cached[0] < ttl:
            return cached[1]
    identity = await asyncio.to_thread(_whoami_sync, token)
    _identity_cache[key] = (time.monotonic(), identity)
    return identity


async def status(session: Any) -> dict[str, Any]:
    """Whether a token is set, where from, and whom it belongs to -- never the token."""
    token, source = await resolve(session)
    answer: dict[str, Any] = {"configured": token is not None, "source": source, "storage_safe": storage_is_safe()}
    if token is not None:
        answer.update(asdict(await identify(token)))
    return answer


async def _audit(session: Any, action: str, actor_id: uuid.UUID | None, metadata: dict[str, Any]) -> None:
    from llm_port_backend.db.dao.audit_dao import AuditDAO  # noqa: PLC0415
    from llm_port_backend.db.models.containers import AuditResult  # noqa: PLC0415

    await AuditDAO(session).log(
        action=action,
        target_type="system_setting",
        target_id=HF_TOKEN_KEY,
        result=AuditResult.ALLOW,
        actor_id=actor_id,
        metadata_json=json.dumps(metadata),
    )


def _forget_cached_hub_answers() -> None:
    """Gated models look different with another token: ask the Hub again."""
    from llm_port_backend.services.marketplace.hub import clear_cache  # noqa: PLC0415

    clear_cache()


async def store(session: Any, token: str, actor_id: uuid.UUID | None) -> dict[str, Any]:
    """Check *token* with Hugging Face, then keep it encrypted.

    A token Hugging Face rejects is not kept. When Hugging Face cannot be
    reached the token is kept unchecked and the answer says so.
    """
    from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO  # noqa: PLC0415

    value = normalize(token)
    if not storage_is_safe():
        raise TokenStoreUnsafe(
            "This server still uses the default settings master key, so a stored token would not be "
            "protected. Set LLM_PORT_BACKEND_SETTINGS_MASTER_KEY to a random value first.",
        )
    identity = await identify(value, fresh=True)
    if identity.check == "invalid":
        raise TokenRejected("Hugging Face does not accept this token. Check it has not expired or been revoked.")
    await SystemSettingsDAO(session).upsert_secret(
        key=HF_TOKEN_KEY,
        ciphertext=_crypto().encrypt(value),
        nonce=None,
        kek_version="fernet-sha256",
        updated_by=actor_id,
    )
    await _audit(session, "settings.hf_token.set", actor_id, {
        "check": identity.check, "username": identity.username, "role": identity.role,
    })
    _forget_cached_hub_answers()
    return {"configured": True, "source": "database", "storage_safe": True, **asdict(identity)}


async def remove(session: Any, actor_id: uuid.UUID | None) -> dict[str, Any]:
    """Forget the stored token. An environment token, if any, applies again."""
    from sqlalchemy import delete  # noqa: PLC0415

    from llm_port_backend.db.models.system_settings import SystemSettingSecret  # noqa: PLC0415

    result = await session.execute(delete(SystemSettingSecret).where(SystemSettingSecret.key == HF_TOKEN_KEY))
    if result.rowcount:
        await _audit(session, "settings.hf_token.remove", actor_id, {})
    _identity_cache.clear()
    _forget_cached_hub_answers()
    return await status(session)
