"""Secret encryption helpers for system settings."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


class SettingsCrypto:
    """Encrypt/decrypt helper based on Fernet symmetric crypto."""

    def __init__(self, master_key: str) -> None:
        digest = hashlib.sha256(master_key.encode("utf-8")).digest()
        fernet_key = base64.urlsafe_b64encode(digest)
        self._fernet = Fernet(fernet_key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string into opaque ciphertext."""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt ciphertext into plaintext string."""
        return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")

    @staticmethod
    def mask(value: str) -> str:
        """Return a non-sensitive preview of a secret value."""
        if not value:
            return ""
        if len(value) <= 6:
            return "*" * len(value)
        return f"{value[:2]}***{value[-2:]}"
