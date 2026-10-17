"""Domain smoke contract test for the construction concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


CONSTRUCTION_CHAIN: Dict[str, Any] = {
    "message": "Here is a construction analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "construction_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag construction risk",
    ],
}


@pytest.fixture
def fake_construction_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a construction kit."""
    return create_fake_engine(tmp_path, monkeypatch, "construction", "construction_v2", "construction_v2.py")


def test_construction_domain_smoke_contract(
    client: TestClient,
    fake_construction_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full construction concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="construction",
        block_id="construction_v2",
        chain=CONSTRUCTION_CHAIN,
        block_filename="construction_v2.py",
    )
