"""F-G1: the function grader probes a handle in the writer gate's block
environment, and one honest-error envelope for both inputs is
``block_unavailable`` (a handler_error), never a stub. The two inputs are two
valid instances of the spec the workspace's ``app/models.py`` declares.

Every case runs twice: once in the grader's harness and once "bare" (the
harness text removed from the runner, which is how the grader ran before
F-G1). The SAME handle must read differently in the two only because of the
environment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import app.factory.build.grader as _grader_module
import app.factory.build.product_grader.function_grader as fg
from tests.factory.grader_contract import write_models

#: A vendored block that needs the store the harness provides: a scratch
#: STORAGE_PATH and the product's own migrations applied to it.
BLOCK = (
    "import os\n"
    "from pathlib import Path\n"
    "def run(data):\n"
    "    root = Path(os.environ['STORAGE_PATH'])\n"
    "    if not (root / 'schema.ready').is_file():\n"
    "        raise RuntimeError('store not migrated')\n"
    "    return {'stored': data['amount'] * 3, 'by': data['owner'][::-1]}\n"
)
MIGRATIONS = (
    "import os\n"
    "from pathlib import Path\n"
    "def upgrade_head():\n"
    "    (Path(os.environ['STORAGE_PATH']) / 'schema.ready').write_text('1')\n"
)
#: A correct handle: it calls its block and fails closed when the block fails.
FAILS_CLOSED = (
    "from app import ledger_block\n"
    "def handle(payload):\n"
    "    try:\n"
    "        out = ledger_block.run(payload)\n"
    "    except Exception as exc:\n"
    "        return {'ok': False, 'error': 'block failed: %s' % exc}\n"
    "    return {'ok': True, 'result': out}\n"
)
STUB = "def handle(payload):\n    return {'ok': True, 'result': 'done'}\n"
NETWORK = (
    "import socket\n"
    "def handle(payload):\n"
    "    try:\n"
    "        socket.create_connection(('203.0.113.9', 9), timeout=0.5)\n"
    "    except OSError as exc:\n"
    "        if 'no network' in str(exc):\n"
    "            return {'ok': True, 'echo': payload['amount'] * 3}\n"
    "        raise\n"
    "    raise RuntimeError('outbound connection was not refused')\n"
)
IDS = ["intake_form", "invoice_total", "visit_log", "tenant_notice"]


def _workspace(tmp_path: Path, body: str, *, migrations: bool = True) -> Path:
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "ledger_block.py").write_text(BLOCK)
    if migrations:
        (tmp_path / "app" / "migrations.py").write_text(MIGRATIONS)
    (actions / "__init__.py").write_text("")
    for cid in IDS:
        (actions / f"{cid}.py").write_text(body)
    write_models(tmp_path, IDS)
    return tmp_path


def _bp():
    return {"capabilities": [{"id": i, "description": f"record the {i} entry with owner and amount"} for i in IDS]}


@pytest.fixture(params=["harness", "bare"])
def env(request, monkeypatch):
    if request.param == "bare":
        assert fg._HARNESS in fg._RUNNER
        monkeypatch.setattr(fg, "_RUNNER", fg._RUNNER.replace(fg._HARNESS, ""))
    return request.param


def _classes(grade):
    record = {"layer1": {"functions": grade.to_dict()}, "layer2": {"ran": True}, "ui": {"ran": True}}
    return {r["capability"]: r["class"] for r in _grader_module.reason_classes(record, IDS) if r["layer"] == "function"}


@pytest.mark.parametrize("env", ["harness", "bare"], indirect=True)
def test_correct_handle_ok_with_blocks_block_unavailable_without(tmp_path, env):
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=_workspace(tmp_path, FAILS_CLOSED))
    if env == "harness":
        assert grade.ok, grade.to_dict()
        assert all(f.detail == "" for f in grade.functions)
        assert _classes(grade) == {}
    else:
        assert not grade.ok
        assert {f.detail for f in grade.functions} == {fg.BLOCK_UNAVAILABLE}
        assert set(_classes(grade).values()) == {"handler_error"}


@pytest.mark.parametrize("migrations", [True, False])
def test_store_missing_reads_block_unavailable_never_stub(tmp_path, migrations):
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=_workspace(tmp_path, FAILS_CLOSED, migrations=migrations))
    assert grade.ok is migrations
    expected = "" if migrations else fg.BLOCK_UNAVAILABLE
    assert {f.detail for f in grade.functions} == {expected}
    assert "stub_handler" not in _classes(grade).values()


@pytest.mark.parametrize("env", ["harness", "bare"], indirect=True)
def test_true_stub_reads_stub_in_both_environments(tmp_path, env):
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=_workspace(tmp_path, STUB))
    assert not grade.ok
    assert {f.detail for f in grade.functions} == {fg.ONE_RESULT}
    assert set(_classes(grade).values()) == {"stub_handler"}


@pytest.mark.parametrize(
    "envelope, detail",
    [
        ("{'ok': False, 'error': 'store offline'}", fg.BLOCK_UNAVAILABLE),
        ("{'ok': False, 'error': '   '}", fg.ONE_RESULT),
        ("{'ok': False}", fg.ONE_RESULT),
        ("{'ok': None, 'error': 'store offline'}", fg.ONE_RESULT),
        ("{'ok': False, 'error': 7}", fg.ONE_RESULT),
        ("['ok', False]", fg.ONE_RESULT),
    ],
)
def test_only_a_named_honest_error_is_block_unavailable(tmp_path, envelope, detail):
    body = f"def handle(payload):\n    return {envelope}\n"
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=_workspace(tmp_path, body))
    assert not grade.ok
    assert {f.detail for f in grade.functions} == {detail}


@pytest.mark.parametrize("env", ["harness", "bare"], indirect=True)
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="the grader's minimal env carries no SYSTEMROOT, so Winsock cannot open a socket (CI is Linux)",
)
def test_outbound_network_is_refused_in_the_harness_only(tmp_path, env):
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=_workspace(tmp_path, NETWORK))
    assert grade.ok is (env == "harness"), grade.to_dict()


@pytest.mark.parametrize("setting", ["PLATFORM_TOKEN", "STORAGE_PATH"])
def test_harness_settings_reach_the_handle(tmp_path, env, setting):
    body = (
        "import os\n"
        "def handle(payload):\n"
        f"    if not os.environ.get({setting!r}):\n"
        "        return {'ok': False, 'error': 'setting missing'}\n"
        "    return {'ok': True, 'echo': payload['amount'] * 3}\n"
    )
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=_workspace(tmp_path, body))
    assert grade.ok is (env == "harness")


@pytest.mark.parametrize("required", [True, False])
def test_deploy_time_setting_is_stood_in(tmp_path, required):
    read = "os.environ['GRADER_PROBE_SECRET']" if required else "os.environ.get('GRADER_PROBE_SECRET')"
    body = (
        "import os\n"
        "def handle(payload):\n"
        f"    if not {read}:\n"
        "        return {'ok': True, 'unset': payload['amount'] * 3}\n"
        "    return {'ok': True, 'set': payload['amount'] * 3}\n"
    )
    ws = _workspace(tmp_path, body)
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=ws)
    assert grade.ok, grade.to_dict()


@pytest.mark.parametrize("fails", [True, False])
def test_migration_failure_is_a_named_harness_error(tmp_path, fails):
    ws = _workspace(tmp_path, FAILS_CLOSED)
    if fails:
        (ws / "app" / "migrations.py").write_text("def upgrade_head():\n    raise RuntimeError('bad revision')\n")
    grade = fg.grade_functions(_bp(), capability_ids=IDS, workspace=ws)
    assert grade.ok is (not fails)
    if fails:
        assert all(f.detail.startswith("harness migration failed") for f in grade.functions)
        assert set(_classes(grade).values()) == {"handler_error"}
