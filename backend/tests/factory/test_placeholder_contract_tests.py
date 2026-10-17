"""The Factory's emitted placeholder contract test is the authority.

A capability whose connector the blueprint declares a placeholder answers the
typed unavailable refusal (#645). The Factory now stamps the test that says
so, and a WRITER-authored test demanding a live answer from such a capability
is a TEST DEFECT: the writer regenerates it, and it is never a product
failure, a rework round, or SAME_FAILURE_TWICE. Decided from typed data only
-- the product's own record of which running test it refused, read beside
pytest's JUnit report -- never from a test's text or name.

Every capability and connector below is invented.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import ProductBlueprint, load_blueprint
from app.factory.build import failure_owner
from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext, gate_suite_green
from app.factory.build.ledger import EventKind
from app.factory.build.placeholder_connectors import (
    CONTRACT_TEST,
    TEST_DEFECT,
    UNAVAILABLE_STATUS,
    render_contract_tests,
    render_product_module,
)
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.roles_constants import _CONFTEST
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

WORKING = "lantern_ledger"
PLACEHOLDER = "tide_relay"
CONNECTOR = "moonwell_ledger_api"


def _blueprint() -> ProductBlueprint:
    return ProductBlueprint.model_validate(
        {
            "schema_version": "product_blueprint.v1",
            "product_id": "invented-product",
            "product_name": "Invented Product",
            "vertical": "product",
            "summary": "An invented product for the placeholder contract.",
            "capabilities": [
                {"id": WORKING, "description": "keeps lantern entries"},
                {"id": PLACEHOLDER, "description": "relays tides",
                 "connectors": [CONNECTOR]},
            ],
            "connectors": [CONNECTOR],
        }
    )


_MAIN = '''
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.placeholders import UNAVAILABLE_STATUS, refusal_for

app = FastAPI()
ROWS = {}


@app.post("/v1/{cap}")
def create(cap: str, payload: dict):
    refused = refusal_for(cap)
    if refused is not None:
        return JSONResponse(status_code=UNAVAILABLE_STATUS, content=refused)
    ROWS.setdefault(cap, []).append(payload)
    return {"ok": True, "stored": payload}


@app.get("/v1/{cap}")
def listing(cap: str):
    return {"items": ROWS.get(cap, [])}
'''

#: A writer test that demands a LIVE answer from the placeholder capability.
_WRITER_DEMANDS_LIVE = f'''
from fastapi.testclient import TestClient
from app.main import app


def test_writer_expects_the_record_back():
    resp = TestClient(app).post("/v1/{PLACEHOLDER}", json={{"title": "x"}})
    assert resp.status_code == 200
'''

#: A writer test that fails on a capability that is NOT a placeholder.
_WRITER_FAILS_ON_WORKING = f'''
from fastapi.testclient import TestClient
from app.main import app


def test_writer_expects_a_created_status():
    resp = TestClient(app).post("/v1/{WORKING}", json={{"title": "x"}})
    assert resp.status_code == 201
'''


def _product(root: Path, writer_tests: dict | None = None) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "main.py").write_text(_MAIN, encoding="utf-8")
    (root / "app" / "placeholders.py").write_text(
        render_product_module(_blueprint()), encoding="utf-8"
    )
    (root / "tests" / "conftest.py").write_text(_CONFTEST, encoding="utf-8")
    (root / CONTRACT_TEST).write_text(
        render_contract_tests(_blueprint(), {PLACEHOLDER: {"title": "x"}}),
        encoding="utf-8",
    )
    for name, body in (writer_tests or {}).items():
        (root / "tests" / name).write_text(body, encoding="utf-8")
    return root


def _suite(root: Path):
    def run(argv, *, cwd, timeout):
        return subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)

    return gate_suite_green(GateContext(workspace=root, role=BuildRole.TESTER, runner=run))


def test_the_contract_test_is_emitted_only_for_declared_capabilities():
    text = render_contract_tests(_blueprint(), {})
    assert f"def test_{PLACEHOLDER}_answers_the_declared_unavailable_refusal" in text
    assert WORKING not in text
    assert str(UNAVAILABLE_STATUS) in text
    plain = ProductBlueprint.model_validate(
        {**_blueprint().model_dump(mode="json"), "connectors": []}
    )
    assert render_contract_tests(plain, {}) == ""


def test_the_emitted_contract_test_passes_against_the_typed_refusal(tmp_path):
    verdict = _suite(_product(tmp_path / "p"))
    assert verdict.ok, (verdict.detail, verdict.findings)


def test_a_writer_test_demanding_a_live_answer_is_a_test_defect(tmp_path):
    root = _product(tmp_path / "p", {"test_writer_claims.py": _WRITER_DEMANDS_LIVE})
    verdict = _suite(root)
    assert not verdict.ok
    rows = verdict.payload["failing_tests"]
    assert [r["placeholder_refusals"] for r in rows] == [[PLACEHOLDER]]

    owned = failure_owner.classify(verdict, [CONTRACT_TEST], [CONTRACT_TEST])
    assert owned["owner"] == failure_owner.TEST_DEFECT
    assert owned["test_defects"] == [
        {"nodeid": "tests/test_writer_claims.py::test_writer_expects_the_record_back",
         "capabilities": [PLACEHOLDER]}
    ]
    # Never a product failure key for the same-failure-twice rule.
    names = failure_owner.failure_names(
        verdict, exclude=[d["nodeid"] for d in owned["test_defects"]]
    )
    assert not any("test_writer_claims" in n for n in names)


def test_a_writer_test_failing_on_a_working_capability_is_still_a_product_failure(tmp_path):
    root = _product(tmp_path / "p", {"test_writer_status.py": _WRITER_FAILS_ON_WORKING})
    verdict = _suite(root)
    assert not verdict.ok
    assert [r["placeholder_refusals"] for r in verdict.payload["failing_tests"]] == [[]]
    owned = failure_owner.classify(verdict, [CONTRACT_TEST], [CONTRACT_TEST])
    assert owned["owner"] == failure_owner.PRODUCT
    assert owned["test_defects"] == []


def test_a_factory_test_refused_is_never_a_test_defect():
    # The authority itself failing is not the writer's test to regenerate.
    verdict = SimpleNamespace(
        reason="suite_red", gate="suite_green", findings=[],
        payload={"failing_tests": [{
            "file": CONTRACT_TEST, "name": "t", "nodeid": CONTRACT_TEST + "::t",
            "kind": "failure", "innermost": CONTRACT_TEST,
            "placeholder_refusals": [PLACEHOLDER],
        }]},
    )
    owned = failure_owner.classify(verdict, [CONTRACT_TEST], [CONTRACT_TEST])
    assert owned["owner"] != failure_owner.TEST_DEFECT


# -- the runner: regenerated, never a product failure ------------------------


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _probe_values() -> str:
    """The payload source every Factory probe renders (``_payload_for``)."""
    from app.factory.build.payload_helpers import PROBE_PAYLOAD_SLOT, render_probe_payload
    from app.factory.build.product_gate import ROUND_TRIP_PROBE

    source = render_probe_payload(PROBE_PAYLOAD_SLOT)
    assert source in ROUND_TRIP_PROBE
    return source


_PROBE_VALUES = _probe_values()


def _smoke_with_a_placeholder() -> ProductBlueprint:
    bp = load_blueprint(SMOKE)
    data = bp.model_dump(mode="json")
    data["capabilities"][0]["connectors"] = [CONNECTOR]
    data["connectors"] = [CONNECTOR]
    return ProductBlueprint.model_validate(data)


def test_the_runner_sends_a_test_defect_back_to_the_writer_not_to_rework(
    tmp_path, stub_coder
):
    bp = _smoke_with_a_placeholder()
    cap = bp.capabilities[0].id
    out = tmp_path / "build"
    real_writer = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]
    seen_work = []

    def writer(ctx):
        result = real_writer(ctx)
        seen_work.append(list(ctx.work_list or ()))
        test = out / "tests" / "test_writer_claims.py"
        test.parent.mkdir(parents=True, exist_ok=True)
        told = any(str(i).startswith(TEST_DEFECT) for i in (ctx.work_list or ()))
        want = UNAVAILABLE_STATUS if told else 200
        # The writer's own test, outside the Factory's stamped files -- as the
        # coding agent writes it. Regenerated when told it is a test defect.
        # A VALID payload, built at test time from the product's own models
        # (the same values the Factory's round-trip probe sends), so the
        # request passes the 422 checks and reaches the refusal.
        test.write_text(
            "from fastapi.testclient import TestClient\n"
            "from app.main import app\n"
            "from app.models import MODELS\n"
            "import os\n\n"
            + _PROBE_VALUES
            + "\n\ndef test_writer_posts_the_capability():\n"
            "    with TestClient(app) as c:\n"
            f"        assert '{cap}' in MODELS\n"
            f"        r = c.post('/v1/{cap}', json=_payload_for('{cap}'),\n"
            "                   headers={'Authorization': 'Bearer ' + os.environ['PLATFORM_TOKEN']})\n"
            f"    assert r.status_code == {want}\n",
            encoding="utf-8",
        )
        return result

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    runner = RoleRunner(bp, out, roles=roles)
    outcome = runner.run()

    events = list(runner.ledger.events())
    assert outcome.ok, outcome.detail
    reworks = [e for e in events if e.kind is EventKind.REWORK]
    assert reworks == [], [
        (e.payload or {}).get("findings") for e in reworks
    ] + [e.payload for e in events if e.kind is EventKind.GATE_FAILED]
    assert outcome.rework_used == 0
    notes = [e for e in events if e.kind is EventKind.NOTE and (e.payload or {}).get("test_defects")]
    assert len(notes) == 1
    assert notes[0].payload["product_failure"] is False
    assert notes[0].payload["test_defects"][0]["capabilities"] == [cap]
    # The second writer pass was handed the typed test_defect item.
    assert any(str(i).startswith(TEST_DEFECT) for i in seen_work[-1])
    assert (out / CONTRACT_TEST).is_file()
