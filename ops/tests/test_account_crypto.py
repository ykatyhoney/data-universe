"""CookieSealer: encrypt → decrypt round-trip; tamper detection."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from account_pool.crypto import CookieSealer, CookieSealError


def _key() -> str:
    return Fernet.generate_key().decode()


def test_roundtrip_list_of_cookies() -> None:
    sealer = CookieSealer(_key())
    payload = [
        {"name": "auth_token", "value": "s3cret", "domain": ".x.com"},
        {"name": "ct0", "value": "csrf-token-xyz", "domain": ".x.com"},
    ]
    sealed = sealer.seal(payload)
    assert isinstance(sealed, bytes)
    assert b"s3cret" not in sealed  # plaintext NOT present in ciphertext
    assert sealer.unseal(sealed) == payload


def test_roundtrip_dict_payload() -> None:
    sealer = CookieSealer(_key())
    payload = {"hello": "world", "n": 42}
    assert sealer.unseal(sealer.seal(payload)) == payload


def test_wrong_key_fails() -> None:
    a = CookieSealer(_key())
    b = CookieSealer(_key())
    sealed = a.seal([{"name": "k", "value": "v"}])
    with pytest.raises(CookieSealError):
        b.unseal(sealed)


def test_tampered_ciphertext_fails() -> None:
    sealer = CookieSealer(_key())
    sealed = bytearray(sealer.seal({"k": "v"}))
    sealed[-3] ^= 0xFF  # flip a byte
    with pytest.raises(CookieSealError):
        sealer.unseal(bytes(sealed))


def test_empty_key_rejected() -> None:
    with pytest.raises(CookieSealError):
        CookieSealer("")


def test_garbage_key_rejected() -> None:
    with pytest.raises(CookieSealError):
        CookieSealer("not-a-real-fernet-key")
