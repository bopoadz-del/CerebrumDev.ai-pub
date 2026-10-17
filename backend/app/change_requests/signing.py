"""Ed25519 signing for change-requests (Factory / product paperwork).

Canonical payload = JSON with sorted keys, ``signature`` field omitted.
Signature value is base64(raw 64-byte Ed25519 signature).
Public keys are registered by ``public_key_id`` (product-scoped), never GitHub/Render creds.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


class SignatureError(ValueError):
    """Malformed or invalid signature."""


def canonical_payload_bytes(document: Dict[str, Any]) -> bytes:
    """Deterministic bytes for signing: signature field stripped, sorted JSON."""
    body = {k: v for k, v in document.items() if k != "signature"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def generate_keypair() -> Tuple[str, str]:
    """Return (private_key_b64, public_key_b64) raw 32-byte keys."""
    private = Ed25519PrivateKey.generate()
    priv_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(priv_raw).decode("ascii"), base64.b64encode(pub_raw).decode("ascii")


def public_key_id_for(product_id: str, public_key_b64: str) -> str:
    digest = hashlib.sha256(f"{product_id}:{public_key_b64}".encode()).hexdigest()[:16]
    return f"{product_id}:{digest}"


def sign_document(
    document: Dict[str, Any],
    *,
    private_key_b64: str,
    public_key_id: str,
) -> Dict[str, Any]:
    """Return a copy of ``document`` with an Ed25519 ``signature`` attached."""
    priv = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64))
    payload = canonical_payload_bytes(document)
    sig = priv.sign(payload)
    out = dict(document)
    out["signature"] = {
        "algorithm": "ed25519",
        "public_key_id": public_key_id,
        "value": base64.b64encode(sig).decode("ascii"),
    }
    return out


def verify_document(document: Dict[str, Any], *, public_key_b64: str) -> None:
    """Raise ``SignatureError`` if signature missing or invalid."""
    sig = document.get("signature")
    if not isinstance(sig, dict):
        raise SignatureError("missing signature object")
    if sig.get("algorithm") != "ed25519":
        raise SignatureError("unsupported signature algorithm")
    value = sig.get("value")
    if not value:
        raise SignatureError("missing signature value")
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
        pub.verify(base64.b64decode(value), canonical_payload_bytes(document))
    except (InvalidSignature, ValueError) as exc:
        raise SignatureError("invalid ed25519 signature") from exc


def keys_dir() -> Path:
    root = Path(os.getenv("STORAGE_PATH", "./storage"))
    path = root / "change_requests" / "keys"
    path.mkdir(parents=True, exist_ok=True)
    return path


def register_public_key(product_id: str, public_key_b64: str, public_key_id: str) -> Path:
    """Persist product public key for Factory intake verification."""
    path = keys_dir() / f"{product_id}.json"
    path.write_text(
        json.dumps(
            {
                "product_id": product_id,
                "public_key_id": public_key_id,
                "public_key_b64": public_key_b64,
                "algorithm": "ed25519",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def load_public_key(product_id: str, public_key_id: Optional[str] = None) -> str:
    path = keys_dir() / f"{product_id}.json"
    if not path.is_file():
        raise SignatureError(f"no registered public key for product_id={product_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if public_key_id and data.get("public_key_id") != public_key_id:
        raise SignatureError("public_key_id mismatch")
    return str(data["public_key_b64"])


def signing_key_from_env() -> Optional[str]:
    """Product-side private key (base64). Never a GitHub/Render credential."""
    return os.getenv("PRODUCT_CHANGE_REQUEST_SIGNING_KEY", "").strip() or None
