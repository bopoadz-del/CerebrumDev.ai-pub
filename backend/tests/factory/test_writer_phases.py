"""One FACTORY_CODE_CLI WRITER, three fail-closed C-BRIEF phases.

Owner 2026-09-12: backend → frontend+RAG → integration. Resume uses the
#403 checkpoint spine. Phase N must accept before phase N+1.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.persist_accept import persist_handler_rel
from app.factory.build.runner import (
    RoleRunner,
    blueprint_hash,
)
from app.factory.build.rag_surface import (
    FACTORY_GROUNDED_RAG_SOURCE,
    RAG_ROUTES_REL,
    emit_factory_grounded_rag_surface,
)
from app.factory.build.writer_phases import (
    CHECKPOINT_STAGE,
    RAG_INGEST_PATHS,
    RAG_QUERY_PATHS,
    WRITER_PHASE_BACKEND,
    WRITER_PHASE_FRONTEND_RAG,
    WRITER_PHASE_INTEGRATION,
    WRITER_PHASES,
    PhaseAcceptHalt,
    accept_writer_phase,
    checkpoint_landed_phase,
    compile_phase_brief,
    inventory_needs_rag,
    landed_phase_ids,
    later_writer_phases,
    pending_writer_phases,
    phase_acceptance_errors,
    prior_writer_phase,
    rag_ingest_route_present,
    rag_query_route_present,
    should_dispatch_writer_phase,
    should_reopen_writer_phase,
)
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


class _Cap:
    def __init__(self, cid, block_ids=(), strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def _compiled_smoke():
    bp = load_blueprint(SMOKE)
    return compile_brief(bp, plan_blueprint(bp))


def _ctx(tmp_path, compiled, landed=()):
    root = tmp_path / "ws"
    root.mkdir()
    notes = []
    ctx = SimpleNamespace(
        workspace=SimpleNamespace(workspace=root),
        state={
            "inputs_hash": "digest",
            "landed_writer_phases": list(landed),
        },
        note=lambda detail, **payload: notes.append((detail, payload)),
    )
    ctx._notes = notes
    ctx._compiled = compiled
    return ctx


def _plant_backend(root: Path, compiled) -> None:
    (root / "app" / "actions").mkdir(parents=True, exist_ok=True)
    (root / "app" / "models.py").write_text("MODELS = {}\n", encoding="utf-8")
    routes = []
    for cid in compiled.capabilities:
        name = cid.replace("-", "_")
        (root / persist_handler_rel(cid)).write_text(
            f"CAPABILITY = {cid!r}\n", encoding="utf-8"
        )
        routes.append(f'@app.post("/v1/{name}")\nasync def create_{name}():\n    return {{}}\n')
        routes.append(f'@app.get("/v1/{name}")\nasync def list_{name}():\n    return {{}}\n')
    (root / "app" / "routes.py").write_text("\n".join(routes), encoding="utf-8")


def _plant_render_ready(root: Path) -> None:
    (root / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (root / "deploy").mkdir(parents=True, exist_ok=True)
    (root / "deploy" / "contract.json").write_text(
        '{"schema": "cerebrum.deploy.v1"}\n', encoding="utf-8"
    )
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "main.py").write_text("app = None\n", encoding="utf-8")


def _compiled_steward_dual_rag():
    """sess_5782f226 Steward inventory: dual_rag_* caps bind vector_search."""
    return compile_brief(
        _Blueprint(),
        _Plan(
            _Cap(
                "dual_rag_estate_docs",
                ["knowledge", "vector_search", "document_engine"],
                "REUSE",
            ),
            _Cap(
                "dual_rag_sop",
                ["knowledge", "vector_search", "document_engine"],
                "REUSE",
            ),
        ),
        store_ids={"knowledge", "vector_search", "document_engine"},
    )


def _plant_ui_module(root: Path, module: str = "dual_rag") -> None:
    manifest = root / "docs" / "blueprint"
    manifest.mkdir(parents=True, exist_ok=True)
    (manifest / "product_blueprint.json").write_text(
        '{"ui_modules": ["' + module + '"]}\n', encoding="utf-8"
    )
    ui = root / "frontend" / "src" / "modules"
    ui.mkdir(parents=True, exist_ok=True)
    (ui / f"{module}.tsx").write_text(
        "export default function DualRag() {\n"
        "  return <section>estate dual rag query surface</section>;\n"
        "}\n" + ("// padding so this is not a placeholder module\n" * 12),
        encoding="utf-8",
    )


def _plant_quoted_routes(path: Path, *routes: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["from fastapi import APIRouter\n", "router = APIRouter()\n"]
    for route in routes:
        method = "post" if route.endswith("/ingest") else "get"
        name = route.replace("/", "_").strip("_")
        lines.append(f'@router.{method}("{route}")\n')
        lines.append(f"def {name}():\n    return {{}}\n")
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(existing + "\n" + "".join(lines), encoding="utf-8")


def test_compiled_brief_carries_three_phase_cuts_and_lints():
    compiled = _compiled_smoke()
    # Each phase's acceptance is a harness check, named by its tag.
    for phase_id in WRITER_PHASES:
        assert f"[check:writer_phase_{phase_id}]" in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_compile_phase_brief_does_not_drop_horizon_or_reuse_accept():
    compiled = _compiled_smoke()
    phase = compile_phase_brief(compiled, WRITER_PHASE_BACKEND)
    assert "PHASE 1 of 3 ACTIVE" in phase.text
    assert "Do only this phase" in phase.text
    assert compiled.text in phase.text
    assert "BLOCK_DEFAULT_ACTIONS" in phase.text
    assert lint_brief(phase).ok, lint_brief(phase).errors


def test_phase_order_is_backend_then_ui_rag_then_integration():
    assert WRITER_PHASES == (
        WRITER_PHASE_BACKEND,
        WRITER_PHASE_FRONTEND_RAG,
        WRITER_PHASE_INTEGRATION,
    )
    assert prior_writer_phase(WRITER_PHASE_BACKEND) is None
    assert prior_writer_phase(WRITER_PHASE_FRONTEND_RAG) == WRITER_PHASE_BACKEND
    assert prior_writer_phase(WRITER_PHASE_INTEGRATION) == WRITER_PHASE_FRONTEND_RAG
    assert later_writer_phases() == (
        WRITER_PHASE_FRONTEND_RAG,
        WRITER_PHASE_INTEGRATION,
    )


def test_fail_closed_phase_two_before_phase_one_accepted(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_gate"):
        accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)


def test_fail_closed_phase_three_before_phase_two_accepted(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled, landed=[WRITER_PHASE_BACKEND])
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_gate"):
        accept_writer_phase(ctx, WRITER_PHASE_INTEGRATION, compiled)


def test_phase_one_accept_requires_handler_route_schema(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_backend"):
        accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    _plant_backend(ctx.workspace.workspace, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    assert WRITER_PHASE_BACKEND in ctx.state["landed_writer_phases"]


def test_rework_reopens_backend_even_when_phase_landed(tmp_path):
    """TESTER findings must reach the coder; resume skip does not apply."""
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    ctx.work_list = ()
    _plant_backend(ctx.workspace.workspace, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_BACKEND) is False
    ctx.work_list = ["tester produced no test files"]
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_BACKEND) is True
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG) is False


def test_resume_skips_landed_earlier_phases(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(tmp_path, compiled)
    _plant_backend(ctx.workspace.workspace, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    assert pending_writer_phases(ctx) == [
        WRITER_PHASE_FRONTEND_RAG,
        WRITER_PHASE_INTEGRATION,
    ]
    assert WRITER_PHASE_BACKEND not in pending_writer_phases(ctx)


def test_landed_phase_ids_read_the_403_checkpoint_spine(tmp_path):
    bp = load_blueprint(SMOKE)
    digest = blueprint_hash(bp)
    other = blueprint_hash(
        load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    )
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="runner-smoke", inputs_hash=digest)
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed writer phase backend",
        payload={
            "stage": CHECKPOINT_STAGE,
            "phase": WRITER_PHASE_BACKEND,
            "inputs_hash": digest,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed capability analytics_surface",
        payload={
            "stage": CHECKPOINT_STAGE,
            "capability": "analytics_surface",
            "inputs_hash": digest,
        },
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed writer phase from another blueprint",
        payload={
            "stage": CHECKPOINT_STAGE,
            "phase": WRITER_PHASE_BACKEND,
            "inputs_hash": other,
        },
    )
    assert landed_phase_ids(ledger, digest) == [WRITER_PHASE_BACKEND]
    assert "frontend_rag" not in landed_phase_ids(ledger, digest)


def test_later_phase_dispatch_only_on_cli_path():
    assert should_dispatch_writer_phase(WRITER_PHASE_BACKEND, None) is True
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG, SimpleNamespace(via="http_oneshot")
    ) is False
    assert should_dispatch_writer_phase(
        WRITER_PHASE_INTEGRATION, SimpleNamespace(via="skipped")
    ) is False
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG, SimpleNamespace(via="cli")
    ) is True


def test_later_phase_skips_cli_when_remaining_work_empty():
    """sess_5782f226 run7: do not re-dispatch the same 6 landed gaps."""
    cli = SimpleNamespace(via="cli")
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG, cli, remaining_work=[]
    ) is False
    assert should_dispatch_writer_phase(
        WRITER_PHASE_INTEGRATION, cli, remaining_work=[]
    ) is False
    assert should_dispatch_writer_phase(
        WRITER_PHASE_FRONTEND_RAG, cli, remaining_work=["estate_maintenance"]
    ) is True
    assert should_dispatch_writer_phase(
        WRITER_PHASE_BACKEND, cli, remaining_work=[]
    ) is True


def test_phase_two_rag_required_when_a_bound_block_retrieves():
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("estate_docs", ["vector_search", "database"], "REUSE")),
        store_ids={"vector_search", "database"},
    )
    assert inventory_needs_rag(compiled)
    assert "RAG ingest/query" in compiled.text
    for path in RAG_INGEST_PATHS + RAG_QUERY_PATHS:
        assert path in compiled.text
    phase = compile_phase_brief(compiled, WRITER_PHASE_FRONTEND_RAG)
    assert lint_brief(phase).ok, lint_brief(phase).errors


def test_binding_a_retrieving_block_owes_the_rag_contract_whatever_the_name():
    """Binding vector_search -- a block whose signed manifest declares the
    vector-store read -- owes the RAG ingest/query contract, the same signal
    that makes rag_roundtrip_hit a veto. The old rule exempted it unless the
    capability was NAMED rag; the name decides nothing now."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("patient_records_management", ["vector_search", "database"], "REUSE")),
        store_ids={"vector_search", "database"},
    )
    assert inventory_needs_rag(compiled) is True


def test_a_capability_whose_blocks_do_not_retrieve_owes_no_rag_contract():
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("rag_answers", ["database"], "REUSE")),
        store_ids={"database"},
    )
    assert inventory_needs_rag(compiled) is False


def test_steward_golden_brief_names_exact_rag_http_paths():
    """Steward.v1 PHASE 2 DO/ACCEPTANCE names the paths the checker looks for."""
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    compiled = compile_brief(bp, plan_blueprint(bp))
    assert inventory_needs_rag(compiled)
    assert any(
        "rag" in str(getattr(item, "capability_id", "")).lower()
        for item in compiled.inventory
    )
    for path in RAG_INGEST_PATHS + RAG_QUERY_PATHS:
        assert path in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_steward_dual_rag_caps_owe_rag_http_even_with_vector_search_binds():
    """Steward dual_rag_* caps bind vector_search; PHASE 2 still owes routes."""
    compiled = _compiled_steward_dual_rag()
    assert inventory_needs_rag(compiled)
    text = compiled.text
    assert "/v1/rag/ingest" in text and "/v1/rag/query" in text
    # The brief states the platform contract; it names no product's routes.
    assert "/v1/steward/rag" not in text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_live_miss_sess_5782f226_run5_phase_two_fails_without_rag_routes(tmp_path):
    """Photograph Steward Continue run5: backend + UI landed, RAG routes absent.

    Outcome was FAILED_WRITER at writer_phase_frontend_rag — ingest route
    missing; query route missing. Not reuse_accept / not BLOCKS_LOCK.
    Phase-1 persist POST/GET on dual_rag_estate_docs must not satisfy the
    ingest/query contract.
    """
    compiled = _compiled_steward_dual_rag()
    ctx = _ctx(tmp_path, compiled)
    _plant_backend(ctx.workspace.workspace, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    _plant_ui_module(ctx.workspace.workspace)
    errors = phase_acceptance_errors(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)
    assert any("ingest route missing" in e for e in errors), errors
    assert any("query route missing" in e for e in errors), errors
    assert not any("UI module" in e for e in errors), errors
    with pytest.raises(PhaseAcceptHalt, match="writer_phase_frontend_rag"):
        accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)
    # Resume must not redo backend from zero after this halt.
    assert WRITER_PHASE_BACKEND in ctx.state["landed_writer_phases"]
    assert pending_writer_phases(ctx) == [
        WRITER_PHASE_FRONTEND_RAG,
        WRITER_PHASE_INTEGRATION,
    ]
    ctx.work_list = ()
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_BACKEND) is False
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG) is True


def test_phase_two_docs_json_stamp_is_not_a_rag_route(tmp_path):
    """docs/rag/dual_rag.json listing paths must not always-pass the check."""
    compiled = _compiled_steward_dual_rag()
    ctx = _ctx(tmp_path, compiled, landed=[WRITER_PHASE_BACKEND])
    _plant_ui_module(ctx.workspace.workspace)
    rag_docs = ctx.workspace.workspace / "docs" / "rag"
    rag_docs.mkdir(parents=True)
    (rag_docs / "dual_rag.json").write_text(
        '{"routes": ["/v1/rag/ingest", "/v1/rag/query", '
        '"/v1/steward/rag/ingest", "/v1/steward/rag/query"]}\n',
        encoding="utf-8",
    )
    errors = phase_acceptance_errors(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)
    assert any("ingest route missing" in e for e in errors), errors
    assert any("query route missing" in e for e in errors), errors


def test_phase_two_accepts_steward_canonical_routes_outside_routes_py(tmp_path):
    """CLI-landed /v1/steward/rag/* in app/steward/api.py must pass."""
    compiled = _compiled_steward_dual_rag()
    ctx = _ctx(tmp_path, compiled, landed=[WRITER_PHASE_BACKEND])
    _plant_ui_module(ctx.workspace.workspace)
    _plant_quoted_routes(
        ctx.workspace.workspace / "app" / "steward" / "api.py",
        "/v1/steward/rag/ingest",
        "/v1/steward/rag/query",
    )
    assert rag_ingest_route_present(ctx.workspace.workspace)
    assert rag_query_route_present(ctx.workspace.workspace)
    accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_FRONTEND_RAG)
    assert WRITER_PHASE_BACKEND in ctx.state["landed_writer_phases"]
    assert WRITER_PHASE_FRONTEND_RAG in ctx.state["landed_writer_phases"]
    assert pending_writer_phases(ctx) == [WRITER_PHASE_INTEGRATION]


def test_phase_two_brief_hard_writes_rag_routes_file():
    compiled = _compiled_steward_dual_rag()
    phase = compile_phase_brief(compiled, WRITER_PHASE_FRONTEND_RAG)
    assert "HARD WRITE" in phase.text
    assert "app/rag_routes.py" in phase.text
    assert "gaps_only" in phase.text
    assert lint_brief(phase).ok, lint_brief(phase).errors


def test_live_miss_sess_5782f226_run6_keep_path_plants_callable_routes(tmp_path):
    """Photograph run6: CLI completed, routes still missing, then Factory plants.

    Repair 2 of 2: do not rely on the coder seeing DO needles. Plant real
    FastAPI ingest/query, then the checker must pass. Resume still skips
    completed backend.
    """
    compiled = _compiled_steward_dual_rag()
    ctx = _ctx(tmp_path, compiled)
    root = ctx.workspace.workspace
    _plant_backend(root, compiled)
    accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)
    _plant_ui_module(root)
    errors = phase_acceptance_errors(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)
    assert any("ingest route missing" in e for e in errors), errors
    planted = emit_factory_grounded_rag_surface(root, compiled)
    assert planted == ["rag_ingest", "rag_query"]
    assert (root / RAG_ROUTES_REL).is_file()
    body = (root / RAG_ROUTES_REL).read_text(encoding="utf-8")
    assert FACTORY_GROUNDED_RAG_SOURCE in body
    assert '"/v1/rag/ingest"' in body
    assert '"/v1/rag/query"' in body
    assert "def rag_ingest(" in body
    assert "RagIngestRequest" in body
    # F-1d: the query/answer surface is RE-POINTED to the real answer path
    # (the shared answer_question -> answer_query helper), NOT the retired
    # keyword-overlap matcher.
    assert "answer_question" in body
    assert "search_documents" not in body
    assert rag_ingest_route_present(root)
    assert rag_query_route_present(root)
    accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)
    checkpoint_landed_phase(ctx, WRITER_PHASE_FRONTEND_RAG)
    assert WRITER_PHASE_BACKEND in ctx.state["landed_writer_phases"]
    assert WRITER_PHASE_FRONTEND_RAG in ctx.state["landed_writer_phases"]
    ctx.work_list = ()
    assert should_reopen_writer_phase(ctx, WRITER_PHASE_BACKEND) is False
    assert pending_writer_phases(ctx) == [WRITER_PHASE_INTEGRATION]


def test_rag_keep_path_does_not_plant_when_inventory_has_no_rag(tmp_path):
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("patient_records_management", ["database"], "REUSE")),
        store_ids={"database"},
    )
    assert inventory_needs_rag(compiled) is False
    planted = emit_factory_grounded_rag_surface(tmp_path, compiled)
    assert planted == []
    assert not (tmp_path / RAG_ROUTES_REL).exists()


def test_rag_keep_path_does_not_overwrite_cli_steward_routes(tmp_path):
    compiled = _compiled_steward_dual_rag()
    ctx = _ctx(tmp_path, compiled, landed=[WRITER_PHASE_BACKEND])
    _plant_ui_module(ctx.workspace.workspace)
    steward = ctx.workspace.workspace / "app" / "steward" / "api.py"
    _plant_quoted_routes(steward, "/v1/steward/rag/ingest", "/v1/steward/rag/query")
    original = steward.read_text(encoding="utf-8")
    planted = emit_factory_grounded_rag_surface(ctx.workspace.workspace, compiled)
    assert planted == []
    assert steward.read_text(encoding="utf-8") == original
    assert not (ctx.workspace.workspace / RAG_ROUTES_REL).exists()
    accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)


def test_phase_two_accepts_kit_v1_rag_routes_in_routes_py(tmp_path):
    compiled = _compiled_steward_dual_rag()
    ctx = _ctx(tmp_path, compiled, landed=[WRITER_PHASE_BACKEND])
    _plant_ui_module(ctx.workspace.workspace)
    _plant_quoted_routes(
        ctx.workspace.workspace / "app" / "routes.py",
        "/v1/rag/ingest",
        "/v1/rag/query",
    )
    accept_writer_phase(ctx, WRITER_PHASE_FRONTEND_RAG, compiled)


def test_phase_three_accept_requires_render_ready_not_live_deploy(tmp_path):
    compiled = _compiled_smoke()
    ctx = _ctx(
        tmp_path,
        compiled,
        landed=[WRITER_PHASE_BACKEND, WRITER_PHASE_FRONTEND_RAG],
    )
    errors = phase_acceptance_errors(ctx, WRITER_PHASE_INTEGRATION, compiled)
    assert errors
    assert any("render-ready" in e for e in errors)
    _plant_render_ready(ctx.workspace.workspace)
    accept_writer_phase(ctx, WRITER_PHASE_INTEGRATION, compiled)


def test_runner_hydrates_landed_writer_phases(tmp_path):
    bp = load_blueprint(SMOKE)
    digest = blueprint_hash(bp)
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="runner-smoke", inputs_hash=digest)
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="landed writer phase backend",
        payload={
            "stage": CHECKPOINT_STAGE,
            "phase": WRITER_PHASE_BACKEND,
            "inputs_hash": digest,
        },
    )
    runner = RoleRunner(bp, out)
    runner._restore_workspace_state()
    assert WRITER_PHASE_BACKEND in runner.state.get("landed_writer_phases", [])
    ctx = SimpleNamespace(state=runner.state)
    assert WRITER_PHASE_BACKEND not in pending_writer_phases(ctx)


def test_rag_route_checker_accepts_any_prefix_and_no_bare_mention(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "routes.py").write_text(
        '@router.post("/v1/zorblat/rag/ingest")\n@router.get("/v1/rag/query")\n',
        encoding="utf-8",
    )
    assert rag_ingest_route_present(tmp_path) and rag_query_route_present(tmp_path)
    (app / "routes.py").write_text("# see /v1/rag/ingest and /v1/rag/query\n", encoding="utf-8")
    assert not rag_ingest_route_present(tmp_path)
    assert not rag_query_route_present(tmp_path)
