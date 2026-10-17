"""Launching-ready full-pilot authorship floor.

need = min(5, max(1, n_required)) when the brief/blueprint names required
capabilities; unknown n_required keeps need=5. A 4-cap golden with 4
authored handlers must package. written=0 stays
FACTORY_CODE_CLI_NO_AUTHORSHIP. Kit/Steward-shaped ≥5 stays exportable.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.session_store import create_session, get_session, update_session
from app.factory.build.authorship import (
    FULL_PILOT_MIN_AUTHORED_ACTIONS,
    full_pilot_authorship_acceptance_line,
    full_pilot_authorship_forbidden_lines,
    full_pilot_authorship_from,
    full_pilot_authorship_need,
    full_pilot_authorship_rules_text,
    is_action_artifact_id,
    n_required_capabilities_from,
    thin_store_green_export_blocker,
)
from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.level_grade import Level, attach_level_grade
from app.factory.build_jobs import _authorship, build_status
from app.factory.product_architect import plan_blueprint, session_domain_from_blueprint
from app.main import app
from fastapi.testclient import TestClient
from tests.factory.test_level_grade import _full_repo


VETCARE_THIN = ("audit", "vetcare_hub_veterinary_core", "workflow")
STEWARD_FIVE = ("audit", "workflow", "team", "document_engine", "validation")
LETTINGS_FOUR = (
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
)
SIX_REQUIRED = STEWARD_FIVE + ("notification",)


def test_action_artifact_id_skips_models_routes_and_extras():
    assert is_action_artifact_id("audit") is True
    assert is_action_artifact_id("model:audit") is False
    assert is_action_artifact_id("route:audit") is False
    assert is_action_artifact_id("readme") is False
    assert is_action_artifact_id("template_0.py") is False


def test_floor_constant_is_five():
    """Mutation: a silent drop to 0/1 would re-open thin Store-green."""
    assert FULL_PILOT_MIN_AUTHORED_ACTIONS == 5
    n = FULL_PILOT_MIN_AUTHORED_ACTIONS
    assert full_pilot_authorship_need(None) == 5
    assert full_pilot_authorship_need(0) == 5
    assert full_pilot_authorship_need(4) == 4
    assert full_pilot_authorship_need(1) == 1
    assert full_pilot_authorship_need(6) == 5
    assert full_pilot_authorship_need(10) == 5
    assert f"≥{n}" in full_pilot_authorship_rules_text()
    assert f"≥{n}" in full_pilot_authorship_acceptance_line()
    assert f"<{n}" in full_pilot_authorship_forbidden_lines()
    assert f"≥{n}" in full_pilot_authorship_acceptance_line()
    assert "dynamic floor" in full_pilot_authorship_rules_text(4)
    assert "≥4" in full_pilot_authorship_rules_text(4)
    assert "≥5" not in full_pilot_authorship_rules_text(4).split("dynamic floor")[0]
    assert "≥4" in full_pilot_authorship_acceptance_line(4)


def test_compiled_cbrief_states_the_same_floor_constant():
    """Coder brief must name the scaled #387 refuse — not a lying fixed ≥5."""
    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/examples/runner_smoke.yaml")
    compiled = compile_brief(bp, plan_blueprint(bp))
    text = compiled.text
    n_required = n_required_capabilities_from(plan=plan_blueprint(bp), blueprint=bp)
    assert n_required == 2
    n = full_pilot_authorship_need(n_required)
    assert n == 2
    assert full_pilot_authorship_rules_text(n_required) in text
    assert full_pilot_authorship_acceptance_line(n_required) in text
    assert full_pilot_authorship_forbidden_lines(n_required) in text


def test_vetcare_three_actions_are_below_floor():
    snap = full_pilot_authorship_from(
        {
            "authorship": {
                "agent_written": 3,
                "action_py": 3,
                "agent_artifacts": list(VETCARE_THIN),
                "cli_authored_ids": list(VETCARE_THIN),
            }
        }
    )
    assert snap.action_py == 3
    assert snap.measured is True
    assert snap.meets_floor is False
    assert snap.below_floor is True


def test_four_cli_ids_are_below_floor_when_n_required_unknown():
    """Unknown n_required keeps the absolute need=5 (Steward-shaped)."""
    four = full_pilot_authorship_from(
        {"authorship": {"cli_authored_ids": list(STEWARD_FIVE[:4])}}
    )
    five = full_pilot_authorship_from(
        {"authorship": {"cli_authored_ids": list(STEWARD_FIVE)}}
    )
    assert four.need == 5
    assert four.below_floor is True
    assert five.meets_floor is True
    assert five.below_floor is False


def test_four_of_four_required_meets_floor_three_still_refuses():
    four = full_pilot_authorship_from(
        {
            "authorship": {
                "cli_authored_ids": list(LETTINGS_FOUR),
                "n_required": 4,
            }
        }
    )
    three = full_pilot_authorship_from(
        {
            "authorship": {
                "cli_authored_ids": list(LETTINGS_FOUR[:3]),
                "n_required": 4,
            }
        }
    )
    assert four.need == 4
    assert four.meets_floor is True
    assert four.below_floor is False
    assert three.need == 4
    assert three.below_floor is True
    assert thin_store_green_export_blocker(
        {
            "cycle": "pilot",
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {
                "agent_written": 4,
                "cli_authored_ids": list(LETTINGS_FOUR),
                "n_required": 4,
            },
        }
    ) is None
    blocked = thin_store_green_export_blocker(
        {
            "cycle": "pilot",
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {
                "agent_written": 3,
                "cli_authored_ids": list(LETTINGS_FOUR[:3]),
                "n_required": 4,
            },
        }
    )
    assert blocked
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in blocked
    assert "need ≥4" in blocked


def test_six_required_five_authored_meets_absolute_floor():
    """n_required≥5 still needs ≥5, not all six."""
    five_of_six = full_pilot_authorship_from(
        {
            "authorship": {
                "cli_authored_ids": list(SIX_REQUIRED[:5]),
                "n_required": 6,
            }
        }
    )
    four_of_six = full_pilot_authorship_from(
        {
            "authorship": {
                "cli_authored_ids": list(SIX_REQUIRED[:4]),
                "n_required": 6,
            }
        }
    )
    assert five_of_six.need == 5
    assert five_of_six.meets_floor is True
    assert four_of_six.need == 5
    assert four_of_six.below_floor is True


def test_unmeasured_authorship_is_below_floor():
    """0.5: unmeasured is not a silent pass -- it is below the floor.

    The old semantics (``measured and not meets_floor``) let a build with no
    authorship record at all slip past the floor. That was the false green.
    """
    snap = full_pilot_authorship_from({"pilot_ready": True})
    assert snap.measured is False
    assert snap.meets_floor is False
    assert snap.below_floor is True


def test_export_blocker_refuses_thin_pilot_not_code_cycle():
    thin = {
        "cycle": "pilot",
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {"agent_written": 3, "cli_authored_ids": list(VETCARE_THIN)},
    }
    blocked = thin_store_green_export_blocker(thin)
    assert blocked
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in blocked
    assert "full-pilot" in blocked

    prototype = {
        "cycle": "code",
        "detail": "CODE PASS — x; PRODUCT NOT RUN — y; STORE NOT RUN — z",
        "authorship": {"agent_written": 3, "cli_authored_ids": list(VETCARE_THIN)},
    }
    assert thin_store_green_export_blocker(prototype) is None

    healthy = {
        "cycle": "pilot",
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {"agent_written": 5, "cli_authored_ids": list(STEWARD_FIVE)},
    }
    assert thin_store_green_export_blocker(healthy) is None


def _succeeded_pilot(out: Path, *, product_id: str) -> None:
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product_id, inputs_hash="floor-hash")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        role=BuildRole.STORE_MANAGER,
        detail="CODE PASS — suite; PRODUCT PASS — persist; STORE PASS — ops",
        payload={"outcome": "SUCCESS", "cycle": "pilot", "pilot_ready": True},
    )


def _write_provenance(out: Path, action_ids: tuple[str, ...]) -> None:
    docs = out / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    sources = {cid: "coder CLI (/usr/local/bin/kimi)" for cid in action_ids}
    sources["readme"] = "deterministic contract template"
    (docs / "build_provenance.json").write_text(
        json.dumps(
            {
                "artifact_sources": sources,
                "brief_dispatch": {
                    "via": "cli",
                    "ok": True,
                    "cli_authored_ids": list(action_ids),
                },
                "coder_failures": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_authorship_status_exposes_action_py_and_cli_ids(tmp_path):
    out = tmp_path / "vetcare-hub"
    out.mkdir()
    _write_provenance(out, VETCARE_THIN)
    auth = _authorship(out)["authorship"]
    assert auth["action_py"] == 3
    assert auth["agent_written"] == 3
    assert set(auth["cli_authored_ids"]) == set(VETCARE_THIN)
    assert set(auth["agent_artifacts"]) == set(VETCARE_THIN)


def test_build_status_demotes_thin_pilot_ready(tmp_path):
    out = tmp_path / "vetcare-hub"
    _full_repo(out)
    _succeeded_pilot(out, product_id="vetcare-hub")
    _write_provenance(out, VETCARE_THIN)
    status = build_status(out)
    assert status["state"] == "succeeded"
    assert status["pilot_ready"] is False
    assert status["level_grade"]["level"] != Level.STORE_GREEN.value
    assert status["level_grade"]["full_pilot"] is False
    assert status["authorship"]["action_py"] == 3


def test_build_status_keeps_steward_shaped_five(tmp_path):
    out = tmp_path / "projectsix"
    _full_repo(out)
    _succeeded_pilot(out, product_id="projectsix")
    _write_provenance(out, STEWARD_FIVE)
    status = build_status(out)
    assert status["pilot_ready"] is True
    assert status["level_grade"]["level"] in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert status["level_grade"]["full_pilot"] is True
    assert status["authorship"]["action_py"] == 5


def test_attach_level_grade_mutation_cannot_keep_store_green_when_thin(tmp_path):
    _full_repo(tmp_path)
    status = {
        "state": "succeeded",
        "cycle": "pilot",
        "pilot_ready": True,
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {"agent_written": 4, "cli_authored_ids": list(STEWARD_FIVE[:4])},
    }
    attached = attach_level_grade(status, tmp_path)
    assert attached["pilot_ready"] is False
    assert attached["level_grade"]["full_pilot"] is False
    assert attached["level_grade"]["level"] != Level.STORE_GREEN.value


def test_attach_level_grade_allows_four_of_four_required(tmp_path):
    _full_repo(tmp_path)
    status = {
        "state": "succeeded",
        "cycle": "pilot",
        "pilot_ready": True,
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        "authorship": {
            "agent_written": 4,
            "cli_authored_ids": list(LETTINGS_FOUR),
            "n_required": 4,
        },
    }
    attached = attach_level_grade(status, tmp_path)
    assert attached["pilot_ready"] is True
    assert attached["level_grade"]["full_pilot"] is True
    assert attached["level_grade"]["level"] in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }


def test_product_package_refuses_thin_store_green_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_thin_floor", "tester")
    out = tmp_path / "vetcare-hub-veterinary"
    _full_repo(out)
    _succeeded_pilot(out, product_id="vetcare-hub-veterinary")
    _write_provenance(out, VETCARE_THIN)
    state = get_session("sess_thin_floor")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "vetcare-hub-veterinary",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_thin_floor", state)

    pkg = client.get("/v1/sessions/sess_thin_floor/product/package")
    assert pkg.status_code == 409, pkg.text
    detail = pkg.json()["detail"]
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in detail
    assert "full-pilot" in detail


def test_product_package_allows_steward_shaped_five(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_steward_floor", "tester")
    out = tmp_path / "projectsix"
    _full_repo(out)
    _succeeded_pilot(out, product_id="projectsix")
    _write_provenance(out, STEWARD_FIVE)
    state = get_session("sess_steward_floor")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "projectsix",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_steward_floor", state)

    pkg = client.get("/v1/sessions/sess_steward_floor/product/package")
    assert pkg.status_code == 200, pkg.text
    assert pkg.headers["content-type"].startswith("application/zip")


def test_product_package_refuses_authorship_only_store_green(tmp_path, monkeypatch):
    """Mutation: authorship floor green must not enable Export."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_authored_only", "tester")
    out = tmp_path / "projectsix"
    _full_repo(out)
    _succeeded_pilot(out, product_id="projectsix")
    _write_provenance(out, STEWARD_FIVE)
    from app.factory.build.store_acceptance import (
        ACCEPTANCE_CHECK_NAMES,
        AcceptanceLine,
        AcceptanceReport,
        write_acceptance_report,
    )

    write_acceptance_report(
        out,
        AcceptanceReport(
            passed=5,
            total=len(ACCEPTANCE_CHECK_NAMES),
            ok=False,
            lines=[
                AcceptanceLine(name=n, status="PASS" if i < 5 else "FAIL")
                for i, n in enumerate(ACCEPTANCE_CHECK_NAMES)
            ],
        ),
    )
    state = get_session("sess_authored_only")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "projectsix",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_authored_only", state)

    pkg = client.get("/v1/sessions/sess_authored_only/product/package")
    assert pkg.status_code == 409, pkg.text
    detail = pkg.json()["detail"]
    assert "STORE_ACCEPTANCE" in detail
    assert f"5/{len(ACCEPTANCE_CHECK_NAMES)}" in detail


def _write_required_blueprint(out: Path, cap_ids: tuple[str, ...]) -> None:
    docs = out / "docs" / "blueprint"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "product_blueprint.json").write_text(
        json.dumps(
            {
                "schema_version": "product_blueprint.v1",
                "product_id": "residential-lettings",
                "product_name": "Residential Lettings Platform",
                "vertical": "residential_lettings",
                "summary": "test",
                "capabilities": [
                    {
                        "id": cid,
                        "description": cid.replace("_", " "),
                        "block_ids": ["team"],
                        "required": True,
                    }
                    for cid in cap_ids
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_product_package_allows_four_of_four_lettings(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_lettings_floor", "tester")
    out = tmp_path / "residential-lettings"
    _full_repo(out)
    _succeeded_pilot(out, product_id="residential-lettings")
    _write_provenance(out, LETTINGS_FOUR)
    _write_required_blueprint(out, LETTINGS_FOUR)
    state = get_session("sess_lettings_floor")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "residential-lettings",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_lettings_floor", state)

    pkg = client.get("/v1/sessions/sess_lettings_floor/product/package")
    assert pkg.status_code == 200, pkg.text
    assert pkg.headers["content-type"].startswith("application/zip")


def test_product_package_refuses_three_of_four_lettings(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_lettings_thin", "tester")
    out = tmp_path / "residential-lettings-thin"
    _full_repo(out)
    _succeeded_pilot(out, product_id="residential-lettings")
    _write_provenance(out, LETTINGS_FOUR[:3])
    _write_required_blueprint(out, LETTINGS_FOUR)
    state = get_session("sess_lettings_thin")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "residential-lettings",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_lettings_thin", state)

    pkg = client.get("/v1/sessions/sess_lettings_thin/product/package")
    assert pkg.status_code == 409, pkg.text
    detail = pkg.json()["detail"]
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in detail
    assert "need ≥4" in detail


def test_role_runner_persists_blueprint_so_n_required_survives(tmp_path):
    """Approve→GENERATE must leave a workspace file the next process can read."""
    from app.factory.build.runner import RoleRunner

    root = Path(__file__).resolve().parents[3]
    out = tmp_path / "build"
    out.mkdir()
    runner = RoleRunner(
        load_blueprint(root / "blueprints/lettings/residential_lettings.v1.yaml"),
        out,
    )
    assert runner.state.get("n_required") == 4
    assert n_required_capabilities_from(workspace=out) == 4
    assert (out / "docs" / "blueprint" / "product_blueprint.json").is_file()
    assert not (out / "factory_plan.json").exists()


def test_n_required_from_compiled_string_capability_ids():
    """C-BRIEF capabilities are a list of ids, not CapabilitySpec objects."""

    class _Compiled:
        capabilities = list(LETTINGS_FOUR)

    assert n_required_capabilities_from(compiled=_Compiled()) == 4
    assert n_required_capabilities_from(
        state={"product_design": {"blueprint": {
            "capabilities": [{"id": cid, "required": True} for cid in LETTINGS_FOUR]
        }}}
    ) == 4


def test_n_required_from_workspace_session_blueprint(tmp_path, monkeypatch):
    """Approve→GENERATE often lacks docs/blueprint; session snapshot still has it."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    create_session("sess_4591d5cc45d04fe1", "tester")
    state = get_session("sess_4591d5cc45d04fe1")
    assert state is not None
    root = Path(__file__).resolve().parents[3]
    state.product_design.blueprint = load_blueprint(
        root / "blueprints/lettings/residential_lettings.v1.yaml"
    ).model_dump(mode="json")
    update_session("sess_4591d5cc45d04fe1", state)
    out = tmp_path / "sessions" / "sess_4591d5cc45d04fe1" / "residential-lettings"
    out.mkdir(parents=True)
    assert n_required_capabilities_from(workspace=out) == 4
    assert (out / "docs" / "blueprint" / "product_blueprint.json").is_file() is False


def _failed_thin_pilot(out: Path, *, product_id: str, detail: str) -> None:
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product_id, inputs_hash="floor-hash")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail=detail,
        payload={
            "outcome": "FAILED_ROLE_ERROR",
            "cycle": "pilot",
            "pilot_ready": False,
        },
    )


def _park_lettings_blueprint(session_id: str) -> None:
    root = Path(__file__).resolve().parents[3]
    state = get_session(session_id)
    assert state is not None
    state.product_design.blueprint = load_blueprint(
        root / "blueprints/lettings/residential_lettings.v1.yaml"
    ).model_dump(mode="json")
    update_session(session_id, state)


_STICKY_NEED_FIVE = (
    "FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the "
    "full-pilot floor (written=4, cli_authored_ids=4, need ≥5). "
    "Do not SUCCESS a Store-green pilot from thin authorship."
)


def test_product_package_allows_four_of_four_from_session_blueprint_only(
    tmp_path, monkeypatch
):
    """No workspace blueprint file — n_required comes from the session draft."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_lettings_session_bp", "tester")
    _park_lettings_blueprint("sess_lettings_session_bp")
    out = tmp_path / "residential-lettings-session-bp"
    _full_repo(out)
    _succeeded_pilot(out, product_id="residential-lettings")
    _write_provenance(out, LETTINGS_FOUR)
    state = get_session("sess_lettings_session_bp")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "residential-lettings",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_lettings_session_bp", state)

    pkg = client.get("/v1/sessions/sess_lettings_session_bp/product/package")
    assert pkg.status_code == 200, pkg.text
    assert pkg.headers["content-type"].startswith("application/zip")
    status = client.get("/v1/sessions/sess_lettings_session_bp/product/build-status")
    assert status.status_code == 200
    auth = status.json()["build"]["authorship"]
    assert auth["n_required"] == 4


def test_product_package_reevaluates_sticky_need_five_failure(tmp_path, monkeypatch):
    """Pre-#391 / unknown-n_required RUN_FAILED must not stay need≥5."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_4591d5cc_sticky", "tester")
    _park_lettings_blueprint("sess_4591d5cc_sticky")
    out = tmp_path / "residential-lettings-sticky"
    _full_repo(out)
    _failed_thin_pilot(
        out, product_id="residential-lettings", detail=_STICKY_NEED_FIVE
    )
    _write_provenance(out, LETTINGS_FOUR)
    state = get_session("sess_4591d5cc_sticky")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "residential-lettings",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_4591d5cc_sticky", state)

    status = client.get("/v1/sessions/sess_4591d5cc_sticky/product/build-status")
    assert status.status_code == 200, status.text
    build = status.json()["build"]
    assert build["state"] == "succeeded"
    assert build["pilot_ready"] is True
    assert build.get("honesty") == "full_pilot_authorship_reevaluated"
    assert build["authorship"]["n_required"] == 4
    assert build["level_grade"]["full_pilot"] is True

    pkg = client.get("/v1/sessions/sess_4591d5cc_sticky/product/package")
    assert pkg.status_code == 200, pkg.text
    assert pkg.headers["content-type"].startswith("application/zip")


def test_product_get_clears_sticky_need_five_for_platforms_card(tmp_path, monkeypatch):
    """GET /product must not keep last_error / generation.build on need≥5."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_4591d5cc_platforms", "tester")
    _park_lettings_blueprint("sess_4591d5cc_platforms")
    out = tmp_path / "residential-lettings-platforms"
    _full_repo(out)
    _failed_thin_pilot(
        out, product_id="residential-lettings", detail=_STICKY_NEED_FIVE
    )
    _write_provenance(out, LETTINGS_FOUR)
    state = get_session("sess_4591d5cc_platforms")
    assert state is not None
    state.product_design.last_error = _STICKY_NEED_FIVE
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "residential-lettings",
        "inputs_hash": "floor-hash",
        "engine": "runner",
        "build": {
            "state": "failed",
            "detail": _STICKY_NEED_FIVE,
            "pilot_ready": False,
        },
    }
    update_session("sess_4591d5cc_platforms", state)

    product = client.get("/v1/sessions/sess_4591d5cc_platforms/product")
    assert product.status_code == 200, product.text
    assert product.headers.get("cache-control") == "no-store"
    body = product.json()
    assert body["last_error"] in (None, "")
    gen = body["generation"]
    assert gen["build"]["state"] == "succeeded"
    assert gen["build"]["pilot_ready"] is True
    assert gen["build"]["authorship"]["n_required"] == 4
    assert "need ≥5" not in str(gen["build"].get("detail") or "")

    parked = get_session("sess_4591d5cc_platforms")
    assert parked is not None
    assert parked.product_design.last_error in (None, "")
    assert (parked.product_design.generation or {}).get("build", {}).get("state") == (
        "succeeded"
    )


def test_product_package_sticky_three_of_four_uses_live_need_four(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_lettings_sticky_thin", "tester")
    _park_lettings_blueprint("sess_lettings_sticky_thin")
    out = tmp_path / "residential-lettings-sticky-thin"
    _full_repo(out)
    _failed_thin_pilot(
        out,
        product_id="residential-lettings",
        detail=(
            "FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the "
            "full-pilot floor (written=3, cli_authored_ids=3, need ≥5)"
        ),
    )
    _write_provenance(out, LETTINGS_FOUR[:3])
    state = get_session("sess_lettings_sticky_thin")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "residential-lettings",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_lettings_sticky_thin", state)

    pkg = client.get("/v1/sessions/sess_lettings_sticky_thin/product/package")
    assert pkg.status_code == 409, pkg.text
    detail = pkg.json()["detail"]
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in detail
    assert "need ≥4" in detail
    assert "need ≥5" not in detail


def test_sticky_four_authored_unknown_n_required_still_need_five(tmp_path, monkeypatch):
    """No session/workspace blueprint → absolute floor stays 5."""
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_unknown_nreq", "tester")
    out = tmp_path / "unknown-nreq"
    _full_repo(out)
    _failed_thin_pilot(out, product_id="mystery-product", detail=_STICKY_NEED_FIVE)
    _write_provenance(out, LETTINGS_FOUR)
    state = get_session("sess_unknown_nreq")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "mystery-product",
        "inputs_hash": "floor-hash",
        "engine": "runner",
    }
    update_session("sess_unknown_nreq", state)

    pkg = client.get("/v1/sessions/sess_unknown_nreq/product/package")
    assert pkg.status_code == 409, pkg.text
    detail = pkg.json()["detail"]
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in detail
    assert "need ≥5" in detail


def test_session_domain_from_blueprint_prefers_product_id():
    root = Path(__file__).resolve().parents[3]
    bp = load_blueprint(root / "blueprints/lettings/residential_lettings.v1.yaml")
    assert session_domain_from_blueprint(bp) == "residential-lettings"
    assert session_domain_from_blueprint(bp.model_dump(mode="json")) == "residential-lettings"
    assert session_domain_from_blueprint({"vertical": "residential_lettings"}) == (
        "residential-lettings"
    )
    assert session_domain_from_blueprint(None) == "construction"


def test_lettings_draft_writes_residential_lettings_domain(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_lettings_domain", "tester")
    state = get_session("sess_lettings_domain")
    assert state is not None
    assert state.config.domain == "construction"

    drafted = client.post(
        "/v1/sessions/sess_lettings_domain/product/draft",
        # The vertical is the Floor's structured hint; the brief's prose is
        # not parsed for one.
        json={
            "brief": "build a platform for residential lettings",
            "vertical_hint": "residential_lettings",
        },
    )
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["blueprint"]["product_id"] == "residential-lettings"

    state = get_session("sess_lettings_domain")
    assert state is not None
    assert state.config.domain == "residential-lettings"

    listed = client.get("/v1/sessions/sess_lettings_domain")
    assert listed.status_code == 200
    assert listed.json()["config"]["domain"] == "residential-lettings"


def test_steward_draft_writes_cerebrum_steward_domain(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # 6.3 zip eligibility requires a recorded CI run id; the local
    # suite stubs it the way GITHUB_RUN_ID does on the runner.
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "floor-test-run")
    client = TestClient(app)

    create_session("sess_steward_domain", "tester")
    drafted = client.post(
        "/v1/sessions/sess_steward_domain/product/draft",
        json={
            "brief": "Generate Projectsix private estate operations",
            "vertical_hint": "estate",
        },
    )
    assert drafted.status_code == 200, drafted.text
    state = get_session("sess_steward_domain")
    assert state is not None
    # The session domain is the DRAFTED product's id (goldens are chosen by
    # structure, not by the words in this brief).
    assert state.config.domain == drafted.json()["blueprint"]["product_id"]
