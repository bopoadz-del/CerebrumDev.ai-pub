"""FG-7(a): one name per capability id, written and read by the same function.

The route renderer writes a capability's handler module and route segment;
every grader layer reads them back. Before this, the two-dataset runner
imported ``app.actions.<id>`` unmapped, so a hyphenated id read "both
absent" (recorded clean) while the same constant handler under an
underscore id read "hardwired constant"; the UI walk asked ``/v1/<id>`` for
a route the renderer serves as ``/v1/<id with '_'>``.

Every case runs on two invented ids, one hyphenated and one underscored, and
nothing here names a real product, client or capability.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.factory.build.grader as grader
from app.factory.build.authority import (
    CAPABILITY_ROUTE_PREFIX,
    capability_module,
    capability_name,
    capability_route,
)
from app.factory.build.product_grader import function_grader, layer2, two_dataset, ui_grader

IDS = ["quell-tally", "brisk_ratio"]


def _workspace(root: Path, cid: str, body: str) -> Path:
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", "utf-8")
    (actions / "__init__.py").write_text("", "utf-8")
    (actions / f"{capability_name(cid)}.py").write_text(
        "def handle(payload):\n    x = float(payload.get('x', 0))\n" + body, "utf-8"
    )
    return root


@pytest.mark.parametrize("cid", IDS)
def test_module_and_route_carry_one_name(cid):
    name = capability_name(cid)
    assert name.isidentifier()
    assert capability_module(cid) == "app.actions." + name
    assert capability_route(cid) == f"{CAPABILITY_ROUTE_PREFIX}/{name}"
    # The hyphen and underscore spellings of one id are one capability.
    assert capability_name(cid.replace("_", "-")) == capability_name(cid.replace("-", "_"))


@pytest.mark.parametrize(
    "ids",
    [IDS, ["ledger-close", "ledger_open", "tally"]],
)
def test_distinct_ids_keep_distinct_names(ids):
    assert len({capability_name(c) for c in ids}) == len(ids)


@pytest.mark.parametrize("cid", IDS)
def test_a_constant_handler_reads_hardwired_under_either_spelling(tmp_path, cid):
    root = _workspace(tmp_path, cid, "    return {'result': 7}\n")
    pair = two_dataset.DatasetPair(cid, {"x": 1.0}, None, {"x": 2.0}, None)
    [check] = two_dataset.run_two_dataset([pair], root)
    assert check.verdict == two_dataset.HARDWIRED_CONSTANT, check


@pytest.mark.parametrize("cid", IDS)
def test_a_computing_handler_reads_ok_under_either_spelling(tmp_path, cid):
    root = _workspace(tmp_path, cid, "    return {'result': x * 3}\n")
    pair = two_dataset.DatasetPair(cid, {"x": 1.0}, 3.0, {"x": 2.0}, 6.0)
    [check] = two_dataset.run_two_dataset([pair], root)
    assert check.verdict == two_dataset.OK, check


@pytest.mark.parametrize("cid", IDS)
def test_the_function_grade_imports_the_rendered_module(tmp_path, cid):
    root = _workspace(tmp_path, cid, "    return {'result': x}\n")
    [graded] = function_grader.derive_functions([cid], root)
    assert graded.module == capability_module(cid)
    assert (root / Path(*graded.module.split("."))).with_suffix(".py").is_file()


@pytest.mark.parametrize("cid", IDS)
def test_the_ui_walk_asks_the_route_the_renderer_serves(cid):
    plan = ui_grader.ui_plan({"product_id": "p", "capabilities": [{"id": cid}]})
    route_steps = [s for s in plan if s.kind in ("route", "honest_error")]
    assert route_steps and {s.target for s in route_steps} == {capability_route(cid)}


def test_the_route_renderer_serves_the_route_the_grader_walks():
    from app.factory.build.roles_handlers import _render_routes

    entries = [
        {"capability_id": c, "name": capability_name(c), "entity": "e", "body": "    return {}\n", "source": "s"}
        for c in IDS
    ]
    routes = _render_routes(entries)
    for c in IDS:
        segment = capability_route(c)[len(CAPABILITY_ROUTE_PREFIX):]
        assert f'@router.post("{segment}")' in routes
        assert f"from {capability_module(c)} import handle" in routes


@pytest.mark.parametrize("cid", IDS)
def test_a_failing_route_step_is_billed_to_its_capability(cid):
    target = capability_route(cid)
    grade = {
        "layer1": {
            "functions": {"functions": [], "ungraded": []},
            "ui": {"ok": False, "ran": True, "steps": [{"name": "route:" + target, "kind": "route", "target": target, "passed": False}]},
        },
        "layer2": {"ran": True, "open_fails": []},
    }
    rows = grader.reason_classes(grade, [cid])
    assert {"capability": cid, "class": "missing_route", "layer": "ui"} in rows


@pytest.mark.parametrize("cid", IDS)
def test_layer2_renders_the_capability_module(cid):
    asked = []

    def render(stems):
        asked.append(list(stems))
        return ""

    layer2.run_layer2({}, {"capabilities": [{"id": cid, "description": "d"}]}, lambda messages: "[]", render=render)
    assert asked == [[capability_name(cid)]]


# -- the WRITER's own guards, on the same two ids -------------------------


class _Cap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = []
        self.strategy = "GENERATE"
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Invented Platform"
    product_id = "invented-platform"
    vertical = "invented"
    summary = "invented test data"


class _PastDispatch(Exception):
    """Raised where the writer leaves its dispatch step, to stop the run there."""


def _writer_ctx(tmp_path, cid, state, notes):
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    return RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace(BuildRole.WRITER, tmp_path / "build"),
        blueprint=_Blueprint(),
        plan=_Plan(_Cap(cid)),
        state=state,
        progress=lambda detail, payload: notes.append(detail),
    )


@pytest.mark.parametrize("cid", IDS)
def test_a_paid_shot_the_writer_never_records_fails_the_writer(tmp_path, monkeypatch, cid):
    """A model-spec shot made for an id the walk never replays is a RoleError.

    The fan-out is replaced by one that reports a shot for an extra id (the
    capability's own name with a suffix); the walk only replays the plan's
    ids, so the writer must refuse rather than ship a run whose ledger lost
    a paid call.
    """
    import app.factory.build.roles_handlers as roles_handlers
    from app.factory.build.roles import RoleError, run_writer
    from app.factory.build.writer_fanout import FanOut

    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    monkeypatch.setattr("app.factory.build.coder_session.deepseek_cli_ready", lambda command=None: False)
    stray = capability_name(cid) + "-stray"
    calls = []

    def _fan_out(ctx, ids, work, workers=1):
        calls.append(list(ids))
        shots = FanOut()
        for shot in [*ids, stray] if len(calls) == 1 else ids:
            shots.notes[shot] = []
        return shots

    monkeypatch.setattr(roles_handlers, "fan_out", _fan_out)
    with pytest.raises(RoleError) as caught:
        run_writer(_writer_ctx(tmp_path, cid, {"resolved_blocks": (), "vendored_blocks": ()}, []))
    assert calls[0] == [cid]
    assert str(caught.value) == "model-spec shots were made but never recorded: " + stray


@pytest.mark.parametrize("cid", IDS)
def test_a_resumed_writer_rehydrates_the_landed_backend_dispatch(tmp_path, monkeypatch, cid):
    """A resume past a landed backend phase reads its dispatch back from state.

    The receipt names the capability; the writer must report the dispatch it
    rehydrated (via and detail from the receipt), not re-dispatch and not read
    the raw state as if it were the dispatch.
    """
    import app.factory.build.roles_handlers as roles_handlers
    from app.factory.build.roles import run_writer
    from app.factory.build.writer_phases import WRITER_PHASE_BACKEND

    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.setattr("app.factory.build.coder_session.deepseek_cli_ready", lambda command=None: False)

    def _stop(ctx):
        raise _PastDispatch()

    monkeypatch.setattr(roles_handlers, "_vendor_product_kernel", _stop)
    detail = "harvested " + capability_name(cid)
    state = {
        "resolved_blocks": (),
        "vendored_blocks": (),
        "landed_writer_phases": [WRITER_PHASE_BACKEND],
        "brief_dispatch": {"via": "cli", "ok": True, "detail": detail, "cli_authored_ids": [cid]},
    }
    notes = []
    with pytest.raises(_PastDispatch):
        run_writer(_writer_ctx(tmp_path, cid, state, notes))
    assert "resume skips landed writer phase backend" in notes
    assert [n for n in notes if n.startswith("brief dispatch via")] == ["brief dispatch via cli: " + detail]
