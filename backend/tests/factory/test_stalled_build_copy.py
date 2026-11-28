"""A build that stalled can be taken as a copy, by one rule at three doors.

A build whose server restarted under it reads ``stalled``. The action list
offered it only "Try again" and "Start over", although the package route
would have served the as-is copy: the owner could not download the work.
The list, the typed ``take_copy`` and the route's ``as_is`` download now
share ``floor_actions.as_is_copy_offered``.
"""

from __future__ import annotations

import io
import json
import os
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core.session_store import create_session, get_session, update_session
from app.factory import build_jobs, platform_chat_flow
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.floor_actions import allowed_build_actions, as_is_copy_offered
from app.main import app
from tests.factory.blocks_root import real_blocks_root


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(real_blocks_root()))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def _workspace(tmp_path, sid, *, idle_s=0.0, failed=""):
    """A runner workspace wired into a session: a WRITER phase started and
    then nothing for ``idle_s`` seconds, or a run that failed with ``failed``."""
    create_session(sid, "tester")
    out = tmp_path / f"{sid}-product"
    (out / "app").mkdir(parents=True)
    (out / "README.md").write_text("the tree as the build left it", encoding="utf-8")
    (out / "app" / "half_written.py").write_text("VALUE = 1\n", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=sid, inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    if failed:
        ledger.append(EventKind.GATE_FAILED, role=BuildRole.WRITER, detail=failed, payload={"gate": "writer_gate"})
        ledger.append(EventKind.RUN_FAILED, role=BuildRole.WRITER, detail=failed, payload={"outcome": "FAILED_GATE"})
    if idle_s:
        old = time.time() - idle_s
        os.utime(out / "build_ledger.jsonl", (old, old))
    state = get_session(sid)
    state.product_design.generation = {
        "output_dir": str(out), "product_id": sid, "inputs_hash": "abc", "engine": "runner",
    }
    update_session(sid, state)
    return out


def _read(client, sid):
    reply = client.get(f"/v1/sessions/{sid}/product/build-status").json()
    return reply["build"], reply["actions"]


ORPHANED = build_jobs._ORPHAN_AFTER_S + 60
QUIET = build_jobs._STALL_AFTER_S + 120


@pytest.mark.parametrize("idle_s", [ORPHANED, QUIET])
def test_a_stalled_build_nothing_is_working_on_can_be_downloaded(client, tmp_path, idle_s):
    """The server restarted under the build (or it went quiet with no runner
    left): the copy is offered, and the download serves the tree as it
    stood, labelled as stopped, never as failed its gates."""
    sid = f"sess_stalled_copy_{int(idle_s)}"
    _workspace(tmp_path, sid, idle_s=idle_s)
    build, actions = _read(client, sid)
    assert build["state"] == "stalled" and not build.get("runner_live")
    assert actions == ["try_again", "download_copy", "start_over"]

    pkg = client.get(f"/v1/sessions/{sid}/product/package?as_is=1")
    assert pkg.status_code == 200, pkg.text
    disposition = pkg.headers.get("content-disposition", "")
    assert "STOPPED-as-is" in disposition and "FAILED" not in disposition
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    assert {"README.md", "app/half_written.py", "EXPORTED-AS-IS.md", "MANIFEST.json"} <= set(zf.namelist())
    note = zf.read("EXPORTED-AS-IS.md").decode("utf-8")
    assert "THIS BUILD STOPPED BEFORE IT FINISHED" in note and "FAILED ITS GATES" not in note
    assert f"Why it stopped: {build['detail']}" in note
    manifest = json.loads(zf.read("MANIFEST.json"))
    assert manifest["certified"] is False and manifest["build_state"] == "stalled"


@pytest.mark.parametrize("idle_s", [QUIET, QUIET + 3600])
def test_a_stalled_build_whose_runner_is_alive_is_not_handed_out(client, tmp_path, monkeypatch, idle_s):
    """The negative: the ledger went quiet but the runner thread is alive,
    so the tree is still being written. No door offers or serves a copy."""
    sid = f"sess_stalled_live_{int(idle_s)}"
    _workspace(tmp_path, sid, idle_s=idle_s)
    monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda _out: True)
    build, actions = _read(client, sid)
    assert build["state"] == "stalled" and build["runner_live"] is True
    assert actions == ["try_again", "start_over"]
    pkg = client.get(f"/v1/sessions/{sid}/product/package?as_is=1")
    assert pkg.status_code == 409 and "still being built" in pkg.json()["detail"]
    assert platform_chat_flow.take_copy(get_session(sid))["ok"] is False


@pytest.mark.parametrize(
    "case, offered",
    [("failed", True), ("orphaned", True), ("quiet_runner_alive", False), ("building", False)],
)
def test_the_three_doors_agree(client, tmp_path, monkeypatch, case, offered):
    """The action list, the typed take_copy and the as-is download answer
    the same for every state a stopped or running build can read."""
    sid = f"sess_doors_{case}"
    if case == "failed":
        _workspace(tmp_path, sid, failed="WRITER gate 'writer_gate' failed: no handler written")
    elif case == "orphaned":
        _workspace(tmp_path, sid, idle_s=ORPHANED)
    elif case == "quiet_runner_alive":
        _workspace(tmp_path, sid, idle_s=QUIET)
        monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda _out: True)
    else:
        _workspace(tmp_path, sid)
        monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda _out: True)
    build, actions = _read(client, sid)
    assert as_is_copy_offered(build) is offered
    assert ("download_copy" in actions) is offered
    assert platform_chat_flow.take_copy(get_session(sid))["ok"] is offered
    assert (client.get(f"/v1/sessions/{sid}/product/package?as_is=1").status_code == 200) is offered


@pytest.mark.parametrize(
    "build, offered",
    [
        ({"state": "failed"}, True),
        ({"state": "stalled"}, True),
        ({"state": "stalled", "runner_live": False}, True),
        ({"state": "stalled", "runner_live": True}, False),
        ({"state": "building"}, False),
        ({"state": "succeeded"}, False),
        ({"state": "not_found"}, False),
        ({}, False),
        (None, False),
    ],
)
def test_as_is_copy_offered_reads_the_state_and_the_runner_flag(build, offered):
    assert as_is_copy_offered(build) is offered
    assert ("download_copy" in allowed_build_actions(build)) is offered


@pytest.mark.parametrize("idle_s", [ORPHANED, QUIET])
def test_take_copy_on_a_stalled_build_says_it_stopped_not_that_it_failed(client, tmp_path, idle_s):
    sid = f"sess_stalled_words_{int(idle_s)}"
    _workspace(tmp_path, sid, idle_s=idle_s)
    reply = platform_chat_flow.take_copy(get_session(sid))
    assert reply["ok"] is True and reply["download"] == {"as_is": True}
    assert "stopped" in reply["summary"] and "not certified" in reply["summary"]
    assert "FAILED" not in reply["summary"]


@pytest.mark.parametrize("detail", ["TESTER gate 'suite_green' failed: suite is red", "WRITER gate failed: nothing written"])
def test_a_failed_build_keeps_its_gate_failed_paperwork(client, tmp_path, detail):
    """The negative of the stalled wording: a build a gate rejected is still
    labelled as failed its gates."""
    sid = f"sess_failed_words_{len(detail)}"
    _workspace(tmp_path, sid, failed=detail)
    build, actions = _read(client, sid)
    assert build["state"] == "failed" and "download_copy" in actions
    pkg = client.get(f"/v1/sessions/{sid}/product/package?as_is=1")
    assert pkg.status_code == 200, pkg.text
    assert "FAILED-GATES-as-is" in pkg.headers.get("content-disposition", "")
    note = zipfile.ZipFile(io.BytesIO(pkg.content)).read("EXPORTED-AS-IS.md").decode("utf-8")
    assert "THIS BUILD FAILED ITS GATES" in note and "STOPPED BEFORE IT FINISHED" not in note
    reply = platform_chat_flow.take_copy(get_session(sid))
    assert reply["ok"] is True and "labelled FAILED" in reply["summary"]
