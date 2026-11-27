"""A stopped build's work is on its branch, and its copy can be taken.

1. Every stop of a run (a refused check, a role error, rounds or time spent,
   the owner's Stop) pushes the work as it stood to the branch of record:
   the product directory plus a writer pass still staged when it stopped.
   The commit and its MANIFEST.json say FAILED / STOPPED, the step, the
   check and ``certified: false``. A failed push never replaces the stop's
   own reason.
2. "Take a copy" answers every build that is stopped and not running --
   ``failed`` or ``stalled``, the two states the card reads as stopped --
   with the build's own stop sentence, and names the branch and head when
   the stop was pushed. A running build offers no copy; a certified one is
   never called failed.

The remote is a real bare git repository; nothing here is a stand-in for git.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.factory.blueprint import blueprint_to_dict, load_blueprint
from app.factory.build import branch_attach
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles_models import RoleResult
from app.factory.build.runner import (
    STOP_OUTCOMES,
    BuildBudget,
    Outcome,
    RoleRunner,
    blueprint_hash,
)
from app.core.session_store import create_session, get_session, update_session
from app.main import app

ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT = ROOT / "blueprints" / "examples" / "runner_smoke.yaml"
BRANCH = "build/plt_stoptest"
NL = chr(10)
BRIEF = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args], cwd=str(cwd),
        capture_output=True, text=True, check=True,
    )
    return out.stdout


@pytest.fixture
def remote(tmp_path, monkeypatch):
    return bare_builds_remote(tmp_path, monkeypatch)


def bare_builds_remote(tmp_path: Path, monkeypatch) -> Path:
    """A bare cerebrum-builds with ``main`` and the build's branch."""
    bare = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(bare), str(seed))
    _git(seed, "checkout", "-q", "-b", "main")
    (seed / "README.md").write_text("builds\n", encoding="utf-8")
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "main")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", BRANCH)
    _git(seed, "push", "-q", "origin", BRANCH)
    monkeypatch.setattr(branch_attach, "_remote_url", lambda env: str(bare))
    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", "t")
    monkeypatch.setenv("CEREBRUM_BUILDS_REPO", "o/r")
    return bare


def _on_branch(bare: Path, rel: str) -> str:
    out = subprocess.run(
        ["git", "show", f"{BRANCH}:{rel}"], cwd=str(bare), capture_output=True, text=True,
    )
    return out.stdout if out.returncode == 0 else ""


def _workspace(tmp_path: Path, branch: str = BRANCH) -> Path:
    bp = load_blueprint(BLUEPRINT)
    out = tmp_path / "ws"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash=blueprint_hash(bp))
    if branch:
        ledger.append(EventKind.NOTE, detail="attached", payload={"attached_branch": branch})
    return out


def _run(out: Path, roles) -> object:
    bp = load_blueprint(BLUEPRINT)
    return RoleRunner(
        bp, out, roles=roles,
        budget=BuildBudget(max_rework=0, wall_clock_s=600, phase_wall_clock_s=600),
        brief=BRIEF,
    ).run()


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _roles(writer_ok: bool, rel: str, text: str):
    """The real roles, with a WRITER that writes ``rel`` and then either dies
    (its pass stays staged, kept for the resume) or returns, so its pass is
    committed into the product directory and its gate judges it."""
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    def writer(ctx):
        ctx.workspace.write_text(rel, text)
        if writer_ok:
            return RoleResult(ok=True, detail="WRITER ok")
        return RoleResult(ok=False, detail="WRITER died", reason="writer_died")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    return roles


# writer_ok False: the stop is inside the writer step; True: its gate refuses.
CASES = [
    (False, "app/actions/count_items.py", "def count():\n    return 1\n"),
    (True, "app/actions/list_hires.py", "def hires():\n    return []\n"),
]


@pytest.mark.parametrize("writer_ok, rel, text", CASES)
def test_a_stopped_run_pushes_its_work_to_the_branch(tmp_path, remote, writer_ok, rel, text):
    out = _workspace(tmp_path)

    result = _run(out, _roles(writer_ok, rel, text))

    assert result.outcome in STOP_OUTCOMES
    assert result.failed_phase is BuildRole.WRITER
    assert _on_branch(remote, rel) == text, "the step's work is not on the branch"
    manifest = json.loads(_on_branch(remote, "MANIFEST.json"))
    assert manifest["certified"] is False
    assert manifest["stopped"]["state"] == "FAILED"
    assert manifest["stopped"]["step"] == "WRITER"
    assert manifest["stopped"]["outcome"] == result.outcome.value
    subject = _git(remote, "log", "-1", "--format=%s", BRANCH).strip()
    assert subject == "factory: FAILED at WRITER, not certified"
    body = _git(remote, "log", "-1", "--format=%b", BRANCH)
    assert "certified: false" in body
    assert not _git(remote, "tag", "--list").strip(), "a stopped build was tagged"
    notes = [e for e in BuildLedger(out / "build_ledger.jsonl").events()
             if (e.payload or {}).get("stop_checkpoint")]
    head = _git(remote, "rev-parse", BRANCH).strip()
    assert [n.payload["stop_checkpoint"] for n in notes] == [head]
    assert notes[0].payload["branch"] == BRANCH


@pytest.mark.parametrize("writer_ok, rel, text", CASES)
def test_the_stop_commit_names_only_the_phases_that_passed(tmp_path, remote, writer_ok, rel, text):
    out = _workspace(tmp_path)

    _run(out, _roles(writer_ok, rel, text))

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", "--branch", BRANCH, str(remote), str(clone))
    passed = branch_attach.checkpointed_phases(clone)
    assert "WRITER" not in passed
    assert set(passed) == {"COLLECTOR", "CLONER"}


@pytest.mark.parametrize(
    "rel, text",
    [("app/actions/owner_a.py", "A = 1" + NL), ("app/actions/owner_b.py", "B = 2" + NL)],
)
def test_the_owners_stop_pushes_a_stopped_commit(tmp_path, remote, rel, text):
    from app.factory.build.coder_session_status import OwnerStopped

    roles = _roles(False, rel, text)
    inner = roles[BuildRole.WRITER]

    def writer(ctx):
        inner(ctx)
        raise OwnerStopped("owner pressed Stop")

    roles[BuildRole.WRITER] = writer
    out = _workspace(tmp_path)

    result = _run(out, roles)

    assert result.outcome is Outcome.STOPPED_BY_OWNER
    assert _on_branch(remote, rel) == text
    manifest = json.loads(_on_branch(remote, "MANIFEST.json"))
    assert manifest["stopped"]["state"] == "STOPPED" and manifest["certified"] is False
    subject = _git(remote, "log", "-1", "--format=%s", BRANCH).strip()
    assert subject == "factory: STOPPED at WRITER, not certified"


@pytest.mark.parametrize("writer_ok", [False, True])
def test_a_failed_push_never_hides_the_stop_that_caused_it(tmp_path, remote, writer_ok):
    """The remote is there for every passed phase and gone when the run
    stops: the stop's own reason stays the run's reason."""

    from app.factory.build.builds_push import BuildsPushError

    roles = _roles(writer_ok, "app/x.py", "x = 1\n")
    inner = roles[BuildRole.WRITER]

    def writer(ctx):
        remote.rename(remote.with_name("moved-away.git"))
        return inner(ctx)

    roles[BuildRole.WRITER] = writer
    out = _workspace(tmp_path)

    result = _run(out, roles)

    assert result.outcome in STOP_OUTCOMES and result.failed_phase is BuildRole.WRITER
    assert "checkpoint" not in result.detail.lower()
    events = BuildLedger(out / "build_ledger.jsonl").events()
    terminal = [e for e in events if e.kind is EventKind.RUN_FAILED][-1]
    assert terminal.detail == result.detail
    failed = [e for e in events if (e.payload or {}).get("push_error")]
    assert len(failed) == 1 and failed[0].payload["stop_checkpoint"] == ""
    assert failed[0].payload["push_error"].startswith(BuildsPushError.__name__)
    assert failed[0].seq > terminal.seq


@pytest.mark.parametrize("writer_ok", [False, True])
def test_a_run_with_no_branch_of_record_pushes_nothing(tmp_path, monkeypatch, writer_ok):
    monkeypatch.delenv("CEREBRUM_BUILDS_GITHUB_TOKEN", raising=False)
    out = _workspace(tmp_path, branch="")

    result = _run(out, _roles(writer_ok, "app/x.py", "x = 1\n"))

    assert result.outcome in STOP_OUTCOMES
    events = BuildLedger(out / "build_ledger.jsonl").events()
    assert not [e for e in events if "stop_checkpoint" in (e.payload or {})]


def test_only_a_stop_is_a_stop_outcome():
    assert Outcome.SUCCESS not in STOP_OUTCOMES
    assert Outcome.HANDOFF_TO_N3 not in STOP_OUTCOMES
    assert Outcome.COLLECT_ALL_REPORT not in STOP_OUTCOMES
    assert Outcome.STOPPED_BY_OWNER in STOP_OUTCOMES
    assert Outcome.FAILED_GATE in STOP_OUTCOMES


# -- the stop manifest leaves the tip once the work moves on ------------------


@pytest.mark.parametrize(
    "manifest, dropped",
    [
        ({"certified": False, "stopped": {"state": "FAILED"}}, True),
        ({"certified": False, "stopped": {"state": "STOPPED"}}, True),
        ({"certified": True}, False),
        ({"certified": False, "stopped": "FAILED"}, False),
        (None, False),
    ],
)
def test_drop_stop_manifest_removes_only_a_stop_record(tmp_path, manifest, dropped):
    tree = tmp_path / "tree"
    tree.mkdir()
    if manifest is not None:
        (tree / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert branch_attach.drop_stop_manifest(tree) is dropped
    assert (tree / "MANIFEST.json").exists() is (manifest is not None and not dropped)


def _stop_commit(tmp_path: Path, state_word: str) -> None:
    """Push a stop commit (its MANIFEST.json says stopped) to the branch."""
    stop_tree = tmp_path / "stop_tree"
    (stop_tree / "app").mkdir(parents=True)
    (stop_tree / "app" / "main.py").write_text("x = 1" + NL, encoding="utf-8")
    (stop_tree / "MANIFEST.json").write_text(
        json.dumps({"certified": False, "stopped": {"state": state_word, "step": "WRITER"}}),
        encoding="utf-8",
    )
    msg = branch_attach.stop_checkpoint_message(state_word, "WRITER", "grader", ["COLLECTOR", "CLONER"])
    branch_attach.checkpoint(stop_tree, BRANCH, msg, stop=True)


@pytest.mark.parametrize("state_word, phase", [("FAILED", "WRITER"), ("STOPPED", "TESTER")])
def test_a_re_entered_branch_drops_the_stop_manifest_at_the_next_checkpoint(
    tmp_path, remote, state_word, phase
):
    """Try again clones the branch into the workspace (stop manifest
    included); the next passed phase is work that moved on."""
    _stop_commit(tmp_path, state_word)
    assert json.loads(_on_branch(remote, "MANIFEST.json"))["stopped"]["state"] == state_word
    ws = tmp_path / "reentered"
    _git(tmp_path, "clone", "-q", "--branch", BRANCH, str(remote), str(ws))
    assert (ws / "MANIFEST.json").is_file()
    (ws / "app" / "main.py").write_text("x = 2" + NL, encoding="utf-8")

    branch_attach.checkpoint(ws, BRANCH, branch_attach.checkpoint_message(phase))

    assert _git(remote, "log", "-1", "--format=%s", BRANCH).strip() == f"factory: {phase} passed"
    assert _on_branch(remote, "MANIFEST.json") == ""
    assert _on_branch(remote, "app/main.py") == "x = 2" + NL


@pytest.mark.parametrize("state_word", ["FAILED", "STOPPED"])
def test_a_stop_checkpoint_keeps_its_own_manifest(tmp_path, remote, state_word):
    _stop_commit(tmp_path, state_word)

    manifest = json.loads(_on_branch(remote, "MANIFEST.json"))
    assert manifest["stopped"]["state"] == state_word and manifest["certified"] is False


@pytest.mark.parametrize("passed", [(), ("COLLECTOR", "CLONER")])
def test_the_stop_message_carries_the_passed_phases_as_a_trailer(passed):
    msg = branch_attach.stop_checkpoint_message("STOPPED", "WRITER", "grader", passed)
    assert msg.splitlines()[0] == "factory: STOPPED at WRITER, not certified"
    assert "certified: false" in msg
    assert (branch_attach.CHECKPOINT_TRAILER in msg) is bool(passed)
    if passed:
        assert msg.rstrip().endswith(",".join(passed))


# -- Take a copy reads the build's own stop ------------------------------------


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def _session(tmp_path: Path, sid: str, events) -> Path:
    """A session whose build ledger holds ``events`` (kind, role, detail,
    payload), shaped like a run the grader stopped."""
    from app.factory.build.platform_identity import mint_platform_id

    bp = load_blueprint(BLUEPRINT)
    create_session(sid, "tester")
    out = tmp_path / f"{sid}-product"
    (out / "app").mkdir(parents=True)
    (out / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=bp.product_id, inputs_hash=blueprint_hash(bp))
    for kind, role, detail, payload in events:
        ledger.append(kind, role=role, detail=detail, payload=payload)
    state = get_session(sid)
    pd = state.product_design
    pd.blueprint = blueprint_to_dict(bp)
    pd.blueprint_approved = True
    pd.plan = {"capabilities": []}
    pd.platform_id = mint_platform_id()
    pd.generation = {"output_dir": str(out), "product_id": bp.product_id,
                     "inputs_hash": blueprint_hash(bp), "engine": "runner"}
    update_session(sid, state)
    return out


def _passed(*names):
    rows = []
    for name in names:
        role = BuildRole(name)
        rows.append((EventKind.PHASE_STARTED, role, name, {}))
        rows.append((EventKind.GATE_PASSED, role, f"{name} ok", {}))
    return rows


def _grader_stop(sha: str = ""):
    rows = [
        (EventKind.GATE_FAILED, BuildRole.WRITER, "GRADER: NOT_CERTIFIED",
         {"gate": "GRADER", "reason": "grader_not_certified"}),
        (EventKind.RUN_FAILED, BuildRole.WRITER, "GRADER: NOT_CERTIFIED -- 25 problems",
         {"outcome": "FAILED_GATE", "findings": ["auth: route refused"]}),
    ]
    if sha:
        rows.append((EventKind.NOTE, BuildRole.WRITER, "stop checkpoint",
                     {"stop_checkpoint": sha, "branch": BRANCH, "certified": False}))
    return rows


def _resumed_then_orphaned():
    """Try again re-entered the run, the writer started, and the server
    restarted under it: no verdict after the re-entry."""
    return [
        (EventKind.NOTE, BuildRole.WRITER, "resume", {"resumed": True}),
        (EventKind.PHASE_STARTED, BuildRole.WRITER, "WRITER", {}),
    ]


def _age(out: Path, seconds: float) -> None:
    path = out / "build_ledger.jsonl"
    then = time.time() - seconds
    os.utime(path, (then, then))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(then))
    path.write_text("".join(json.dumps({**r, "ts": stamp}) + "\n" for r in rows), encoding="utf-8")
    os.utime(path, (then, then))


def _take_copy(client, sid: str) -> str:
    resp = client.post(f"/v1/sessions/{sid}/chat", json={"message": "", "action": "take_copy"})
    assert resp.status_code == 200
    return resp.text


@pytest.mark.parametrize(
    "sid, extra, state",
    [
        # Stopped by the grader, resumed by Try again, stopped by it again.
        ("sess_copy_failed_twice",
         _passed("COLLECTOR", "CLONER") + _grader_stop() + _resumed_then_orphaned()
         + [(EventKind.GATE_PASSED, BuildRole.WRITER, "WRITER ok", {})] + _grader_stop("c" * 40),
         "failed"),
        # Stopped by the grader, resumed, and the server restarted under it.
        ("sess_copy_orphaned",
         _passed("COLLECTOR", "CLONER") + _grader_stop("d" * 40) + _resumed_then_orphaned(),
         "stalled"),
    ],
)
def test_a_stopped_build_offers_its_copy(client, tmp_path, sid, extra, state):
    from app.factory import platform_chat_flow
    from app.factory.build_jobs import build_status

    out = _session(tmp_path, sid, extra)
    _age(out, 3600)
    st = build_status(out)
    assert st["state"] == state

    text = _take_copy(client, sid)

    assert "did not stop with a failure" not in text
    reply = platform_chat_flow.take_copy(get_session(sid))
    assert reply["ok"] is True and reply["download"] == {"as_is": True}
    plain = str((st.get("stop") or {}).get("plain") or "")
    assert plain and reply["summary"].startswith(plain)
    assert plain in text


@pytest.mark.parametrize("sid, sha", [("sess_copy_branch_a", "e" * 40), ("sess_copy_branch_b", "1" * 40)])
def test_take_copy_names_the_branch_and_head_the_stop_pushed(client, tmp_path, sid, sha):
    from app.factory import platform_chat_flow

    _session(tmp_path, sid, _passed("COLLECTOR", "CLONER") + _grader_stop(sha))

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["branch"] == BRANCH and reply["head"] == sha
    assert f"{BRANCH} at {sha[:12]}" in reply["summary"]


@pytest.mark.parametrize("sid, error", [("sess_copy_nopush_a", "BuildsPushError: x"), ("sess_copy_nopush_b", "OSError: y")])
def test_take_copy_names_no_branch_when_the_push_failed(client, tmp_path, sid, error):
    from app.factory import platform_chat_flow

    rows = _passed("COLLECTOR", "CLONER") + _grader_stop("f" * 40) + [
        (EventKind.NOTE, BuildRole.WRITER, "stop checkpoint not pushed",
         {"stop_checkpoint": "", "branch": BRANCH, "push_error": error}),
    ]
    _session(tmp_path, sid, rows)

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is True
    assert reply["branch"] == "" and BRANCH not in reply["summary"]


@pytest.mark.parametrize("sid", ["sess_copy_running_a", "sess_copy_running_b"])
def test_a_running_build_offers_no_copy(client, tmp_path, monkeypatch, sid):
    from app.factory import build_jobs, platform_chat_flow

    out = _session(tmp_path, sid, _passed("COLLECTOR") + [(EventKind.PHASE_STARTED, BuildRole.CLONER, "CLONER", {})])
    monkeypatch.setattr(build_jobs, "_live_runner_thread", lambda output_dir: object())
    assert build_jobs.build_status(out)["state"] == "building"

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is False and "download" not in reply
    assert "still running" in reply["summary"]


@pytest.mark.parametrize("sid", ["sess_copy_certified_a", "sess_copy_certified_b"])
def test_a_certified_build_is_not_labelled_failed(client, tmp_path, sid):
    from app.factory import platform_chat_flow

    rows = _passed("COLLECTOR", "CLONER", "WRITER", "TESTER", "STORE_MANAGER") + [
        (EventKind.RUN_SUCCEEDED, None, "ok", {"outcome": "SUCCESS"}),
    ]
    _session(tmp_path, sid, rows)

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is False and "download" not in reply
    assert "FAILED" not in reply["summary"]


@pytest.mark.parametrize("sid, noted", [("sess_copy_handoff_a", True), ("sess_copy_handoff_b", False)])
def test_a_build_waiting_for_its_store_gate_offers_no_copy(client, tmp_path, sid, noted):
    """A designed hand-off ends the run with RUN_FAILED(HANDOFF_TO_N3); the
    status reads it as building, so it is not a stopped build."""
    from app.factory import platform_chat_flow
    from app.factory.build_jobs import build_status

    store = BuildRole("STORE_MANAGER")
    rows = _passed("COLLECTOR", "CLONER", "WRITER", "TESTER") + [
        (EventKind.PHASE_STARTED, store, "STORE_MANAGER", {}),
        (EventKind.GATE_FAILED, store, "docker", {"reason": "docker_unavailable"}),
    ]
    if noted:
        rows.append((EventKind.NOTE, store, "handed off", {"handoff": "HANDOFF_TO_N3"}))
    rows.append((EventKind.RUN_FAILED, store, "handed off", {"outcome": "HANDOFF_TO_N3"}))
    out = _session(tmp_path, sid, rows)
    assert build_status(out)["state"] == "building"

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is False and "download" not in reply
    assert "FAILED" not in reply["summary"]


# -- the pieces, each on its own -----------------------------------------------


@pytest.mark.parametrize(
    "env, refused",
    [
        ({}, True),
        ({"CEREBRUM_BUILDS_GITHUB_TOKEN": "   "}, True),
        ({"CEREBRUM_BUILDS_GITHUB_TOKEN": "t"}, False),
    ],
)
def test_checkpoint_refuses_without_a_builds_token(tmp_path, remote, env, refused):
    from app.factory.build.builds_push import BuildsPushError

    src = tmp_path / "src"
    (src / "app").mkdir(parents=True)
    (src / "app" / "main.py").write_text("x = 2\n", encoding="utf-8")
    before = _git(remote, "rev-parse", BRANCH).strip()

    if refused:
        with pytest.raises(BuildsPushError, match="CEREBRUM_BUILDS_GITHUB_TOKEN"):
            branch_attach.checkpoint(src, BRANCH, "m", env=env)
        assert _git(remote, "rev-parse", BRANCH).strip() == before
        assert _on_branch(remote, "app/main.py") == ""
    else:
        sha = branch_attach.checkpoint(src, BRANCH, "m", env=env)
        assert sha != before and _git(remote, "rev-parse", BRANCH).strip() == sha
        assert _on_branch(remote, "app/main.py") == "x = 2\n"


@pytest.mark.parametrize(
    "rel, text",
    [("app/actions/count_items.py", "N = 1\n"), ("app/actions/list_hires.py", "N = 2\n")],
)
def test_the_stop_tree_carries_only_exported_staged_files(tmp_path, rel, text):
    """A staged file ships; a staged directory, bytecode and a cache do not."""
    bp = load_blueprint(BLUEPRINT)
    out = _workspace(tmp_path, branch="")
    (out / "app").mkdir()
    (out / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
    runner = RoleRunner(bp, out, budget=BuildBudget(max_rework=0), brief=BRIEF)
    staging = runner._staging_for(BuildRole.WRITER)
    assert staging is not None
    (staging / rel).parent.mkdir(parents=True, exist_ok=True)
    (staging / rel).write_text(text, encoding="utf-8")
    (staging / "app" / "empty_dir").mkdir(parents=True)
    (staging / "app" / "__pycache__").mkdir(parents=True)
    (staging / "app" / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\x00")
    (staging / ".git").mkdir()
    (staging / ".git" / "config").write_text("[core]" + NL, encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()

    runner._stop_tree(tree)

    assert (tree / rel).read_text(encoding="utf-8") == text
    assert (tree / "app" / "main.py").is_file()
    assert not (tree / "app" / "__pycache__").exists()
    assert not (tree / ".git").exists()


@pytest.mark.parametrize("design, sha", [("none", ""), ("no_output", ""), ("own", "a" * 40), ("own", "b" * 40)])
def test_stop_checkpoint_of_reads_only_the_builds_own_ledger(tmp_path, design, sha):
    """No build (no output directory) names no branch; a build names the
    head its own stop NOTE recorded."""
    from types import SimpleNamespace

    from app.factory import platform_chat_flow

    out = tmp_path / "ws"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(EventKind.RUN_FAILED, detail="stop", payload={"outcome": "FAILED_GATE"})
    ledger.append(EventKind.NOTE, detail="stop", payload={"stop_checkpoint": sha or "c" * 40, "branch": BRANCH})
    if design == "none":
        state = SimpleNamespace(product_design=None, session_id="s")
    elif design == "no_output":
        state = SimpleNamespace(product_design=SimpleNamespace(generation={}, blueprint={}), session_id=None)
    else:
        state = SimpleNamespace(
            product_design=SimpleNamespace(generation={"output_dir": str(out)}, blueprint={}), session_id="s",
        )

    got = platform_chat_flow.stop_checkpoint_of(state)

    assert got == ({"stop_checkpoint": sha, "branch": BRANCH} if sha else {})


# -- the reviewers' faults on 82ffd73, each answered by a test ------------------


@pytest.mark.parametrize("token", ["ghs_probeSECRETaaaa1111", "ghp_otherSECRETbbbb2222"])
def test_a_timed_out_stop_push_never_writes_the_token(tmp_path, remote, monkeypatch, caplog, token):
    """The stop push times out inside git; the timeout's own text is the
    whole command line with the authenticated URL in it."""
    monkeypatch.setenv("CEREBRUM_BUILDS_GITHUB_TOKEN", token)
    real_git = branch_attach._git
    stopping = {"now": False}

    def git(args, cwd):
        if stopping["now"] and args and args[0] == "clone":
            url = f"https://x-access-token:{token}@github.com/o/r.git"
            raise subprocess.TimeoutExpired(cmd=["git", *args[:-2], url, "."], timeout=600)
        return real_git(args, cwd)

    monkeypatch.setattr(branch_attach, "_git", git)
    roles = _roles(False, "app/x.py", "x = 1" + NL)
    inner = roles[BuildRole.WRITER]

    def writer(ctx):
        stopping["now"] = True
        return inner(ctx)

    roles[BuildRole.WRITER] = writer
    out = _workspace(tmp_path)

    with caplog.at_level("WARNING"):
        result = _run(out, roles)

    assert result.outcome in STOP_OUTCOMES
    events = BuildLedger(out / "build_ledger.jsonl").events()
    failed = [e for e in events if (e.payload or {}).get("push_error")]
    assert len(failed) == 1
    assert failed[0].payload["push_error"].startswith("TimeoutExpired")
    assert "<redacted>" in failed[0].payload["push_error"]
    assert token not in (out / "build_ledger.jsonl").read_text(encoding="utf-8")
    assert token not in caplog.text and "stop checkpoint failed" in caplog.text


@pytest.mark.parametrize("token, verb", [("ghs_gitSECRETcccc3333", "clone"), ("ghp_gitSECRETdddd4444", "push")])
def test_a_git_call_that_times_out_names_only_its_verb(tmp_path, monkeypatch, token, verb):
    from app.factory.build.builds_push import BuildsPushError

    url = f"https://x-access-token:{token}@github.com/o/r.git"

    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(branch_attach.subprocess, "run", run)

    with pytest.raises(BuildsPushError) as caught:
        branch_attach._git([verb, url], tmp_path)

    assert str(caught.value) == f"git {verb} timed out after 600s"
    assert caught.value.__suppress_context__ is True


def _url_to_bare(remote: Path):
    def git(args, *, cwd):
        args = [str(remote) if str(a).startswith("https://") else a for a in args]
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)

    return git


@pytest.mark.parametrize("message", ["factory: FAILED at WRITER, not certified", "factory: TESTER passed"])
def test_the_first_push_of_a_platform_branch_carries_its_message(tmp_path, remote, message):
    """A platform with no branch yet cuts it from main; the commit says what
    the push is (a stop's FAILED subject, a passed phase), not a seed."""
    from app.factory.build.builds_push import push_workspace
    from app.factory.build.platform_identity import branch_of_record, mint_platform_id

    ws = tmp_path / "first"
    (ws / "app").mkdir(parents=True)
    (ws / "app" / "main.py").write_text("x = 1" + NL, encoding="utf-8")
    branch = branch_of_record(mint_platform_id())

    push_workspace(ws, env=dict(os.environ), session_id="", run_git=_url_to_bare(remote), branch=branch, message=message)

    assert _git(remote, "log", "-1", "--format=%B", branch).strip() == message


@pytest.mark.parametrize("readme", ["one", "two"])
def test_a_platform_branch_cut_without_a_message_is_a_seed(tmp_path, remote, readme):
    from app.factory.build.builds_push import SEED_MESSAGE, push_workspace
    from app.factory.build.platform_identity import branch_of_record, mint_platform_id

    branch = branch_of_record(mint_platform_id())
    ws = tmp_path / "seed_ws"
    ws.mkdir()
    (ws / "README.md").write_text(readme + NL, encoding="utf-8")

    push_workspace(ws, env=dict(os.environ), session_id="", run_git=_url_to_bare(remote), branch=branch)

    assert _git(remote, "log", "-1", "--format=%s", branch).strip() == SEED_MESSAGE


@pytest.mark.parametrize(
    "message, stop",
    [("factory: FAILED at COLLECTOR, not certified", True), ("factory: CLONER passed", False)],
)
def test_push_to_branch_of_record_hands_its_message_to_the_first_push(monkeypatch, tmp_path, message, stop):
    from app.factory.build import builds_push, platform_branch
    from app.factory.build.platform_identity import mint_platform_id

    seen = {}
    monkeypatch.setattr(platform_branch, "builds_remote", lambda env: "r")
    monkeypatch.setattr(platform_branch, "branch_head", lambda remote, pid: seen.get("pushed"))
    monkeypatch.setattr(platform_branch, "restore_branch", lambda remote, pid: None)

    def push(workspace, **kw):
        seen.update(kw)
        seen["pushed"] = "a" * 40

    monkeypatch.setattr(builds_push, "push_workspace", push)

    head = platform_branch.push_to_branch_of_record(tmp_path, mint_platform_id(), message, env={}, stop=stop)

    assert head == "a" * 40 and seen["message"] == message


@pytest.mark.parametrize("stop", [True, False])
def test_push_to_branch_of_record_hands_stop_to_the_checkpoint(monkeypatch, tmp_path, stop):
    from app.factory.build import branch_attach as attach
    from app.factory.build import platform_branch
    from app.factory.build.platform_identity import mint_platform_id

    seen = {}
    monkeypatch.setattr(platform_branch, "builds_remote", lambda env: "r")
    monkeypatch.setattr(platform_branch, "branch_head", lambda remote, pid: "b" * 40)

    def checkpoint(workspace, branch, message, env=None, *, stop=False):
        seen["stop"] = stop
        return "c" * 40

    monkeypatch.setattr(attach, "checkpoint", checkpoint)

    head = platform_branch.push_to_branch_of_record(tmp_path, mint_platform_id(), "m", env={}, stop=stop)

    assert head == "c" * 40 and seen["stop"] is stop


@pytest.mark.parametrize("sid, sha", [("sess_copy_stale_a", "a" * 40), ("sess_copy_stale_b", "2" * 40)])
def test_take_copy_never_names_an_older_stops_head(client, tmp_path, sid, sha):
    """Stop pushed at sha; Try again; WRITER passed and checkpointed; the
    server restarted in TESTER. The branch has moved on: sha is not the
    same work."""
    from app.factory import platform_chat_flow
    from app.factory.build_jobs import build_status

    rows = _passed("COLLECTOR", "CLONER") + _grader_stop(sha) + _resumed_then_orphaned() + [
        (EventKind.GATE_PASSED, BuildRole.WRITER, "WRITER ok", {}),
        (EventKind.NOTE, BuildRole.WRITER, "CHECKPOINT WRITER", {"checkpoint": "9" * 40, "attached_branch": BRANCH}),
        (EventKind.PHASE_STARTED, BuildRole.TESTER, "TESTER", {}),
    ]
    out = _session(tmp_path, sid, rows)
    _age(out, 3600)
    assert build_status(out)["state"] == "stalled"

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is True
    assert reply["head"] == "" and reply["branch"] == ""
    assert sha[:12] not in reply["summary"]


@pytest.mark.parametrize("sid, sha", [("sess_copy_restop_a", "a" * 40), ("sess_copy_restop_b", "3" * 40)])
def test_take_copy_names_no_head_when_the_newest_stop_pushed_nothing(client, tmp_path, sid, sha):
    """Stopped and pushed at sha, re-entered, stopped again with no push
    recorded: the older head belongs to the older verdict."""
    from app.factory import platform_chat_flow

    rows = _passed("COLLECTOR", "CLONER") + _grader_stop(sha) + _resumed_then_orphaned() + _grader_stop("")
    _session(tmp_path, sid, rows)

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is True and reply["head"] == "" and sha[:12] not in reply["summary"]


@pytest.mark.parametrize("sid", ["sess_copy_quiet_live_a", "sess_copy_quiet_live_b"])
def test_a_quiet_build_with_a_live_runner_offers_no_copy(client, tmp_path, monkeypatch, sid):
    """Quiet for an hour with no model call in flight reads ``stalled``,
    but its runner thread is alive in this process: it is running."""
    from app.factory import build_jobs, platform_chat_flow

    out = _session(tmp_path, sid, _passed("COLLECTOR") + [(EventKind.PHASE_STARTED, BuildRole.CLONER, "CLONER", {})])
    _age(out, 3600)
    monkeypatch.setattr(build_jobs, "live_runner_thread", lambda output_dir: object())
    assert build_jobs.build_status(out)["state"] == "stalled"

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is False and "download" not in reply
    assert "still running" in reply["summary"]


@pytest.mark.parametrize("sid", ["sess_copy_quiet_dead_a", "sess_copy_quiet_dead_b"])
def test_a_quiet_build_with_no_runner_offers_its_copy(client, tmp_path, monkeypatch, sid):
    from app.factory import build_jobs, platform_chat_flow

    out = _session(tmp_path, sid, _passed("COLLECTOR") + [(EventKind.PHASE_STARTED, BuildRole.CLONER, "CLONER", {})])
    _age(out, 3600)
    monkeypatch.setattr(build_jobs, "live_runner_thread", lambda output_dir: None)
    assert build_jobs.build_status(out)["state"] == "stalled"

    reply = platform_chat_flow.take_copy(get_session(sid))

    assert reply["ok"] is True and reply["download"] == {"as_is": True}


@pytest.mark.parametrize("name, secret", [("link_a.py", "TOKEN=aaa"), ("link_b.py", "TOKEN=bbb")])
def test_the_stop_tree_never_follows_a_staged_symlink(tmp_path, name, secret):
    """A writer-staged symlink pointing outside the tree: its target's
    contents never reach the branch."""
    bp = load_blueprint(BLUEPRINT)
    out = _workspace(tmp_path, branch="")
    (out / "app").mkdir()
    (out / "app" / "main.py").write_text("app = None" + NL, encoding="utf-8")
    runner = RoleRunner(bp, out, budget=BuildBudget(max_rework=0), brief=BRIEF)
    staging = runner._staging_for(BuildRole.WRITER)
    assert staging is not None
    outside = tmp_path / "outside.env"
    outside.write_text(secret + NL, encoding="utf-8")
    (staging / "app").mkdir(parents=True, exist_ok=True)
    (staging / "app" / "real.py").write_text("R = 1" + NL, encoding="utf-8")
    try:
        os.symlink(outside, staging / "app" / name)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks; Linux CI runs it")
    tree = tmp_path / "tree"
    tree.mkdir()

    runner._stop_tree(tree)

    assert (tree / "app" / "real.py").read_text(encoding="utf-8") == "R = 1" + NL
    assert not (tree / "app" / name).exists()
    shipped = "".join(p.read_text(encoding="utf-8", errors="replace") for p in tree.rglob("*") if p.is_file())
    assert secret not in shipped
