"""Domain smoke contract test for the aviation concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


AVIATION_CHAIN: Dict[str, Any] = {
    "message": "Here is a aviation analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "aviation_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag aviation risk",
    ],
}


@pytest.fixture
def fake_aviation_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a aviation kit."""
    return create_fake_engine(tmp_path, monkeypatch, "aviation", "aviation_v2", "aviation_v2.py")


def test_aviation_domain_smoke_contract(
    client: TestClient,
    fake_aviation_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full aviation concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="aviation",
        block_id="aviation_v2",
        chain=AVIATION_CHAIN,
        block_filename="aviation_v2.py",
    )
