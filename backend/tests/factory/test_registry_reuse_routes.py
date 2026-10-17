"""Factory stub of GET /v1/registry/blocks|reuse/{id} — always 200, exact id."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.factory.build.reuse_lookup import extract_l2_fields, load_local_block_json


def test_registry_blocks_present_event_bus_is_200(client: TestClient):
    resp = client.get("/v1/registry/blocks/event_bus")
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["reuse"] is True
    assert body["id"] == "event_bus"
    # Lock-parity pin harvests L2.2 from Store block.json — do not invent extras.
    pin = load_local_block_json("event_bus")
    assert pin is not None
    fields, declared = extract_l2_fields(pin)
    assert declared is True
    assert body["reads"] == fields["reads"]
    assert body["writes"] == fields["writes"]
    assert body["never"] == fields["never"]
    assert body["acceptance"] == fields["acceptance"]
    assert "appointment_slot" not in str(body)
    assert body["manifest"]["id"] == "event_bus"


def test_registry_reuse_alias_matches_canonical(client: TestClient):
    blocks = client.get("/v1/registry/blocks/document_engine").json()
    reuse = client.get("/v1/registry/reuse/document_engine").json()
    assert blocks == reuse
    assert reuse["present"] is True
    assert reuse["id"] == "document_engine"


def test_registry_absent_is_200_not_404(client: TestClient):
    resp = client.get("/v1/registry/reuse/not_a_real_block")
    assert resp.status_code == 200
    assert resp.json() == {
        "present": False,
        "id": "not_a_real_block",
        "reuse": False,
    }


def test_registry_id_is_case_sensitive(client: TestClient):
    folded = client.get("/v1/registry/blocks/DocumentEngine")
    assert folded.status_code == 200
    assert folded.json()["present"] is False
    exact = client.get("/v1/registry/blocks/document_engine")
    assert exact.json()["present"] is True


def test_registry_echoes_declared_l2_from_local_block_json(
    client: TestClient, tmp_path, monkeypatch
):
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
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path))
    resp = client.get("/v1/registry/blocks/event_bus")
    assert resp.status_code == 200
    body = resp.json()
    assert body["present"] is True
    assert body["reads"] == ["topic"]
    assert body["writes"] == ["event"]
    assert body["never"] == ["channel"]
    assert body["acceptance"] == ["publish succeeds"]
