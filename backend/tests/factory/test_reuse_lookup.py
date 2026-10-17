"""Blocks REUSE HTTP is feature-detected; local dual-registry is the fallback."""

from __future__ import annotations

import json

from app.factory.build.reuse_lookup import (
    ReuseRecord,
    extract_l2_fields,
    is_store_exact_id_miss,
    load_local_block_json,
    lookup_reuse,
    parse_reuse_body,
    reset_http_surface_cache,
    resolve_store_presence,
    reuse_payload_for,
)
import pytest


def test_parse_always_200_present_true_pulls_scope():
    rec = parse_reuse_body(
        "event_bus",
        {
            "present": True,
            "reuse": True,
            "id": "event_bus",
            "reads": ["topic"],
            "writes": ["event"],
            "never": ["channel"],
            "acceptance": ["publish succeeds"],
        },
        source="registry/blocks",
    )
    assert rec.present is True
    assert rec.scope_declared is True
    assert rec.reads == ["topic"]
    assert rec.writes == ["event"]
    assert rec.never == ["channel"]
    assert rec.acceptance == ["publish succeeds"]


def test_parse_present_false_is_not_assumed():
    rec = parse_reuse_body(
        "nope",
        {"present": False, "id": "nope", "reuse": False},
        source="registry/blocks",
    )
    assert rec.present is False
    assert rec.scope_declared is False
    assert rec.reads == []


def test_parse_raw_block_json_200_matching_id_is_present():
    """A 200 that is just block.json (no present/reuse keys) is present."""
    rec = parse_reuse_body(
        "event_bus",
        {"id": "event_bus", "name": "event_bus", "version": "1.0.0"},
        source="registry/blocks",
    )
    assert rec.present is True
    assert rec.scope_declared is False
    assert rec.reads == []
    assert rec.writes == []
    assert rec.never == []
    assert rec.acceptance == []


def test_parse_l2_from_nested_manifest():
    rec = parse_reuse_body(
        "event_bus",
        {
            "present": True,
            "reuse": True,
            "id": "event_bus",
            "manifest": {
                "id": "event_bus",
                "reads": ["topic"],
                "writes": ["event"],
                "never": ["channel"],
                "acceptance": ["publish succeeds"],
            },
        },
        source="registry/blocks",
    )
    assert rec.present is True
    assert rec.scope_declared is True
    assert rec.reads == ["topic"]
    assert rec.acceptance == ["publish succeeds"]


def test_parse_id_mismatch_is_absent_case_sensitive():
    rec = parse_reuse_body(
        "DocumentEngine",
        {
            "present": True,
            "reuse": True,
            "id": "document_engine",
            "reads": ["path"],
        },
        source="registry/blocks",
    )
    assert rec.present is False
    assert rec.reads == []


def test_http_unavailable_falls_back_to_local_ids():
    rec = lookup_reuse(
        "only_on_shelf",
        local_ids={"only_on_shelf", "database"},
        base_url="",
    )
    assert rec.present is True
    assert rec.source == "local_dual_registry"
    missing = lookup_reuse("not_a_block", local_ids={"event_bus"}, base_url="")
    assert missing.present is False


def test_injected_getter_runs_when_api_url_unset(monkeypatch):
    """CI has no CEREBRUM_API_URL; STEP 0 still has to call the injected lookup."""
    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)
    seen = []

    def fake_get(block_id, base_url=None):
        seen.append(block_id)
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    rec = lookup_reuse(
        "event_bus",
        local_ids={"event_bus"},
        base_url="",
        http_get=fake_get,
    )
    assert seen == ["event_bus"]
    assert rec.present is False


def test_http_200_wins_over_local_absence():
    def fake_get(block_id, base_url=None):
        return ReuseRecord(
            block_id=block_id,
            present=True,
            source="registry/blocks",
            reads=["payload"],
            scope_declared=True,
        )

    rec = lookup_reuse(
        "remote_only",
        local_ids=set(),
        base_url="https://blocks.example",
        http_get=fake_get,
    )
    assert rec.present is True
    assert rec.reads == ["payload"]


def test_http_present_false_fails_closed_even_if_local_has_id():
    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    rec = lookup_reuse(
        "event_bus",
        local_ids={"event_bus"},
        base_url="https://blocks.example",
        http_get=fake_get,
    )
    assert rec.present is False


def test_resolve_maps_claimed_ids():
    records = resolve_store_presence(
        ["only_on_shelf", "ghost"],
        local_ids={"only_on_shelf"},
        base_url="",
    )
    assert records["only_on_shelf"].present is True
    assert records["ghost"].present is False


def test_case_sensitive_id_is_not_folded():
    rec = lookup_reuse(
        "DocumentEngine",
        local_ids={"document_engine"},
        base_url="",
    )
    assert rec.present is False
    exact = lookup_reuse(
        "document_engine",
        local_ids={"document_engine"},
        base_url="",
    )
    assert exact.present is True


@pytest.fixture
def preflip_store(tmp_path, monkeypatch):
    """A Store whose estate_registry declares NO L2 scopes.

    The pre-flip honesty path ("not declared on block.json -- do not invent
    scopes") used to be proven on the Factory's estate stub, which had no L2
    keys. That stub is gone and the Store's real block declares its scopes,
    so the premise is rebuilt explicitly instead of borrowed from a fake.
    """
    import json as _json

    block = tmp_path / "block_registry" / "estate_registry"
    block.mkdir(parents=True)
    (block / "block.json").write_text(
        _json.dumps({"id": "estate_registry", "version": "1.0.0"}), encoding="utf-8"
    )
    (block / "block.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path))
    return tmp_path


def test_a_block_with_no_l2_keys_does_not_get_invented_scopes(preflip_store):
    """A pin with no L2.2 keys — lookup must not invent them."""
    rec = lookup_reuse("estate_registry", local_ids={"estate_registry"}, base_url="")
    assert rec.present is True
    assert rec.scope_declared is False
    assert rec.reads == []
    assert rec.writes == []
    assert rec.never == []
    assert rec.acceptance == []


def test_vendor_mirror_store_pin_harvests_declared_scopes():
    """Store-sourced event_bus pin declares L2 — harvest those bytes, no extras."""
    rec = lookup_reuse("event_bus", local_ids={"event_bus"}, base_url="")
    pin = load_local_block_json("event_bus")
    assert pin is not None
    fields, declared = extract_l2_fields(pin)
    assert rec.present is True
    assert rec.scope_declared is True
    assert declared is True
    assert rec.reads == fields["reads"]
    assert rec.writes == fields["writes"]
    assert rec.never == fields["never"]
    assert rec.acceptance == fields["acceptance"]
    assert "appointment_slot" not in rec.reads


def test_local_block_json_l2_is_harvested_not_invented(tmp_path):
    d = tmp_path / "block_registry" / "event_bus"
    d.mkdir(parents=True)
    (d / "block.json").write_text(
        json.dumps(
            {
                "id": "event_bus",
                "reads": ["topic"],
                "writes": ["event"],
                "never": ["channel"],
                "acceptance": ["publish succeeds"],
            }
        ),
        encoding="utf-8",
    )
    rec = lookup_reuse(
        "event_bus",
        local_ids={"event_bus"},
        base_url="",
        blocks_root=tmp_path,
    )
    assert rec.present is True
    assert rec.scope_declared is True
    assert rec.reads == ["topic"]
    assert rec.writes == ["event"]
    assert rec.never == ["channel"]
    assert rec.acceptance == ["publish succeeds"]


def test_local_block_json_wrong_case_dir_is_absent(tmp_path):
    d = tmp_path / "block_registry" / "document_engine"
    d.mkdir(parents=True)
    (d / "block.json").write_text(
        json.dumps({"id": "document_engine"}), encoding="utf-8"
    )
    assert load_local_block_json("DocumentEngine", blocks_root=tmp_path) is None
    rec = lookup_reuse("DocumentEngine", local_ids=set(), base_url="", blocks_root=tmp_path)
    assert rec.present is False


def test_reuse_payload_omits_undeclared_l2_keys():
    body = reuse_payload_for("only_on_shelf", local_ids={"only_on_shelf"})
    assert body["present"] is True
    assert "reads" not in body
    assert "writes" not in body
    assert "never" not in body
    assert "acceptance" not in body


def test_reuse_payload_absent_is_not_404_shape():
    body = reuse_payload_for("ghost", local_ids={"event_bus"})
    assert body == {"present": False, "id": "ghost", "reuse": False}


def test_extract_l2_empty_declared_is_not_invented():
    fields, declared = extract_l2_fields({"reads": [], "writes": [], "never": [], "acceptance": []})
    assert declared is True
    assert fields["reads"] == []


def test_reset_http_surface_cache_is_exported():
    reset_http_surface_cache()


def test_store_exact_id_miss_is_registry_present_false_only():
    assert (
        is_store_exact_id_miss(
            ReuseRecord(block_id="readiness_engine", present=False, source="registry/blocks")
        )
        is True
    )
    assert (
        is_store_exact_id_miss(
            ReuseRecord(block_id="readiness_engine", present=False, source="registry/reuse")
        )
        is True
    )
    assert (
        is_store_exact_id_miss(
            ReuseRecord(block_id="readiness_engine", present=True, source="registry/blocks")
        )
        is False
    )
    assert (
        is_store_exact_id_miss(
            ReuseRecord(block_id="ghost", present=False, source="absent")
        )
        is False
    )
    assert (
        is_store_exact_id_miss(
            ReuseRecord(block_id="event_bus", present=True, source="local_dual_registry")
        )
        is False
    )
    assert is_store_exact_id_miss(None) is False
