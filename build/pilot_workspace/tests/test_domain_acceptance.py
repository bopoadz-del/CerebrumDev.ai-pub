"""S12 domain acceptance — performed through execute_action.

Factory code-phase names the ten outcomes. Pilot performs them against
the migrated store. HTTP ok:true is not acceptance.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain_ops import OUTCOMES, perform_all
from app.migrations import upgrade_head

EXPECTED = ['create_persists', 'read_returns_persisted', 'update_persists', 'delete_persists', 'list_only_persisted', 'queue_item_processed', 'refused_action_errors', 'idempotent_duplicate_safe', 'unauthorized_rejected', 'missing_field_rejected']
CAPABILITY = 'analytics_surface'


def test_ten_named_outcomes_are_the_contract():
    assert list(OUTCOMES) == EXPECTED
    assert len(OUTCOMES) == 10
    assert "create_persists" in OUTCOMES
    assert "queue_item_processed" in OUTCOMES
    assert "refused_action_errors" in OUTCOMES


@pytest.mark.pilot
def test_ten_business_outcomes_are_performed_through_the_kernel(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    upgrade_head()
    result = asyncio.run(perform_all(CAPABILITY or None))
    assert result["kernel"] == "execute_action"
    assert result["ok"] is True, result
    assert result["failed"] == []
    assert result["performed"] == list(OUTCOMES)
    for name in OUTCOMES:
        assert result["outcomes"][name]["status"] == "performed", (name, result)
