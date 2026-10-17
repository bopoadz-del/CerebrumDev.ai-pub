"""Domain smoke contract test for the real_estate concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


REAL_ESTATE_CHAIN: Dict[str, Any] = {
    "message": "Here is a real_estate analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "real_estate_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag real_estate risk",
    ],
}


@pytest.fixture
def fake_real_estate_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a real_estate kit."""
    return create_fake_engine(tmp_path, monkeypatch, "real_estate", "real_estate_v2", "real_estate_v2.py")


def test_real_estate_domain_smoke_contract(
    client: TestClient,
    fake_real_estate_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full real_estate concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="real_estate",
        block_id="real_estate_v2",
        chain=REAL_ESTATE_CHAIN,
        block_filename="real_estate_v2.py",
    )
