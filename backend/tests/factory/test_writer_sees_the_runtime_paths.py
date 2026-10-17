"""The writer is told which paths its image must carry (FACTORY defect, cycles 8-9).

Live, twice, two different writers, the same omission:

* cycle 8 co-op (sess_ece9a0e5c44e4650, build/plt_dfa8d788d9aa4019):
  "image missing /srv/app/vendor/blocks/analytics/block.py: the Dockerfile
  does not put it in the image", SAME_FAILURE_TWICE;
* cycle 9 smoke A (sess_83c0da8c463948fd, build/plt_f146e43661954e09 @
  efe7cbc0): "image missing /app/vendor/blocks/agent_state_sync/block.py",
  stopped after the rework round that named the missing COPY.

Cause: the WRITER works in a STAGING checkout that starts empty -- the CLONER's
vendored blocks live only in the product tree the pass is merged into. The
writer's first ``ls`` showed app/, docs/, scripts/ and no vendor/; the prompt
said "work in this checkout" and never named a runtime path. The smoke-A
writer's own Dockerfile says it: "vendor/ ... absent from a writer's staging
checkout. A COPY of a path the build context does not carry fails the build,
so neither is listed here". A writer cannot avoid a defect in a tree it is
not shown, so the Factory now tells it: the paths the app's loader reads are
read off the product's lock (data) and rendered into the prompt, and the
rework cause says the build context is the merged product tree.

Synthetic products only: nothing here is a real blueprint, block or branch.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build import image_sufficiency
from app.factory.build.writer_prompt import render_writer_prompt


class _Bp:
    product_id = product_name = vertical = summary = "probe"


def _locked_product(root: Path, locked: dict, *, present=None) -> Path:
    """A product tree whose lock declares ``locked`` ({id: path}); every path
    in ``present`` (default: all of them) carries a block.py."""
    root.mkdir(parents=True, exist_ok=True)
    for rel in (locked.values() if present is None else present):
        (root / rel).mkdir(parents=True, exist_ok=True)
        (root / rel / "block.py").write_text("X = 1\n", encoding="utf-8")
    (root / "blocks.lock.json").write_text(
        json.dumps({"blocks": {bid: {"path": rel} for bid, rel in locked.items()}}),
        encoding="utf-8",
    )
    return root


def _section(prompt: str) -> str:
    head, sep, rest = prompt.partition("BUILD CONTEXT")
    assert sep, "the prompt names no build context"
    return rest.partition("FACTORY-OWNED FILES")[0]


# -- the paths, read off the lock ------------------------------------------------------


def test_runtime_paths_are_the_locked_paths_the_product_tree_carries(tmp_path):
    root = _locked_product(
        tmp_path,
        {"beta": "stock/blocks/beta", "alpha": "stock/blocks/alpha", "gone": "stock/blocks/gone"},
        present=["stock/blocks/beta", "stock/blocks/alpha"],
    )
    assert image_sufficiency.runtime_paths(root) == ["stock/blocks/alpha", "stock/blocks/beta"]


def test_no_lock_means_no_runtime_path(tmp_path):
    assert image_sufficiency.runtime_paths(tmp_path) == []


# -- the prompt states them -----------------------------------------------------------


def test_the_prompt_names_each_runtime_root_the_dockerfile_must_copy():
    prompt = render_writer_prompt(
        _Bp(), brief="x", runtime_paths=["stock/blocks/alpha", "stock/blocks/beta", "kits/one"]
    )
    section = _section(prompt)
    # One line per top-level directory the image must carry, with what it holds.
    assert "- stock/" in section and "- kits/" in section
    assert "stock/blocks/alpha" in section and "stock/blocks/beta" in section
    # Where they are, and what the Dockerfile and .dockerignore must do.
    assert "not in this checkout" in section
    assert "COPY" in section and ".dockerignore" in section
    assert "never create, edit or delete" in section


def test_the_section_is_built_from_the_paths_given_never_from_a_known_name():
    section = _section(render_writer_prompt(_Bp(), brief="x", runtime_paths=["kits/one"]))
    assert "kits/one" in section
    assert "vendor" not in section


def test_no_runtime_path_renders_no_build_context_section():
    prompt = render_writer_prompt(_Bp(), brief="x")
    assert "BUILD CONTEXT" not in prompt
    assert render_writer_prompt(_Bp(), brief="x") == prompt


def test_the_section_is_deterministic_whatever_order_the_paths_arrive_in():
    one = render_writer_prompt(_Bp(), brief="x", runtime_paths=["b/x", "a/y", "b/w"])
    two = render_writer_prompt(_Bp(), brief="x", runtime_paths=["b/w", "a/y", "b/x"])
    assert one == two


# -- the writer step hands the product tree's paths to the prompt ---------------------


def _receipt():
    base = {
        "status": "completed",
        "termination_reason": "resolved",
        "provider": "deepseek",
        "model": "m",
        "output": "built",
        "tools": [{"tool": "write", "path": "app/actions/cap.py"}],
        "error": None,
        "error_category": None,
    }
    base["to_dict"] = lambda self: {
        k: v for k, v in type(self).__dict__.items() if k != "to_dict" and not k.startswith("__")
    }
    return type("R", (), base)()


def test_a_staged_writer_is_told_the_paths_its_checkout_does_not_show(tmp_path, monkeypatch):
    """The live shape: the CLONER's tree is in the destination, the writer's
    checkout (staging) starts without it."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from app.factory.build.workspace import RoleWorkspace

    product = _locked_product(
        tmp_path / "build", {"alpha": "stock/blocks/alpha", "beta": "stock/blocks/beta"}
    )
    staging = tmp_path / ".build.staging-writer"
    ws = RoleWorkspace(BuildRole.WRITER, product, staging=staging)
    ctx = RoleContext(role=BuildRole.WRITER, workspace=ws, blueprint=_Bp(), plan=None, state={})
    seen = {}

    def fake_run(prompt, dest, **_kw):
        seen["prompt"] = prompt
        seen["checkout"] = Path(dest)
        actions = Path(dest) / "app" / "actions"
        actions.mkdir(parents=True, exist_ok=True)
        (actions / "cap.py").write_text('AUTHORED_BY = "codewhale exec"\n', encoding="utf-8")
        return _receipt()

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    _run_writer_via_codewhale_worker(ctx)

    assert not (seen["checkout"] / "stock").exists(), "precondition: the checkout does not show the stock"
    section = _section(seen["prompt"])
    assert "stock/blocks/alpha" in section and "stock/blocks/beta" in section


def test_an_unstaged_writer_that_sees_the_paths_is_not_told_they_are_absent(tmp_path, monkeypatch):
    """The section claims the paths are not in the checkout; when the writer
    works in the product tree itself, that would be untrue, so it is not said."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from app.factory.build.workspace import RoleWorkspace

    product = _locked_product(tmp_path / "build", {"alpha": "stock/blocks/alpha"})
    ws = RoleWorkspace(BuildRole.WRITER, product)
    ctx = RoleContext(role=BuildRole.WRITER, workspace=ws, blueprint=_Bp(), plan=None, state={})
    seen = {}

    def fake_run(prompt, dest, **_kw):
        seen["prompt"] = prompt
        actions = Path(dest) / "app" / "actions"
        actions.mkdir(parents=True, exist_ok=True)
        (actions / "cap.py").write_text('AUTHORED_BY = "codewhale exec"\n', encoding="utf-8")
        return _receipt()

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    _run_writer_via_codewhale_worker(ctx)
    assert "BUILD CONTEXT" not in seen["prompt"]


# -- the rework cause says where the build context is ---------------------------------


def test_the_missing_copy_cause_says_the_context_is_the_merged_product_tree(tmp_path):
    root = _locked_product(tmp_path, {"alpha": "stock/blocks/alpha"})
    (root / "Dockerfile").write_text("FROM python:3.12-slim\nWORKDIR /app\nCOPY app ./app\n", encoding="utf-8")
    cause = image_sufficiency.context_cause(
        "/app/stock/blocks/alpha/block.py", "/app", root, image_sufficiency.read_dockerignore(root)
    )
    assert "stock/blocks/alpha/block.py" in cause and "no COPY/ADD" in cause
    assert "product tree" in cause and "even when your checkout does not" in cause
