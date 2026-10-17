"""Gates must fail on the shapes that look like success.

New-shape tests for the manufacturing gates. Each of these guards a
*plausible green* -- an outcome that passes a naive check and produces a
platform that does not work: a cloner that vendored nothing, a suite where
no tests ran, a block that only imports because the store happened to be
configured. A gate that cannot catch those is decoration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


from app.factory.build.authority import BuildRole
from app.factory.build.gates import (
    GateContext,
    gate_blocks_import_offline,
    gate_for,
    gate_gaps_enumerated,
    gate_store_ops_authorised,
    gate_suite_green,
    gate_workspace_compiles,
)


def _proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _ctx(workspace: Path, role: BuildRole, *, result=None, **kw) -> GateContext:
    calls = []

    def runner(argv, *, cwd, timeout):
        calls.append({"argv": argv, "cwd": cwd, "timeout": timeout})
        return result if result is not None else _proc(0)

    ctx = GateContext(workspace=workspace, role=role, runner=runner, **kw)
    # Attached for assertions; GateContext itself stays frozen.
    object.__setattr__(ctx, "_calls", calls)
    return ctx


# -- COLLECTOR -----------------------------------------------------------


def test_declared_gaps_pass_and_are_handed_to_the_writer(tmp_path):
    ctx = _ctx(tmp_path, BuildRole.COLLECTOR, gaps=("loyalty_scoring",))
    result = gate_gaps_enumerated(ctx)
    assert result.ok
    assert result.payload["gaps"] == ["loyalty_scoring"]


def test_an_unnamed_gap_fails_rather_than_vanishing(tmp_path):
    """A capability dropped without a name ships a platform missing it silently."""
    ctx = _ctx(tmp_path, BuildRole.COLLECTOR, gaps=("", "  "))
    result = gate_gaps_enumerated(ctx)
    assert not result.ok
    assert len(result.findings) == 2


# -- CLONER --------------------------------------------------------------


def test_cloner_gate_fails_when_nothing_was_vendored(tmp_path):
    result = gate_blocks_import_offline(_ctx(tmp_path, BuildRole.CLONER))
    assert not result.ok
    assert "vendor/blocks is missing" in result.detail


def test_registered_block_missing_from_disk_is_caught(tmp_path):
    """The ledger says it was cloned; the disk disagrees."""
    (tmp_path / "vendor" / "blocks").mkdir(parents=True)
    ctx = _ctx(tmp_path, BuildRole.CLONER, vendored_blocks=("web", "invoice_parser"))
    result = gate_blocks_import_offline(ctx)
    assert not result.ok
    assert result.findings == [
        "vendor/blocks/web/block.py missing",
        "vendor/blocks/invoice_parser/block.py missing",
    ]


def test_import_failure_surfaces_the_probe_stderr(tmp_path):
    (tmp_path / "vendor" / "blocks" / "web").mkdir(parents=True)
    (tmp_path / "vendor" / "blocks" / "web" / "block.py").write_text("x = 1\n", encoding="utf-8")
    ctx = _ctx(
        tmp_path,
        BuildRole.CLONER,
        vendored_blocks=("web",),
        result=_proc(1, stderr="web: ModuleNotFoundError: No module named 'httpx'"),
    )
    result = gate_blocks_import_offline(ctx)
    assert not result.ok
    assert "No module named 'httpx'" in result.findings[0]


def test_clean_offline_import_passes(tmp_path):
    (tmp_path / "vendor" / "blocks" / "web").mkdir(parents=True)
    (tmp_path / "vendor" / "blocks" / "web" / "block.py").write_text("x = 1\n", encoding="utf-8")
    ctx = _ctx(tmp_path, BuildRole.CLONER, vendored_blocks=("web",))
    assert gate_blocks_import_offline(ctx).ok


def test_import_probe_strips_the_store_environment(tmp_path):
    """The probe must not pass only because the store happened to be set."""
    from app.factory.build.gates import _IMPORT_PROBE

    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
        assert var in _IMPORT_PROBE
    assert "os.environ.pop" in _IMPORT_PROBE


def test_import_probe_really_runs(tmp_path):
    """Execute the probe for real -- every other cloner test mocks the
    subprocess away, so a syntax or type error inside this code string would
    pass the entire suite and only fail during a live build."""
    import sys as _sys
    from app.factory.build.gates import _IMPORT_PROBE

    blocks = tmp_path / "vendor" / "blocks"
    (blocks / "good").mkdir(parents=True)
    (blocks / "good" / "block.py").write_text("VALUE = 1\n", encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(_IMPORT_PROBE, encoding="utf-8")

    ok = subprocess.run(
        [_sys.executable, str(probe)], cwd=tmp_path, capture_output=True, text=True
    )
    assert ok.returncode == 0, ok.stderr

    (blocks / "bad").mkdir()
    (blocks / "bad" / "block.py").write_text(
        "import nonexistent_module_xyz\n", encoding="utf-8"
    )
    bad = subprocess.run(
        [_sys.executable, str(probe)], cwd=tmp_path, capture_output=True, text=True
    )
    assert bad.returncode == 1
    # str(exc) not exc -- concatenating the exception object raises TypeError
    # and would report the wrong failure entirely.
    assert "bad: ModuleNotFoundError" in bad.stderr


# -- WRITER --------------------------------------------------------------


def test_writer_gate_fails_when_no_app_was_produced(tmp_path):
    result = gate_workspace_compiles(_ctx(tmp_path, BuildRole.WRITER))
    assert not result.ok
    assert "writer produced nothing" in result.detail


def test_syntax_error_stops_the_handoff_to_the_tester(tmp_path):
    (tmp_path / "app").mkdir()
    ctx = _ctx(
        tmp_path,
        BuildRole.WRITER,
        result=_proc(1, stdout="*** Error compiling 'app/actions/orders.py'"),
    )
    result = gate_workspace_compiles(ctx)
    assert not result.ok
    assert "orders.py" in result.findings[0]


def test_compiling_app_passes(tmp_path):
    (tmp_path / "app").mkdir()
    assert gate_workspace_compiles(_ctx(tmp_path, BuildRole.WRITER)).ok


# -- TESTER --------------------------------------------------------------


def test_no_tests_written_is_a_failure_not_a_pass(tmp_path):
    """'No tests ran' is the most dangerous green in a generated platform."""
    (tmp_path / "app").mkdir()
    (tmp_path / "tests").mkdir()
    result = gate_suite_green(_ctx(tmp_path, BuildRole.TESTER))
    assert not result.ok
    assert "no tests were written" in result.detail


def test_missing_app_package_fails_fast_naming_the_gap(tmp_path):
    """A coder checkout with no app/ must be named, not masked as
    'No module named app' collection noise that burns the rework budget."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test_x(): pass\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path, BuildRole.TESTER)
    result = gate_suite_green(ctx)
    assert not result.ok
    assert "no app/ package" in result.detail
    assert any("app/__init__.py" in f for f in result.findings)
    # The suite must NOT have been run: the gap is named before pytest.
    assert ctx._calls == []


def test_missing_tests_dir_is_a_failure(tmp_path):
    assert not gate_suite_green(_ctx(tmp_path, BuildRole.TESTER)).ok


def test_red_suite_reports_the_failing_test_names_as_the_work_list(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    ctx = _ctx(
        tmp_path,
        BuildRole.TESTER,
        result=_proc(1, stdout="FAILED tests/test_x.py::test_x\n1 failed"),
    )
    result = gate_suite_green(ctx)
    assert not result.ok
    assert result.findings == ["FAILED tests/test_x.py::test_x"]


def test_green_suite_passes(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    ctx = _ctx(tmp_path, BuildRole.TESTER, result=_proc(0, stdout="1 passed in 0.01s"))
    result = gate_suite_green(ctx)
    assert result.ok
    assert "1 passed" in result.detail
    argv = ctx._calls[0]["argv"]
    assert "not pilot" in argv
    marker_at = len(argv) - 1 - argv[::-1].index("-m")
    assert argv[marker_at + 1] == "not pilot"


def test_factory_gate_ignores_a_red_pilot_test(tmp_path):
    """Store-backed execute-all is not the factory code-phase gate.

    A failing @pytest.mark.pilot test must not fail gate_suite_green, or
    TESTER rework burns 20–30 min of coder time on Store channels.
    """
    from app.factory.build.gates import GateContext

    tests = tmp_path / "tests"
    (tmp_path / "app").mkdir()
    tests.mkdir()
    (tests / "conftest.py").write_text(
        "def pytest_configure(config):\n"
        "    config.addinivalue_line(\n"
        "        'markers',\n"
        "        'pilot: Store-backed; excluded from the factory gate',\n"
        "    )\n",
        encoding="utf-8",
    )
    (tests / "test_code.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    (tests / "test_pilot.py").write_text(
        "import pytest\n"
        "@pytest.mark.pilot\n"
        "def test_store_must_accept():\n"
        "    assert False, 'pilot must be deselected'\n",
        encoding="utf-8",
    )
    result = gate_suite_green(
        GateContext(workspace=tmp_path, role=BuildRole.TESTER)
    )
    assert result.ok, result.detail


# -- wiring --------------------------------------------------------------


def test_pilot_marker_runs_pytest_m_pilot(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    ctx = _ctx(
        tmp_path,
        BuildRole.TESTER,
        result=_proc(0, stdout="2 passed in 0.01s"),
        suite_marker="pilot",
    )
    result = gate_suite_green(ctx)
    assert result.ok
    assert result.gate == "pilot_green"
    argv = ctx._calls[0]["argv"]
    marker_at = len(argv) - 1 - argv[::-1].index("-m")
    assert argv[marker_at + 1] == "pilot"


def test_pilot_store_gate_requires_ops(tmp_path):
    empty = GateContext(
        workspace=tmp_path, role=BuildRole.STORE_MANAGER, cycle="pilot"
    )
    failed = gate_store_ops_authorised(empty)
    assert not failed.ok
    applied = GateContext(
        workspace=tmp_path,
        role=BuildRole.STORE_MANAGER,
        cycle="pilot",
        store_ops=({"op": "STORE_READ", "block_id": "database"},),
        store_unwired=True,
    )
    ok = gate_store_ops_authorised(applied)
    assert ok.ok
    assert "applied 1 store op" in ok.detail
    assert "store unwired" in ok.detail


def test_code_phase_store_gate_still_accepts_empty(tmp_path):
    ctx = GateContext(workspace=tmp_path, role=BuildRole.STORE_MANAGER)
    result = gate_store_ops_authorised(ctx)
    assert result.ok
    assert result.detail == "no store ops applied"


def test_every_role_has_a_gate():
    for role in BuildRole:
        assert gate_for(role) is not None


def test_gates_never_inherit_stdin(tmp_path, monkeypatch):
    """A gate subprocess that blocks on input stalls the build undiagnosed."""
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return _proc(0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    from app.factory.build.gates import _real_run

    _real_run([str(tmp_path)], cwd=tmp_path, timeout=1.0)
    assert seen["stdin"] is subprocess.DEVNULL
    assert seen["timeout"] == 1.0
