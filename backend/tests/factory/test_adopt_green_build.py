"""A session whose build already passed the Store gate is adopted, not rebuilt
-- but only when the branch IS the workspace.

The zip is cut from the local workspace; the gate verified the branch's tree.
Stamping "passed" on files the gate never saw would be a lie.
"""

from __future__ import annotations

from app.factory.build import adopt_green
from app.factory.build.adopt_green import git_blob_sha, workspace_matches_tree


def _ws(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return tmp_path


def _tree(files):
    return {rel: git_blob_sha(text.encode("utf-8")) for rel, text in files.items()}


def test_git_blob_sha_is_gits_own_id():
    # `git hash-object` of the bytes "hello\n"
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_identical_trees_match(tmp_path):
    files = {"app/main.py": "x = 1\n", "Dockerfile": "FROM python\n"}
    same, differing = workspace_matches_tree(_ws(tmp_path, files), _tree(files))
    assert same is True and differing == []


def test_a_workspace_rewritten_after_the_push_does_not_match(tmp_path):
    pushed = {"app/main.py": "x = 1\n", "Dockerfile": "FROM python\n"}
    ws = _ws(tmp_path, {**pushed, "app/main.py": "x = 2  # a later failed run\n"})
    same, differing = workspace_matches_tree(ws, _tree(pushed))
    assert same is False and differing == ["app/main.py"]


def test_a_file_missing_locally_does_not_match(tmp_path):
    pushed = {"app/main.py": "x = 1\n", "app/extra.py": "y = 1\n"}
    ws = _ws(tmp_path, {"app/main.py": "x = 1\n"})
    assert workspace_matches_tree(ws, _tree(pushed))[0] is False


def test_the_gates_own_workflow_and_the_growing_ledger_are_not_compared(tmp_path):
    product = {"app/main.py": "x = 1\n"}
    tree = {**_tree(product), ".github/workflows/store-gate.yml": "0" * 40,
            "build_ledger.jsonl": "1" * 40, "receipt.json": "2" * 40}
    assert workspace_matches_tree(_ws(tmp_path, product), tree)[0] is True


def test_an_empty_comparison_is_not_a_match(tmp_path):
    """Nothing comparable must never read as 'identical'."""
    assert workspace_matches_tree(tmp_path, {".github/x.yml": "0" * 40})[0] is False


def test_no_token_or_no_session_adopts_nothing(tmp_path):
    assert adopt_green.find_adoptable_branch(tmp_path, "sess_x", env={}) is None
    assert adopt_green.find_adoptable_branch(tmp_path, "", env={"CEREBRUM_BUILDS_GITHUB_TOKEN": "t"}) is None


def test_the_continue_door_tries_adoption_before_rebuilding():
    import inspect

    from app.factory import platform_chat_flow as f

    src = inspect.getsource(f.start_or_resume_coder)
    assert src.index("_adopt_green_build") < src.index("resume_failed_platform")
