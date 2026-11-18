"""F-2 (#250 CI-M cancelled at 6156e95): direct tests for the changed helpers
that no test named.

The mutation gate runs a mutant's mapped tests with ``-x`` and puts the files
that NAME the mutated function first. A changed function that no test names
sends its mutant through every mapped file (61 in the cancelled run) until the
600 s timeout. Each helper below is called by name with two inputs, so its
mutants die in this file.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from app.factory.build import store_override as so
from app.factory.build.store_override import (
    ExamProof,
    _read_signed,
    _signature,
    open_proof_store,
    proof_refusal,
    unusable_reason,
    write_proof,
)

KEY = b"k" * 32


def _store(tmp_path: Path, key: bytes | None = KEY, build_id: str = "b-1") -> so.ProofStore:
    return open_proof_store(build_id, tmp_path / "builds" / "ws", key=key, base=tmp_path / "proofs")


# --- store_override -------------------------------------------------------


@pytest.mark.parametrize("key", [None, b""])
def test_write_proof_without_a_key_is_refused_and_writes_nothing(tmp_path: Path, key: bytes | None) -> None:
    store = _store(tmp_path, key=key)
    with pytest.raises(so.OverrideError, match=f"{so.PROOF_KEY_ENV} is not set"):
        write_proof(store, ExamProof("e1", "blk", "h1", True))
    assert not store.root.exists() or list(store.root.iterdir()) == []


@pytest.mark.parametrize("exc", [OSError("/secret/path"), PermissionError("/x")])
def test_unusable_reason_names_the_error_type_and_no_path(exc: BaseException) -> None:
    reason = unusable_reason(exc)
    assert type(exc).__name__ in reason
    assert "/" not in reason


@pytest.mark.parametrize("payload", [{"b": 1, "a": "x"}, {"exam_id": "e2", "passed": False}])
def test_signature_is_a_digest_without_a_key_and_an_hmac_with_one(payload: dict) -> None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert _signature(None, payload) == "digest:" + hashlib.sha256(body).hexdigest()
    assert _signature(KEY, payload) == hmac.new(KEY, body, hashlib.sha256).hexdigest()
    assert _signature(KEY, payload) != _signature(b"other", payload)


@pytest.mark.parametrize("examined", ["h1", "sha256:other"])
def test_read_signed_returns_the_payload_of_a_record_this_build_signed(tmp_path: Path, examined: str) -> None:
    store = _store(tmp_path)
    path = write_proof(store, ExamProof("e1", "blk", examined, True))
    got = _read_signed(store, path.name, so.EXAM_KIND)
    assert got is not None and got["examined_hash"] == examined
    assert _read_signed(store, path.name, "another_kind") is None
    other = so.ProofStore(root=store.root, build_id="b-2", key=KEY)
    assert _read_signed(other, path.name, so.EXAM_KIND) is None


@pytest.mark.parametrize("payload", [["a", "list"], "text"])
def test_read_signed_refuses_a_signed_payload_that_is_not_a_mapping(tmp_path: Path, payload: object) -> None:
    store = _store(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    body = {"payload": payload, "signature": _signature(KEY, payload)}  # type: ignore[arg-type]
    (store.root / "odd.json").write_text(json.dumps(body), encoding="utf-8")
    assert _read_signed(store, "odd.json", so.EXAM_KIND) is None


def test_read_signed_refuses_a_forged_signature_and_a_missing_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = write_proof(store, ExamProof("e1", "blk", "h1", True))
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["signature"] = "0" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _read_signed(store, path.name, so.EXAM_KIND) is None
    assert _read_signed(store, "absent.json", so.EXAM_KIND) is None


def test_proof_refusal_accepts_only_a_passing_proof_of_these_bytes() -> None:
    good = ExamProof(exam_id="e1", block_id="blk", examined_hash="h1", passed=True)
    assert proof_refusal(good, "blk", "h1") is None
    assert "no Factory-held proof" in (proof_refusal(None, "blk", "h1") or "")
    assert "not the tree" in (proof_refusal(good, "blk", "h2") or "")
    failed = ExamProof(exam_id="e1", block_id="blk", examined_hash="h1", passed=False)
    assert "does not show it passing" in (proof_refusal(failed, "blk", "h1") or "")
    assert "does not show it passing" in (proof_refusal(good, "other", "h1") or "")


# --- store_proposal -------------------------------------------------------


# --- main -----------------------------------------------------------------


@pytest.mark.parametrize("reason", [None, "the exam proofs directory is not usable (OSError)"])
def test_probe_builds_reports_availability_from_the_proofs_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reason: str | None
) -> None:
    from app import main as app_main
    from app.factory import paths

    seen: list[Path] = []
    monkeypatch.setattr(paths, "factory_outputs_root", lambda: tmp_path)
    monkeypatch.setattr(so, "builds_unavailable_reason", lambda base: seen.append(base) or reason)
    assert app_main._probe_builds() == {"available": reason is None, "reason": reason or ""}
    assert seen == [tmp_path]
