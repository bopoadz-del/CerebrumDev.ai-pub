"""FG-8: a resume keeps the rework budget it had.

A Floor resume of a failed run continues the same build with the rounds it
had left: the runner's own round counter reads the same number after the
resume as before it. Only Start over (a fresh workspace and ledger) begins
with the full budget.

Every product id, capability id and message below is invented.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.runner import RoleRunner
from app.factory.build_jobs import start_runner_build


def _blueprint():
    from app.factory.blueprint import CapabilitySpec, ProductBlueprint

    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="quillmoor",
        product_name="Quillmoor",
        vertical="quillmoor_vertical",
        summary="quillmoor",
        capabilities=[
            CapabilitySpec(
                id="ledgerling",
                description="ledgerling",
                block_ids=["audit"],
                strategy_hint="REUSE",
            )
        ],
    )


def _rounds(ledger: BuildLedger) -> int:
    """The runner's own count: rework rounds since the last budget reset."""
    return len(RoleRunner._reworks(SimpleNamespace(ledger=ledger)))


@pytest.mark.parametrize("spent", [0, 1])
def test_a_floor_resume_keeps_the_rounds_already_spent(tmp_path: Path, monkeypatch, spent: int):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    out = tmp_path / "quillmoor"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="quillmoor", inputs_hash="h-quillmoor")
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.TESTER, detail="tester broke on glimfen")
    for n in range(spent):
        ledger.append(EventKind.REWORK, role=BuildRole.WRITER, detail=f"round {n + 1}")
    ledger.append(EventKind.RUN_FAILED, role=BuildRole.TESTER, detail="tester broke on glimfen")
    assert _rounds(ledger) == spent

    resumed = start_runner_build(_blueprint(), out, brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}')

    assert resumed["fresh_workspace"] is False and Path(resumed["output_dir"]) == out
    assert _rounds(BuildLedger(out / "build_ledger.jsonl")) == spent
