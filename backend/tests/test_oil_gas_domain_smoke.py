"""Domain smoke contract test for the oil_gas concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


OIL_GAS_CHAIN: Dict[str, Any] = {
    "message": "Here is a oil_gas analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "oil_gas_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag oil_gas risk",
    ],
}


@pytest.fixture
def fake_oil_gas_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a oil_gas kit."""
    return create_fake_engine(tmp_path, monkeypatch, "oil_gas", "oil_gas_v2", "oil_gas_v2.py")


def test_oil_gas_domain_smoke_contract(
    client: TestClient,
    fake_oil_gas_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full oil_gas concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="oil_gas",
        block_id="oil_gas_v2",
        chain=OIL_GAS_CHAIN,
        block_filename="oil_gas_v2.py",
    )
