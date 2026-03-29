from __future__ import annotations
import json, os
from typing import Any
from cryptography.fernet import Fernet, InvalidToken
from .logging import get_logger

logger = get_logger(__name__)
_KEY = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")

if not _KEY:
    import base64, hashlib
    _fallback = base64.urlsafe_b64encode(hashlib.sha256(b"opslens-dev-only").digest())
    logger.warning("CREDENTIAL_ENCRYPTION_KEY not set — using insecure dev fallback.")
    _fernet = Fernet(_fallback)
else:
    _fernet = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)

def encrypt_credentials(credentials: dict) -> dict:
    return {"encrypted": _fernet.encrypt(json.dumps(credentials).encode()).decode()}

def decrypt_credentials(stored: dict) -> dict:
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
