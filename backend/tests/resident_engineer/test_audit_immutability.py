"""Heal audit log is append-only."""

from __future__ import annotations

import pytest

from app.resident_engineer.heal.audit_log import AuditImmutabilityError, HealAuditLog


def test_audit_append_only(tmp_path):
    log = HealAuditLog(tmp_path / "audit.jsonl")
    log.append({"action_id": "resident.retry_ingestion", "status": "success"})
    log.append({"action_id": "resident.rebuild_index", "status": "success"})
    rows = log.read_all()
    assert len(rows) == 2
    with pytest.raises(AuditImmutabilityError):
        log.rewrite_forbidden([])
    # Prior records still intact
    assert len(log.read_all()) == 2
