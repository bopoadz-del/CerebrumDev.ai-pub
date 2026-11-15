"""FG-7c: before a handler is judged, the grader establishes in the probe
environment whether its declared block can run at all.

A capability whose declared block cannot run there (it does not load, a
package it imports is not installed, it reaches for the network the probe
refuses) is not judgeable: it is recorded as ungraded -- a gap the Factory
owes -- never a function FAIL billed to the writer. A handler that fails
closed while its block runs fine is still judged, and is still a
handler_error.

Every name here (capabilities, blocks, packages) is invented and decides
nothing; the grader reads the handle's own ``BLOCK_IDS`` and the product's
own loader.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.factory.build.grader as _grader_module
import app.factory.build.product_grader.function_grader as fg
from tests.factory.grader_contract import write_models

IDS = ["intake_form", "invoice_total", "visit_log", "tenant_notice"]
BLOCK = "ledger"

#: The product's own loader and dispatcher (the shape every Factory-rendered
#: product carries: ``app.dispatch.load_block`` / ``execute``).
DISPATCH = (
    "import importlib\n"
    "_CACHE = {}\n"
    "def load_block(block_id):\n"
    "    if block_id not in _CACHE:\n"
    "        _CACHE[block_id] = importlib.import_module('app.blocks.' + block_id)\n"
    "    return _CACHE[block_id]\n"
    "def execute(block_id, payload):\n"
    "    module = load_block(block_id)\n"
    "    try:\n"
    "        return module.run(payload)\n"
    "    except Exception as exc:\n"
    "        return {'status': 'error', 'error': type(exc).__name__}\n"
)
#: A correct block-backed handle: it declares its block, calls it through the
#: dispatcher, computes from its answer, and fails closed when it fails.
HANDLE = (
    "from app.dispatch import execute\n"
    f"BLOCK_IDS = [{BLOCK!r}]\n"
    "def handle(payload):\n"
    "    try:\n"
    f"        out = execute({BLOCK!r}, payload)\n"
    "    except Exception as exc:\n"
    "        return {'ok': False, 'error': 'block failed: %s' % type(exc).__name__}\n"
    "    if out.get('status') == 'error':\n"
    "        return {'ok': False, 'error': 'block refused: %s' % out.get('error')}\n"
    "    return {'ok': True, 'result': out}\n"
)
COMPUTES = "def run(data):\n    return {'stored': data['amount'] * 3, 'by': data['owner'][::-1]}\n"


def _workspace(tmp_path: Path, block: str | None) -> Path:
    app = tmp_path / "app"
    (app / "actions").mkdir(parents=True)
    (app / "blocks").mkdir()
    for pkg in (app, app / "actions", app / "blocks"):
        (pkg / "__init__.py").write_text("")
    (app / "dispatch.py").write_text(DISPATCH)
    if block is not None:
        (app / "blocks" / f"{BLOCK}.py").write_text(block)
    for cid in IDS:
        (app / "actions" / f"{cid}.py").write_text(HANDLE)
    write_models(tmp_path, IDS)
    return tmp_path


def _bp():
    return {"capabilities": [{"id": i, "description": f"record the {i} entry with owner and amount"} for i in IDS]}


def _grade(tmp_path, block):
    return fg.grade_functions(_bp(), capability_ids=IDS, workspace=_workspace(tmp_path, block))


def _classes(grade):
    record = {"layer1": {"functions": grade.to_dict()}, "layer2": {"ran": True}, "ui": {"ran": True}}
    return {r["capability"]: r["class"] for r in _grader_module.reason_classes(record, IDS) if r["layer"] == "function"}


def _not_judgeable(grade, cause):
    assert not grade.ok
    assert grade.functions == [] and grade.failing == [], grade.to_dict()
    assert [u.capability_id for u in grade.ungraded] == IDS
    for u in grade.ungraded:
        assert u.detail.startswith(fg.NOT_JUDGEABLE) and list(u.blocks_cannot_run) == [BLOCK]
        assert u.blocks_cannot_run[BLOCK].startswith(cause)
    assert set(_classes(grade).values()) == {"ungraded"}


# --- through the grader's own runner (a subprocess in the probe environment) ---


@pytest.mark.parametrize("present", [True, False])
def test_a_declared_block_that_does_not_load_is_not_judgeable(tmp_path, present):
    grade = _grade(tmp_path, COMPUTES if present else None)
    if present:
        assert grade.ok, grade.to_dict()
        assert grade.ungraded == []
    else:
        _not_judgeable(grade, fg.CANNOT_LOAD)


@pytest.mark.parametrize("package", ["json", "grader_probe_absent_package"])
def test_a_block_importing_a_package_not_installed_is_not_judgeable(tmp_path, package):
    block = f"def run(data):\n    import {package}\n    return {{'stored': data['amount'] * 3, 'by': data['owner'][::-1]}}\n"
    grade = _grade(tmp_path, block)
    if package == "json":
        assert grade.ok, grade.to_dict()
    else:
        _not_judgeable(grade, fg.NOT_INSTALLED)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the grader's minimal env carries no SYSTEMROOT, so Winsock cannot open a socket (CI is Linux)",
)
@pytest.mark.parametrize("host", ["127.0.0.1", "203.0.113.9"])
def test_a_block_reaching_for_the_network_is_not_judgeable(tmp_path, host):
    # The block swallows its own connection error and answers as data, so
    # only the probe's refusal count can tell it never ran.
    block = (
        "import socket\n"
        "def run(data):\n"
        "    try:\n"
        f"        socket.create_connection(({host!r}, 9), timeout=0.5)\n"
        "    except OSError:\n"
        "        pass\n"
        "    return {'stored': data['amount'] * 3, 'by': data['owner'][::-1]}\n"
    )
    grade = _grade(tmp_path, block)
    if host == "127.0.0.1":
        assert grade.ok, grade.to_dict()
    else:
        _not_judgeable(grade, fg.NEEDS_NETWORK)


@pytest.mark.parametrize("block_runs", [True, False])
def test_a_fail_closed_handle_is_a_handler_error_only_when_its_block_runs(tmp_path, block_runs):
    # The block runs and refuses (its own answer): the handle fails closed,
    # is judged, and is the writer's handler_error. The block cannot load:
    # the same handle is not judgeable.
    refuses = "def run(data):\n    return {'status': 'error', 'error': 'refused'}\n"
    grade = _grade(tmp_path, refuses if block_runs else None)
    if block_runs:
        assert not grade.ok and grade.ungraded == []
        assert {f.detail for f in grade.functions} == {fg.BLOCK_UNAVAILABLE}
        assert set(_classes(grade).values()) == {"handler_error"}
        assert grade.failing == IDS
    else:
        _not_judgeable(grade, fg.CANNOT_LOAD)


@pytest.mark.parametrize("raises", ["ValueError('bad amount')", "KeyError('owner')"])
def test_a_block_that_raises_its_own_error_runs_and_is_judged(tmp_path, raises):
    grade = _grade(tmp_path, f"def run(data):\n    raise {raises}\n")
    assert grade.ungraded == [] and not grade.ok
    assert {f.detail for f in grade.functions} == {fg.BLOCK_UNAVAILABLE}


# --- the probe-side functions, in process (what the mutation gate sees) ---------


class _Offline(OSError):
    pass


def _chained(inner):
    try:
        try:
            raise inner
        except BaseException as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError as outer:
        return outer


@pytest.mark.parametrize(
    "exc, cause",
    [
        (ModuleNotFoundError("No module named 'absent'"), fg.NOT_INSTALLED),
        (_chained(ImportError("cannot import name 'x'")), fg.NOT_INSTALLED),
        (_Offline("refused (no network)"), fg.NEEDS_NETWORK),
        (_chained(_Offline("refused")), fg.NEEDS_NETWORK),
        (ValueError("bad amount"), None),
        (OSError("disk full"), None),
        (_chained(KeyError("owner")), None),
    ],
)
def test_block_cannot_run_reads_the_cause_by_type(exc, cause):
    why = fg.block_cannot_run(exc, _Offline)
    if cause is None:
        assert why is None
    else:
        assert why.startswith(cause)


def test_block_cannot_run_ends_on_a_cause_cycle():
    a, b = ValueError("a"), ValueError("b")
    a.__cause__, b.__cause__ = b, a
    assert fg.block_cannot_run(a, _Offline) is None


def _fake(run=None, load_error=None, declared=(BLOCK,)):
    module = SimpleNamespace(run=run) if run is not None else SimpleNamespace()

    def load_block(block_id):
        if load_error is not None:
            raise load_error
        return module

    dispatch = SimpleNamespace(load_block=load_block)
    handle = SimpleNamespace(BLOCK_IDS=list(declared), load_block=load_block)
    return dispatch, handle, module, load_block


@pytest.mark.parametrize("load_error", [None, ModuleNotFoundError("No module named 'absent'")])
def test_watch_loads_every_declared_block_before_judging(load_error):
    dispatch, handle, _module, real = _fake(run=lambda data: data, load_error=load_error)
    cannot = fg.watch_blocks(dispatch, handle, _Offline, [0], {})
    assert dispatch.load_block is not real and handle.load_block is dispatch.load_block
    if load_error is None:
        assert cannot == {}
        assert dispatch.load_block(BLOCK) is _module
    else:
        assert list(cannot) == [BLOCK] and cannot[BLOCK].startswith(fg.CANNOT_LOAD)
        assert "ModuleNotFoundError" in cannot[BLOCK]
        # The handle still sees the loader's own exception, so its fail-closed
        # answer names the real cause.
        with pytest.raises(type(load_error)) as raised:
            dispatch.load_block(BLOCK)
        assert raised.value is load_error


@pytest.mark.parametrize("declared", [(BLOCK,), ()])
def test_watch_records_only_declared_or_used_blocks(declared):
    dispatch, handle, _m, _r = _fake(load_error=RuntimeError("not vendored"), declared=declared)
    cannot = fg.watch_blocks(dispatch, handle, _Offline, [0], {})
    assert list(cannot) == ([BLOCK] if declared else [])


@pytest.mark.parametrize(
    "error, recorded",
    [(ModuleNotFoundError("No module named 'absent'"), fg.NOT_INSTALLED), (ValueError("bad"), None)],
)
def test_watch_records_a_run_that_cannot_run_and_reraises(error, recorded):
    def run(data):
        raise error

    dispatch, handle, module, _r = _fake(run=run)
    cannot = fg.watch_blocks(dispatch, handle, _Offline, [0], {})
    assert cannot == {}
    with pytest.raises(type(error)):
        dispatch.load_block(BLOCK).run({"amount": 1})
    if recorded is None:
        assert cannot == {}
    else:
        assert cannot[BLOCK].startswith(recorded)


@pytest.mark.parametrize("refuses", [True, False])
def test_watch_records_a_run_that_reached_for_the_network(refuses):
    refusals = [0]

    def run(data):
        if refuses:
            refusals[0] += 1  # the probe refused one outbound connection
        return {"stored": data}

    dispatch, handle, module, _r = _fake(run=run)
    cannot = fg.watch_blocks(dispatch, handle, _Offline, refusals, {})
    assert dispatch.load_block(BLOCK).run(3) == {"stored": 3}
    assert cannot == ({BLOCK: fg.NEEDS_NETWORK} if refuses else {})


def test_watch_wraps_a_block_run_once():
    dispatch, handle, module, _r = _fake(run=lambda data: data)
    fg.watch_blocks(dispatch, handle, _Offline, [0], {})
    first = module.run
    dispatch.load_block(BLOCK)
    assert module.run is first and getattr(first, "grader_watched", False) is True


@pytest.mark.parametrize("has_loader", [True, False])
def test_watch_leaves_a_product_with_no_loader_as_it_is(has_loader):
    dispatch = SimpleNamespace(load_block=lambda bid: SimpleNamespace()) if has_loader else SimpleNamespace()
    handle = SimpleNamespace(BLOCK_IDS=[BLOCK])
    cannot = fg.watch_blocks(dispatch if has_loader else None, handle, _Offline, [0], {})
    assert cannot == {}
    assert hasattr(handle, "load_block") is False


# --- the floor and the record --------------------------------------------------


def _ok(cid):
    return fg.FunctionResult(cid, True, ["field_dependence"], [], "")


@pytest.mark.parametrize("n_ungraded", [0, 2])
def test_a_not_judgeable_capability_is_not_billed_as_too_few(n_ungraded):
    cannot = {BLOCK: fg.CANNOT_LOAD}
    gaps = [fg.FunctionResult(f"u{i}", False, [], [], fg.not_judgeable_detail(cannot), blocks_cannot_run=cannot)
            for i in range(n_ungraded)]
    g = fg.FunctionGrade([_ok(f"c{i}") for i in range(fg.MIN_FUNCTIONS - 2)], ungraded=gaps)
    assert g.ok is False
    if n_ungraded:
        assert g.failing == []
    else:
        assert g.failing == [f"too few gradeable functions: {fg.MIN_FUNCTIONS - 2} < {fg.MIN_FUNCTIONS}"]


@pytest.mark.parametrize("cannot", [{"b": "x"}, {"b": "x", "a": "y"}])
def test_not_judgeable_detail_names_each_block_and_why(cannot):
    detail = fg.not_judgeable_detail(cannot)
    assert detail.startswith(fg.NOT_JUDGEABLE + ": ")
    assert detail.endswith("; ".join(f"{k}: {v}" for k, v in sorted(cannot.items())))


# --- the product's own loader refuses to start (it loads every locked block) ---


def _refusing_workspace(tmp_path: Path, block_loads: bool, handle_imports: str) -> Path:
    """A workspace carrying the Factory's own rendered loader and a lock: the
    loader loads every locked block when it is imported and refuses to start
    if one cannot load."""
    import json

    from app.factory.build.roles_handlers import _render_dispatch

    ws = _workspace(tmp_path, None)
    (ws / "app" / "dispatch.py").write_text(_render_dispatch({}), encoding="utf-8")
    vendored = ws / "vendor" / "blocks" / BLOCK
    vendored.mkdir(parents=True)
    head = "" if block_loads else "import grader_probe_absent_package\n"
    (vendored / "block.py").write_text(head + COMPUTES, encoding="utf-8")
    (ws / "blocks.lock.json").write_text(
        json.dumps({"blocks": {BLOCK: {"path": f"vendor/blocks/{BLOCK}"}}}), encoding="utf-8"
    )
    for cid in IDS:
        (ws / "app" / "actions" / f"{cid}.py").write_text(handle_imports + HANDLE, encoding="utf-8")
    return ws


@pytest.mark.parametrize("block_loads", [True, False])
def test_a_handle_the_loader_refuses_to_import_is_not_judgeable(tmp_path, block_loads):
    # The handle's own import is clean; whether it can be imported at all is
    # decided by the loader's start-up check of the locked block.
    ws = _refusing_workspace(tmp_path, block_loads, "")
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=ws)
    if block_loads:
        assert grade.ungraded == [] and len(grade.functions) == len(IDS), grade.to_dict()
        assert not any(f.detail.startswith("import failed") for f in grade.functions)
    else:
        _not_judgeable(grade, fg.CANNOT_LOAD)
        assert all("ModuleNotFoundError" in u.blocks_cannot_run[BLOCK] for u in grade.ungraded)


@pytest.mark.parametrize("own_import", ["import json\n", "import grader_probe_absent_handle_package\n"])
def test_a_handle_that_fails_its_own_import_is_still_judged(tmp_path, own_import):
    # The block loads, so the loader starts; a handle whose OWN import fails
    # is the writer's, and is judged.
    ws = _refusing_workspace(tmp_path, True, own_import)
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=ws)
    assert grade.ungraded == [] and len(grade.functions) == len(IDS), grade.to_dict()
    failed_import = [f for f in grade.functions if f.detail.startswith("import failed")]
    if own_import == "import json\n":
        assert failed_import == []
    else:
        assert [f.capability_id for f in failed_import] == IDS
        assert grade.failing[: len(IDS)] == IDS


class _Refusal(RuntimeError):
    pass


def _refusal(unloadable):
    exc = _Refusal("refusing to start")
    exc.unloadable = unloadable
    return exc


def _context_chained(inner):
    try:
        try:
            raise inner
        except BaseException:
            raise ImportError("while importing the handle")
    except ImportError as outer:
        return outer


@pytest.mark.parametrize(
    "exc, blocks",
    [
        (_refusal({BLOCK: ModuleNotFoundError("No module named 'absent'")}), [BLOCK]),
        (_chained(_refusal({"b": OSError("x"), "a": KeyError("y")})), ["b", "a"]),
        (_context_chained(_refusal({BLOCK: SyntaxError("bad")})), [BLOCK]),
        (_refusal({}), []),
        (_refusal(["not", "a", "map"]), []),
        (ImportError("No module named 'absent'"), []),
        (_chained(ValueError("bad amount")), []),
    ],
)
def test_loader_refused_reads_the_blocks_by_type(exc, blocks):
    named = fg.loader_refused(exc)
    assert list(named) == blocks
    for bid in blocks:
        assert named[bid].startswith(fg.CANNOT_LOAD + ": ")


@pytest.mark.parametrize(
    "err, why",
    [
        (ModuleNotFoundError("No module named 'absent'"), "ModuleNotFoundError: No module named 'absent'"),
        (SyntaxError("bad"), "SyntaxError: bad"),
    ],
)
def test_loader_refused_names_each_blocks_own_exception(err, why):
    assert fg.loader_refused(_refusal({BLOCK: err})) == {BLOCK: fg.CANNOT_LOAD + ": " + why}


@pytest.mark.parametrize("length", [1, 2])
def test_loader_refused_ends_on_a_cause_cycle(length):
    chain = [ValueError(str(i)) for i in range(length)]
    for i, exc in enumerate(chain):
        exc.__cause__ = chain[(i + 1) % length]
    assert fg.loader_refused(chain[0]) == {}
