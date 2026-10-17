"""A build's resume key must not move when a Factory release adds a field.

Live 2026-10-05 (smoke run 37284007668 on 42c795c2): a build started on one
release, the next release (#645) deployed mid-build and added
``CapabilitySpec.connectors`` with a default of ``[]``. Startup orphan
recovery resumed the in-flight build, re-hashed the SAME blueprint under the
new schema, got a different digest because the dump now carried
``connectors: []`` on every capability, and the resume guard refused it:
"cannot resume: ledger was started from inputs 5a3846cf8aa9 but this run
supplies f034f6569741". The export died with it.

The guard itself is right and stays: a blueprint whose given values differ
is still refused. What changes is what counts as an input -- a value the
schema filled in by default is not one.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest
from pydantic import Field

from app.factory.blueprint import CapabilitySpec, ProductBlueprint, load_blueprint
from app.factory.build.ledger import BuildLedger, LedgerError
from app.factory.build.runner import blueprint_hash

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints" / "examples" / "runner_smoke.yaml"


# A later release of the schema: one more capability field and one more
# blueprint field, both with defaults -- exactly the shape of #645's change.
class _NextCapabilitySpec(CapabilitySpec):
    zorblat_links: List[str] = Field(default_factory=list)


class _NextProductBlueprint(ProductBlueprint):
    capabilities: List[_NextCapabilitySpec]  # type: ignore[assignment]
    quillon_mode: str = "off"


def _authored(bp: ProductBlueprint) -> dict:
    """What a user / the architect actually gave: the non-default values."""
    return bp.model_dump(mode="json", exclude_defaults=True)


def test_a_release_that_adds_defaulted_fields_keeps_the_resume_key():
    today = load_blueprint(SMOKE)
    next_release = _NextProductBlueprint.model_validate(_authored(today))
    # The next release's full dump carries the new fields -- this is what
    # moved the old hash.
    full = next_release.model_dump(mode="json")
    assert "quillon_mode" in full
    assert all("zorblat_links" in cap for cap in full["capabilities"])
    assert blueprint_hash(next_release) == blueprint_hash(today)


def test_the_live_sequence_resumes_across_the_release(tmp_path):
    """Ledger opened by release N; release N+1's runner re-hashes and resumes."""
    today = load_blueprint(SMOKE)
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id=today.product_id, inputs_hash=blueprint_hash(today))

    # Orphan recovery reloads the persisted blueprint under the new schema.
    reloaded = _NextProductBlueprint.model_validate(today.model_dump(mode="json"))
    ledger.assert_resumable(inputs_hash=blueprint_hash(reloaded))


def test_a_genuinely_different_blueprint_is_still_refused(tmp_path):
    today = load_blueprint(SMOKE)
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id=today.product_id, inputs_hash=blueprint_hash(today))

    data = today.model_dump(mode="json")
    data["capabilities"][0]["description"] += " -- and it also files the tax return"
    changed = ProductBlueprint.model_validate(data)
    assert blueprint_hash(changed) != blueprint_hash(today)
    with pytest.raises(LedgerError, match="cannot resume"):
        ledger.assert_resumable(inputs_hash=blueprint_hash(changed))


def test_a_value_given_away_from_its_default_still_moves_the_hash():
    today = load_blueprint(SMOKE)
    data = today.model_dump(mode="json")
    data["capabilities"][0]["connectors"] = ["moonwell_ledger_api"]
    with_connector = ProductBlueprint.model_validate(data)
    assert blueprint_hash(with_connector) != blueprint_hash(today)


def _start_capturing(monkeypatch, bp, out):
    """start_runner_build with the thread body replaced by a recorder."""
    from app.factory.build_jobs import start_runner_build

    seen = {}
    # The coding-CLI readiness gate is not what is under test (same stub the
    # orphan-recovery tests use); the build's identity is.
    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready",
        lambda *a, **k: None,
    )

    def _record(blueprint, output_dir, *args, **kwargs):
        seen["blueprint"] = blueprint
        seen["inputs_hash"] = args[-1] if args else kwargs.get("inputs_hash")

    monkeypatch.setattr("app.factory.build_jobs._run", _record)
    result = start_runner_build(bp, out)
    import time

    for _ in range(100):
        if seen:
            break
        time.sleep(0.02)
    return result, seen


def test_the_build_gets_a_frozen_copy_and_the_one_hash_taken_at_approval(
    monkeypatch, tmp_path
):
    bp = load_blueprint(SMOKE)
    approved = blueprint_hash(bp)
    out = tmp_path / "sessions" / "s1" / bp.product_id
    result, seen = _start_capturing(monkeypatch, bp, out)

    assert result["inputs_hash"] == approved
    assert seen["inputs_hash"] == approved
    assert BuildLedger(out / "build_ledger.jsonl").inputs_hash() == approved
    # The thread holds its own copy, not the caller's object.
    assert seen["blueprint"] is not bp
    assert seen["blueprint"].capabilities[0] is not bp.capabilities[0]

    # Anything the caller does after approval -- a later typed field, a
    # chat turn editing the session's blueprint -- lands on ITS object only.
    bp.capabilities[0].description += " -- edited after approval"
    bp.capabilities.append(bp.capabilities[0].model_copy(update={"id": "late_cap"}))
    assert blueprint_hash(seen["blueprint"]) == approved
    BuildLedger(out / "build_ledger.jsonl").assert_resumable(
        inputs_hash=seen["inputs_hash"]
    )


def test_the_runner_never_rehashes_the_blueprint_it_was_given(tmp_path):
    from app.factory.build.runner import RoleRunner

    bp = load_blueprint(SMOKE)
    approved = blueprint_hash(bp)
    runner = RoleRunner(bp, tmp_path / "ws", inputs_hash=approved)
    runner.blueprint.capabilities[0].description += " -- touched mid-build"
    assert runner.inputs_hash == approved

    # run() checks the ledger with the CARRIED hash: a ledger opened from
    # other inputs is refused before any role runs, whatever self.blueprint
    # now says.
    ledger = BuildLedger(tmp_path / "ws" / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash="0" * 64)
    with pytest.raises(LedgerError, match="cannot resume"):
        runner.run()


def test_a_second_start_with_identical_inputs_resumes(monkeypatch, tmp_path):
    bp = load_blueprint(SMOKE)
    out = tmp_path / "sessions" / "s1" / bp.product_id
    first, _ = _start_capturing(monkeypatch, bp, out)
    # The same approval arriving again, as the session stores it (JSON round
    # trip) -- what orphan recovery and a repeated Approve both hand in.
    again = ProductBlueprint.model_validate(bp.model_dump(mode="json"))
    second, _ = _start_capturing(monkeypatch, again, out)
    assert second["inputs_hash"] == first["inputs_hash"]
    BuildLedger(out / "build_ledger.jsonl").assert_resumable(
        inputs_hash=second["inputs_hash"]
    )


def test_a_value_spelled_out_at_its_default_is_the_same_input():
    today = load_blueprint(SMOKE)
    spelled_out = ProductBlueprint.model_validate(today.model_dump(mode="json"))
    assert blueprint_hash(spelled_out) == blueprint_hash(today)
