"""Level grade is fail-closed: pilot_ready false cannot read as founding."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES
from app.factory.build.level_grade import (
    Level,
    three_gate_verdict,
    grade_workspace,
)
from app.factory.build.product_gate import GATE_SCOPES


def test_the_three_gates_come_from_the_typed_state_not_the_sentence():
    """state + cycle (+ the failed phase) decide; the printed sentence does not."""
    assert set(GATE_SCOPES) == {"CODE", "PRODUCT", "STORE"}
    code = {"state": "succeeded", "cycle": "code",
            "detail": "CODE FAIL — zorblat; PRODUCT PASS — zorblat; STORE PASS — zorblat"}
    assert three_gate_verdict(code) == {"CODE": "PASS", "PRODUCT": "NOT_RUN", "STORE": "NOT_RUN"}
    assert three_gate_verdict({"state": "succeeded", "cycle": "pilot"}) == {
        "CODE": "PASS", "PRODUCT": "PASS", "STORE": "PASS"}
    tester = {"state": "failed", "cycle": "pilot", "failure": {"phase": "TESTER"}}
    assert three_gate_verdict(tester) == {"CODE": "PASS", "PRODUCT": "FAIL", "STORE": "NOT_RUN"}
    store = {"state": "failed", "cycle": "pilot", "failure": {"phase": "STORE_MANAGER"}}
    assert three_gate_verdict(store)["STORE"] == "FAIL"
    writer = {"state": "failed", "cycle": "code", "failure": {"phase": "WRITER"}}
    assert three_gate_verdict(writer)["CODE"] == "FAIL"
    assert set(three_gate_verdict({"state": "building"}).values()) == {"UNKNOWN"}


def test_code_cycle_success_is_code_green_not_founding(tmp_path):
    status = {
        "state": "succeeded",
        "cycle": "code",
        "pilot_ready": False,
        "detail": (
            "CODE PASS — the code-phase suite; "
            "PRODUCT NOT RUN — post-boot; "
            "STORE NOT RUN — publish"
        ),
    }
    grade = grade_workspace(tmp_path, status=status)
    assert grade["level"] == Level.CODE_GREEN.value
    assert grade["founding_customer_ready"] is False
    assert grade["pilot_ready"] is False
    assert any("pilot_ready is false" in b for b in grade["blockers"])


def test_pilot_ready_false_cannot_become_store_green_even_if_verdict_overclaims(tmp_path):
    """Honesty lock. Mutation killed: grading STORE_GREEN off the sentence alone."""
    status = {
        "state": "succeeded",
        "cycle": "pilot",
        "pilot_ready": False,
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
    }
    grade = grade_workspace(tmp_path, status=status)
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert grade["founding_customer_ready"] is False


def test_failed_build_is_scaffold(tmp_path):
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "failed",
            "cycle": "pilot",
            "pilot_ready": False,
            "failure": {"phase": "TESTER", "reason": "suite_red"},
            "detail": "TESTER failed: suite is red",
        },
    )
    assert grade["level"] == Level.SCAFFOLD.value
    assert grade["three_gate"]["PRODUCT"] == "FAIL"


def _full_repo(root: Path) -> None:
    for rel in FOURTEEN_ARTIFACT_CLASSES:
        path = root / rel
        if rel.endswith(".py") or "." in Path(rel).name:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# ok\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / ".keep").write_text("", encoding="utf-8")
    extras = {
        "Dockerfile": "FROM python:3.12-slim\n",
        "README.md": "pilot\n",
        "requirements.txt": "fastapi\n",
        "app/block_inputs.py": "def prepare_block_input(*a, **k):\n    return {}\n",
        "tests/test_routes.py": (
            "def test_every_capability_route_accepts_payload():\n    assert True\n"
        ),
        "frontend/src/App.tsx": "export default function App() { return null }\n",
        "app/actions/unit_registry_and_vacancy_tracking.py": (
            "def handle(payload):\n    return {'ok': True}\n"
        ),
    }
    for rel, body in extras.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")
    from app.factory.build.store_acceptance import (
        ACCEPTANCE_CHECK_NAMES,
        AcceptanceLine,
        AcceptanceReport,
        write_acceptance_report,
    )

    write_acceptance_report(
        root,
        AcceptanceReport(
            passed=len(ACCEPTANCE_CHECK_NAMES),
            total=len(ACCEPTANCE_CHECK_NAMES),
            ok=True,
            lines=[AcceptanceLine(name=n, status="PASS") for n in ACCEPTANCE_CHECK_NAMES],
        ),
    )


def test_full_repo_with_pilot_ready_is_founding(tmp_path):
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
            # 0.5: measured authorship is now required to meet the floor.
            # This fixture represents a real founding build; giving it the
            # measured record it would have carried is the honest fix, not
            # a silent flip. Non-thin (agent-majority) so the founding
            # grade's own templated-majority blocker does not fire.
            "authorship": {"artifacts": 24, "agent_written": 22, "templated": 2},
        },
    )
    assert grade["level"] == Level.FOUNDING_CUSTOMER_READY.value
    assert grade["founding_customer_ready"] is True
    assert grade["blockers"] == []


def test_cli_billing_keep_path_is_store_green_not_founding(tmp_path):
    """#338 keep-path: PRODUCT/STORE pass, but CLI billing is not founding.

    Authorship must still meet the full-pilot floor (need=5 when
    n_required is unknown or ≥5). A 1-written billing keep-path is thin
    and is demoted separately.
    """
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
            "authorship": {"artifacts": 24, "agent_written": 6, "templated": 18},
            "coder_receipt": {
                "ok": False,
                "blocker": "FACTORY_CODE_CLI_BILLING",
                "honesty_class": "FACTORY_CODE_CLI_FAILED",
                "detail": "FACTORY_CODE_CLI_BILLING: 429 — insufficient balance",
            },
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert grade["pilot_ready"] is True
    assert grade["full_pilot"] is True
    assert any("FACTORY_CODE_CLI_BILLING" in b or "templated" in b for b in grade["blockers"])


def test_thin_authorship_cannot_claim_store_green(tmp_path):
    """VetCare 1206: action_py=3 is not Store-green (unknown n_required → need=5)."""
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
            "authorship": {
                "artifacts": 24,
                "agent_written": 3,
                "templated": 21,
                "action_py": 3,
                "agent_artifacts": [
                    "audit",
                    "vetcare_hub_veterinary_core",
                    "workflow",
                ],
                "cli_authored_ids": [
                    "audit",
                    "vetcare_hub_veterinary_core",
                    "workflow",
                ],
            },
        },
    )
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert grade["founding_customer_ready"] is False
    assert grade["pilot_ready"] is False
    assert grade["full_pilot"] is False
    assert grade["action_py"] == 3
    assert any("full-pilot floor" in b for b in grade["blockers"])


def test_lettings_four_of_four_meets_scaled_floor(tmp_path):
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {
                "artifacts": 27,
                "agent_written": 4,
                "templated": 23,
                "action_py": 4,
                "n_required": 4,
                "cli_authored_ids": [
                    "unit_registry_and_vacancy_tracking",
                    "viewing_management",
                    "maintenance_issue_tracking",
                    "tenancy_application_pipeline",
                ],
            },
        },
    )
    assert grade["full_pilot"] is True
    assert grade["pilot_ready"] is True
    assert grade["level"] == Level.STORE_GREEN.value


def test_lettings_four_actions_unknown_n_required_still_need_five(tmp_path):
    """Without n_required the absolute floor of 5 still refuses 4 authored."""
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {
                "artifacts": 27,
                "agent_written": 4,
                "templated": 23,
                "action_py": 4,
                "cli_authored_ids": [
                    "unit_registry_and_vacancy_tracking",
                    "viewing_management",
                    "maintenance_issue_tracking",
                    "tenancy_application_pipeline",
                ],
            },
        },
    )
    assert grade["full_pilot"] is False
    assert grade["pilot_ready"] is False
    assert grade["level"] == Level.CODE_GREEN.value


def test_five_cli_authored_ids_still_store_green(tmp_path):
    """Mutation: floor of 5 must not refuse a kit/Steward-shaped keep-path."""
    _full_repo(tmp_path)
    ids = ["audit", "workflow", "team", "document_engine", "validation"]
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {
                "artifacts": 20,
                "agent_written": 5,
                "templated": 15,
                "action_py": 5,
                "agent_artifacts": ids,
                "cli_authored_ids": ids,
            },
        },
    )
    assert grade["level"] == Level.STORE_GREEN.value
    assert grade["pilot_ready"] is True
    assert grade["full_pilot"] is True
    assert grade["action_py"] == 5


def test_template_majority_authorship_is_store_green_not_founding(tmp_path):
    """sess_45729 0639: 8/16 factory-LLM thin zip is Store-green, not founding."""
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
            "authorship": {"artifacts": 24, "agent_written": 8, "templated": 16},
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert grade["pilot_ready"] is True
    assert any("templated" in b for b in grade["blockers"])


def test_factory_llm_fallthrough_blocks_founding_without_cli_name(tmp_path):
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {"artifacts": 28, "agent_written": 22, "templated": 6},
            "coder_receipt": {
                "ok": True,
                "factory_llm_generate_fallthrough": True,
                "factory_llm_written_ids": ["veterinary_care_core"],
            },
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert any("factory_llm_generate_fallthrough" in b for b in grade["blockers"])


def test_http_store_callback_blocks_founding(tmp_path):
    _full_repo(tmp_path)
    (tmp_path / "app" / "actions" / "viewing_management.py").write_text(
        # A real Store callback reads the Store's location setting.
        "import os\nimport httpx\nurl = os.environ['CEREBRUM_API_URL'] + '/v1/execute'\n",
        encoding="utf-8",
    )
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            # 0.5: this test is about the HTTP store callback, not
            # authorship -- give it a measured floor so the callback
            # blocker is the only thing being asserted.
            "authorship": {"artifacts": 24, "agent_written": 6, "templated": 18},
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert any("httpx" in b or "store" in b for b in grade["blockers"])


def test_authorship_only_cannot_claim_store_green(tmp_path):
    """Mutation: authorship floor is not acceptance. 5/13 refuses Store-green."""
    _full_repo(tmp_path)
    from app.factory.build.store_acceptance import (
        ACCEPTANCE_CHECK_NAMES,
        AcceptanceLine,
        AcceptanceReport,
        write_acceptance_report,
    )

    write_acceptance_report(
        tmp_path,
        AcceptanceReport(
            passed=5,
            total=len(ACCEPTANCE_CHECK_NAMES),
            ok=False,
            lines=[
                AcceptanceLine(
                    name=n,
                    status="PASS" if i < 5 else "FAIL",
                    detail="authorship-only",
                )
                for i, n in enumerate(ACCEPTANCE_CHECK_NAMES)
            ],
        ),
    )
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {"artifacts": 24, "agent_written": 6, "templated": 18, "action_py": 6},
        },
    )
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert grade["pilot_ready"] is False
    expected = "acceptance is 5/%d" % len(ACCEPTANCE_CHECK_NAMES)
    assert any(expected in b for b in grade["blockers"])
