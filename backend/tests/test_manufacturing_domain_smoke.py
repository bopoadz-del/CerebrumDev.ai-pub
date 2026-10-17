"""Domain smoke contract test for the manufacturing concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


MANUFACTURING_CHAIN: Dict[str, Any] = {
    "message": "Here is a manufacturing analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "manufacturing_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag manufacturing risk",
    ],
}


@pytest.fixture
def fake_manufacturing_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a manufacturing kit."""
    return create_fake_engine(tmp_path, monkeypatch, "manufacturing", "manufacturing_v2", "manufacturing_v2.py")


def test_manufacturing_domain_smoke_contract(
    client: TestClient,
    fake_manufacturing_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full manufacturing concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="manufacturing",
        block_id="manufacturing_v2",
        chain=MANUFACTURING_CHAIN,
        block_filename="manufacturing_v2.py",
    )
