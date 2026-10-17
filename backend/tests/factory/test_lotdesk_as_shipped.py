"""LotDesk-as-shipped is rejected at a named gate. The fixture is not patched."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.lotdesk_gate import (
    GATE_NAME,
    LOTDESK_PUBLIC_SHA256,
    reject_lotdesk_as_shipped,
    resolve_lotdesk_fixture,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[3]
COMMITTED_ZIP = (
    ROOT / "backend" / "tests" / "factory" / "fixtures" / "lotdesk_pilot_ready.zip"
)


def test_committed_lotdesk_zip_matches_its_pinned_identity():
    # S0 less its private kit members (see LOTDESK_PUBLIC_SHA256)
    assert COMMITTED_ZIP.is_file(), "CI must see the LotDesk zip fixture"
    assert sha256_file(COMMITTED_ZIP) == LOTDESK_PUBLIC_SHA256


def test_named_gate_rejects_lotdesk_as_shipped_with_f18_and_f19():
    result = reject_lotdesk_as_shipped(COMMITTED_ZIP)
    assert result["ok"] is False
    assert result["gate"] == GATE_NAME
    codes = set(result["codes"])
    assert "F18" in codes, result
    assert "F19" in codes, result
    assert result["required_codes_present"] is True
    assert result["lotdesk"] == "fixture only; not patched"
    details = " ".join(item["detail"] for item in result["findings"])
    assert "_default_block_field" in details or "_ALWAYS_FILL" in details
    assert "release_gate" in details


def test_gate_resolves_the_committed_fixture():
    path = resolve_lotdesk_fixture()
    assert path.exists()
