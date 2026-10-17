"""Domain smoke contract test for the medical concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


MEDICAL_CHAIN: Dict[str, Any] = {
    "message": "Here is a medical analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "medical_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag medical risk",
    ],
}


@pytest.fixture
def fake_medical_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a medical kit."""
    return create_fake_engine(tmp_path, monkeypatch, "medical", "medical_v2", "medical_v2.py")


def test_medical_domain_smoke_contract(
    client: TestClient,
    fake_medical_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full medical concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="medical",
        block_id="medical_v2",
        chain=MEDICAL_CHAIN,
        block_filename="medical_v2.py",
    )
