"""CD-LAUNCH-1 GATE 1: kill mid-WRITER resumes from the last landed cap.

C-BRIEF is one indivisible CLI call today, then keep-path emit writes
handlers in plan order. A process killed after capability N lands must
not restart from capability 0. Persist each landed cap into the existing
resume spine (``resume_point()`` / ``blueprint_hash``) so the next WRITER
pass skips already-landed ids.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.persist_accept import persist_handler_rel
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import (
    BuildBudget,
    RoleRunner,
    blueprint_hash,
    checkpoint_landed_capability,
    landed_capability_ids,
    pending_capability_ids,
)

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


class _Killed(BaseException):
    """Hard kill: not caught by the runner."""


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def test_landed_capability_ids_read_the_resume_spine(blueprint, tmp_path):
    """Checkpoints are ledger NOTES keyed by blueprint_hash — not a new store."""
    digest = blueprint_hash(blueprint)
    other = blueprint_hash(load_blueprint(ROOT / "blueprints/examples/basic_product.yaml"))
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="runner-smoke", inputs_hash=digest)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed capability analytics_surface",
        payload={
            "stage": "checkpoint",
            "capability": "analytics_surface",
            "inputs_hash": digest,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed capability from another blueprint",
        payload={
            "stage": "checkpoint",
            "capability": "stray",
            "inputs_hash": other,
        },
    )
    assert landed_capability_ids(ledger, digest) == ["analytics_surface"]
    assert "stray" not in landed_capability_ids(ledger, digest)
    assert landed_capability_ids(ledger, other) == ["stray"]


def test_resume_skips_already_landed_capabilities(blueprint, tmp_path):
    """Kill after the first cap lands; resume must start at the next one."""
    digest = blueprint_hash(blueprint)
    cap_ids = [c.id for c in blueprint.capabilities]
    assert len(cap_ids) >= 2, cap_ids
    first, second = cap_ids[0], cap_ids[1]
    seen: list[str] = []

    def writing(ctx):
        pending = pending_capability_ids(ctx)
        for cid in pending:
            seen.append(cid)
            rel = persist_handler_rel(cid)
            ctx.workspace.write_text(rel, f"CAPABILITY = {cid!r}\n")
            checkpoint_landed_capability(ctx, cid)
            if cid == first:
                raise _Killed(f"killed after landing {cid}")
        return RoleResult(ok=True, detail="remaining caps landed")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writing
    out = tmp_path / "build"
    first_runner = RoleRunner(
        blueprint,
        out,
        roles=roles,
        budget=BuildBudget(wall_clock_s=600, phase_wall_clock_s=0),
    )
    with pytest.raises(_Killed, match=first):
        first_runner.run()

    ledger = BuildLedger(out / "build_ledger.jsonl")
    assert ledger.resume_point() is BuildRole.WRITER
    assert ledger.inputs_hash() == digest
    assert first in landed_capability_ids(ledger, digest)
    dest_handler = out / persist_handler_rel(first)
    assert dest_handler.is_file(), "landed cap must survive the staging wipe"
    assert first in dest_handler.read_text(encoding="utf-8")

    seen.clear()
    second_runner = RoleRunner(
        blueprint,
        out,
        roles=roles,
        budget=BuildBudget(wall_clock_s=600, phase_wall_clock_s=0),
    )
    second_runner.run()

    assert first not in seen, (
        f"resume rewrote already-landed {first}; pending was {seen}"
    )
    assert second in seen
    assert first in landed_capability_ids(
        BuildLedger(out / "build_ledger.jsonl"), digest
    )
    assert second in landed_capability_ids(
        BuildLedger(out / "build_ledger.jsonl"), digest
    )
