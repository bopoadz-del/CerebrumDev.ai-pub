"""Domain smoke contract test for the finance concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


# Deterministic LLM response for the finance analysis concept.
FINANCE_CHAIN: Dict[str, Any] = {
    "message": "Here is a finance analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload_financials"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "finance_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag revenue decline",
        "Always flag margin compression",
        "Always flag debt covenant risk",
        "Always flag abnormal working capital movement",
    ],
}


@pytest.fixture
def fake_finance_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a finance kit."""
    return create_fake_engine(tmp_path, monkeypatch, "finance", "finance_v2")


def test_finance_domain_smoke_contract(
    client: TestClient,
    fake_finance_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full finance concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="finance",
        block_id="finance_v2",
        chain=FINANCE_CHAIN,
    )
