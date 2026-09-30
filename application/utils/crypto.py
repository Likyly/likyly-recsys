"""Encryption at rest for data source credentials (Shopify access tokens, generic REST API
keys, webhook shared secrets, ...) - stored in data_sources.credentials_encrypted, never in
plaintext. Uses Fernet (AES-128-CBC + HMAC, from the `cryptography` package already a
dependency of this project via PyJWT[crypto]/pyOpenSSL) rather than inventing anything - it's
authenticated (tamper-evident) and versioned, which a bare AES call isn't.

LIKYLY_DATA_SOURCE_ENCRYPTION_KEY must be a Fernet key (Fernet.generate_key(), base64
urlsafe, 32 bytes before encoding). Generate one once per environment and never rotate it by
just changing the env var - that would make every already-stored credential undecryptable.
"""
import json
import os
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken

_ENV_VAR = "LIKYLY_DATA_SOURCE_ENCRYPTION_KEY"


class EncryptionNotConfigured(Exception):
    """Raised instead of ever storing credentials in plaintext when the server has no key."""


def _fernet() -> Fernet:
    key = os.environ.get(_ENV_VAR)
    if not key:
        raise EncryptionNotConfigured(
            f"{_ENV_VAR} is not set on this server - a data source with credentials can't be "
            "created until it is (see docs/data-sources.md). Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"`."
        )
    try:
        return Fernet(key.encode("utf-8") if isinstance(key, str) else key)
    except Exception as error:
        raise EncryptionNotConfigured(f"{_ENV_VAR} is not a valid Fernet key: {error}")


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(credentials).encode("utf-8")).decode("utf-8")


def decrypt_credentials(token: Optional[str]) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode("utf-8")).decode("utf-8"))
    except InvalidToken:
        raise EncryptionNotConfigured(
            f"Stored credentials could not be decrypted - {_ENV_VAR} on this server no longer "
            "matches the key they were encrypted with."
        )
