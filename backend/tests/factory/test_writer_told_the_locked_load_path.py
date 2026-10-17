"""The writer is told where each locked block must load from, and keeps hearing
it (FACTORY defects, release cycle 9).

Live, two picks, the same stop -- ``locked_block_loaded_elsewhere``,
SAME_FAILURE_TWICE:

* co-op anchor (sess_e41f2375a98342d1, build/plt_b0d888f2309b471e @ efe7cbc0):
  "block analytics loaded from /srv/platform/vendor/cerebrum/blocks/analytics.py,
  not its locked path vendor/blocks/analytics". The writer's app/dispatch.py
  imported each block's class from the runtime package the locked file itself
  imports, skipping the locked file.
* craft marketplace (sess_97062cf0afd54e2a, build/plt_50925fc5d2254b28): "block
  aesthetic_kit loaded from /app/app/blocks/aesthetic_kit.py". The writer, whose
  staging checkout does not show the CLONER's tree, re-implemented every bound
  block under app/blocks/. Its rework fixed the loader (the WRITER gate passed,
  "loads all ... from their locked paths"); a later TESTER rework sent it back
  to an empty checkout with a work list that no longer named the rule, it
  re-authored the tree, re-implemented the blocks and was stopped.

Cause 1: the rule the WRITER gate holds -- a locked block loads from its locked
path or the app refuses to start -- was never in the writer prompt. The prompt
listed the vendored paths as data inside the C-BRIEF; nothing said the loader
must read them. A writer can only learn it by failing the gate, and a later
pass that never failed it does not know it at all. The prompt now states the
rule, with the locked paths and the module files under each, read off the
product's lock (data), on every pass.

Cause 2 (co-op): the rework round's finding was handed to one pass; that pass
touched a Factory-owned file, was re-prompted at no cost -- and the re-prompt's
work list held ONLY the touched files. The finding the rework round existed to
fix vanished; the next pass never heard it and was charged the same failure a
second time. A re-prompt now carries the work list the rejected pass was
handed.

Synthetic products only: nothing here is a real blueprint, block or branch.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build import image_sufficiency
from app.factory.build.writer_prompt import render_writer_prompt

SECTION = "LOCKED BLOCKS"


class _Bp:
    product_id = product_name = vertical = summary = "probe"


def _locked_product(root: Path, locked: dict, *, modules=("entry.py",)) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel in locked.values():
        (root / rel).mkdir(parents=True, exist_ok=True)
        for name in modules:
            (root / rel / name).write_text("X = 1\n", encoding="utf-8")
        (root / rel / "manifest.json").write_text("{}\n", encoding="utf-8")
    (root / "blocks.lock.json").write_text(
        json.dumps({"blocks": {bid: {"path": rel} for bid, rel in locked.items()}}),
        encoding="utf-8",
    )
    return root


def _section(prompt: str) -> str:
    head, sep, rest = prompt.partition(SECTION)
    assert sep, "the prompt does not state where locked blocks load from"
    return rest.partition("FACTORY-OWNED FILES")[0].partition("BUILD CONTEXT")[0]


# -- the entries, read off the lock -------------------------------------------------


def test_locked_entries_are_the_module_files_under_each_locked_path(tmp_path):
    root = _locked_product(
        tmp_path, {"beta": "stock/units/beta", "alpha": "stock/units/alpha"}, modules=("entry.py",)
    )
    assert image_sufficiency.locked_entries(root) == {
        "alpha": ["stock/units/alpha/entry.py"],
        "beta": ["stock/units/beta/entry.py"],
    }


def test_a_locked_path_the_tree_does_not_carry_names_the_path_itself(tmp_path):
    root = _locked_product(tmp_path, {"alpha": "stock/units/alpha"})
    lock = json.loads((root / "blocks.lock.json").read_text(encoding="utf-8"))
    lock["blocks"]["gone"] = {"path": "stock/units/gone"}
    (root / "blocks.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    assert image_sufficiency.locked_entries(root)["gone"] == ["stock/units/gone"]


def test_no_lock_means_no_entry(tmp_path):
    assert image_sufficiency.locked_entries(tmp_path) == {}


# -- the prompt states the rule ------------------------------------------------------


def test_the_prompt_states_the_load_rule_with_every_locked_entry():
    prompt = render_writer_prompt(
        _Bp(),
        brief="x",
        locked_blocks={"alpha": ["stock/units/alpha/entry.py"], "beta": ["stock/units/beta/entry.py"]},
    )
    section = _section(prompt)
    assert "alpha: stock/units/alpha/entry.py" in section
    assert "beta: stock/units/beta/entry.py" in section
    # What the loader must do, and what the gate refuses.
    assert "app.dispatch.load_block" in section
    assert "start-up" in section and "never a fallback" in section
    assert "re-implementation" in section
    assert "refuses" in section


def test_the_rule_is_stated_whether_or_not_the_checkout_shows_the_paths():
    """BUILD CONTEXT is rendered only for paths the checkout does not show; the
    load rule holds either way, so it is stated either way."""
    shown = render_writer_prompt(_Bp(), brief="x", locked_blocks={"alpha": ["stock/units/alpha/entry.py"]})
    hidden = render_writer_prompt(
        _Bp(),
        brief="x",
        runtime_paths=["stock/units/alpha"],
        locked_blocks={"alpha": ["stock/units/alpha/entry.py"]},
    )
    assert SECTION in shown and SECTION in hidden
    assert "BUILD CONTEXT" not in shown and "BUILD CONTEXT" in hidden


def test_no_locked_block_renders_no_section():
    assert SECTION not in render_writer_prompt(_Bp(), brief="x")


def test_the_section_is_deterministic():
    one = render_writer_prompt(_Bp(), brief="x", locked_blocks={"b": ["s/b/e.py"], "a": ["s/a/e.py"]})
    two = render_writer_prompt(_Bp(), brief="x", locked_blocks={"a": ["s/a/e.py"], "b": ["s/b/e.py"]})
    assert one == two


# -- the writer step hands the product tree's entries to the prompt ------------------


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


def _run_writer(tmp_path, monkeypatch, *, staged: bool) -> str:
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from app.factory.build.workspace import RoleWorkspace

    product = _locked_product(tmp_path / "build", {"alpha": "stock/units/alpha"})
    ws = (
        RoleWorkspace(BuildRole.WRITER, product, staging=tmp_path / ".build.staging-writer")
        if staged
        else RoleWorkspace(BuildRole.WRITER, product)
    )
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
    return seen["prompt"]


def test_a_staged_writer_is_told_where_the_locked_blocks_load_from(tmp_path, monkeypatch):
    section = _section(_run_writer(tmp_path, monkeypatch, staged=True))
    assert "alpha: stock/units/alpha/entry.py" in section


def test_an_unstaged_writer_is_told_it_too(tmp_path, monkeypatch):
    section = _section(_run_writer(tmp_path, monkeypatch, staged=False))
    assert "alpha: stock/units/alpha/entry.py" in section


# -- a re-prompt keeps the finding the rejected pass was sent to fix ----------------


def _runner(tmp_path):
    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    root = Path(__file__).resolve().parents[3]
    return RoleRunner(load_blueprint(root / "blueprints/examples/runner_smoke.yaml"), tmp_path / "build")


def _touched(tmp_path: Path, rel: str):
    from app.factory.build import factory_owned
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateContext, gate_writer_contract

    root = tmp_path / "touched"
    factory_owned.record(root, [{"path": rel, "change": "modified"}])
    return gate_writer_contract(GateContext(workspace=root, role=BuildRole.WRITER))


def _finding(reason: str, finding: str):
    from app.factory.build import brief_gates
    from app.factory.build.gates import GateResult

    return GateResult(
        ok=False, gate=brief_gates.WRITER_CONTRACT_CHECK, reason=reason,
        detail=f"{reason}: {finding}", findings=[finding],
    )


def test_a_reprompt_carries_the_findings_the_rejected_pass_was_handed(tmp_path):
    from app.factory.build.authority import BuildRole

    runner = _runner(tmp_path)
    rework = runner.decide(
        BuildRole.WRITER,
        _finding("quillfeather_misload", "unit alpha read from a stray copy: the real finding"),
        rework_used=runner._rework_rounds_this_build(),
    )
    assert rework.kind == "REWORK"
    # That pass touched a Factory-owned file instead: re-prompted at no cost.
    reprompt = runner.decide(
        BuildRole.WRITER,
        _touched(tmp_path, "conftest.py"),
        rework_used=runner._rework_rounds_this_build(),
        handed=rework.work_list,
    )
    assert reprompt.kind == "REPROMPT"
    assert any("conftest.py" in item for item in reprompt.work_list)
    # ...and the finding the rework round exists to fix is still in front of
    # the writer -- it was never re-judged.
    assert any("the real finding" in item for item in reprompt.work_list), reprompt.work_list


def test_a_second_reprompt_does_not_repeat_the_first_ones_touched_files(tmp_path):
    from app.factory.build.authority import BuildRole

    runner = _runner(tmp_path)
    rework = runner.decide(
        BuildRole.WRITER, _finding("quillfeather_misload", "the real finding"),
        rework_used=runner._rework_rounds_this_build(),
    )
    first = runner.decide(
        BuildRole.WRITER, _touched(tmp_path / "1", "conftest.py"),
        rework_used=runner._rework_rounds_this_build(), factory_file_reprompts=0,
        handed=rework.work_list,
    )
    second = runner.decide(
        BuildRole.WRITER, _touched(tmp_path / "2", "constraints.txt"),
        rework_used=runner._rework_rounds_this_build(), factory_file_reprompts=1,
        handed=first.work_list,
    )
    assert second.kind == "REPROMPT"
    assert any("constraints.txt" in item for item in second.work_list)
    assert not any("conftest.py" in item for item in second.work_list), second.work_list
    assert sum("the real finding" in item for item in second.work_list) == 1, second.work_list


def test_the_run_loop_hands_decide_the_pass_work_list():
    import inspect

    from app.factory.build.runner import RoleRunner

    assert "handed=work_list" in inspect.getsource(RoleRunner.run)
