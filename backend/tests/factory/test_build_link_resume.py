"""R1-R3: pasting a cerebrum-builds session link resumes THAT build.

End-to-end against the real voice-agent branch was run from a driver: the
paste replied "Attached sess_a7a3ea8aaabd4575 @ 240cae9 -- resuming at TESTER"
and the ledger showed no COLLECTOR/CLONER/WRITER after RESUMED. These tests
pin the parts that must never regress, with a local git repository standing
in for cerebrum-builds so nothing here touches the network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.build import branch_attach
from app.factory.build.branch_attach import (
    NOT_A_BUILD_LINK,
    attach,
    checkpoint_message,
    checkpointed_phases,
    parse_build_link,
)

BRANCH = "build/sess_0123abcd-feed01"


# ── R1 parser ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "https://github.com/bopoadz-del/cerebrum-builds/tree/build/sess_0123abcd-feed01",
        "resume https://github.com/bopoadz-del/cerebrum-builds/tree/build/sess_0123abcd-feed01 please",
        "sess_0123abcd-feed01",
        "build/sess_0123abcd-feed01",
        # Live, 2026-09-29: the owner pasted the branch from a phone and the
        # keyboard appended a period. The strict full-match parser called it
        # "not a link", the message fell through to the terminal-failure
        # resume door, and a FULL fresh build started from COLLECTOR over a
        # tree whose gate was already green 21/21. A session token in a chat
        # message attaches that session -- however it is wrapped.
        "build/sess_0123abcd-feed01.",
        "Build/sess_0123abcd-feed01",
        "sess_0123abcd-feed01,",
        "resume build/sess_0123abcd-feed01 please",
        "pull sess_0123abcd-feed01 and hand it to the writer",
        "(build/sess_0123abcd-feed01)",
    ],
)
def test_a_session_link_resolves_to_its_branch(message):
    assert parse_build_link(message, env={}) == (BRANCH, None)


@pytest.mark.parametrize(
    "message",
    [
        "build/sess_0123abcd-feed01. Didnt work",
        "why is sess_0123abcd-feed01 stopped??",
    ],
)
def test_a_session_token_never_falls_through_to_chat(message):
    """(None, None) means ordinary chat -- and ordinary chat can reach the
    fresh-generation door. A message carrying a session token must never
    get there: it attaches, or it is refused by name."""
    branch, refusal = parse_build_link(message, env={})
    assert branch == BRANCH and refusal is None


@pytest.mark.parametrize(
    "message",
    [
        "https://github.com/someone/else/tree/build/sess_0123abcd-feed01",
        "https://github.com/bopoadz-del/cerebrum-builds/tree/main",
        "https://github.com/bopoadz-del/cerebrum-builds/tree/garbage!!",
    ],
)
def test_anything_else_that_looks_like_a_link_is_refused(message):
    assert parse_build_link(message, env={}) == (None, NOT_A_BUILD_LINK)


def test_ordinary_chat_is_not_a_link():
    assert parse_build_link("build me a hotel platform", env={}) == (None, None)


def test_a_refused_link_changes_no_state(tmp_path):
    from app.factory.platform_chat_flow import attach_from_link

    pd = SimpleNamespace(blueprint=None, blueprint_approved=False, plan=None, generation=None, brief="")
    state = SimpleNamespace(session_id="s", user_id=None, product_design=pd)

    reply = attach_from_link(state, "https://github.com/x/y/tree/build/sess_1-2", output_root=tmp_path)

    assert reply["summary"] == NOT_A_BUILD_LINK and reply["ok"] is False
    assert pd.blueprint is None and pd.generation is None
    assert not any(tmp_path.iterdir())


# ── R2 attach, against a local stand-in for cerebrum-builds ────────────────


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _remote_with_a_finished_writer(tmp_path, extra_commits=()):
    src = tmp_path / "src"
    for rel, body in {
        "app/actions/thing.py": "CAPABILITY_ID = 'thing'\n",
        "tests/test_thing.py": "def test_x():\n    pass\n",
        "docs/blueprint/product_blueprint.json": "{}",
        "product-dna/capability_resolution.json": '{"capabilities": []}',
    }.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(body, encoding="utf-8")
    _git(["init", "-q", "-b", BRANCH], src)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], src)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "factory: seed cli-pivot workspace"], src)
    for message in extra_commits:
        _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", message], src)
    bare = tmp_path / "remote.git"
    _git(["clone", "-q", "--bare", str(src), str(bare)], tmp_path)
    return bare


@pytest.fixture()
def local_remote(monkeypatch):
    def use(bare):
        monkeypatch.setattr(branch_attach, "_remote_url", lambda env: str(bare))
        monkeypatch.setattr(
            branch_attach, "_branch_blueprint",
            lambda ws: {"product_id": "thing", "product_name": "Thing", "vertical": "x"},
        )

    return use


def test_attach_clones_the_branch_and_trusts_its_tree(tmp_path, local_remote):
    local_remote(_remote_with_a_finished_writer(tmp_path))

    got = attach(BRANCH, tmp_path / "attached", env={})

    assert got.passed == ("COLLECTOR", "CLONER", "WRITER")
    assert (got.workspace / "app" / "actions" / "thing.py").is_file()
    assert got.workspace.name.endswith("@" + got.sha[:7])


def test_the_same_head_re_enters_the_same_workspace(tmp_path, local_remote):
    local_remote(_remote_with_a_finished_writer(tmp_path))

    first = attach(BRANCH, tmp_path / "attached", env={})
    (first.workspace / "build_ledger.jsonl").write_text("", encoding="utf-8")
    second = attach(BRANCH, tmp_path / "attached", env={})

    assert second.workspace == first.workspace and second.reused


def test_checkpoints_at_the_tip_count_as_passed(tmp_path, local_remote):
    local_remote(_remote_with_a_finished_writer(
        tmp_path, extra_commits=(checkpoint_message("TESTER"), checkpoint_message("STORE_MANAGER"))
    ))

    got = attach(BRANCH, tmp_path / "attached", env={})

    assert got.passed == ("COLLECTOR", "CLONER", "WRITER", "STORE_MANAGER", "TESTER")


def test_a_later_tree_change_invalidates_older_checkpoints(tmp_path):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(["init", "-q"], repo)
    for message in (checkpoint_message("TESTER"), "writer: rework round"):
        _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", message], repo)

    assert checkpointed_phases(repo) == ()


def test_a_checkpoint_is_the_trailer_not_the_subject(tmp_path):
    """The phase is a typed trailer; a subject that merely reads like a
    checkpoint records nothing."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(["init", "-q"], repo)
    for message in ("factory: ZORBLAT passed", checkpoint_message("TESTER")):
        _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", message], repo)

    assert checkpointed_phases(repo) == ("TESTER",)


def test_a_branch_without_a_finished_writer_is_not_attached(tmp_path, local_remote, monkeypatch):
    bare = _remote_with_a_finished_writer(tmp_path)
    local_remote(bare)
    monkeypatch.setattr(branch_attach, "branch_proves_writer_done", lambda tree: False)

    with pytest.raises(branch_attach.AttachError, match="finished WRITER"):
        attach(BRANCH, tmp_path / "attached", env={})


# ── R3: a link never reaches a fresh generation ────────────────────────────


def test_no_code_path_from_a_link_into_a_fresh_generation():
    import inspect

    from app.factory import platform_chat_flow

    src = inspect.getsource(platform_chat_flow.attach_from_link)
    assert "start_fresh_generation(" not in src
    assert "next_fresh_output(" not in src


def test_the_link_is_checked_before_any_other_chat_routing():
    from app.routers import chat

    src = Path(chat.__file__).read_text(encoding="utf-8")
    assert src.index("attach_from_link(") < src.index("has_pending_blueprint(state):")


def test_empty_blueprint_file_is_not_a_blueprint(tmp_path):
    assert branch_attach._branch_blueprint(Path(tmp_path)) is None
