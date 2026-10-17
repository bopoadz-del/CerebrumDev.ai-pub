"""Domain smoke contract test for the legal concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


# Deterministic LLM response for the legal contract-review concept.
LEGAL_CHAIN: Dict[str, Any] = {
    "message": "Here is a legal contract review chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "ocr_v2", "params": {"action": "scan"}},
            {"id": "legal_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag termination clauses",
        "Always flag payment obligations",
        "Always flag liability caps",
        "Always flag governing law",
        "Always flag dispute resolution",
        "Always flag missing signature pages",
    ],
}


@pytest.fixture
def fake_legal_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a legal kit."""
    return create_fake_engine(tmp_path, monkeypatch, "legal", "legal_v2")


def test_legal_domain_smoke_contract(
    client: TestClient,
    fake_legal_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full legal concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="legal",
        block_id="legal_v2",
        chain=LEGAL_CHAIN,
    )
