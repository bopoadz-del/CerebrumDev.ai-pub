"""The writer never authors a Factory-owned file (owner rule, 2026-10-08).

Live: the cycle-2 fintech writer (sess_59090c3bd0964425,
build/plt_464389e32e544810 @ 22e6be03) invented
docs/provenance/provenance.json from the blueprint and blocks.lock.json; the
gap-filling converge kept it, so the Factory's own provenance never landed and
provenance_complete failed on unknown commits. Nothing named the overwrite.

Now: one declared list of Factory-owned paths; a writer pass that creates,
edits or deletes one has the Factory's version put back and is sent to rework
naming the path -- never a silent overwrite, never silently kept.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build import factory_owned


# -- the one list -------------------------------------------------------------------


def test_the_list_is_derived_from_the_stamps_plus_provenance_and_the_store_gate():
    from app.factory.build.builds_push import STORE_GATE_PATH
    from app.factory.build.stamp_registry import owned_paths

    paths = set(factory_owned.factory_owned_paths())
    assert set(owned_paths()) <= paths
    assert {factory_owned.PROVENANCE_REL, STORE_GATE_PATH} <= paths
    assert len(paths) == len(set(owned_paths()) | {factory_owned.PROVENANCE_REL, STORE_GATE_PATH})


def test_the_list_carries_the_handovers_named_paths():
    paths = set(factory_owned.factory_owned_paths())
    for rel in (
        "docs/provenance/provenance.json",
        "scripts/acceptance.py",
        "scripts/factory_checks.py",
        "conftest.py",
        ".github/workflows/store-gate.yml",
    ):
        assert rel in paths, rel


# -- detection ------------------------------------------------------------------------


def test_created_modified_and_deleted_are_each_named(tmp_path):
    paths = ["a.txt", "b.txt", "c.txt", "d.txt"]
    (tmp_path / "b.txt").write_text("factory", encoding="utf-8")
    (tmp_path / "c.txt").write_text("factory", encoding="utf-8")
    (tmp_path / "d.txt").write_text("same", encoding="utf-8")
    before = factory_owned.snapshot(tmp_path, paths)
    (tmp_path / "a.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "b.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "c.txt").unlink()
    touched = factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path, paths))
    assert touched == [
        {"path": "a.txt", "change": "created"},
        {"path": "b.txt", "change": "modified"},
        {"path": "c.txt", "change": "deleted"},
    ]


def test_restore_puts_the_factorys_version_back(tmp_path):
    paths = ["a.txt", "b.txt", "c.txt"]
    (tmp_path / "b.txt").write_text("factory", encoding="utf-8")
    (tmp_path / "c.txt").write_text("factory c", encoding="utf-8")
    before = factory_owned.snapshot(tmp_path, paths)
    (tmp_path / "a.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "b.txt").write_text("writer", encoding="utf-8")
    (tmp_path / "c.txt").unlink()
    touched = factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path, paths))
    factory_owned.restore(tmp_path, before, touched)
    assert not (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "factory"
    assert (tmp_path / "c.txt").read_text(encoding="utf-8") == "factory c"
    assert factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path, paths)) == []


def test_the_record_is_factory_internal_and_never_shipped():
    from app.factory.build.builds_push import FACTORY_INTERNAL_PATHS

    assert factory_owned.VIOLATIONS_REL in FACTORY_INTERNAL_PATHS


# -- the WRITER gate turns it into rework naming the path ------------------------------


def test_the_writer_gate_sends_a_touched_factory_file_to_rework_by_name(tmp_path):
    from app.factory.build.gates import GateContext, gate_writer_contract
    from app.factory.build.authority import BuildRole

    factory_owned.record(tmp_path, [{"path": factory_owned.PROVENANCE_REL, "change": "created"}])
    result = gate_writer_contract(GateContext(workspace=tmp_path, role=BuildRole.WRITER))
    assert result.ok is False
    assert result.reason == "writer_authored_factory_file"
    assert any(factory_owned.PROVENANCE_REL in f and "Factory-owned" in f for f in result.findings)


def test_a_clean_pass_record_does_not_trip_the_gate(tmp_path):
    from app.factory.build.gates import GateContext, gate_writer_contract
    from app.factory.build.authority import BuildRole

    factory_owned.record(tmp_path, [])
    result = gate_writer_contract(GateContext(workspace=tmp_path, role=BuildRole.WRITER))
    assert result.reason != "writer_authored_factory_file"


# -- the production (CodeWhale) path --------------------------------------------------


def _run_pass(tmp_path, monkeypatch, write):
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from tests.factory.test_codewhale_writer_dispatch import (
        _ctx,
        _plant_authored_handler,
        _receipt,
    )

    ctx = _ctx(tmp_path)
    dest = tmp_path / "build"
    _plant_authored_handler(dest)

    def worker(_prompt, root, **_kw):
        write(root)
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", worker)
    result = _run_writer_via_codewhale_worker(ctx)
    return ctx, dest, result


def _writer_invents_provenance(root):
    from pathlib import Path

    target = Path(root) / factory_owned.PROVENANCE_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"product_id": "product", "schema_version": "1.0.0", "sources": [], "bindings": []}),
        encoding="utf-8",
    )


def test_a_writer_that_invents_provenance_is_recorded_and_never_kept(tmp_path, monkeypatch):
    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, _writer_invents_provenance)
    touched = factory_owned.recorded(dest)
    # The Factory's record is there before the pass (cycle 7), so writing it
    # is a modification -- still recorded, still never kept.
    assert any(
        row["path"] == factory_owned.PROVENANCE_REL and row["change"] in ("created", "modified")
        for row in touched
    ), touched
    # The writer's invention is gone; whatever provenance the product carries
    # now is the Factory's own (converge writes it after the writer).
    prov = dest / factory_owned.PROVENANCE_REL
    if prov.exists():
        data = json.loads(prov.read_text(encoding="utf-8"))
        assert "bindings" not in data and data.get("schema") == "factory_provenance.v1"


def test_a_writer_that_edits_a_stamped_file_is_recorded_and_restored(tmp_path, monkeypatch):
    from pathlib import Path

    rel = "scripts/factory_checks.py"
    (tmp_path / "build" / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "build" / rel).write_text("# the factory's self-check\n", encoding="utf-8")

    def edit(root):
        (Path(root) / rel).write_text("# the writer's version\n", encoding="utf-8")

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, edit)
    assert {"path": rel, "change": "modified"} in factory_owned.recorded(dest)
    assert (dest / rel).read_text(encoding="utf-8") != "# the writer's version\n"


def test_a_clean_writer_pass_records_nothing(tmp_path, monkeypatch):
    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, lambda _root: None)
    assert factory_owned.recorded(dest) == []


# -- the prompt -----------------------------------------------------------------------------


def test_the_prompt_names_every_factory_owned_file_and_asks_for_none():
    from app.factory.build.writer_prompt import render_writer_prompt

    class _Bp:
        product_id = product_name = vertical = summary = "probe"

    prompt = render_writer_prompt(_Bp(), brief="x")
    head, _, owned_section = prompt.partition("FACTORY-OWNED FILES")
    assert owned_section, "the prompt has no Factory-owned list"
    for rel in factory_owned.factory_owned_paths():
        assert f"- {rel}" in owned_section, rel
    # The OUTPUT list no longer asks the writer for a Factory-owned file.
    output = head.partition("OUTPUT")[2]
    for rel in factory_owned.factory_owned_paths():
        assert f"- {rel}\n" not in output, rel


# -- the Factory's files are there BEFORE the writer starts --------------------------
#
# Live (cycle 5, d024b231 -- co-op sess_47e920344d24453e, fintech
# sess_61d2b2fc79184b4b): the Factory rendered its owned files only after
# the WRITER (the deploy modules after the pass, the refresh set before
# TESTER), so a writer pass found .github/workflows/ci.yml, app/health.py,
# app/observe.py, app/revision.py and constraints.txt ABSENT, created them for
# a complete product, and was stopped for authoring Factory files. Every
# file the Factory can render without the writer's output is now stamped
# before each pass; touching one is still a violation, sent to rework.

PRESTAMPED = (
    # Cycle 7 (a3e1fd7): TESTER's bootstrap and the provenance record were
    # still rendered after the pass; writers created them for their own
    # self-check and three builds (smoke A, fintech, vineyard) stopped on it.
    "conftest.py",
    ".github/workflows/ci.yml",
    "scripts/acceptance.py",
    "scripts/factory_checks.py",
    "scripts/release_gate.py",
    "app/health.py",
    "app/observe.py",
    "app/revision.py",
)


def test_the_factorys_renderable_files_exist_when_the_writer_starts(tmp_path, monkeypatch):
    seen = {}

    def look(root):
        from pathlib import Path

        seen.update({rel: (Path(root) / rel).is_file() for rel in PRESTAMPED})

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, look)
    assert seen and all(seen.values()), {k: v for k, v in seen.items() if not v}
    # A writer that leaves them alone touched nothing.
    assert factory_owned.recorded(dest) == []


def test_every_prestamped_path_is_factory_owned():
    owned = set(factory_owned.factory_owned_paths())
    assert set(PRESTAMPED) <= owned


def test_a_writer_that_edits_a_prestamped_file_is_still_sent_to_rework(tmp_path, monkeypatch):
    from pathlib import Path

    def edit(root):
        (Path(root) / "app/health.py").write_text("# the writer's health\n", encoding="utf-8")

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, edit)
    assert {"path": "app/health.py", "change": "modified"} in factory_owned.recorded(dest)
    assert (dest / "app/health.py").read_text(encoding="utf-8") != "# the writer's health\n"


# -- the same failure twice is the same files touched the same way -------------------


def _touched_verdict(tmp_path, rows):
    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import GateContext, gate_writer_contract

    factory_owned.record(tmp_path, rows)
    return gate_writer_contract(GateContext(workspace=tmp_path, role=BuildRole.WRITER))


def test_different_files_touched_are_different_failures(tmp_path):
    from app.factory.build.runner import _failure_keys

    a = _touched_verdict(tmp_path / "a", [{"path": "scripts/factory_checks.py", "change": "modified"}])
    b = _touched_verdict(tmp_path / "b", [{"path": ".github/workflows/ci.yml", "change": "created"}])
    assert set(_failure_keys(a)).isdisjoint(_failure_keys(b))


def test_the_same_files_touched_the_same_way_is_the_same_failure(tmp_path):
    from app.factory.build.runner import _failure_keys

    rows = [{"path": "app/health.py", "change": "modified"}, {"path": "conftest.py", "change": "created"}]
    a = _touched_verdict(tmp_path / "a", rows)
    b = _touched_verdict(tmp_path / "b", list(reversed(rows)))
    assert _failure_keys(a) == _failure_keys(b)


def test_the_provenance_record_is_rendered_before_the_writer_from_the_build_inputs(tmp_path):
    """The record comes from the blueprint, plan and resolved commits the
    writer step already holds -- not from the writer's output."""
    from tests.factory.test_provenance_is_the_factorys import _ctx as real_ctx

    ctx = real_ctx(tmp_path)
    root = Path(ctx.workspace.destination)
    changed = factory_owned.prestamp_late_files(root, ctx)
    assert factory_owned.PROVENANCE_REL in changed
    prov = json.loads((root / factory_owned.PROVENANCE_REL).read_text(encoding="utf-8"))
    assert prov["factory_commit"] == "f" * 40 and prov["blocks_commit"] == "b" * 40


def test_the_writer_step_hands_its_context_to_the_prestamp():
    import inspect

    from app.factory.build import roles_handlers

    src = inspect.getsource(roles_handlers._run_writer_via_codewhale_worker)
    assert "factory_owned.prestamp(Path(dest), ctx.blueprint, ctx)" in src


def test_the_prestamped_bootstrap_is_testers_own(tmp_path, monkeypatch):
    from app.factory.build.roles_handlers import _CONFTEST

    seen = {}

    def look(root):
        from pathlib import Path

        seen["text"] = (Path(root) / "conftest.py").read_text(encoding="utf-8")

    _run_pass(tmp_path, monkeypatch, look)
    assert seen["text"] == _CONFTEST


def test_prestamp_never_overwrites_a_bootstrap_or_record_already_there(tmp_path):
    (tmp_path / "conftest.py").write_text("# already here\n", encoding="utf-8")
    changed = factory_owned.prestamp_late_files(tmp_path, ctx=None)
    assert "conftest.py" not in changed
    assert (tmp_path / "conftest.py").read_text(encoding="utf-8") == "# already here\n"
