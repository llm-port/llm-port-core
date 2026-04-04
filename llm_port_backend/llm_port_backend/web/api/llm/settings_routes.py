"""LLM Settings endpoints - HF token management.

The token is stored as an encrypted secret in the system_settings DB table
(key ``llm_backend.hf_token``).  It is **never** sent over RabbitMQ or
exposed in API responses.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO
from llm_port_backend.db.models.users import User
from llm_port_backend.services.system_settings.crypto import SettingsCrypto
from llm_port_backend.settings import settings
from llm_port_backend.web.api.llm.schema import HFTokenSetRequest, HFTokenStatusDTO
from llm_port_backend.web.api.rbac import require_permission

HF_TOKEN_KEY = "llm_backend.hf_token"

router = APIRouter()


def _crypto() -> SettingsCrypto:
    return SettingsCrypto(settings.settings_master_key)


@router.get("/hf-token", response_model=HFTokenStatusDTO)
async def get_hf_token_status(
    user: User = Depends(require_permission("llm.settings", "read")),
    dao: SystemSettingsDAO = Depends(),
) -> HFTokenStatusDTO:
    """Check whether a Hugging Face token is configured (never returns the token)."""
    secret = await dao.get_secret(HF_TOKEN_KEY)
    return HFTokenStatusDTO(configured=secret is not None and bool(secret.ciphertext))


@router.put("/hf-token", response_model=HFTokenStatusDTO)
async def set_hf_token(
    body: HFTokenSetRequest,
    user: User = Depends(require_permission("llm.settings", "update")),
    dao: SystemSettingsDAO = Depends(),
) -> HFTokenStatusDTO:
    """Encrypt and persist the Hugging Face token to the database."""
    crypto = _crypto()
    ciphertext = crypto.encrypt(body.token)
    await dao.upsert_secret(
        key=HF_TOKEN_KEY,
        ciphertext=ciphertext,
        nonce=None,
        kek_version="fernet-sha256",
        updated_by=user.id,
    )
    return HFTokenStatusDTO(configured=True)
