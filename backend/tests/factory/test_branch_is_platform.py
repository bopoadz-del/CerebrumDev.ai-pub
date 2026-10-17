"""RUNNER / FLOOR RULE -- failures, branches, continuity.

1. A failed build is still a product: it exports with FAILED(gate, check,
   finding), k/N, its build level and ``certified: false``; never released.
2. One platform = one branch: Continue resumes the failed run where it
   stopped (no COLLECTOR/CLONER/WRITER rerun, rework budget reset); a fresh
   workspace exists only on Start over, which tags the old head FIRST.
3. Every passed phase is pushed to ``build/<platform_id>``.
"""

from __future__ import annotations

import ast
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.factory.blueprint import blueprint_to_dict, load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import (
    PLATFORM_ID_KEY,
    BuildLedger,
    EventKind,
)
from app.factory.build.platform_identity import branch_of_record, mint_platform_id
from app.factory.build.rule_decision import BUDGET_RESET_KEY as REWORK_BUDGET_RESET
from app.factory.build.runner import blueprint_hash
from app.main import app
from app.core.session_store import create_session, get_session, update_session

ROOT = Path(__file__).resolve().parents[3]
# The pinned Store the runner tests plan against, resolved once at import.
from app.factory.blocks_source import resolve_blocks_root  # noqa: E402

STORE = resolve_blocks_root()
BLUEPRINT = ROOT / "blueprints" / "examples" / "runner_smoke.yaml"
FLOW = ROOT / "backend" / "app" / "factory" / "platform_chat_flow.py"
CHAT = ROOT / "backend" / "app" / "routers" / "chat.py"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def _failed_platform(tmp_path, session_id, *, passed=("COLLECTOR", "CLONER", "WRITER")):
    """A FAILED platform: the named phases passed, TESTER failed under the
    runner rule's FAILED(gate, check, finding)."""
    from app.factory.blueprint import ProductBlueprint
    from app.factory.build.runner import frozen_blueprint
    from app.factory.locale_choice import sync_blueprint_intake
    from app.models.session import ProductDesignState

    # The ledger records the hash of the blueprint the session will supply
    # (the user's intake synced onto it), exactly as an approval does.
    draft = ProductDesignState(blueprint=blueprint_to_dict(load_blueprint(BLUEPRINT)),
                               build_level="production")
    sync_blueprint_intake(draft)
    bp = frozen_blueprint(ProductBlueprint.model_validate(draft.blueprint))
    create_session(session_id, "tester")
    out = tmp_path / f"{session_id}-product"
    (out / "app").mkdir(parents=True)
    (out / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
    (out / "vendor" / "blocks").mkdir(parents=True)
    (out / "blocks.lock.json").write_text("{}", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash=blueprint_hash(bp))
    for name in passed:
        role = BuildRole(name)
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=name)
        ledger.append(EventKind.GATE_PASSED, role=role, detail=f"{name} ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.TESTER,
        detail="FAILED(TESTER, suite_green, tests/test_routes.py::test_every_capability failed)",
        payload={
            "outcome": "FAILED_GATE",
            "gate": "TESTER",
            "check": "suite_green",
            "finding": "tests/test_routes.py::test_every_capability failed",
            "findings": ["tests/test_routes.py::test_every_capability failed"],
        },
    )
    state = get_session(session_id)
    pd = state.product_design
    pd.blueprint = blueprint_to_dict(bp)
    pd.blueprint_approved = True
    pd.plan = {"capabilities": []}
    pd.build_level = "production"
    pd.platform_id = mint_platform_id()
    pd.generation = {
        "output_dir": str(out),
        "product_id": bp.product_id,
        "inputs_hash": blueprint_hash(bp),
        "engine": "runner",
    }
    update_session(session_id, state)
    return out, get_session(session_id)


# -- rule 1 -----------------------------------------------------------------


def test_a_failed_build_exports_with_the_failed_fields(client, tmp_path):
    out, state = _failed_platform(tmp_path, "sess_plt_failed_export")
    status = client.get("/v1/sessions/sess_plt_failed_export/product/build-status").json()["build"]
    assert status["state"] == "failed"
    assert status["certified"] is False
    assert status["failed"] == {
        "gate": "TESTER",
        "check": "suite_green",
        "finding": "tests/test_routes.py::test_every_capability failed",
    }
    assert status["failed_label"].startswith("FAILED(TESTER, suite_green, ")
    assert "suite_green" in status["next_continue"]  # what Continue will fix
    assert status.get("pilot_ready") is False  # never shown green

    pkg = client.get("/v1/sessions/sess_plt_failed_export/product/package?as_is=1")
    assert pkg.status_code == 200, pkg.text
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    manifest = json.loads(zf.read("MANIFEST.json"))
    assert manifest["certified"] is False
    assert manifest["failed"] == status["failed"]
    assert manifest["platform_id"] == state.product_design.platform_id
    assert set(manifest["acceptance"]) == {"passed", "total"}
    assert "build_level" in manifest
    assert "release_tag" not in manifest  # a failed build is never released


def test_a_failed_build_is_never_released(monkeypatch):
    from app.routers import session_product

    called = []
    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", "t")
    monkeypatch.setattr(
        "app.factory.build.platform_branch.release_certified",
        lambda *a, **k: called.append(a) or ("release/x/1", "s"),
    )

    class _PD:
        platform_id = mint_platform_id()

    class _State:
        product_design = _PD()

    assert session_product._tag_certified_release(_State(), {"certified": False}) is None
    assert called == []


# -- rule 2: Continue resumes, Start over is the only fresh door --------------


def _inline_runner(monkeypatch, ran):
    """start_runner_build's thread runs inline, with roles that record."""
    from app.factory import build_jobs
    from app.factory.build.roles_models import RoleResult
    from app.factory.build.runner import BuildBudget, RoleRunner

    def recording(role):
        def _role(ctx):
            ran.append(role.value)
            return RoleResult(ok=True, detail=f"{role.value} ok")

        return _role

    def fake_run(blueprint, output_dir, blocks_root, cycle="code", tenant_store=None,
                 brief="", inputs_hash=""):
        roles = {r: recording(r) for r in BuildRole}
        RoleRunner(
            blueprint, output_dir, roles=roles, blocks_root=blocks_root,
            budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=600),
            inputs_hash=inputs_hash,
        ).run()

    class _Inline:
        def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None):
            self._t, self._a, self._k, self.name = target, args, kwargs or {}, name

        def start(self):
            self._t(*self._a, **self._k)

        def is_alive(self):
            return False

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(build_jobs, "_run", fake_run)
    monkeypatch.setattr(build_jobs.threading, "Thread", _Inline)
    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready", lambda: None
    )


def test_continue_resumes_the_same_workspace_at_the_failing_phase(tmp_path, monkeypatch):
    from app.factory import build_jobs, platform_chat_flow


    out, state = _failed_platform(tmp_path, "sess_plt_continue")
    ran: list = []
    _inline_runner(monkeypatch, ran)
    seen = {}

    def via_runner(bp, output_dir, **kwargs):
        seen["output_dir"] = Path(output_dir)
        seen["platform_id"] = kwargs.get("platform_id")
        seen["start_over"] = kwargs.get("start_over", False)
        kwargs.pop("start_over", None)
        return build_jobs.start_runner_build(bp, output_dir, **kwargs)

    monkeypatch.setattr(platform_chat_flow, "generate_product", via_runner)
    monkeypatch.setattr(platform_chat_flow, "_blocks_root", lambda: STORE)
    fresh = []
    monkeypatch.setattr(platform_chat_flow, "start_fresh_generation", lambda *a, **k: fresh.append(1))

    reply = platform_chat_flow.start_or_resume_coder(state)

    assert fresh == [], "Continue must never reach a fresh workspace"
    assert reply.get("resumed") is True and reply.get("fresh") is False
    assert seen["output_dir"] == out  # the same workspace, not __run2
    assert seen["start_over"] is False
    assert seen["platform_id"] == state.product_design.platform_id
    assert not (out.parent / f"{out.name}__run2").exists()
    # The run re-entered at the failing phase, read off the ledger: after the
    # resume the first phase started is TESTER; COLLECTOR and CLONER never
    # start again; a WRITER after it is a REWORK round (the runner rule sending
    # the writer TESTER's findings), never a rerun of the WRITER phase.
    assert ran and ran[0] == "TESTER"
    events = list(BuildLedger(out / "build_ledger.jsonl").events())
    resumed_at = max(i for i, e in enumerate(events) if (e.payload or {}).get("resumed"))
    after = events[resumed_at + 1:]
    started = [e.role.value for e in after if e.kind is EventKind.PHASE_STARTED and e.role]
    assert started and started[0] == "TESTER"
    assert "COLLECTOR" not in started and "CLONER" not in started
    for i, e in enumerate(after):
        if e.kind is EventKind.PHASE_STARTED and e.role is BuildRole.WRITER:
            assert any(p.kind is EventKind.REWORK for p in after[:i]), "WRITER reran without a rework"
    assert any((e.payload or {}).get(REWORK_BUDGET_RESET) for e in events)
    assert any((e.payload or {}).get(PLATFORM_ID_KEY) == state.product_design.platform_id for e in events)


def test_start_over_tags_the_old_head_before_creating_a_workspace(tmp_path, monkeypatch):
    from app.factory import platform_chat_flow
    from app.factory.build import platform_branch

    out, state = _failed_platform(tmp_path, "sess_plt_start_over")
    pid = state.product_design.platform_id
    order: list = []
    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", "t")
    monkeypatch.setattr(platform_branch, "builds_remote", lambda env=None: "remote")

    def tag(remote, platform_id, day=None):
        order.append(("tag", platform_id))
        return platform_branch.Archived(platform_id, branch_of_record(platform_id),
                                        f"archive/{platform_id}/2026-10-05", "a" * 40, False)

    monkeypatch.setattr(platform_branch, "tag_head_for_archive", tag)

    def generate(bp, output_dir, **kwargs):
        order.append(("create", Path(output_dir).name, kwargs.get("start_over")))
        return {"engine": "runner", "output_dir": str(output_dir), "product_id": bp.product_id,
                "inputs_hash": "h", "build": {"state": "building"}}

    monkeypatch.setattr(platform_chat_flow, "generate_product", generate)
    reply = platform_chat_flow.start_over(state)

    assert order[0] == ("tag", pid)
    assert order[1][0] == "create" and order[1][2] is True
    assert order[1][1] != out.name  # a fresh workspace, never the old one
    assert reply["archived"]["tag"] == f"archive/{pid}/2026-10-05"
    notes = [e for e in BuildLedger(out / "build_ledger.jsonl").events()
             if (e.payload or {}).get("start_over")]
    assert notes and notes[-1].payload["archived_tag"] == f"archive/{pid}/2026-10-05"


def test_start_over_creates_nothing_when_the_head_cannot_be_tagged(tmp_path, monkeypatch):
    from app.factory import platform_chat_flow
    from app.factory.build import platform_branch

    _out, state = _failed_platform(tmp_path, "sess_plt_start_over_fail")
    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", "t")
    monkeypatch.setattr(platform_branch, "builds_remote", lambda env=None: "remote")

    def broken(*a, **k):
        raise platform_branch.PlatformBranchError("remote unreachable")

    monkeypatch.setattr(platform_branch, "tag_head_for_archive", broken)
    created = []
    monkeypatch.setattr(platform_chat_flow, "generate_product", lambda *a, **k: created.append(1))
    reply = platform_chat_flow.start_over(state)
    assert reply["ok"] is False and created == []


def _callers(path: Path, name: str) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    target = node.func
                    called = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                    if called == name:
                        found.add(fn.name)
    return found


def test_no_floor_path_reaches_a_fresh_workspace_except_start_over():
    # Inside the flow, only start_over() calls start_fresh_generation ...
    assert _callers(FLOW, "start_fresh_generation") == {"start_over"}
    # ... and in the chat router only the typed START_OVER branch reaches
    # start_over(); no router calls start_fresh_generation at all.
    assert _callers(CHAT, "start_fresh_generation") == set()
    src = CHAT.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "FloorAction.START_OVER" in ast.unparse(node.test):
            body = ast.unparse(ast.Module(body=node.body, type_ignores=[]))
            assert "platform_chat_flow.start_over(" in body
            break
    else:
        pytest.fail("chat.py has no typed START_OVER branch")
    assert src.count("platform_chat_flow.start_over(") == 1


# -- rule 2: every passed phase is pushed to the branch of record --------------


def test_every_passed_phase_is_pushed_to_the_platform_branch(tmp_path, monkeypatch):
    from app.factory.build import platform_branch
    from app.factory.build.roles_models import RoleResult
    from app.factory.build.runner import BuildBudget, RoleRunner

    bp = load_blueprint(BLUEPRINT)
    pid = mint_platform_id()
    out = tmp_path / "ws"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash=blueprint_hash(bp))
    ledger.append(EventKind.NOTE, detail="platform", payload={PLATFORM_ID_KEY: pid})
    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", "t")
    pushes: list = []
    monkeypatch.setattr(
        platform_branch,
        "push_to_branch_of_record",
        lambda ws, platform_id, message, env=None: pushes.append((platform_id, message)) or "b" * 40,
    )
    roles = {r: (lambda ctx, r=r: RoleResult(ok=True, detail=f"{r.value} ok")) for r in BuildRole}
    RoleRunner(bp, out, roles=roles,
               budget=BuildBudget(max_rework=1, wall_clock_s=600, phase_wall_clock_s=600)).run()
    assert pushes, "no phase reached the branch of record"
    assert {p for p, _m in pushes} == {pid}
    notes = [e for e in BuildLedger(out / "build_ledger.jsonl").events()
             if str(e.detail or "").startswith("CHECKPOINT")]
    assert notes and all(branch_of_record(pid) in str(n.detail) for n in notes)
