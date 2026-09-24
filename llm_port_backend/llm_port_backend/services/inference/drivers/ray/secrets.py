"""Cluster token storage for the Ray driver.

A Ray cluster auth token is never stored in plain columns or node-command
payloads.  It is encrypted with the system-settings master key (Fernet) and
persisted in the ``system_setting_secret`` table.  What *is* stored on the
:class:`~llm_port_backend.db.models.inference.InferenceControlPlane` row and
shipped in node-command payloads is an opaque, non-secret ``credential_ref``
derived from the owning control plane id (``cp-<uuid>``).

The matching delivery endpoint
(:func:`llm_port_backend.web.api.admin.system.views.system_node_secret_delivery`)
resolves the ref to a control plane, verifies the *requesting* node actually
belongs to that control plane's membership, and only then decrypts and serves
the token.
"""

from __future__ import annotations

import secrets as _std_secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO
from llm_port_backend.services.system_settings.crypto import SettingsCrypto
from llm_port_backend.settings import settings

__all__ = [
    "credential_ref_for_control_plane",
    "control_plane_id_from_credential_ref",
    "generate_cluster_token",
    "store_cluster_token",
    "retrieve_cluster_token",
]

# Fernet embeds its own nonce/iv in the output, so the secret row's nonce
# column stays NULL and the only thing tracked is the key version.
_KEK_VERSION = "v1"
_SECRET_KEY_PREFIX = "inference.ray_cluster_token/"


def _crypto() -> SettingsCrypto:
    return SettingsCrypto(settings.settings_master_key)


def _secret_key(credential_ref: str | None) -> str | None:
    if credential_ref is None:
        return None
    return f"{_SECRET_KEY_PREFIX}{credential_ref}"


def credential_ref_for_control_plane(control_plane_id: uuid.UUID | str) -> str:
    """Build the opaque credential_ref for a control plane."""
    return f"cp-{control_plane_id}"


def control_plane_id_from_credential_ref(
    credential_ref: str | None,
) -> uuid.UUID | None:
    """Invert :func:`credential_ref_for_control_plane`; ``None`` if not a ref."""
    if not credential_ref or not credential_ref.startswith("cp-"):
        return None
    try:
        return uuid.UUID(credential_ref.removeprefix("cp-"))
    except (ValueError, TypeError):
        return None


def generate_cluster_token() -> str:
    """Generate a fresh 256-bit random Ray auth token."""
    return _std_secrets.token_hex(32)


async def store_cluster_token(
    session: AsyncSession,
    control_plane_id: uuid.UUID | str,
    token: str,
) -> str:
    """Encrypt and persist *token* for a control plane.

    Returns the opaque ``credential_ref`` that should be persisted on the
    control plane row.  Idempotent per control plane: re-storing overwrites
    the ciphertext (token rotation).
    """
    ref = credential_ref_for_control_plane(control_plane_id)
    dao = SystemSettingsDAO(session)
    await dao.upsert_secret(
        key=_secret_key(ref),
        ciphertext=_crypto().encrypt(token),
        nonce=None,
        kek_version=_KEK_VERSION,
        updated_by=None,
    )
    return ref


def seal_token(token: str) -> str:
    """*token* encrypted with the settings key, for where it has to be stored in passing."""
    return _crypto().encrypt(token)


def unseal_token(sealed: str) -> str | None:
    """The token :func:`seal_token` sealed, or ``None`` when it will not decrypt."""
    try:
        return _crypto().decrypt(sealed)
    except Exception:  # noqa: BLE001 - a bad key or a mangled value is "no token"
        return None


async def retrieve_cluster_token(
    session: AsyncSession, credential_ref: str | None
) -> str | None:
    """Decrypt and return the token for *credential_ref*.

    Returns ``None`` for an unknown ref or a row that fails to decrypt
    (wrong/rotated master key) -- callers treat both as "no token".
    """
    secret_key = _secret_key(credential_ref)
    if secret_key is None:
        return None
    row = await SystemSettingsDAO(session).get_secret(secret_key)
    if row is None:
        return None
    try:
        return _crypto().decrypt(row.ciphertext)
    except Exception:  # noqa: BLE001 - a bad key shouldn't 500 the endpoint
        return None

