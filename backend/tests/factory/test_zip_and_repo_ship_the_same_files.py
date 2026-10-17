"""The zip and the cerebrum-builds repo ship the same files.

They used to apply different rules. The zip skipped bytecode and tool caches
at any depth; the push to cerebrum-builds ignored only ``.git``. Build
sess_065fc3eac75c4f62 (FinOps) pushed 131 ``.pyc`` files and a ``data/``
tree -- an empty cerebrum.db plus the TESTER probes' sample uploads -- that
its zip would never have carried. Both now use builds_push.is_exported.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.factory.build.builds_push import _sync_workspace_onto_tree, is_exported
from app.routers.session_product import zip_generated_product

PRODUCT = {
    "app/main.py": "app = None\n",
    "app/actions/record.py": "CAPABILITY_ID = 'record'\n",
    "app/data/seed.json": "{}\n",
    "frontend/src/data/labels.ts": "export const x = 1\n",
    "Dockerfile": "FROM python:3.11\n",
}
NOT_PRODUCT = {
    # The Factory's own record of the build, and how it was manufactured.
    # The customer gets the platform, not the transcript of making it.
    "build_ledger.jsonl": "{}",
    "product-dna/generation_manifest.json": "{}",
    "docs/writer_prompt.txt": "prompt",
    "docs/coder_brief.md": "brief",
    "docs/coder_receipt.json": "{}",
    "docs/writer_argv.json": "{}",
    "docs/writer_progress.jsonl": "{}",
    "docs/writer_progress.log": "STEP 1",
    "docs/build_provenance.json": "{}",
    "app/__pycache__/main.cpython-311.pyc": "x",
    "app/actions/__pycache__/record.cpython-311.pyc": "x",
    "tests/.pytest_cache/v/cache/lastfailed": "{}",
    "data/cerebrum.db": "",
    "data/storage/file_2d44b1dadba857c7": "sample",
    ".codewhale/lock": "1",
    "app/.codewhale/state": "1",
    "stray.pyo": "x",
}


@pytest.mark.parametrize("rel", sorted(PRODUCT))
def test_product_files_are_exported(rel):
    assert is_exported(Path(rel))


@pytest.mark.parametrize("rel", sorted(NOT_PRODUCT))
def test_runtime_state_and_caches_are_not(rel):
    assert not is_exported(Path(rel))


def _workspace(root: Path) -> Path:
    for rel, body in {**PRODUCT, **NOT_PRODUCT}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _files(root: Path) -> set:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_the_repo_push_carries_exactly_the_product(tmp_path):
    ws = _workspace(tmp_path / "ws")
    dest = tmp_path / "repo"

    _sync_workspace_onto_tree(ws, dest)

    assert _files(dest) == set(PRODUCT)


def test_the_zip_carries_exactly_the_product(tmp_path):
    ws = _workspace(tmp_path / "ws")

    archive = zip_generated_product(ws, tmp_path / "export")

    with zipfile.ZipFile(archive) as zf:
        names = {n for n in zf.namelist() if not n.endswith("/")}
    names.discard("CODE_CYCLE_PROTOTYPE.txt")  # marker added to code-cycle zips only
    assert names == set(PRODUCT)


def test_repo_and_zip_agree_file_for_file(tmp_path):
    ws = _workspace(tmp_path / "ws")
    dest = tmp_path / "repo"
    _sync_workspace_onto_tree(ws, dest)
    archive = zip_generated_product(ws, tmp_path / "export")
    with zipfile.ZipFile(archive) as zf:
        zipped = {n for n in zf.namelist() if not n.endswith("/")}
    zipped.discard("CODE_CYCLE_PROTOTYPE.txt")

    assert _files(dest) == zipped
