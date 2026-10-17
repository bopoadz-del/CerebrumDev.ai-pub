"""Gate replay (owner rule, 2026-10-08): a gate change is replayed before merge
on the newest N build branches this gate certified, and fails when it fails
any of them.

Selection reads the gate's own record (runs + the store-gate status), never a
name; the flip rule fails certified -> red and only that; the workflow runs
the real gate (store-gate.yml, called), never a copy of its steps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import replay_flips
import replay_select

WORKFLOWS = HERE.parent / "workflows"


def _run(branch, sha, conclusion="success", status="completed", rid=1, at="2026-10-07T00:00:00Z"):
    return {
        "id": rid,
        "head_branch": branch,
        "head_sha": sha,
        "status": status,
        "conclusion": conclusion,
        "created_at": at,
    }


def _green(_sha):
    return "success"


def _exists(_branch):
    return True


# -- selection --------------------------------------------------------------------


def test_only_certified_branches_are_selected():
    runs = [
        _run("build/plt_a", "a" * 40, rid=5),
        _run("build/plt_b", "b" * 40, conclusion="failure", rid=4),
        _run("build/plt_c", "c" * 40, rid=3),
    ]
    statuses = {"a" * 40: "success", "c" * 40: "failure"}
    picked = replay_select.certified(runs, lambda s: statuses.get(s, ""), _exists, 3)
    assert [p["branch"] for p in picked] == ["build/plt_a"]


def test_a_branch_counts_by_its_newest_completed_run():
    runs = [
        _run("build/plt_a", "a2" * 20, status="in_progress", conclusion=None, rid=9),
        _run("build/plt_a", "a1" * 20, conclusion="failure", rid=8),
        _run("build/plt_a", "a0" * 20, conclusion="success", rid=7),
    ]
    # Its newest verdict is red: not certified, whatever it scored before.
    assert replay_select.certified(runs, _green, _exists, 3) == []


def test_the_newest_n_are_selected_newest_first():
    runs = [_run(f"build/plt_{i}", f"{i:040d}", rid=100 - i) for i in range(6)]
    picked = replay_select.certified(runs, _green, _exists, 2)
    assert [p["branch"] for p in picked] == ["build/plt_0", "build/plt_1"]
    assert picked[0]["sha"] == f"{0:040d}"


def test_non_build_refs_and_deleted_branches_are_skipped():
    runs = [_run("main", "m" * 40, rid=3), _run("build/plt_gone", "g" * 40, rid=2), _run("build/plt_x", "x" * 40, rid=1)]
    picked = replay_select.certified(runs, _green, lambda b: b != "build/plt_gone", 3)
    assert [p["branch"] for p in picked] == ["build/plt_x"]


def test_n_comes_from_the_config(tmp_path):
    cfg = tmp_path / "gate_replay.json"
    cfg.write_text(json.dumps({"certified_branches": 5}), encoding="utf-8")
    assert replay_select.configured_n({}, cfg) == 5
    assert replay_select.configured_n({"GATE_REPLAY_N": "2"}, cfg) == 2
    assert replay_select.configured_n({"GATE_REPLAY_N": ""}, cfg) == 5


def test_n_defaults_to_three(tmp_path):
    assert replay_select.configured_n({}, tmp_path / "absent.json") == 3
    bad = tmp_path / "bad.json"
    bad.write_text('{"certified_branches": 0}', encoding="utf-8")
    assert replay_select.configured_n({"GATE_REPLAY_N": "x"}, bad) == 3


def test_the_repo_config_is_read():
    want = json.loads((HERE.parent / "gate_replay.json").read_text(encoding="utf-8"))["certified_branches"]
    assert replay_select.configured_n({}) == want


def test_labels_are_artifact_safe():
    assert replay_select.label("build/plt_9f7fac60d89e4a80") == "plt_9f7fac60d89e4a80"
    assert "/" not in replay_select.label("build/sess_x/y")


# -- the flip rule ------------------------------------------------------------------


def _result(failing=(), ok=None, score="22/22"):
    names = ["no_token_401", "audit_clean", "ci_present_and_full_suite"]
    return {
        "ok": (not failing) if ok is None else ok,
        "score": score,
        "lines": [{"name": n, "status": "FAIL" if n in failing else "PASS"} for n in names],
    }


def test_certified_to_red_is_a_flip_naming_the_checks():
    why = replay_flips.flip(True, _result(failing=("ci_present_and_full_suite",), score="21/22"))
    assert why and "ci_present_and_full_suite" in why and "21/22" in why


def test_certified_still_green_is_no_flip():
    assert replay_flips.flip(True, _result()) is None


def test_red_to_red_is_not_a_flip():
    assert replay_flips.flip(False, _result(failing=("audit_clean",))) is None


def test_a_missing_result_on_a_certified_build_is_a_flip():
    assert replay_flips.flip(True, None)


def test_a_factory_that_routes_a_certified_result_off_green_is_a_flip():
    why = replay_flips.flip(True, _result(), {"ok": False, "honesty": "N3_STORE_GATE_FACTORY_OWED", "detail": "x"})
    assert why and "N3_STORE_GATE_FACTORY_OWED" in why


def test_the_cli_fails_on_a_flip_and_passes_when_all_hold(tmp_path):
    sel = [
        {"branch": "build/plt_a", "sha": "a" * 40, "label": "plt_a"},
        {"branch": "build/plt_b", "sha": "b" * 40, "label": "plt_b"},
    ]
    (tmp_path / "selection.json").write_text(json.dumps(sel), encoding="utf-8")
    results = tmp_path / "results"
    for label, res in (("plt_a", _result()), ("plt_b", _result())):
        d = results / f"{replay_flips.ARTIFACT_PREFIX}{label}"
        d.mkdir(parents=True)
        (d / "store_gate.json").write_text(json.dumps(res), encoding="utf-8")
    assert replay_flips.main([str(tmp_path / "selection.json"), str(results)]) == 0
    (results / f"{replay_flips.ARTIFACT_PREFIX}plt_b" / "store_gate.json").write_text(
        json.dumps(_result(failing=("audit_clean",), score="21/22")), encoding="utf-8"
    )
    assert replay_flips.main([str(tmp_path / "selection.json"), str(results)]) == 1


def test_an_empty_selection_never_passes(tmp_path):
    (tmp_path / "selection.json").write_text("[]", encoding="utf-8")
    assert replay_flips.main([str(tmp_path / "selection.json"), str(tmp_path)]) == 1


# -- the workflows --------------------------------------------------------------------


def _wf(name):
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    data["on"] = data.get("on", data.get(True))
    return data


def test_a_gate_change_triggers_the_replay():
    paths = set(_wf("gate-replay.yml")["on"]["pull_request"]["paths"])
    assert {".github/store_gate/**", ".github/workflows/store-gate.yml", ".github/gate_replay.json"} <= paths
    # The scripts' own unit tests run on the same paths: every gate change is
    # tested AND replayed.
    assert set(_wf("store-gate-tests.yml")["on"]["pull_request"]["paths"]) <= paths | {
        ".github/workflows/store-gate-tests.yml"
    }


def test_the_replay_calls_the_real_gate_never_a_copy():
    jobs = _wf("gate-replay.yml")["jobs"]
    assert jobs["replay"]["uses"] == "./.github/workflows/store-gate.yml"
    for name, job in jobs.items():
        runs = " ".join(str(s.get("run") or "") for s in job.get("steps") or [])
        assert "docker build" not in runs and "acceptance.py inside" not in runs, name


def test_the_replay_has_one_named_verdict_check():
    jobs = _wf("gate-replay.yml")["jobs"]
    verdict = jobs["verdict"]
    assert verdict["name"] == "gate replay (certified builds)"
    assert set(verdict["needs"]) >= {"select", "replay"}
    assert "always()" in str(verdict["if"])
    assert "replay_flips.py" in " ".join(str(s.get("run") or "") for s in verdict["steps"])


def test_the_gate_is_callable_and_a_replay_never_writes_the_builds_status():
    gate = _wf("store-gate.yml")
    on = gate["on"]
    assert "workflow_dispatch" in on and "workflow_call" in on
    assert "push" not in on and "pull_request" not in on
    steps = gate["jobs"]["store-gate"]["steps"]
    status = next(s for s in steps if s.get("name") == "Write commit status")
    assert "!inputs.replay_label" in str(status["if"])
    upload = next(s for s in steps if s.get("name") == "Upload k/N artifact")
    assert replay_flips.ARTIFACT_PREFIX in upload["with"]["name"]



def test_the_gates_service_containers_never_pull_anonymously_from_docker_hub():
    """Live 2026-10-09 20:59-21:04 UTC: Docker Hub answered 429 to the
    runners, every replay leg died at "Initialize containers" (the postgres
    service pull) and every gate step was skipped -- "no score" that judged
    nothing. Service images come from a mirror of the same official image
    that is not under Docker Hub's anonymous pull limit."""
    gate = _wf("store-gate.yml")
    for job in gate["jobs"].values():
        for name, svc in (job.get("services") or {}).items():
            image = str(svc.get("image") or "")
            registry = image.split("/", 1)[0] if "/" in image else ""
            assert "." in registry and registry != "docker.io", f"service {name} pulls {image!r} from Docker Hub"

# -- known-bad fixtures (owner rule, 2026-10-08) ----------------------------------------
#
# A replay passes only if every certified build still certifies AND every
# known-bad fixture still fails on its expected check. A fixture that fails for
# another reason was not caught by the check it exists to prove.


def _fixture(**over):
    row = {
        "branch": "known-bad/x",
        "sha": "f" * 40,
        "expected_failing_check": "audit_clean",
        "reason": "r",
    }
    row.update(over)
    return row


def test_known_bad_fixtures_join_the_selection_marked_to_fail():
    rows = replay_select.known_bad({"fixtures": [_fixture()]}, _exists)
    assert rows == [
        {
            "branch": "known-bad/x",
            "sha": "f" * 40,
            "label": "known-bad-x",
            "expect": "fail",
            "expected_check": "audit_clean",
        }
    ]


def test_a_fixture_that_cannot_be_replayed_is_an_error_not_a_skip():
    import pytest

    with pytest.raises(ValueError, match="expected_failing_check"):
        replay_select.known_bad({"fixtures": [_fixture(expected_failing_check="")]}, _exists)
    with pytest.raises(ValueError, match="sha"):
        replay_select.known_bad({"fixtures": [_fixture(sha="abc")]}, _exists)
    with pytest.raises(ValueError, match="does not exist"):
        replay_select.known_bad({"fixtures": [_fixture()]}, lambda _b: False)


def test_a_fixture_never_sits_on_a_build_branch():
    import pytest

    with pytest.raises(ValueError, match="build/"):
        replay_select.known_bad({"fixtures": [_fixture(branch="build/plt_x")]}, _exists)


def test_the_repo_fixture_list_is_valid_data():
    data = replay_select.read_known_bad()
    rows = replay_select.known_bad(data, _exists)
    assert rows and all(r["expect"] == "fail" for r in rows)


def test_known_bad_failing_its_expected_check_is_caught():
    assert replay_flips.missed("audit_clean", _result(failing=("audit_clean",), score="21/22")) is None


def test_known_bad_that_passes_is_missed():
    why = replay_flips.missed("audit_clean", _result())
    assert why and "passed" in why


def test_known_bad_failing_another_check_is_not_caught():
    why = replay_flips.missed("audit_clean", _result(failing=("no_token_401",), score="21/22"))
    assert why and "no_token_401" in why and "audit_clean" in why


def test_known_bad_without_a_result_is_missed():
    assert replay_flips.missed("audit_clean", None)


def test_a_factory_that_routes_a_known_bad_green_misses_it():
    why = replay_flips.missed(
        "audit_clean", _result(failing=("audit_clean",), score="21/22"), {"ok": True}
    )
    assert why and "Factory" in why


def _write_results(results, rows):
    for label, res in rows:
        d = results / f"{replay_flips.ARTIFACT_PREFIX}{label}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "store_gate.json").write_text(json.dumps(res), encoding="utf-8")


def test_the_cli_needs_certified_held_and_known_bad_caught(tmp_path):
    sel = [
        {"branch": "build/plt_a", "sha": "a" * 40, "label": "plt_a"},
        {
            "branch": "known-bad/x",
            "sha": "f" * 40,
            "label": "known-bad-x",
            "expect": "fail",
            "expected_check": "audit_clean",
        },
    ]
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(sel), encoding="utf-8")
    results = tmp_path / "results"
    _write_results(results, [("plt_a", _result()), ("known-bad-x", _result(failing=("audit_clean",), score="21/22"))])
    assert replay_flips.main([str(path), str(results)]) == 0
    # The gate under change lets the known-bad build through: the replay fails.
    _write_results(results, [("known-bad-x", _result())])
    assert replay_flips.main([str(path), str(results)]) == 1


def test_known_bad_rows_alone_never_pass(tmp_path):
    sel = [{"branch": "known-bad/x", "sha": "f" * 40, "label": "known-bad-x", "expect": "fail", "expected_check": "audit_clean"}]
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(sel), encoding="utf-8")
    results = tmp_path / "results"
    _write_results(results, [("known-bad-x", _result(failing=("audit_clean",)))])
    assert replay_flips.main([str(path), str(results)]) == 1


def test_a_replays_score_never_fails_its_job_but_a_dispatched_gate_keeps_the_exit():
    steps = _wf("store-gate.yml")["jobs"]["store-gate"]["steps"]
    run = next(s for s in steps if s.get("name") == "Run scripts/acceptance.py inside the image")["run"]
    tail = run.rstrip().splitlines()[-6:]
    assert any("| tee acceptance.out || rc=$?" in line for line in tail)
    assert any('if [ -n "${REPLAY_LABEL:-}" ]; then exit 0; fi' in line for line in tail)
    assert tail[-1].strip() == 'exit "${rc:-0}"'
