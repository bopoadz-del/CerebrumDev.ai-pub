"""F-G1c: every grader runner probes a handle in ONE environment, chosen by
construction -- the product's app booted (its startup wiring done), never a
bare import of ``app.actions``.

Pilot 43510059079 (07e59bc) read ``stub_handler=7`` one merge after a pilot
that read 0, with the grader code unchanged between them: the two runs drafted
different products, and the grader called each handle straight off
``app.actions`` without entering the app, while the writer gate's probe enters
``app.main`` through ``TestClient``. A product that wires its store at startup
answered one result for two inputs in the grader only.

The workspace below wires its store ONLY in the app's lifespan. If any grader
runner ever probes without that wiring, the ``boots=True`` cases go red.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from app.factory.build.grader import reason_classes
from app.factory.build.product_grader import function_grader as fg
from app.factory.build.product_grader import two_dataset as td

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the grader's minimal env carries no SYSTEMROOT, so the booted app's event loop cannot open its socketpair (CI is Linux)",
)

STORE = "WIRED = {}\n"
MAIN = (
    "import contextlib, os\n"
    "from fastapi import FastAPI\n"
    "from app import store\n"
    "@contextlib.asynccontextmanager\n"
    "async def lifespan(app):\n"
    "    root = os.environ['STORAGE_PATH']\n"
    "    if not os.path.isdir(root) or not os.environ.get('PLATFORM_TOKEN'):\n"
    "        raise RuntimeError('no scratch store or no gate token')\n"
    "    store.WIRED['root'] = root\n"
    "    yield\n"
    "app = FastAPI(lifespan=lifespan)\n"
)
BOOT_FAILS = (
    "import contextlib\n"
    "from fastapi import FastAPI\n"
    "@contextlib.asynccontextmanager\n"
    "async def lifespan(app):\n"
    "    raise RuntimeError('store url unreadable')\n"
    "    yield\n"
    "app = FastAPI(lifespan=lifespan)\n"
)
#: Computes over its input once the store is wired; unwired, it answers the
#: same thing for every input -- exactly what a stub answers.
HANDLE = (
    "from app import store\n"
    "def handle(payload):\n"
    "    if not store.WIRED:\n"
    "        return {'ok': True, 'result': 0}\n"
    "    nums = [v for v in payload.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]\n"
    "    return {'ok': True, 'result': sum(nums), 'echo': sorted((k, str(v)) for k, v in payload.items())}\n"
)
IDS = ["intake_form", "invoice_total", "visit_log", "tenant_notice"]


def _workspace(tmp_path: Path, main: str | None) -> Path:
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "store.py").write_text(STORE)
    if main is not None:
        (tmp_path / "app" / "main.py").write_text(main)
    (actions / "__init__.py").write_text("")
    for cid in IDS:
        (actions / f"{cid}.py").write_text(HANDLE)
    return tmp_path


def _bp():
    return {
        "capabilities": [
            {"id": i, "description": f"record the {i} entry with owner and amount"} for i in IDS
        ]
    }


def _classes(grade):
    record = {
        "layer1": {"functions": grade.to_dict()},
        "layer2": {"ran": True},
        "ui": {"ran": True},
    }
    return {
        r["capability"]: r["class"] for r in reason_classes(record, IDS) if r["layer"] == "function"
    }


@pytest.mark.parametrize("runner", ["function_grader", "two_dataset"])
def test_every_runner_enters_the_one_probe_env_before_importing_a_handle(runner):
    text = {"function_grader": fg._RUNNER, "two_dataset": td._RUNNER}[runner]
    assert fg.PROBE_ENV in text
    entered = text.index("_enter_probe_env(spec)\n")
    imported = re.search(r"importlib\.import_module\(spec\[\"module\"\]\)", text)
    assert imported is not None and entered < imported.start()


@pytest.mark.parametrize("boots", [True, False])
def test_function_grade_sees_startup_wiring(tmp_path, boots):
    grade = fg.grade_functions(_bp(), _workspace(tmp_path, MAIN if boots else None))
    if boots:
        assert grade.ok, grade.to_dict()
        assert _classes(grade) == {}
    else:
        assert not grade.ok
        assert {f.detail for f in grade.functions} == {fg.ONE_RESULT}
        assert set(_classes(grade).values()) == {"stub_handler"}


@pytest.mark.parametrize("boots", [True, False])
def test_two_dataset_sees_startup_wiring(tmp_path, boots):
    ws = _workspace(tmp_path, MAIN if boots else None)
    a, b = td.run_function_pair("app.actions.invoice_total", {"amount": 3.0}, {"amount": 5.0}, ws)
    if boots:
        assert (a, b) == (3.0, 5.0)
        assert td.verdict(a, b, None, None) == td.OK
    else:
        assert (a, b) == (0, 0)
        assert td.verdict(a, b, None, None) == td.HARDWIRED_CONSTANT


@pytest.mark.parametrize("fails", [True, False])
def test_boot_failure_is_a_named_harness_error_in_both_runners(tmp_path, fails):
    ws = _workspace(tmp_path, BOOT_FAILS if fails else MAIN)
    grade = fg.grade_functions(_bp(), ws)
    pair = td.run_function_pair("app.actions.invoice_total", {"amount": 3.0}, {"amount": 5.0}, ws)
    if fails:
        assert all(
            f.detail.startswith("harness boot failed: RuntimeError") for f in grade.functions
        )
        assert set(_classes(grade).values()) == {"handler_error"}
        assert pair == (None, None)
        assert td.verdict(*pair, None, None) == td.BOTH_ABSENT
    else:
        assert grade.ok, grade.to_dict()
        assert pair == (3.0, 5.0)
