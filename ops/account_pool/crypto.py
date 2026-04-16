"""Symmetric sealing for account cookies.

Uses :class:`cryptography.fernet.Fernet` (AES-128-CBC + HMAC-SHA256 — AEAD).
Key format is URL-safe base64, 32 raw bytes; env var ``OPS_ACCOUNT_POOL_KEY``.

Generate a key::

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Service refuses to start without a valid key (see
:func:`CookieSealer.from_env`). We do this instead of silently falling back
to "no encryption" because the whole point of this layer is to keep
cookies safe at rest — a dev who ships without a key would burn accounts
silently.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AccountCryptoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPS_ACCOUNT_POOL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Fernet key (URL-safe base64, 44 chars). Required in prod.
    key: str = Field(default="")


class CookieSealError(Exception):
    """Raised on any encrypt/decrypt failure — caller treats as fatal."""


class CookieSealer:
    """Seals/unseals cookie bundles with a single Fernet key.

    Thread-safe — Fernet instances have no mutable state.
    """

    def __init__(self, key: str) -> None:
        if not key:
            raise CookieSealError("OPS_ACCOUNT_POOL_KEY is empty; set it to a Fernet key before starting")
        try:
            self._fernet = Fernet(key.encode() if isinstance(key, str) else key)
        except (ValueError, InvalidToken) as e:
            raise CookieSealError(f"invalid Fernet key: {e}") from e

    @classmethod
    def from_env(cls) -> CookieSealer:
        return cls(AccountCryptoSettings().key)

    def seal(self, plaintext: dict[str, Any] | list[Any]) -> bytes:
        """JSON-serialise, encrypt, return ciphertext bytes."""
        return self._fernet.encrypt(json.dumps(plaintext).encode("utf-8"))

    def unseal(self, ciphertext: bytes) -> Any:
        try:
            data = self._fernet.decrypt(ciphertext)
        except InvalidToken as e:
            # Fernet's error doesn't include the ciphertext; good — it
            # stays out of the log.
            raise CookieSealError("cookie ciphertext failed integrity check") from e
        return json.loads(data.decode("utf-8"))
