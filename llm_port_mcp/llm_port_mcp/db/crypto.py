"""Column-level encryption for sensitive MCP configuration data.

Provides EncryptedText and EncryptedJSON TypeDecorators that encrypt
values at rest using Fernet with HKDF-derived per-purpose subkeys.

Mirrors the pattern from llm_port_api.db.crypto.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Callable

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

logger = logging.getLogger(__name__)

_key_provider: Callable[[str], bytes] | None = None
_FERNET_PREFIX = b"gAAAAA"


def configure(master_key: str) -> None:
    """Derive per-purpose encryption keys from *master_key* via HKDF."""
    if not master_key:
        logger.warning(
            "ENCRYPTION_KEY is not set — MCP secrets stored in plaintext. "
            "Set LLM_PORT_MCP_ENCRYPTION_KEY in production.",
        )
        return

    raw = master_key.encode("utf-8")

    def _derive(purpose: str) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=purpose.encode("utf-8"),
        ).derive(raw)

    set_key_provider(_derive)


def set_key_provider(provider: Callable[[str], bytes]) -> None:
    """Replace the key provider."""
    global _key_provider  # noqa: PLW0603
    _key_provider = provider


def _fernet_for(purpose: str) -> Fernet | None:
    if _key_provider is None:
        return None
    raw = _key_provider(purpose)
    return Fernet(base64.urlsafe_b64encode(raw))


def _is_ciphertext(value: str) -> bool:
    try:
        return value.encode("ascii")[:6] == _FERNET_PREFIX
    except (UnicodeEncodeError, AttributeError):
        return False


class EncryptedText(TypeDecorator):
    """A Text column whose value is Fernet-encrypted at rest."""

    impl = Text
    cache_ok = True

    def __init__(self, purpose: str = "default") -> None:
        super().__init__()
        self._purpose = purpose

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        f = _fernet_for(self._purpose)
        if f is None:
            return value
        return f.encrypt(value.encode("utf-8")).decode("ascii")

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        if value is None:
            return None
        f = _fernet_for(self._purpose)
        if f is None or not _is_ciphertext(value):
            return value
        try:
            return f.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken:
            logger.warning("Failed to decrypt EncryptedText — returning raw value")
            return value


class EncryptedJSON(TypeDecorator):
    """A Text column storing JSON encrypted at rest."""

    impl = Text
    cache_ok = True

    def __init__(self, purpose: str = "default") -> None:
        super().__init__()
        self._purpose = purpose

    def process_bind_param(
        self,
        value: dict[str, Any] | list[Any] | None,
        dialect: Any,
    ) -> str | None:
        if value is None:
            return None
        raw = json.dumps(value, separators=(",", ":"), sort_keys=True)
        f = _fernet_for(self._purpose)
        if f is None:
            return raw
        return f.encrypt(raw.encode("utf-8")).decode("ascii")

    def process_result_value(
        self,
        value: str | None,
        dialect: Any,
    ) -> dict[str, Any] | list[Any] | None:
        if value is None:
            return None
        f = _fernet_for(self._purpose)
        if f is not None and _is_ciphertext(value):
            try:
                value = f.decrypt(value.encode("ascii")).decode("utf-8")
            except InvalidToken:
                logger.warning(
                    "Failed to decrypt EncryptedJSON — attempting JSON parse",
                )
        try:
            return json.loads(value)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            logger.warning("Failed to parse EncryptedJSON — returning None")
            return None
