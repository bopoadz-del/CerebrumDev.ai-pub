"""Domain smoke contract test for the supply_chain concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


SUPPLY_CHAIN_CHAIN: Dict[str, Any] = {
    "message": "Here is a supply_chain analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "supply_chain_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag supply_chain risk",
    ],
}


@pytest.fixture
def fake_supply_chain_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a supply_chain kit."""
    return create_fake_engine(tmp_path, monkeypatch, "supply_chain", "supply_chain_v2", "supply_chain_v2.py")


def test_supply_chain_domain_smoke_contract(
    client: TestClient,
    fake_supply_chain_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full supply_chain concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="supply_chain",
        block_id="supply_chain_v2",
        chain=SUPPLY_CHAIN_CHAIN,
        block_filename="supply_chain_v2.py",
    )
