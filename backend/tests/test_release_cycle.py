"""The release cycle: smoke A and the repro builds run AT THE SAME TIME, each
on its own verified account; smoke B runs after all of them; one report.

Owner's standing rule (2026-10-08): repro builds run in parallel with smoke A
on separate accounts, the smoke keeps its reserved slot, slots queue and never
fail, cycle target <= 1.25 h. Serial runs where parallel is available are
forbidden -- on 2026-10-07 every cycle ran vineyard AFTER smoke A and cost an
hour each time.
"""

from __future__ import annotations

import importlib.util
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CYCLE_PATH = REPO_ROOT / "scripts" / "release_cycle.py"
CONFIG_PATH = REPO_ROOT / "scripts" / "release_cycle.json"
SMOKE_PATH = REPO_ROOT / "scripts" / "post_deploy_smoke.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "post-deploy-smoke.yml"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cycle():
    return _load("release_cycle", CYCLE_PATH)


# -- account assignment ------------------------------------------------------


def test_the_smoke_keeps_index_zero_and_every_repro_gets_its_own_account(cycle):
    plan = cycle.assign_accounts(["one", "two"], available=3)
    assert plan["smoke"] == cycle.SMOKE_ACCOUNT_INDEX == 0
    assert plan["repros"] == {"one": 1, "two": 2}
    used = [plan["smoke"], *plan["repros"].values()]
    assert len(used) == len(set(used)), "two runs on one account share its slot"


def test_a_short_roster_fails_closed_instead_of_doubling_up(cycle):
    with pytest.raises(cycle.CycleError) as exc:
        cycle.assign_accounts(["one", "two"], available=2)
    assert "3" in str(exc.value) and "2" in str(exc.value)


def test_repro_names_are_distinct(cycle):
    with pytest.raises(cycle.CycleError):
        cycle.assign_accounts(["one", "one"], available=5)


# -- concurrency -------------------------------------------------------------


def test_runs_start_together_not_one_after_another(cycle):
    # Each run waits at a barrier for ALL the others. Run serially, the first
    # one would wait alone and the barrier would time out.
    barrier = threading.Barrier(3, timeout=10)

    def run():
        barrier.wait()
        return {"status": "exported"}

    results = cycle.run_parallel({"smoke_a": run, "one": run, "two": run})
    assert {k: v["status"] for k, v in results.items()} == {
        "smoke_a": "exported", "one": "exported", "two": "exported",
    }


def test_one_run_failing_never_cancels_the_others(cycle):
    finished = threading.Event()

    def boom():
        raise RuntimeError("writer died")

    def slow():
        time.sleep(0.2)
        finished.set()
        return {"status": "exported"}

    results = cycle.run_parallel({"boom": boom, "slow": slow})
    assert finished.is_set()
    assert results["slow"]["status"] == "exported"
    assert results["boom"]["status"] == "error"
    assert "writer died" in results["boom"]["error"]


# -- driving one build -------------------------------------------------------


def _zip_with_manifest(manifest, extra=3):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("MANIFEST.json", json.dumps(manifest))
        for i in range(extra):
            zf.writestr(f"app/f{i}.py", "x = 1\n")
    return buf.getvalue()


class _FakeSmoke:
    """The smoke client's surface the cycle drives, scripted."""

    TRANSIENT = {502, 503, 504}
    BuildDeadline = _load("post_deploy_smoke_for_cycle", SMOKE_PATH).BuildDeadline

    def __init__(self, package_answers, states, deadline_in_s=None):
        self.package_answers = list(package_answers)
        self.states = list(states)
        self.deadline_in_s = deadline_in_s
        self.calls = []

    def req(self, method, path, body=None, token=None, raw=False, **kw):
        self.calls.append((method, path, token))
        if path == "/v1/sessions/":
            return 200, {"session_id": "sess_x"}
        if path.endswith("/product/package"):
            return self.package_answers.pop(0)
        if path.endswith("/product/build-status"):
            state = self.states.pop(0) if self.states else "building"
            build = {"state": state, "phases_done": 2, "phases_total": 5}
            if self.deadline_in_s is not None:
                build["deadline"] = {"deadline_in_s": self.deadline_in_s}
            return 200, {"build": build}
        return 200, {}

    def chat_until_drafted(self, sid, tok, brief):
        return "blueprint", 1

    def chat(self, sid, tok, msg, action=None, value=None, **kw):
        return "generation"


def test_a_gateway_timeout_while_polling_is_not_the_builds_verdict(cycle):
    """Live 2026-10-08: a 504 on /product/package during a platform restart
    ended the repro as 'exported False' while the build itself was still
    running. A transient answer is a reason to keep polling, never a verdict."""
    blob = _zip_with_manifest(
        {"certified": True, "build_level": {"build_level": "production"}, "advisory_checks": []}
    )
    smoke = _FakeSmoke(
        package_answers=[(409, b"{}"), (504, b"bad gateway"), (409, b"{}"), (200, blob)],
        states=["building", "building", "building", "succeeded"],
    )
    out = cycle.drive_build(
        smoke, "tok-1", "a brief", level="production", wait_s=60, poll_s=0,
    )
    assert out["status"] == "exported", out
    assert out["package_http"] == 200
    assert out["export"]["bytes"] == len(blob)
    assert out["export"]["manifest"]["certified"] is True
    assert all(token == "tok-1" for _m, _p, token in smoke.calls)


def test_a_repro_waits_on_the_builds_own_deadline_not_its_budget(cycle):
    """Owner rule: timeouts at the phase-wall ceiling only. wait_s=0 would end
    the repro on the first poll; the build declares an hour left."""
    blob = _zip_with_manifest({"certified": True, "build_level": {}, "advisory_checks": []})
    smoke = _FakeSmoke(
        package_answers=[(409, b"{}"), (409, b"{}"), (200, blob)],
        states=["building", "building", "succeeded"],
        deadline_in_s=3600,
    )
    out = cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=0, poll_s=0)
    assert out["status"] == "exported", out


def test_a_repro_stops_when_the_builds_own_deadline_has_passed(cycle):
    smoke = _FakeSmoke(
        package_answers=[(409, b"{}")] * 5,
        states=["building"] * 5,
        deadline_in_s=-1,
    )
    out = cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=3600, poll_s=0)
    assert out["package_http"] == 409
    assert len([c for c in smoke.calls if c[1].endswith("/product/package")]) == 1


def test_a_failed_build_is_reported_with_the_servers_reason(cycle):
    smoke = _FakeSmoke(
        package_answers=[(409, json.dumps({"detail": "gate X failed"}).encode())],
        states=["failed"],
    )
    out = cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=60, poll_s=0)
    assert out["status"] == "failed"
    assert "gate X failed" in out["detail"]


# -- the export summary and the report ---------------------------------------


def test_the_export_summary_reads_the_manifest_inside_the_zip(cycle):
    blob = _zip_with_manifest(
        {
            "certified": True,
            "build_level": {"build_level": "production", "stop_gate": "STORE"},
            "advisory_checks": [{"check": "c", "reason": "r"}],
        },
        extra=4,
    )
    summary = cycle.summarize_export(blob)
    assert summary == {
        "bytes": len(blob),
        "files": 5,
        "manifest": {
            "certified": True,
            "build_level": {"build_level": "production", "stop_gate": "STORE"},
            "advisory_checks": [{"check": "c", "reason": "r"}],
        },
    }


def _repro(status="exported", certified=True):
    return {
        "status": status,
        "account_index": 1,
        "session_id": "sess_x",
        "package_http": 200 if status == "exported" else 409,
        "export": {"bytes": 10, "files": 6, "manifest": {"certified": certified}} if status == "exported" else None,
        "wall_s": 100.0,
    }


def test_the_report_carries_every_run_and_the_cycle_time(cycle):
    report = cycle.build_report(
        smoke_a={"status": "pass", "wall_s": 10.0},
        repros={"one": _repro(), "two": _repro()},
        smoke_b={"status": "pass", "wall_s": 12.0},
        started_at=1000.0,
        finished_at=1000.0 + 3600.0,
        commit="abc123",
        live_sha="abc123",
    )
    assert report["verdict"] == "pass"
    assert report["commit"] == "abc123"
    assert report["live_sha"] == "abc123"
    assert report["cycle_s"] == 3600.0
    # The report records cycle time only -- no target, no pass/miss on time.
    assert "target_s" not in report and "within_target" not in report
    assert "target" not in cycle.render_markdown(report).lower()
    assert set(report["runs"]) == {"smoke_a", "one", "two", "smoke_b"}
    assert report["runs"]["one"]["export"]["manifest"]["certified"] is True


def test_an_uncertified_export_or_a_red_smoke_fails_the_cycle(cycle):
    common = dict(started_at=0.0, finished_at=100.0, commit="c", live_sha="c")
    assert cycle.build_report(
        smoke_a={"status": "pass"}, smoke_b={"status": "pass"},
        repros={"one": _repro(certified=False)}, **common,
    )["verdict"] == "fail"
    assert cycle.build_report(
        smoke_a={"status": "fail"}, smoke_b={"status": "pass"},
        repros={"one": _repro()}, **common,
    )["verdict"] == "fail"
    assert cycle.build_report(
        smoke_a={"status": "pass"}, smoke_b={"status": "pass"},
        repros={"one": _repro(status="failed")}, **common,
    )["verdict"] == "fail"


def test_a_cycle_whose_live_commit_moved_or_is_unknown_fails_closed(cycle):
    """The report names ONE commit. If live /version is not that commit when
    the report is written (a redeploy mid-cycle, a dead backend), the runs
    were not all on it, so the cycle cannot pass."""
    good = dict(
        smoke_a={"status": "pass"}, smoke_b={"status": "pass"},
        repros={"one": _repro()}, started_at=0.0, finished_at=1.0, commit="abc",
    )
    moved = cycle.build_report(live_sha="def", **good)
    assert moved["verdict"] == "fail" and "def" in moved["reason"]
    unknown = cycle.build_report(live_sha=None, **good)
    assert unknown["verdict"] == "fail"
    assert cycle.build_report(live_sha="abc", **good)["verdict"] == "pass"


# -- the live commit ---------------------------------------------------------


class _VersionSmoke:
    TRANSIENT = {502, 503, 504}

    def __init__(self, answers):
        self.answers = list(answers)

    def req(self, method, path, *a, **k):
        assert (method, path) == ("GET", "/version")
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def test_the_live_sha_is_read_off_version(cycle):
    smoke = _VersionSmoke([(200, {"git_sha": "abc123"})])
    assert cycle.read_live_sha(smoke) == "abc123"
    assert cycle.read_live_sha(_VersionSmoke([(502, b"gw")])) is None


def test_waiting_for_the_expected_sha_rides_out_a_restart(cycle):
    smoke = _VersionSmoke(
        [(502, b"gw"), (200, {"git_sha": "old"}), (200, {"git_sha": "abc123"})]
    )
    assert cycle.wait_for_live_sha(smoke, "abc123", wait_s=5, poll_s=0) == "abc123"


def test_waiting_for_the_expected_sha_fails_closed(cycle):
    smoke = _VersionSmoke([(200, {"git_sha": "old"})])
    with pytest.raises(cycle.CycleError) as exc:
        cycle.wait_for_live_sha(smoke, "abc123", wait_s=0, poll_s=0)
    assert "old" in str(exc.value) and "abc123" in str(exc.value)


def test_a_prefix_of_the_live_sha_is_never_accepted_as_it(cycle):
    smoke = _VersionSmoke([(200, {"git_sha": "abc123ff"})])
    with pytest.raises(cycle.CycleError):
        cycle.wait_for_live_sha(smoke, "abc123", wait_s=0, poll_s=0)


# -- the briefs are data -----------------------------------------------------


def test_the_briefs_come_from_the_config_file_not_the_code(cycle):
    config = cycle.load_config(CONFIG_PATH)
    names = [r["name"] for r in config["anchors"]]
    assert len(names) == len(set(names)) >= 1
    source = CYCLE_PATH.read_text(encoding="utf-8")
    for anchor in config["anchors"]:
        assert anchor["brief"] not in source


def test_a_config_entry_without_a_brief_is_refused(cycle, tmp_path):
    bad = tmp_path / "c.json"
    bad.write_text(
        json.dumps({
            "anchors": [{"name": "x"}],
            "rotation": {"pool": "p.json"},
            "user_build_slots": 2,
        }),
        encoding="utf-8",
    )
    with pytest.raises(cycle.CycleError):
        cycle.load_config(bad)


# -- the roster client -------------------------------------------------------


def test_the_roster_is_asked_of_the_server_by_count(monkeypatch):
    smoke = _load("post_deploy_smoke", SMOKE_PATH)
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "gate")
    seen = {}

    def req(method, path, body=None, **kw):
        seen["body"] = body
        return 200, {"login_tokens": ["t0", "t1", "t2"], "login_token": "t0"}

    smoke.req = req
    assert smoke.verified_token_roster(3) == ["t0", "t1", "t2"]
    assert seen["body"] == {"principals": 3}


def test_an_older_server_yields_its_pair_and_the_cycle_sees_it_is_short(monkeypatch):
    smoke = _load("post_deploy_smoke", SMOKE_PATH)
    monkeypatch.setenv("SMOKE_GATE_TOKEN", "gate")
    smoke.req = lambda *a, **k: (200, {"login_token": "t0", "login_token_b": "t1"})
    assert smoke.verified_token_roster(3) == ["t0", "t1"]


# -- the workflow ------------------------------------------------------------


def _workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _needs(job):
    needs = job.get("needs") or []
    return {needs} if isinstance(needs, str) else set(needs)


def _on(wf):
    # YAML 1.1 reads a bare `on:` key as boolean True.
    return wf.get("on", wf.get(True))


#: The phase-wall ceiling a build may take (post_deploy_smoke.BUILD_WAIT_S
#: default) plus the wait for the deployed commit to serve (SMOKE_READY_WAIT_S
#: in the workflow), in minutes. A job that runs a build must outlast both.
BUILD_CEILING_MIN = (5400 + 1200) // 60

CYCLE_JOBS = ("resolve", "live-smoke", "release-repros", "smoke-b")


def test_the_cycle_is_one_github_workflow_with_the_right_order():
    """The whole cycle -- resolve the live commit, smoke A beside the repros,
    smoke B after both, the report -- is jobs on GitHub Actions. Nothing waits
    on a laptop process."""
    jobs = _workflow()["jobs"]
    for name in CYCLE_JOBS:
        assert name in jobs, name
    # Smoke A and the repros both wait ONLY for the resolved commit, so they
    # start together; neither waits for the other.
    assert _needs(jobs["live-smoke"]) == {"resolve"}
    assert _needs(jobs["release-repros"]) == {"resolve"}
    # Smoke B (and the report) after smoke A AND every repro.
    assert _needs(jobs["smoke-b"]) == {"resolve", "live-smoke", "release-repros"}


def test_a_cycle_can_be_started_on_demand_against_the_live_commit():
    on = _on(_workflow())
    assert "workflow_run" in on, "a deploy still starts a cycle"
    dispatch = on["workflow_dispatch"]
    inputs = (dispatch or {}).get("inputs") or {}
    assert "sha" in inputs
    assert inputs["sha"].get("required") is False, "empty = whatever is live now"


def test_every_job_runs_the_commit_the_resolve_job_verified_live():
    wf = _workflow()
    jobs = wf["jobs"]
    resolve = jobs["resolve"]
    assert "sha" in (resolve.get("outputs") or {})
    text = yaml.safe_dump(resolve)
    assert "scripts/release_cycle.py live-sha" in text
    for name in ("live-smoke", "release-repros", "smoke-b"):
        body = yaml.safe_dump(jobs[name])
        assert "needs.resolve.outputs.sha" in body, name
        for step in jobs[name]["steps"]:
            if step.get("uses", "").startswith("actions/checkout"):
                assert step["with"]["ref"] == "${{ needs.resolve.outputs.sha }}", name
    # The report re-reads live /version and records it beside the commit.
    report = [s for s in jobs["smoke-b"]["steps"] if "release_cycle.py report" in (s.get("run") or "")]
    assert report and "--base" in report[0]["run"]


def test_build_jobs_outlast_the_phase_wall_ceiling():
    jobs = _workflow()["jobs"]
    for name in ("live-smoke", "release-repros", "smoke-b"):
        assert int(jobs[name]["timeout-minutes"]) >= BUILD_CEILING_MIN, name
    assert int(jobs["resolve"]["timeout-minutes"]) >= 1200 // 60


def test_build_jobs_outlast_the_builds_own_declared_deadline():
    """A poller now waits on build-status ``deadline`` (run ceiling, then the
    Store-gate wall), so the job must outlast ready wait + both."""
    import sys

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.factory.build.n3_store_gate import DEFAULT_WALL_S
    from app.factory.build.runner import BuildBudget

    need_min = (1200 + BuildBudget().hard_ceiling_s + DEFAULT_WALL_S) / 60
    jobs = _workflow()["jobs"]
    for name in ("live-smoke", "release-repros", "smoke-b"):
        assert int(jobs[name]["timeout-minutes"]) >= need_min, name


def test_two_cycles_never_overlap_and_a_running_one_is_never_cancelled():
    concurrency = _workflow()["concurrency"]
    assert concurrency["group"]
    assert concurrency.get("cancel-in-progress") is False


def test_exports_manifests_and_the_report_upload_even_when_a_run_fails():
    jobs = _workflow()["jobs"]
    uploads = {
        name: [
            s for s in jobs[name]["steps"]
            if s.get("uses", "").startswith("actions/upload-artifact")
        ]
        for name in ("release-repros", "smoke-b")
    }
    for name, steps in uploads.items():
        assert steps, name
        for step in steps:
            assert step.get("if") == "always()", (name, step)
    repro_paths = uploads["release-repros"][0]["with"]["path"]
    assert "cycle" in repro_paths  # zips + MANIFEST_*.json + repros.json
    report_paths = uploads["smoke-b"][0]["with"]["path"]
    assert "cycle_report.json" in report_paths
    # The report job runs even when a run failed, so a red cycle still
    # writes its report -- only an unresolved commit stops it.
    assert "always()" in jobs["smoke-b"]["if"]
    assert "needs.resolve.result == 'success'" in jobs["smoke-b"]["if"]


def test_the_cycle_path_touches_no_local_only_state():
    """Every run step calls a script in this repository against a URL; no
    path, host or helper that exists only on one machine."""
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    for local in ("C:\\", "C:/", "AppData", "scratchpad", "localhost", "127.0.0.1", "~/", "/Users/", "/home/"):
        assert local not in text, local
    jobs = _workflow()["jobs"]
    for name in CYCLE_JOBS:
        job = jobs[name]
        assert job["runs-on"] == "ubuntu-latest", name
        for step in job["steps"]:
            run = step.get("run") or ""
            for line in run.splitlines():
                if "python3 " in line:
                    assert "python3 scripts/" in line, (name, line)


def test_the_cycle_reads_only_secrets_that_already_exist():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    import re

    used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
    assert used <= {"SMOKE_GATE_TOKEN", "SMOKE_EMAIL", "SMOKE_PASSWORD", "GITHUB_TOKEN"}, used


def test_the_workflow_runs_the_cycle_script_for_the_repros_and_the_report():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/release_cycle.py repros" in text
    assert "scripts/release_cycle.py report" in text


def test_report_download_follows_the_redirect_without_the_token(cycle):
    """The artifact API redirects to a signed URL that refuses a bearer token:
    the redirect is followed, and the token never reaches the second host."""
    import http.server

    seen = {}
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as zf:
        zf.writestr(cycle.REPORT_NAME, json.dumps({"verdict": "pass"}))
    payload = blob.getvalue()

    class Signed(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):  # noqa: ANN002
            pass

    signed = http.server.HTTPServer(("127.0.0.1", 0), Signed)

    class Api(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen["api_auth"] = self.headers.get("Authorization")
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{signed.server_port}/blob")
            self.end_headers()

        def log_message(self, *a):  # noqa: ANN002
            pass

    api = http.server.HTTPServer(("127.0.0.1", 0), Api)
    threads = [threading.Thread(target=s.handle_request, daemon=True) for s in (api, signed)]
    for t in threads:
        t.start()
    try:
        report = cycle._github_report(f"http://127.0.0.1:{api.server_port}/zip", "tok-123")
    finally:
        api.server_close()
        signed.server_close()
    assert seen["api_auth"] == "Bearer tok-123"
    assert seen["auth"] is None
    assert report == {"verdict": "pass"}
