"""A certification the gate later proves false is revoked as DATA.

Live 2026-10-08: build/plt_5ac16f50c9384536 was certified 22/22 by run
37767964892, whose older harness ran from the mounted checkout and judged the
checkout (which carries vendor/) instead of the image (which does not). The
revocation record names branch, sha, run, reason and evidence; nothing in code
names a branch.

* the replay's "select certified builds" skips a revoked run;
* ``revocations.py apply`` withdraws the ``store-gate`` commit status the run
  wrote (the record the Factory and the replay already read) -- only while the
  revoked run is still the branch's newest completed gate run, so a re-gated
  branch (a newer run) is never overwritten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import replay_select  # noqa: E402
import revocations  # noqa: E402

WORKFLOW = HERE.parent / "workflows" / "revoke-certifications.yml"


def _entry(**kw):
    row = {
        "branch": "build/plt_0000000000000001",
        "sha": "a" * 40,
        "run_id": 111,
        "reason": "older harness judged the checkout",
        "evidence": "https://example.invalid/run/111",
    }
    row.update(kw)
    return row


def _record(tmp_path: Path, rows) -> Path:
    path = tmp_path / "revoked_certifications.json"
    path.write_text(json.dumps({"revoked": rows}), encoding="utf-8")
    return path


def test_the_committed_record_is_valid_and_names_the_live_false_certification():
    rows = revocations.load()
    assert rows, "the record must carry the live false certification"
    for row in rows:
        assert revocations.problems(row) == [], row
    assert any(r["run_id"] == 37767964892 for r in rows)


def test_a_false_regate_with_the_branch_s_stale_gate_is_revoked_too():
    """Live 2026-10-10, run 38040326004: the revoked branch was re-gated by a
    dispatch that ran the branch's own pre-fix gate code, and a second false
    22/22 superseded the revocation (``apply`` withdraws only the branch's
    newest run). That run is recorded too, so the record withdraws the status
    again whichever false run is the newest."""
    rows = revocations.load()
    assert any(r["run_id"] == 38040326004 for r in rows)
    newest = {}
    for row in rows:
        newest[row["branch"]] = max(newest.get(row["branch"], 0), row["run_id"])
    written = []
    out = revocations.apply(
        rows, newest_run_id=newest.get, write_status=lambda *a: written.append(a)
    )
    assert sorted(set(out["withdrawn"])) == sorted(newest)
    assert {w[0] for w in written} == {r["sha"] for r in rows if r["run_id"] == newest[r["branch"]]}
    for row in rows:
        assert revocations.revoked_run_ids(rows) >= {row["run_id"]}


def test_an_entry_missing_a_field_is_refused(tmp_path):
    bad = _entry()
    del bad["evidence"]
    with pytest.raises(ValueError):
        revocations.load(_record(tmp_path, [bad]))


def test_no_branch_name_lives_in_the_code():
    for name in ("revocations.py", "replay_select.py"):
        assert "plt_" not in (HERE / name).read_text(encoding="utf-8")


def _run(branch, sha, run_id, conclusion="success"):
    return {
        "status": "completed", "head_branch": branch, "head_sha": sha,
        "id": run_id, "conclusion": conclusion, "created_at": "2026-10-08T11:07:12Z",
    }


def test_the_replay_skips_a_revoked_certification():
    runs = [_run("build/plt_x", "a" * 40, 111), _run("build/plt_y", "b" * 40, 112)]
    picked = replay_select.certified(
        runs, lambda sha: "success", lambda b: True, 3, revoked_runs={111}
    )
    assert [p["branch"] for p in picked] == ["build/plt_y"]


def test_a_regated_branch_is_selected_again():
    # A newer successful run on the same branch is a new certification.
    runs = [_run("build/plt_x", "a" * 40, 222), _run("build/plt_x", "a" * 40, 111)]
    picked = replay_select.certified(
        runs, lambda sha: "success", lambda b: True, 3, revoked_runs={111}
    )
    assert [p["run_id"] for p in picked] == [222]


def test_apply_withdraws_the_status_only_while_the_revoked_run_is_newest():
    rows = [_entry(), _entry(branch="build/plt_0000000000000002", sha="b" * 40, run_id=333)]
    newest = {"build/plt_0000000000000001": 111, "build/plt_0000000000000002": 444}
    written = []
    out = revocations.apply(
        rows, newest_run_id=newest.get, write_status=lambda sha, state, desc, url: written.append((sha, state, desc, url))
    )
    assert written == [(
        "a" * 40, "failure",
        "certification revoked: older harness judged the checkout",
        "https://example.invalid/run/111",
    )]
    assert out == {"withdrawn": ["build/plt_0000000000000001"], "superseded": ["build/plt_0000000000000002"]}


def test_the_status_description_fits_github_s_limit():
    long = _entry(reason="x" * 500)
    written = []
    revocations.apply([long], newest_run_id=lambda b: 111, write_status=lambda *a: written.append(a))
    assert len(written[0][2]) <= revocations.DESCRIPTION_MAX


def test_the_workflow_applies_the_record_on_change_and_on_demand():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    on = doc.get("on") or doc.get(True)
    assert "workflow_dispatch" in on
    assert ".github/store_gate/revoked_certifications.json" in on["push"]["paths"]
    perms = doc["permissions"]
    assert perms["statuses"] == "write" and perms["actions"] == "read"
    runs = " ".join(str(s.get("run") or "") for job in doc["jobs"].values() for s in job["steps"])
    assert "revocations.py apply" in runs
