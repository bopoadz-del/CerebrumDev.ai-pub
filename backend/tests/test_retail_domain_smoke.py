"""Domain smoke contract test for the retail concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


RETAIL_CHAIN: Dict[str, Any] = {
    "message": "Here is a retail analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "retail_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag retail risk",
    ],
}


@pytest.fixture
def fake_retail_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a retail kit."""
    return create_fake_engine(tmp_path, monkeypatch, "retail", "retail_v2", "retail_v2.py")


def test_retail_domain_smoke_contract(
    client: TestClient,
    fake_retail_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full retail concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="retail",
        block_id="retail_v2",
        chain=RETAIL_CHAIN,
        block_filename="retail_v2.py",
    )
