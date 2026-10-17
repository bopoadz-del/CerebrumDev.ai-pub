"""S13: PILOT_READY is machine-emitted. LotDesk cannot promote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from app.factory.build.harvest import (
    BLOCKS_REPO,
    CONSEQUENCE,
    _store_write_authorized,
    evaluate_harvest,
)
from app.factory.build.preflight import S4_EVIDENCE_FILENAME
from app.factory.build.promotion import (
    EMITTER_ID,
    evaluate_promotion,
    reject_lotdesk_promotion,
    reread_matches,
    write_evidence,
    write_reread_twin,
)
from app.factory.build.roles import _coder_route_body

ROOT = Path(__file__).resolve().parents[3]


def _write_stage(
    stages: Path,
    name: str,
    *,
    verdict: str = "PASS",
    performed: Any = None,
    extra: Dict[str, Any] | None = None,
    twin_verdict: str | None = None,
    twin_disagreements: list | None = None,
) -> None:
    stage = name.split("_", 1)[0]
    body: Dict[str, Any] = {
        "stage": stage,
        "name": name,
        "verdict": verdict,
        "first_failing_criterion": None if verdict == "PASS" else "forced",
    }
    if performed is None and stage in {"S10", "S11", "S12"}:
        if stage == "S12":
            performed = [
                "create_persists",
                "read_returns_persisted",
                "update_persists",
                "delete_persists",
                "list_only_persisted",
                "queue_item_processed",
                "refused_action_errors",
                "idempotent_duplicate_safe",
                "unauthorized_rejected",
                "missing_field_rejected",
            ]
            body["outcomes"] = {
                item: {"status": "performed", "via": "execute_action"}
                for item in performed
            }
        else:
            performed = {
                "restore_drill"
                if stage == "S10"
                else "rollback_drill": {
                    "result": "PASS",
                    "detail": "performed, not configured",
                }
            }
    if performed is not None:
        body["performed"] = performed
    if extra:
        body.update(extra)
    (stages / f"{name}.json").write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )
    twin = {
        "stage": stage,
        "name": name.split("_", 1)[-1],
        "verdict": verdict if twin_verdict is None else twin_verdict,
        "reread_of": f"{name}.json",
        "disagreements": [] if twin_disagreements is None else twin_disagreements,
    }
    (stages / f"{name}.reread.json").write_text(
        json.dumps(twin, indent=2) + "\n", encoding="utf-8"
    )


def _pilot_workspace(tmp_path: Path) -> Path:
    """A ledger showing a pilot cycle actually succeeded (U7).

    Stage evidence alone no longer promotes: evaluate_promotion requires a
    build ledger with RUN_SUCCEEDED at cycle=pilot, because a document can
    be written by hand and a pilot cycle cannot.
    """
    from app.factory.build.package import write_identity

    class _Stampable:
        def __init__(self, root):
            self.workspace = root

        def write_text(self, rel, text):
            dest = self.workspace / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")

    ws = tmp_path / "pilot-ws"
    (ws / "app").mkdir(parents=True, exist_ok=True)
    (ws / "app" / "routes.py").write_text("# payload\n", encoding="utf-8")
    # Identity is stamped because the proof binds the ledger to these bytes:
    # a ledger with no package_identity.json proves a cycle ran somewhere,
    # not that it ran on this tree.
    write_identity(_Stampable(ws))
    event = json.dumps({"kind": "RUN_SUCCEEDED", "payload": {"cycle": "pilot"}})
    (ws / "build_ledger.jsonl").write_text(event + "\n", encoding="utf-8")
    return ws


def _complete_s10_s12(stages: Path) -> None:
    _write_stage(
        stages,
        "S10_data",
        extra={
            "sqlite_on_mounted_disk": {
                "spof": "Render persistent disk is single-instance.",
            }
        },
    )
    _write_stage(
        stages,
        "S11_deploy",
        extra={
            "sqlite_on_mounted_disk": {
                "spof": "SPOF: Render rollback restarts a prior image.",
            }
        },
    )
    _write_stage(stages, "S12_domain")


def test_complete_s10_s11_s12_emits_pilot_ready_true(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    result = evaluate_promotion(stages, pilot_workspace=_pilot_workspace(tmp_path))
    assert result["PILOT_READY"] is True, result
    assert result["verdict"] == "PASS"
    assert result["first_failing_criterion"] is None
    assert result["emitter"] == EMITTER_ID
    assert result["provenance"]["emitter"] == EMITTER_ID
    assert result["provenance"]["git_sha"]
    assert result["provenance"]["kernel_ownership"]["_coder_route_body_is_None"] is True
    assert result["provenance"]["kernel_ownership"]["execute_action_callable"] is True
    assert result["provenance"]["spof"]["S10"]
    assert result["provenance"]["spof"]["S11"]
    assert result["provenance"]["network_posture"]["chosen"] == "P1"
    assert result["harvest"]["verdict"] == "BLOCKED"
    assert result["harvest"]["copied_count"] == 0


def test_missing_stage_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _write_stage(stages, "S10_data")
    _write_stage(stages, "S11_deploy")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert result["verdict"] == "FAIL"
    assert "S12" in result["missing"]
    assert result["first_failing_criterion"]


def test_failed_stage_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S11_deploy", verdict="FAIL")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "S11_deploy.json" in result["failed"]
    assert "verdict_not_pass" in (result["first_failing_criterion"] or "")


def test_configured_only_stage_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(
        stages,
        "S10_data",
        performed={"restore_drill": {"status": "configured-only", "detail": "script present"}},
    )
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "configured_only" in (result["first_failing_criterion"] or "")


def test_reread_mismatch_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S12_domain", twin_verdict="FAIL")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "reread_mismatch" in (result["first_failing_criterion"] or "")


def test_reread_disagreements_are_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S10_data", twin_disagreements=["verdict drifted"])
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "reread_mismatch" in (result["first_failing_criterion"] or "")


def test_missing_reread_twin_is_not_pilot_ready(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    (stages / "S11_deploy.reread.json").unlink()
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "reread_missing" in (result["first_failing_criterion"] or "")


def test_earlier_present_stage_mismatch_blocks_promotion(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S9_test", twin_verdict="FAIL")
    result = evaluate_promotion(stages)
    assert result["PILOT_READY"] is False
    assert "S9_test.json" in result["failed"]
    assert "S9" in result["required_stages"]


def test_s4_alias_is_not_a_reader_input(tmp_path):
    """S4_kernel.json must not dual-count next to S4_ship_kernel.json."""
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    _write_stage(stages, "S4_ship_kernel")
    _write_stage(stages, "S4_kernel", verdict="FAIL")
    result = evaluate_promotion(stages, pilot_workspace=_pilot_workspace(tmp_path))
    names = [record.get("evidence") for record in result["records"]]
    assert S4_EVIDENCE_FILENAME in names
    assert "S4_kernel.json" not in names
    assert result["PILOT_READY"] is True, result
    assert "S4_kernel.json" not in result["failed"]


def test_lotdesk_cannot_become_pilot_ready():
    result = reject_lotdesk_promotion()
    assert result["PILOT_READY"] is False
    assert result["ok"] is False
    assert result["lotdesk"] == "fixture only; not patched"
    assert result["f1_present"] is True
    assert result["f5_present"] is True
    assert result["f6_present"] is True
    assert result["f24_present"] is True
    for code in ("F1", "F5", "F6", "F24"):
        assert code in result["codes"]
    assert result["domain"]["performed"] == []
    assert len(result["domain"]["failed"]) == 10


def test_harvest_is_honestly_blocked():
    harvest = evaluate_harvest()
    assert harvest["verdict"] == "BLOCKED"
    assert harvest["ok"] is False
    assert harvest["copied"] == []
    assert harvest["copied_count"] == 0
    assert harvest["authorized_write_path"] is False
    assert _store_write_authorized() is False
    assert not (ROOT / "build" / "stages" / "HARVEST_AUTHORIZED.json").exists()
    assert "will not receive" in harvest["consequence"]
    assert harvest["consequence"] == CONSEQUENCE
    assert harvest["unharvested_fixes"]


def test_authorization_alone_does_not_copy(tmp_path, monkeypatch):
    """Authorized means a harvest MAY be written, never that one was.

    Before U6 this asserted that a planted marker could not authorize,
    because store_write_exists was the literal False. The write path now
    exists, so the marker does authorize — and the guarantee that still
    matters is the separation: evaluate_harvest never copies anything.
    Writing is store_write.execute_harvest, onto a review branch.
    """
    stages = tmp_path / "build" / "stages"
    stages.mkdir(parents=True)
    (stages / "HARVEST_AUTHORIZED.json").write_text(
        json.dumps({"authorized": True, "blocks_repo": BLOCKS_REPO}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.factory.build.harvest._repo_root", lambda: tmp_path)

    assert _store_write_authorized() is True

    # An explicit non-checkout keeps this off whatever clone the machine has.
    not_a_checkout = tmp_path / "not-a-checkout"
    not_a_checkout.mkdir()
    harvest = evaluate_harvest(not_a_checkout)

    assert harvest["copied_count"] == 0
    assert harvest["copied"] == []
    assert harvest["verdict"] == "BLOCKED"
    assert harvest["checkout_writable"] is False


def test_coder_route_body_stays_none():
    assert _coder_route_body(None, None, None) is None
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")
    assert "prepare_pilot_workspace" not in Path(runner_mod.__file__).read_text(
        encoding="utf-8"
    )


def test_reread_matches_requires_same_verdict_and_empty_disagreements():
    evidence = {"verdict": "PASS"}
    assert reread_matches(evidence, {"verdict": "PASS", "disagreements": []}) is True
    assert reread_matches(evidence, {"verdict": "FAIL", "disagreements": []}) is False
    assert reread_matches(evidence, {"verdict": "PASS", "disagreements": ["x"]}) is False


def test_write_evidence_is_the_only_true_pilot_ready_sink(tmp_path):
    stages = tmp_path / "stages"
    stages.mkdir()
    _complete_s10_s12(stages)
    result = evaluate_promotion(stages, pilot_workspace=_pilot_workspace(tmp_path))
    dest = tmp_path / "S13_promotion.json"
    write_evidence(dest, result)
    twin = write_reread_twin(dest, result)
    written = json.loads(dest.read_text(encoding="utf-8"))
    reread = json.loads(twin.read_text(encoding="utf-8"))
    assert written["PILOT_READY"] is True
    assert reread["PILOT_READY"] is True
    assert reread["disagreements"] == []
    assert reread["verdict"] == written["verdict"]
    assert reread["git_sha"] == written["provenance"]["git_sha"]
    assert reread["git_sha"]


def test_in_tree_stage_evidence_is_clean_but_still_needs_a_pilot_cycle():
    """Every in-tree stage now agrees; the U7 guard is what holds the line.

    S9's twin was stale rather than disagreeing — written thirty minutes
    before the primary, denying a CI job that had since been added. S4's
    recorded its remaining emitter work, which #190 closed. Both were
    re-read against the tree they describe and now agree.

    That leaves nothing in the documents to block promotion, which is
    exactly why the pilot-cycle requirement matters: evidence alone must
    not be sufficient. Without a proven pilot cycle this tree is still not
    PILOT_READY, and that is the only thing stopping it.
    """
    lotdesk = reject_lotdesk_promotion()
    assert lotdesk["PILOT_READY"] is False

    stages = ROOT / "build" / "stages"
    for required in ("S10_data.json", "S11_deploy.json", "S12_domain.json"):
        assert (stages / required).is_file()

    result = evaluate_promotion(stages)

    # No stage record objects any more.
    assert [r for r in result["records"] if not r["ok"]] == []
    # And promotion is still refused, on the pilot cycle alone.
    assert result["PILOT_READY"] is False
    assert "pilot_cycle" in str(result["first_failing_criterion"])
    assert result["pilot_cycle"]["ok"] is False
