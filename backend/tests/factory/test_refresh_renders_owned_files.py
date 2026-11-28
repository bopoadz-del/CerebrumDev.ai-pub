"""The refresh renders every Factory-owned file it owns, present or not.

Live cycle 3 (5410237b, 2026-10-08): the writer-owned-files guard (#710)
rightly removed a ci.yml the CodeWhale writer had created, but the refresh
re-rendered .github/workflows/ci.yml only when one already existed. The push
then carried the base branch's pytest-only workflow and every build failed the
Store gate on ci_present_and_full_suite and audit_clean (20/22). A file the
Factory owns is the Factory's to render; its absence is never a reason to skip.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build import factory_owned, stamp_registry
from app.factory.build.factory_refresh import refresh_factory_files


def _refresh_owned() -> list:
    return [
        p for s in stamp_registry.stamps()
        if s.kind == stamp_registry.OWNED and s.apply is stamp_registry._refresh
        for p in s.paths
    ]


def test_every_owned_path_of_the_refresh_is_rendered_into_a_tree_that_lacks_it(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    refresh_factory_files(tmp_path, "Any Product", render_absent=True)
    missing = [p for p in _refresh_owned() if not (tmp_path / p).is_file()]
    assert not missing, missing


def test_a_writer_created_owned_file_removed_by_the_guard_is_rendered_by_the_refresh(tmp_path):
    ci = Path(".github") / "workflows" / "ci.yml"
    before = factory_owned.snapshot(tmp_path)
    (tmp_path / ci).parent.mkdir(parents=True)
    (tmp_path / ci).write_text("name: writer's own\n", encoding="utf-8")
    touched = factory_owned.writer_touched(before, factory_owned.snapshot(tmp_path))
    factory_owned.restore(tmp_path, before, touched)
    assert not (tmp_path / ci).exists()
    refresh_factory_files(tmp_path, "Any Product", render_absent=True)
    from app.factory.build.store_acceptance import render_github_ci

    assert (tmp_path / ci).read_text(encoding="utf-8") == render_github_ci()


def test_a_replayed_or_re_entered_tree_is_not_given_files_it_never_had(tmp_path):
    """Default (replay, re-entry): the tree is refreshed as it is."""
    (tmp_path / "app").mkdir()
    assert refresh_factory_files(tmp_path, "Any Product") == []


def test_the_build_runner_refreshes_with_render_absent():
    import inspect

    from app.factory.build.runner import RoleRunner

    assert "render_absent=True" in inspect.getsource(RoleRunner._refresh_factory_files)
