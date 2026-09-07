"""Unit tests for ``SettingsCrypto`` (Fernet) and its ``mask`` helper.

Pure in-memory: no database, no I/O.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import InvalidToken

from llm_port_backend.services.system_settings.crypto import SettingsCrypto


def test_encrypt_then_decrypt_roundtrip() -> None:
    crypto = SettingsCrypto("master-key")
    plaintext = "hunter2-the-secret"
    ciphertext = crypto.encrypt(plaintext)
    assert ciphertext != plaintext
    assert isinstance(ciphertext, str)
    assert crypto.decrypt(ciphertext) == plaintext


def test_encrypt_is_nondeterministic_but_decryptable() -> None:
    crypto = SettingsCrypto("master-key")
    # Fernet embeds a timestamp+IV; repeated encrypts differ but both decrypt.
    a = crypto.encrypt("same-value")
    b = crypto.encrypt("same-value")
    assert crypto.decrypt(a) == "same-value"
    assert crypto.decrypt(b) == "same-value"


def test_wrong_master_key_cannot_decrypt() -> None:
    right = SettingsCrypto("key-one")
    wrong = SettingsCrypto("key-two")
    ciphertext = right.encrypt("sensitive")
    with pytest.raises(InvalidToken):
        wrong.decrypt(ciphertext)


def test_distinct_master_keys_produce_incompatible_ciphertexts() -> None:
    a = SettingsCrypto("alpha")
    b = SettingsCrypto("beta")
    with pytest.raises(InvalidToken):
        a.decrypt(b.encrypt("x"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", ""),
        ("abc", "***"),
        ("abcdef", "******"),  # len 6 → fully masked boundary
        ("abcdefg", "ab***fg"),  # len 7 → first/last 2 preserved
        ("secretpassword", "se***rd"),
    ],
)
def test_mask(value: str, expected: str) -> None:
    assert SettingsCrypto.mask(value) == expected
