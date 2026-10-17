"""Domain smoke contract test for the hr concept flow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient

from tests.helpers.domain_smoke import create_fake_engine, run_domain_smoke_contract


HR_CHAIN: Dict[str, Any] = {
    "message": "Here is a hr analysis chain.",
    "chain": {
        "blocks": [
            {"id": "document_engine", "params": {"action": "upload"}},
            {"id": "vector_search", "params": {"action": "retrieve_context"}},
            {"id": "hr_v2", "params": {"action": "analyze"}},
            {"id": "chat", "params": {"action": "ask"}},
        ],
        "connections": [
            {"from": 0, "to": 1},
            {"from": 1, "to": 2},
            {"from": 2, "to": 3},
        ],
    },
    "rules": [
        "Always flag hr risk",
    ],
}


@pytest.fixture
def fake_hr_engine(tmp_path: Path, monkeypatch) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a hr kit."""
    return create_fake_engine(tmp_path, monkeypatch, "hr", "hr_v2", "hr_v2.py")


def test_hr_domain_smoke_contract(
    client: TestClient,
    fake_hr_engine: Path,
    monkeypatch,
    tmp_path: Path,
):
    """Full hr concept flow: chat -> preview -> approve -> package -> inspect."""
    run_domain_smoke_contract(
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        domain="hr",
        block_id="hr_v2",
        chain=HR_CHAIN,
        block_filename="hr_v2.py",
    )
