"""The Factory verifies a Store block's signature with the key the Store's own
registry publishes -- read from the tree the block came from, never typed."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.factory.build.vendored_integrity import lock_record, verify_signature


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _store(tmp_path: Path, *, revoked: bool = False):
    """A Store tree with one publisher and one signed block, both invented."""
    key = Ed25519PrivateKey.generate()
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    root = tmp_path / "store"
    (root / "data").mkdir(parents=True)
    (root / "data" / "publishers.json").write_text(json.dumps({"publishers": [{
        "publisher_id": "zorblat_pub", "public_key": base64.b64encode(pub).decode(),
        "tier": "revoked" if revoked else "platform", "revoked_at": "2026-01-01" if revoked else None,
    }]}), encoding="utf-8")
    block = root / "block_registry" / "zorblat_block"
    block.mkdir(parents=True)
    (block / "block.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")
    manifest = {"id": "zorblat_block", "publisher_id": "zorblat_pub",
                "reads": [{"kind": "database", "scope": "vector"}]}
    digests = {"block.json": hashlib.sha256(_canon(manifest)).hexdigest(),
               "block.py": hashlib.sha256((block / "block.py").read_bytes()).hexdigest()}
    signature = key.sign(_canon({"publisher_id": "zorblat_pub", "digests": digests}))
    (block / "block.json").write_text(json.dumps(
        {**manifest, "digests": digests, "signature": base64.b64encode(signature).decode()}), encoding="utf-8")
    return block


def test_a_signed_block_verifies_against_its_store_registry(tmp_path):
    block = _store(tmp_path)
    assert verify_signature(block)["verified"] is True
    record = lock_record(block)
    assert record["verified"] is True and record["signature_verified"] is True


def test_editing_a_declared_field_after_signing_is_refused(tmp_path):
    block = _store(tmp_path)
    data = json.loads((block / "block.json").read_text(encoding="utf-8"))
    data["reads"] = []  # drop the declared vector read without re-signing
    (block / "block.json").write_text(json.dumps(data), encoding="utf-8")
    verdict = verify_signature(block)
    assert verdict["verified"] is False and "after signing" in verdict["reason"]
    assert lock_record(block)["verified"] is False


def test_a_revoked_publisher_is_refused(tmp_path):
    assert verify_signature(_store(tmp_path, revoked=True))["verified"] is False


def test_a_signature_from_another_key_is_refused(tmp_path):
    block = _store(tmp_path)
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    reg = block.parents[1] / "data" / "publishers.json"
    data = json.loads(reg.read_text(encoding="utf-8"))
    data["publishers"][0]["public_key"] = base64.b64encode(other).decode()
    reg.write_text(json.dumps(data), encoding="utf-8")
    assert verify_signature(block)["verified"] is False


def test_no_registry_is_recorded_not_passed(tmp_path):
    block = _store(tmp_path)
    (block.parents[1] / "data" / "publishers.json").unlink()
    verdict = verify_signature(block)
    assert verdict["verified"] is None and "no publisher registry" in verdict["reason"]
    assert lock_record(block)["signed_unverified"] is True
