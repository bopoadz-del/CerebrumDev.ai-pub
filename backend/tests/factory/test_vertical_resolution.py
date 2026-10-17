"""A kit declares who it serves AND who it excludes; the Factory never does.

``serves_verticals`` / ``excludes_verticals`` live in the Store kit manifest.
An excluded vertical resolves UNSUPPORTED with the excluding kit named as the
reason, whatever else claims to serve it.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory import inventory
from app.factory.store_kits import (
    SERVED,
    UNKNOWN,
    UNSUPPORTED,
    domain_kits,
    resolve_vertical,
)


def _store(tmp_path: Path, **kits: dict) -> Path:
    for kit_id, manifest in kits.items():
        d = tmp_path / "block_store" / "kits" / kit_id
        d.mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"id": kit_id, **manifest}), encoding="utf-8")
    return tmp_path


def test_an_excluded_vertical_resolves_unsupported_with_the_kit_named(tmp_path):
    root = _store(tmp_path, zorblat_care={
        "serves_verticals": ["zorblat_care"], "excludes_verticals": ["quillon_care"],
        "build_ready": False, "blocks": ["database"]})
    status, kit, reason = resolve_vertical("quillon-care", domain_kits(root))
    assert status == UNSUPPORTED and kit == "zorblat_care"
    assert "zorblat_care" in reason and "excluded" in reason
    assert inventory.vertical_is_excluded("quillon_care", root) is True
    assert inventory.ready_kit("quillon_care", root) is None
    note = inventory.inventory_drafting_note("quillon_care", root)
    assert UNSUPPORTED in note and "zorblat_care" in note


def test_an_exclusion_wins_over_any_claim_to_serve(tmp_path):
    root = _store(
        tmp_path,
        alpha={"serves_verticals": ["yards"], "build_ready": True, "blocks": ["database"]},
        beta={"serves_verticals": ["beta"], "excludes_verticals": ["yards"], "blocks": ["database"]},
    )
    status, kit, _ = resolve_vertical("yards", domain_kits(root))
    assert (status, kit) == (UNSUPPORTED, "beta")
    assert inventory.ready_kit("yards", root) is None


def test_served_and_unknown_verticals_resolve_as_such(tmp_path):
    root = _store(tmp_path, alpha={"serves_verticals": ["yards"], "blocks": ["database"]})
    assert resolve_vertical("yards", domain_kits(root))[0] == SERVED
    assert resolve_vertical("nowhere", domain_kits(root))[:2] == (UNKNOWN, None)
