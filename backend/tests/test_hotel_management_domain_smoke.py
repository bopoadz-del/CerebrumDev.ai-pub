"""Domain smoke contract test for the hotel_management concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


HOTEL_MANAGEMENT_CHAIN: Dict[str, Any] = {
    "message": "Here is a hotel_management analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "hotel_management_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag hotel_management risk",
    ],
}


@pytest.fixture
def fake_hotel_management_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a hotel_management kit."""
    return create_fake_engine(tmp_path, monkeypatch, "hotel_management", "hotel_management_v2", "hotel_v2.py")


def test_hotel_management_domain_smoke_contract(
    client: TestClient,
    fake_hotel_management_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full hotel_management concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="hotel_management",
        block_id="hotel_management_v2",
        chain=HOTEL_MANAGEMENT_CHAIN,
        block_filename="hotel_v2.py",
    )
