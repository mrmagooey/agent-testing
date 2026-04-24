"""Fernet-based symmetric encryption for LLM provider API keys.

Reads ``LLM_PROVIDER_ENCRYPTION_KEY`` from the environment at import time.
The value is a comma-separated list of base64-encoded 32-byte keys:

    LLM_PROVIDER_ENCRYPTION_KEY=key1b64,key2b64,...

The **first** key is the active write key; remaining keys are accepted for
decryption only (rotation scenario).  ``MultiFernet`` handles this natively.

Fails fast at import if the env var is missing or any key is malformed, so
mis-configuration is caught at startup rather than at the first DB write.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

__all__ = ["encrypt_str", "decrypt_bytes", "InvalidToken"]

_ENV_VAR = "LLM_PROVIDER_ENCRYPTION_KEY"


def _load_fernet() -> MultiFernet:
    raw = os.environ.get(_ENV_VAR, "")
    if not raw.strip():
        raise RuntimeError(
            f"{_ENV_VAR} is not set. "
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    fernets: list[Fernet] = []
    for i, k in enumerate(keys):
        try:
            fernets.append(Fernet(k.encode() if isinstance(k, str) else k))
        except Exception as exc:
            raise RuntimeError(
                f"{_ENV_VAR}: key at position {i} is malformed: {exc}"
            ) from exc
    return MultiFernet(fernets)


# Module-level singleton — evaluated once at import time.
_fernet: MultiFernet = _load_fernet()


def encrypt_str(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string and return Fernet token bytes."""
    return _fernet.encrypt(plaintext.encode("utf-8"))


def decrypt_bytes(ciphertext: bytes) -> str:
    """Decrypt Fernet token bytes and return the plaintext string."""
    return _fernet.decrypt(ciphertext).decode("utf-8")
