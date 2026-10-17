"""Wave 2: audit ledger model and store tests."""

from __future__ import annotations

from pathlib import Path


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit


ROOT = Path(__file__).resolve().parents[3]
KIT = _estate_kit() / "steward_runtime"


def test_audit_models_declare_append_only_ledger():
    text = (KIT / "audit_models.py").read_text(encoding="utf-8")
    assert "class AuditEvent" in text
    assert "class HealApproval" in text
    assert "append-only" in text.lower() or "Append-only" in text


def test_audit_store_persists_events():
    text = (KIT / "audit_store.py").read_text(encoding="utf-8")
    assert "persist_audit_event" in text
    assert "session.add" in text


def test_migration_0003_exists():
    migration = KIT / "migrations" / "versions" / "0003_wave2_audit_heal_money.py"
    assert migration.is_file()
    text = migration.read_text(encoding="utf-8")
    assert "AuditEvent" in text
    assert "HealApproval" in text
    assert "NUMERIC(20,4)" in text
