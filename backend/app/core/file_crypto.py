"""Encryption at rest for uploaded documents and OAuth tokens.

This module is copied from The_Fork's proven implementation so that
CerebrumDev.ai and generated platforms share one token-storage contract.

The feature is opt-in via ``DATA_ENCRYPTION_KEY``. If the variable is unset,
files are stored plaintext and every function behaves transparently.
"""

import contextlib
import os
import tempfile
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_FERNET_VERSION = 0x80
_ENV_KEY = "DATA_ENCRYPTION_KEY"


class DecryptionError(Exception):
    """A stored Fernet token could not be decrypted."""


def _load_fernet() -> Optional[Fernet]:
    """Build a Fernet instance from the env var, or None if unset/invalid."""
    raw = os.getenv(_ENV_KEY)
    if not raw:
        return None
    try:
        return Fernet(raw.encode() if isinstance(raw, str) else raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"{_ENV_KEY} is set but is not a valid Fernet key. Generate one "
            f"with cryptography.fernet.Fernet.generate_key(). ({exc})"
        ) from exc


def encryption_enabled() -> bool:
    """True when a valid ``DATA_ENCRYPTION_KEY`` is configured."""
    return _load_fernet() is not None


def looks_encrypted(blob: bytes) -> bool:
    """Heuristically detect whether ``blob`` is a Fernet token."""
    if not blob:
        return False
    try:
        import base64

        decoded = base64.urlsafe_b64decode(blob)
    except Exception:
        return False
    return len(decoded) >= 57 and decoded[0] == _FERNET_VERSION


def encrypt_bytes(data: bytes) -> bytes:
    """Encrypt ``data``; returns it unchanged when encryption is off."""
    fernet = _load_fernet()
    if fernet is None:
        return data
    return fernet.encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    """Decrypt a Fernet token; returns input unchanged if not encrypted."""
    fernet = _load_fernet()
    if fernet is None:
        return token
    if not looks_encrypted(token):
        return token
    try:
        return fernet.decrypt(token)
    except InvalidToken as exc:
        raise DecryptionError(
            "Stored data is encrypted but could not be decrypted — "
            "DATA_ENCRYPTION_KEY does not match the key it was written with."
        ) from exc


def write_document(path: str, data: bytes) -> None:
    """Write ``data`` to ``path``, encrypting it iff encryption is enabled."""
    payload = encrypt_bytes(data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)


def read_document(path: str) -> bytes:
    """Read ``path`` and return plaintext bytes."""
    with open(path, "rb") as fh:
        raw = fh.read()
    return decrypt_bytes(raw)


@contextlib.contextmanager
def open_plaintext(path: str):
    """Yield a filesystem path that contains the document's plaintext."""
    with open(path, "rb") as fh:
        raw = fh.read()

    if not (encryption_enabled() and looks_encrypted(raw)):
        yield path
        return

    plaintext = decrypt_bytes(raw)
    suffix = os.path.splitext(path)[1] or ""
    fd, tmp_path = tempfile.mkstemp(suffix=suffix, prefix="fork_dec_")
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(plaintext)
        yield tmp_path
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
