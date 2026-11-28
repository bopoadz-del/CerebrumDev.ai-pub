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
    # The stop is recorded as a stop: no gate failed this build.
    assert manifest["stopped"] == {"state": "stalled", "marker": build.get("honesty") or "", "detail": build["detail"]}
    assert "failed" not in manifest and "failed_label" not in manifest
    # With no runner left, both ages read as the restart they are.
    assert "restarted" in build["detail"] and "still running" not in build["detail"]


@pytest.mark.parametrize("idle_s", [QUIET, QUIET + 3600])
def test_a_stalled_build_whose_runner_is_alive_is_not_handed_out(client, tmp_path, monkeypatch, idle_s):
    """The negative: the ledger went quiet but the runner thread is alive,
    so the tree is still being written. No door offers or serves a copy."""
    sid = f"sess_stalled_live_{int(idle_s)}"
    _workspace(tmp_path, sid, idle_s=idle_s)
    monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda _out: True)
    build, actions = _read(client, sid)
    assert build["state"] == "stalled" and build["runner_live"] is True
    # What the status says agrees with the refusal: the process is running.
    assert "still running" in build["detail"] and "is gone" not in build["detail"]
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
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    note = zf.read("EXPORTED-AS-IS.md").decode("utf-8")
    assert "THIS BUILD FAILED ITS GATES" in note and "STOPPED BEFORE IT FINISHED" not in note
    manifest = json.loads(zf.read("MANIFEST.json"))
    assert manifest["failed"]["gate"] == "WRITER" and "stopped" not in manifest
    reply = platform_chat_flow.take_copy(get_session(sid))
    assert reply["ok"] is True and "labelled FAILED" in reply["summary"]



# -- the export is this zip's paperwork: the build's tree is not written to ----


def _succeed(out):
    ledger = BuildLedger(out / "build_ledger.jsonl")
    for role in (BuildRole.WRITER, BuildRole.TESTER, BuildRole.STORE_MANAGER):
        ledger.append(EventKind.GATE_PASSED, role=role, payload={"gate": role.value})
    ledger.append(EventKind.RUN_SUCCEEDED, payload={"outcome": "SUCCESS", "pilot_ready": False})


def _tree(out):
    return {p.relative_to(out).as_posix(): p.read_bytes() for p in sorted(out.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("case", ["orphaned", "failed"])
def test_an_as_is_download_writes_nothing_into_the_builds_tree(client, tmp_path, case):
    """The note and the manifest go into the zip only. A manifest the tree
    already holds is left as it is, byte for byte."""
    sid = f"sess_tree_untouched_{case}"
    out = _workspace(tmp_path, sid, **({"idle_s": ORPHANED} if case == "orphaned" else {"failed": "WRITER gate failed: x"}))
    (out / "MANIFEST.json").write_text('{"kept": "the tree\'s own"}', encoding="utf-8")
    before = _tree(out)
    pkg = client.get(f"/v1/sessions/{sid}/product/package?as_is=1")
    assert pkg.status_code == 200, pkg.text
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    assert "EXPORTED-AS-IS.md" in zf.namelist()
    assert json.loads(zf.read("MANIFEST.json"))["certified"] is False  # the zip's own manifest
    assert zf.namelist().count("MANIFEST.json") == 1
    assert _tree(out) == before


@pytest.mark.parametrize("idle_s", [ORPHANED, QUIET])
def test_a_stalled_build_downloaded_then_continued_to_success_ships_no_stop_note(client, tmp_path, idle_s):
    """The sequence the button invites: download the stalled copy, press Try
    again, the build finishes. The finished platform's zip holds no note
    saying it stopped."""
    from app.routers.session_product import zip_generated_product

    sid = f"sess_then_success_{int(idle_s)}"
    out = _workspace(tmp_path, sid, idle_s=idle_s)
    assert client.get(f"/v1/sessions/{sid}/product/package?as_is=1").status_code == 200
    _succeed(out)
    build, _actions = _read(client, sid)
    assert build["state"] == "succeeded"
    names = zipfile.ZipFile(zip_generated_product(out, tmp_path / f"shipped-{int(idle_s)}")).namelist()
    assert "README.md" in names and "EXPORTED-AS-IS.md" not in names and "MANIFEST.json" not in names


@pytest.mark.parametrize("held", [None, b"the tree's own"])
def test_zip_extra_is_written_once_and_replaces_a_tree_file_of_its_name(tmp_path, held):
    from app.routers.session_product import zip_generated_product

    out = tmp_path / "tree"
    out.mkdir()
    (out / "README.md").write_text("x", encoding="utf-8")
    if held is not None:
        (out / "NOTE.md").write_bytes(held)
    zf = zipfile.ZipFile(zip_generated_product(out, tmp_path / "with", extra={"NOTE.md": b"the zip's own"}))
    assert zf.namelist().count("NOTE.md") == 1 and zf.read("NOTE.md") == b"the zip's own"
    plain = zipfile.ZipFile(zip_generated_product(out, tmp_path / "without"))
    assert ("NOTE.md" in plain.namelist()) is (held is not None)
    assert held is None or plain.read("NOTE.md") == held


# -- the doors read the live status, never a remembered one --------------------


@pytest.mark.parametrize("remembered", ["stalled", "failed"])
def test_a_remembered_stop_with_no_ledger_offers_no_copy(client, tmp_path, remembered):
    """The workspace is there, its ledger is not, and the session still
    remembers a stopped build. The list and take_copy read the ledger, find
    nothing, and offer no copy."""
    sid = f"sess_remembered_{remembered}"
    out = _workspace(tmp_path, sid, idle_s=ORPHANED)
    (out / "build_ledger.jsonl").unlink()
    state = get_session(sid)
    state.product_design.generation["build"] = {"state": remembered, "detail": "remembered"}
    update_session(sid, state)
    _build, actions = _read(client, sid)
    assert "download_copy" not in actions
    assert platform_chat_flow.take_copy(get_session(sid))["ok"] is False


# -- the route's wider explicit ask keeps its gate-failed paperwork -------------


@pytest.mark.parametrize("case", ["succeeded", "no_ledger"])
def test_an_as_is_download_of_a_build_that_did_not_stall_keeps_the_gate_failed_label(client, tmp_path, case):
    """Only a stalled build is labelled "stopped". The explicit as-is ask on
    a finished build, or on a tree with no ledger, is served as before and
    labelled as before; the list offers no copy there."""
    sid = f"sess_wider_ask_{case}"
    out = _workspace(tmp_path, sid)
    if case == "succeeded":
        _succeed(out)
    else:
        (out / "build_ledger.jsonl").unlink()
    _build, actions = _read(client, sid)
    assert "download_copy" not in actions
    pkg = client.get(f"/v1/sessions/{sid}/product/package?as_is=1")
    assert pkg.status_code == 200, pkg.text
    disposition = pkg.headers.get("content-disposition", "")
    assert "FAILED-GATES-as-is" in disposition and "STOPPED" not in disposition
    zf = zipfile.ZipFile(io.BytesIO(pkg.content))
    assert "THIS BUILD FAILED ITS GATES" in zf.read("EXPORTED-AS-IS.md").decode("utf-8")
    assert "stopped" not in json.loads(zf.read("MANIFEST.json"))


@pytest.mark.parametrize(
    "status, marker",
    [
        ({"state": "stalled", "detail": "the factory server restarted", "honesty": "BUILD_THREAD_ORPHANED"}, "BUILD_THREAD_ORPHANED"),
        ({"state": "stalled", "detail": "no build activity for 32 min"}, ""),
    ],
)
def test_the_stopped_manifest_records_the_stop_and_no_failed_gate(status, marker):
    from app.factory.build.export_manifest import MANIFEST_SCHEMA, manifest_text, stopped_export_manifest

    manifest = stopped_export_manifest(status, product_id="p", platform_id="plt_x")
    assert manifest["schema"] == MANIFEST_SCHEMA and manifest["certified"] is False
    assert manifest["stopped"] == {"state": "stalled", "marker": marker, "detail": status["detail"]}
    assert (manifest["product_id"], manifest["platform_id"], manifest["build_state"]) == ("p", "plt_x", "stalled")
    assert not {"failed", "failed_label", "next_continue", "failure_narrative"} & set(manifest)
    assert json.loads(manifest_text(manifest)) == manifest and manifest_text(manifest).endswith("\n")
