"""FG-2(a): one toolchain check, owned by the UI grader.

``toolchain_check`` runs a one-test spec through the same command and
environment ``grade_ui`` uses; it is ok only when that test passed. The same
function runs at grade time and, in CI, inside the built runtime image as the
app user. Every case below has two inputs, so no single literal can pass it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import app.factory.build.product_grader.ui_grader as ug

BPS = [
    {"product_id": "harbour-log", "product_name": "Harbour Log", "summary": "berth bookings",
     "ui_modules": ["quay_board"], "capabilities": [{"id": "berth_plan", "description": "Plan berths."}]},
    {"product_id": "kiln-desk", "product_name": "Kiln Desk", "summary": "firing schedules",
     "ui_modules": ["kiln_view", "rota"], "capabilities": [{"id": "firing_log", "description": "Log firings."}]},
]


def _which_found(name, path=None):
    return f"/usr/bin/{name}"


def _report(title: str, status: str) -> str:
    return json.dumps({"suites": [{"specs": [{"title": title, "tests": [{"results": [{"status": status}]}]}]}]})


def _runner(*, report=None, returncode=0, stderr="", seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.append((cmd, kw))
        if report is not None:
            Path(kw["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"]).write_text(report, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)
    return run


@pytest.mark.parametrize("path_seen", [None, ""])
def test_no_npx_on_the_run_path_is_not_ok(path_seen):
    asked = []

    def which(name, path=None):
        asked.append((name, path))
        return path_seen

    status = ug.toolchain_check(run=_runner(seen=[]), which=which)
    assert not status.ok and "npx" in status.detail
    assert asked == [("npx", ug.RUN_PATH)]


@pytest.mark.parametrize("stdout_report", [False, True])
def test_a_passed_probe_is_ok(stdout_report):
    seen = []
    report = _report(ug.TOOLCHAIN_TEST, "passed")
    if stdout_report:
        def run(cmd, **kw):
            seen.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, 0, stdout=report, stderr="")
    else:
        run = _runner(report=report, seen=seen)
    status = ug.toolchain_check(run=run, which=_which_found)
    assert status.ok
    (cmd, kw), = seen
    # the probe takes the grade's own path: same command shape, same PATH
    assert cmd[:3] == ["npx", "playwright", "test"] and cmd[-1] == "--reporter=json"
    assert kw["env"]["PATH"] == ug.RUN_PATH
    assert Path(cmd[3]).name == "toolchain.spec.ts"


@pytest.mark.parametrize("returncode,stderr", [
    (1, "Error: browserType.launch: Executable doesn't exist at /x/chrome"),
    (127, "sh: 1: playwright: not found"),
])
def test_no_report_names_exit_code_and_stderr(returncode, stderr):
    status = ug.toolchain_check(run=_runner(returncode=returncode, stderr=stderr), which=_which_found)
    assert not status.ok
    assert f"exit {returncode}" in status.detail
    assert stderr.split()[-1] in status.detail


@pytest.mark.parametrize("report", [
    _report(ug.TOOLCHAIN_TEST, "failed"),
    _report("some other test", "passed"),
])
def test_a_report_without_a_passed_probe_is_not_ok(report):
    status = ug.toolchain_check(run=_runner(report=report, returncode=1, stderr="boom"), which=_which_found)
    assert not status.ok


@pytest.mark.parametrize("report", ["{not json", "[]"])
def test_an_unreadable_report_is_not_ok(report):
    status = ug.toolchain_check(run=_runner(report=report, returncode=1), which=_which_found)
    assert not status.ok


@pytest.mark.parametrize("exc", [FileNotFoundError("npx"), subprocess.TimeoutExpired("npx", 120)])
def test_a_probe_that_cannot_start_is_not_ok(exc):
    def run(cmd, **kw):
        raise exc

    status = ug.toolchain_check(run=run, which=_which_found)
    assert not status.ok and type(exc).__name__ in status.detail


def test_the_probe_spec_names_no_product():
    for bp in BPS:
        for value in (bp["product_id"], bp["product_name"], bp["capabilities"][0]["id"]):
            assert value not in ug.TOOLCHAIN_SPEC
    assert json.dumps(ug.TOOLCHAIN_TEST) in ug.TOOLCHAIN_SPEC


@pytest.mark.parametrize("bp,detail", [
    (BPS[0], "npx is not on PATH /usr/bin"),
    (BPS[1], "playwright gave no report: exit 1; stderr: Executable doesn't exist"),
])
def test_grade_time_toolchain_outage_is_named_and_no_spec_runs(monkeypatch, tmp_path, bp, detail):
    monkeypatch.setattr(ug, "toolchain_check", lambda **kw: ug.ToolchainStatus(False, detail))
    g = ug.grade_ui(bp, tmp_path, base_url="http://127.0.0.1:9")
    assert not g.ok and not g.ran
    assert g.errors == [f"UI run did not happen: UI toolchain unavailable: {detail}"]
    assert not (tmp_path / "grade_ui.spec.ts").exists()


@pytest.mark.parametrize("bp", BPS)
def test_grade_time_toolchain_ok_runs_the_products_spec(monkeypatch, tmp_path, bp):
    monkeypatch.setattr(ug, "toolchain_check", lambda **kw: ug.ToolchainStatus(True, "ok"))
    ran = []

    def fake_run(cmd, **kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no browser here")

    # the default runner is the only one gated by the check; route it to a fake
    monkeypatch.setattr(ug.subprocess, "run", fake_run)
    g = ug.grade_ui(bp, tmp_path, base_url="http://127.0.0.1:9", run=fake_run)
    assert ran and Path(ran[0][3]).name == "grade_ui.spec.ts"
    assert (tmp_path / "grade_ui.spec.ts").read_text(encoding="utf-8") == ug.render_spec(bp)
    assert g.errors == ["UI run did not happen: no playwright report: exit 1; stderr: no browser here"]


@pytest.mark.parametrize("bp,base_url", [(BPS[0], None), (BPS[1], "")])
def test_no_base_url_is_named_and_nothing_runs(monkeypatch, tmp_path, bp, base_url):
    calls = []
    monkeypatch.setattr(ug, "toolchain_check", lambda **kw: calls.append("check") or ug.ToolchainStatus(True, "ok"))

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    g = ug.grade_ui(bp, tmp_path / "wd", base_url=base_url, run=run)
    assert not g.ok and not g.ran
    assert g.errors == ["UI run did not happen: no base URL for the rendered product"]
    assert calls == [] and not (tmp_path / "wd").exists()
