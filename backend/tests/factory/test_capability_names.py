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
