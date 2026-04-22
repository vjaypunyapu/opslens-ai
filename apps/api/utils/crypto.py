from __future__ import annotations
import json, os
from typing import Any
from cryptography.fernet import Fernet, InvalidToken
from .logging import get_logger

logger = get_logger(__name__)

_KEY = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
_ENV = os.getenv("ENVIRONMENT", os.getenv("RAILWAY_ENVIRONMENT", "development")).lower()
_IS_PRODUCTION = _ENV in ("production", "prod", "staging")

_USING_FALLBACK = False

if not _KEY:
    import base64, hashlib
    _fallback = base64.urlsafe_b64encode(hashlib.sha256(b"opslens-dev-only").digest())
    _fernet = Fernet(_fallback)
    _USING_FALLBACK = True

    if _IS_PRODUCTION:
        # In production with no key set, credentials are encrypted with a known
        # hardcoded key — effectively plaintext for anyone who reads this source.
        # Raise loudly so the deployment fails visibly rather than silently.
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY is not set in a production environment. "
            "Generate a key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\" "
            "and set it as an environment variable. "
            "This is a critical security requirement — refusing to start."
        )
    else:
        logger.warning(
            "CREDENTIAL_ENCRYPTION_KEY not set — using insecure dev fallback. "
            "Never deploy to production without setting this variable."
        )
else:
    try:
        _fernet = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)
    except Exception as exc:
        raise RuntimeError(
            f"CREDENTIAL_ENCRYPTION_KEY is set but is not a valid Fernet key: {exc}. "
            "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def is_using_fallback_key() -> bool:
    """Returns True if the insecure dev fallback key is in use (no CREDENTIAL_ENCRYPTION_KEY set)."""
    return _USING_FALLBACK


def encrypt_credentials(credentials: dict) -> dict:
    """Encrypt a credentials dict (API keys, tokens) before writing to PostgreSQL."""
    return {"encrypted": _fernet.encrypt(json.dumps(credentials).encode()).decode()}


def decrypt_credentials(stored: dict) -> dict:
    """Decrypt a credentials dict fetched from PostgreSQL."""
    try:
        return json.loads(_fernet.decrypt(stored.get("encrypted", "").encode()))
    except Exception as exc:
        logger.error("Failed to decrypt credentials: %s", exc)
        return {}


def encrypt(plaintext: str) -> str:
    """Encrypt a plaintext string with Fernet (AES-256). Returns a base64-encoded token."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to plaintext. Raises InvalidToken on failure."""
    return _fernet.decrypt(token.encode()).decode()
