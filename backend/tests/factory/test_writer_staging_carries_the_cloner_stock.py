"""The writer's checkout must physically carry the CLONER's stock.

Live 20 Nov, fleet-ops build: the writer's staging tree lacked
``vendor/blocks/audit/block.py`` although the CLONER had vendored it, so the
post-pass suite failed ``locked block 'audit' is absent from
...staging-writer/vendor/blocks/audit/block.py`` and the grader failed every
REUSE capability. Two writer passes and one rework were spent on a tree that
could never pass. The stock is now seeded read-only into the staging tree
before the pass — the same tree the suite is run in.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from app.factory.build.runner import (
    remove_staging,
    sealed_stock_paths,
    seed_sealed_stock,
)

SMOKE = Path(__file__).resolve().parents[3] / "blueprints/examples/runner_smoke.yaml"
BRIEF = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'


def _stock(root: Path) -> None:
    (root / "vendor" / "blocks" / "audit").mkdir(parents=True)
    (root / "vendor" / "blocks" / "audit" / "block.py").write_text("AUDIT = 1\n", encoding="utf-8")
    (root / "vendor" / "cerebrum" / "core").mkdir(parents=True)
    (root / "vendor" / "cerebrum" / "core" / "runtime.py").write_text("X = 1\n", encoding="utf-8")
    (root / "kits" / "platform").mkdir(parents=True)
    (root / "kits" / "platform" / "manifest.json").write_text(
        '{"name": "Platform"}\n', encoding="utf-8"
    )
    (root / "runtime_guard.py").write_text("GUARD = 1\n", encoding="utf-8")
    (root / "blocks.lock.json").write_text(
        json.dumps(
            {
                "blocks": {"audit": {"path": "vendor/blocks/audit"}},
                "kits": {"platform": {"path": "kits/platform"}},
                "runtime": {
                    "path": "vendor/cerebrum",
                    "files": ["vendor/cerebrum/core/runtime.py", "runtime_guard.py"],
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "rel",
    [
        "vendor/blocks/audit/block.py",
        "blocks.lock.json",
        "kits/platform/manifest.json",
        "vendor/cerebrum/core/runtime.py",
    ],
)
def test_the_sealed_stock_covers_blocks_kits_runtime_and_the_lock(tmp_path, rel):
    _stock(tmp_path)
    assert rel in sealed_stock_paths(tmp_path)


@pytest.mark.parametrize("rel", ["runtime_guard.py", "vendor/cerebrum/core/runtime.py"])
def test_a_lock_vendored_file_is_carried_even_outside_vendor(tmp_path, rel):
    """The lock names the runtime files it vendored; each is stock even when
    it does not sit under vendor/ (a file passed straight to the walker)."""
    _stock(tmp_path)
    assert rel in sealed_stock_paths(tmp_path)


@pytest.mark.parametrize(
    "rel", ["vendor/blocks/audit/block.py", "kits/platform/manifest.json"]
)
def test_the_staging_is_seeded_with_the_stock_read_only(tmp_path, rel):
    dest = tmp_path / "product"
    dest.mkdir()
    _stock(dest)
    staging = tmp_path / "staging"
    staging.mkdir()
    assert seed_sealed_stock(dest, staging) > 0
    copied = staging / rel
    assert copied.is_file()
    assert copied.read_bytes() == (dest / rel).read_bytes()
    assert not (stat.S_IMODE(copied.stat().st_mode) & stat.S_IWUSR)


@pytest.mark.parametrize("preexisting", ["the writer's own copy\n", "written by the pass\n"])
def test_an_existing_staging_file_is_never_overwritten(tmp_path, preexisting):
    dest = tmp_path / "product"
    dest.mkdir()
    _stock(dest)
    staging = tmp_path / "staging"
    target = staging / "vendor/blocks/audit/block.py"
    target.parent.mkdir(parents=True)
    target.write_text(preexisting, encoding="utf-8")
    seed_sealed_stock(dest, staging)
    assert target.read_text(encoding="utf-8") == preexisting


@pytest.mark.parametrize(
    "rel", ["vendor/blocks/audit/block.py", "kits/platform/manifest.json"]
)
def test_remove_staging_removes_read_only_seeded_files(tmp_path, rel):
    dest = tmp_path / "product"
    dest.mkdir()
    _stock(dest)
    staging = tmp_path / "staging"
    staging.mkdir()
    seed_sealed_stock(dest, staging)
    assert (staging / rel).is_file()
    remove_staging(staging)
    assert staging.exists() is False


def test_the_writer_phase_seeds_the_stock_before_the_role_runs(tmp_path):
    """The wiring: the phase seeds the staging tree, so the role (and the
    suite after it) sees the stock."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build import runner as runner_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleResult

    out = tmp_path / "build"
    out.mkdir()
    _stock(out)
    seen = {}

    def fake_writer(ctx):
        tree = ctx.workspace.workspace
        seen["block"] = (tree / "vendor/blocks/audit/block.py").is_file()
        seen["lock"] = (tree / "blocks.lock.json").is_file()
        return RoleResult(ok=True, detail="stub")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = fake_writer
    runner = runner_mod.RoleRunner(
        load_blueprint(SMOKE),
        out,
        roles=roles,
        brief=BRIEF,
        budget=runner_mod.BuildBudget(max_rework=0, wall_clock_s=30),
    )
    runner.ledger.start_run(product_id="p", inputs_hash="h")
    runner._run_phase(BuildRole.WRITER, ())
    assert seen["block"] is True
    assert seen["lock"] is True
