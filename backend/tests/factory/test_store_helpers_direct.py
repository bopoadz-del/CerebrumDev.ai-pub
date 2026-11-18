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
from app.factory.build import store_proposal as sp
from app.factory.build.store_override import (
    ExamProof,
    OverrideRecord,
    StoreDefect,
    _override_refusal,
    _read_signed,
    _row_value,
    _signature,
    _vendored_payload,
    open_proof_store,
    proof_refusal,
    record_vendored,
    unusable_reason,
    vendored_at,
    vendored_path,
    vendored_rows,
    write_proof,
)
from app.factory.build.store_proposal import (
    _defect_rows,
    _saved_override,
    _text_lines,
    _write_rows,
    proposals_path,
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
    record_vendored(store, {"blk": examined})
    got = _read_signed(store, so.VENDORED_RECORD, so.VENDORED_KIND)
    assert got is not None and got["blocks"] == {"blk": examined}
    assert _read_signed(store, so.VENDORED_RECORD, so.EXAM_KIND) is None
    other = so.ProofStore(root=store.root, build_id="b-2", key=KEY)
    assert _read_signed(other, so.VENDORED_RECORD, so.VENDORED_KIND) is None


@pytest.mark.parametrize("payload", [["a", "list"], "text"])
def test_read_signed_refuses_a_signed_payload_that_is_not_a_mapping(tmp_path: Path, payload: object) -> None:
    store = _store(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    body = {"payload": payload, "signature": _signature(KEY, payload)}  # type: ignore[arg-type]
    (store.root / "odd.json").write_text(json.dumps(body), encoding="utf-8")
    assert _read_signed(store, "odd.json", so.VENDORED_KIND) is None


def test_read_signed_refuses_a_forged_signature_and_a_missing_file(tmp_path: Path) -> None:
    store = _store(tmp_path)
    path = record_vendored(store, {"blk": "h1"})
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["signature"] = "0" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert _read_signed(store, so.VENDORED_RECORD, so.VENDORED_KIND) is None
    assert _read_signed(store, "absent.json", so.VENDORED_KIND) is None


def test_proof_refusal_accepts_only_a_passing_proof_of_these_bytes() -> None:
    good = ExamProof(exam_id="e1", block_id="blk", examined_hash="h1", passed=True)
    assert proof_refusal(good, "blk", "h1") is None
    assert "no Factory-held proof" in (proof_refusal(None, "blk", "h1") or "")
    assert "not the tree" in (proof_refusal(good, "blk", "h2") or "")
    failed = ExamProof(exam_id="e1", block_id="blk", examined_hash="h1", passed=False)
    assert "does not show it passing" in (proof_refusal(failed, "blk", "h1") or "")
    assert "does not show it passing" in (proof_refusal(good, "other", "h1") or "")


@pytest.mark.parametrize("blocks", [{"blk": "h1"}, {"a": "h2", "b": "h3"}])
def test_vendored_payload_is_none_without_a_store_and_the_record_with_one(tmp_path: Path, blocks: dict) -> None:
    assert _vendored_payload(None) is None
    store = _store(tmp_path)
    assert _vendored_payload(store) is None
    record_vendored(store, blocks)
    got = _vendored_payload(store)
    assert got is not None and got["blocks"] == blocks


@pytest.mark.parametrize("commit", ["c1", "c2"])
def test_vendored_rows_are_empty_without_a_record_and_the_rows_with_one(tmp_path: Path, commit: str) -> None:
    assert vendored_rows(None) == {}
    store = _store(tmp_path)
    assert vendored_rows(store) == {}
    record_vendored(store, {"blk": "h1"}, rows={"blk": {"source": "s", "commit": commit}})
    assert vendored_rows(store) == {"blk": {"source": "s", "commit": commit}}


@pytest.mark.parametrize(
    "path,block,expected",
    [
        (vendored_path("blk"), "blk", True),
        ("./" + vendored_path("blk").replace("/", "\\"), "blk", True),
        (vendored_path("blk"), "other", False),
        ("elsewhere/blk", "blk", False),
    ],
)
def test_vendored_at_is_true_only_at_the_blocks_one_path(path: str, block: str, expected: bool) -> None:
    assert vendored_at(path, block) is expected


def test_override_refusal_refuses_no_record_and_a_failed_exam(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert "no override is recorded" in (_override_refusal("blk", "new", "old", None, store) or "")
    rec = OverrideRecord("blk", "old", "new", "r", "e1", exam_passed=False).to_dict()
    assert "did not pass" in (_override_refusal("blk", "new", "old", rec, store) or "")
    unnamed = OverrideRecord("blk", "old", "new", "r", "", exam_passed=True).to_dict()
    assert "names no exam" in (_override_refusal("blk", "new", "old", unnamed, store) or "")


def test_override_refusal_accepts_a_record_whose_exam_the_factory_proved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    rec = OverrideRecord("blk", "old", "new", "r", "e1", exam_passed=True).to_dict()
    assert "no Factory-held proof" in (_override_refusal("blk", "new", "old", rec, store) or "")
    write_proof(store, ExamProof(exam_id="e1", block_id="blk", examined_hash="new", passed=True))
    assert _override_refusal("blk", "new", "old", rec, store) is None
    assert "replaces a different copy" in (_override_refusal("blk", "new", "other", rec, store) or "")
    assert "differs from both" in (_override_refusal("blk", "newer", "old", rec, store) or "")


@pytest.mark.parametrize(
    "field_name,value,expected",
    [
        ("path", "./vendor\\blocks//blk", "vendor/blocks/blk"),
        ("path", None, ""),
        ("commit", "./a\\b", "./a\\b"),
        ("source", None, ""),
    ],
)
def test_row_value_normalises_only_the_path(field_name: str, value: object, expected: str) -> None:
    assert _row_value(field_name, value) == expected


# --- store_proposal -------------------------------------------------------


@pytest.mark.parametrize("raw, lines", [(b"one\r\ntwo\n", ["one\n", "two\n"]), (b"solo", ["solo"])])
def test_text_lines_reads_lf_lines_and_is_empty_without_a_file(tmp_path: Path, raw: bytes, lines: list) -> None:
    assert _text_lines(None) == []
    f = tmp_path / "a.txt"
    f.write_bytes(raw)
    assert _text_lines(f) == lines


def test_text_lines_is_none_for_bytes_that_are_not_text(tmp_path: Path) -> None:
    f = tmp_path / "b.bin"
    f.write_bytes(b"\xff\xfe\x00bad")
    assert _text_lines(f) is None


@pytest.mark.parametrize("failing_input", [{"x": 1}, [2, 3]])
def test_defect_rows_keep_path_kind_and_input_of_each_defect(failing_input: object) -> None:
    defects = [StoreDefect("blk", "a.py", "raises", failing_input), StoreDefect("blk", "b.py", "wrong", None)]
    assert _defect_rows(defects) == (
        {"path": "a.py", "kind": "raises", "failing_input": failing_input},
        {"path": "b.py", "kind": "wrong", "failing_input": None},
    )
    assert _defect_rows([]) == ()


@pytest.mark.parametrize("rows", [[{"id": "p1"}, {"id": "p2", "b": 2}], [{"id": "q1", "c": [1]}]])
def test_write_rows_replaces_the_proposals_file_with_one_json_line_per_row(tmp_path: Path, rows: list) -> None:
    proposals_path(tmp_path).parent.mkdir(parents=True)
    _write_rows(tmp_path, rows)
    lines = proposals_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert [json.loads(x) for x in lines] == rows
    _write_rows(tmp_path, [{"id": "p3"}])
    assert proposals_path(tmp_path).read_text(encoding="utf-8") == '{"id": "p3"}\n'


def test_saved_override_is_true_only_for_the_exact_saved_record(tmp_path: Path) -> None:
    rec = OverrideRecord("blk", "old", "new", "r", "e1", exam_passed=True)
    assert _saved_override(tmp_path, rec) is False
    (tmp_path / sp.LOCK_FILE).write_text(
        json.dumps({so.OVERRIDES_KEY: {"blk": rec.to_dict()}}), encoding="utf-8"
    )
    assert _saved_override(tmp_path, rec) is True
    other = OverrideRecord("blk", "old", "newer", "r", "e1", exam_passed=True)
    assert _saved_override(tmp_path, other) is False


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
