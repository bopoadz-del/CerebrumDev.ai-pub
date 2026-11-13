"""F-R1: the WRITER rework seed is derived from the committed tree.

Before F-R1 the seed copied only app/, docs/, tests/ and scripts/, so a
writer's top-level files (alembic/, alembic.ini, requirements*.txt, ...) were
missing from the rework tree and the writer re-made them. Each test pairs a
file that must be seeded with one that must not, decided by the same rule.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from app.factory.build.runner import RoleRunner, rework_seed_paths

OWNED = ("Dockerfile", "app/domain_ops.py")


@pytest.fixture(autouse=True)
def _owned(monkeypatch):
    monkeypatch.setattr("app.factory.build.factory_owned.factory_owned_paths", lambda: OWNED)


def _tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "writer_file, owned_file",
    [
        ("alembic.ini", "Dockerfile"),
        ("requirements.txt", "app/domain_ops.py"),
        ("alembic/versions/0001_baseline.py", "Dockerfile"),
        ("requirements-dev.txt", "app/domain_ops.py"),
    ],
)
def test_top_level_writer_file_is_seeded_factory_owned_is_not(tmp_path, writer_file, owned_file):
    root = _tree(tmp_path, {writer_file: "W", owned_file: "F"})
    seeded = rework_seed_paths(root)
    assert writer_file in seeded
    assert owned_file not in seeded


@pytest.mark.parametrize(
    "app_file, vendor_file",
    [
        ("app/actions/progress.py", "vendor/blocks/audit/block.py"),
        ("app/services/ledger/rules.py", "vendor/cerebrum/blocks/pdf.py"),
        ("app/actions/notice.py", "runtime_kit/loader.py"),
        ("frontend/src/main.ts", "blocks.lock.json"),
    ],
)
def test_nested_app_file_is_seeded_vendor_file_is_not(tmp_path, app_file, vendor_file):
    lock = {"blocks": {"audit": {"path": "vendor/blocks/audit"}}, "runtime": {"path": "runtime_kit", "files": []}}
    files = {app_file: "A", vendor_file: "V", "blocks.lock.json": json.dumps(lock)}
    root = _tree(tmp_path, files)
    seeded = rework_seed_paths(root)
    assert app_file in seeded
    assert vendor_file not in seeded


@pytest.mark.parametrize(
    "kept, artifact",
    [
        ("docs/openapi.json", "data/cerebrum.db"),
        ("app/models.py", "app/__pycache__/models.cpython-311.pyc"),
        ("scripts/acceptance_notes.md", "build_ledger.jsonl"),
        ("tests/test_progress.py", "docs/writer_prompt.txt"),
        (".env.example", ".pytest_cache/v/cache/lastfailed"),
    ],
)
def test_build_artifacts_are_not_seeded(tmp_path, kept, artifact):
    root = _tree(tmp_path, {kept: "K", artifact: "X"})
    seeded = rework_seed_paths(root)
    assert kept in seeded
    assert artifact not in seeded


@pytest.mark.parametrize("sealed, seeded_vendor", [(("vendor/**",), False), ((), True)])
def test_sealed_globs_decide_vendor(tmp_path, sealed, seeded_vendor):
    root = _tree(tmp_path, {"vendor/extra/x.py": "V", "app/main.py": "A"})
    seeded = rework_seed_paths(root, sealed=sealed)
    assert ("vendor/extra/x.py" in seeded) is seeded_vendor
    assert "app/main.py" in seeded


@pytest.mark.parametrize("extra", ["alembic.ini", "requirements.txt"])
def test_seed_staging_copies_top_level_files_byte_identical(tmp_path, extra):
    dest = _tree(tmp_path / "dest", {extra: "line-1\nline-2\n", "app/actions/a.py": "H\n", "Dockerfile": "F\n"})
    staging = tmp_path / "staging"
    staging.mkdir()
    kept = RoleRunner._seed_rework_staging(types.SimpleNamespace(workspace=dest), staging)
    assert (staging / extra).read_bytes() == (dest / extra).read_bytes()
    assert not (staging / "Dockerfile").exists()
    assert kept == 2


@pytest.mark.parametrize("writer_file", ["app/actions/a.py", "alembic.ini"])
def test_real_factory_owned_set_is_never_seeded(tmp_path, monkeypatch, writer_file):
    monkeypatch.undo()
    from app.factory.build.factory_owned import factory_owned_paths

    owned = factory_owned_paths()
    assert owned
    assert writer_file not in owned
    root = _tree(tmp_path, {**{rel: "F" for rel in owned}, writer_file: "H"})
    seeded = rework_seed_paths(root)
    assert seeded == [writer_file]
