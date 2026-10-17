"""The emitted lifecycle suite expects only DECLARED data back from the store.

Live 2026-10-06 (879ed1e1, vineyard repro): TESTER re-stamps
tests/test_data_lifecycle.py from the product's declared models (#660,
declared_specs). A model's FIELDS lists ``id`` -- the row id the STORE
assigns -- and the reader typed it ``str``, so the lifecycle sample inserted
``id='s10-row'`` and read back the store's own integer id:
``assert 1 == 's10-row'`` in both the schema-change and restore-drill tests.
The writer may not edit a Factory test, so its rework round was spent for
nothing and the build stopped SAME_FAILURE_TWICE.

Store-managed columns (data_lifecycle.STORE_MANAGED_COLUMNS) are never
declared data: not a column of COLUMNS, not in a sample, not migrated as a
declared field. And an emitted sample that expects an undeclared key is the
Factory's defect, refused at render time -- never a writer rework.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.data_lifecycle import (
    STORE_MANAGED_COLUMNS,
    EmittedSuiteContractError,
    check_sample_is_declared,
    columns_map,
    first_entity_sample,
    render_product_tests,
    render_store,
    sample_for_spec,
)
from app.factory.build.declared_specs import specs_from_product_models
from app.factory.build.runner import RoleRunner
from tests.factory.conftest import stub_coder_patches

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

#: A declared-model spec exactly as declared_specs reads it from FIELDS: the
#: model lists the store's own columns among its fields.
MODEL_SPEC = {
    "lantern_ledger": {
        "entity": "lantern_ledger",
        "fields": [
            {"name": "id", "type": "str"},
            {"name": "tenant_id", "type": "str"},
            {"name": "reference", "type": "str"},
            {"name": "wick_count", "type": "int"},
            {"name": "status", "type": "str", "allowed_values": ["open", "closed"]},
        ],
    }
}


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_store_managed_columns_are_never_declared_data():
    cols = columns_map(MODEL_SPEC)["lantern_ledger"]
    assert cols == ["reference", "wick_count", "status"]
    sample = sample_for_spec(MODEL_SPEC["lantern_ledger"])
    assert not set(sample) & set(STORE_MANAGED_COLUMNS)
    entity, first = first_entity_sample(MODEL_SPEC)
    assert entity == "lantern_ledger" and set(first) == set(cols)


def test_the_store_renders_its_managed_columns_from_the_one_definition():
    src = render_store(MODEL_SPEC)
    assert f"names = [*{list(STORE_MANAGED_COLUMNS)!r}, *COLUMNS[entity]]" in src
    # COLUMNS (what save() inserts from a record) carries no managed column.
    assert "'lantern_ledger': ['reference', 'wick_count', 'status']" in src


def test_a_sample_expecting_an_undeclared_key_is_refused_at_render_time():
    with pytest.raises(EmittedSuiteContractError):
        check_sample_is_declared("lantern_ledger", {"id": "s10-row"}, ["reference"])
    # The real renderer never trips it: sample and columns share one source.
    assert "SAMPLE = {" in render_product_tests(MODEL_SPEC)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path_factory.mktemp("lifecycle") / "build"
    with stub_coder_patches():
        outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    return out


def _restamp_from_declared_models(root: Path) -> None:
    """What TESTER does: re-render the lifecycle suite from the product's
    own declared models. The live writer's models list the store's ``id``
    first in FIELDS (``FIELDS = ['id', 'reference', ...]``, live vineyard
    models.py); the stub writer's do not, so the copy is given that shape."""
    models = root / "app" / "models.py"
    text = models.read_text(encoding="utf-8")
    if "FIELDS = ['id'," not in text:
        models.write_text(text.replace("FIELDS = [", "FIELDS = ['id', "), encoding="utf-8")
    specs = specs_from_product_models(root)
    assert any(
        f.get("name") == "id" for s in specs.values() for f in s.get("fields") or []
    ), "precondition: the declared models list the store-managed id"
    (root / "tests" / "test_data_lifecycle.py").write_text(
        render_product_tests(specs), encoding="utf-8"
    )


def _run_lifecycle(root: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_data_lifecycle.py", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
        env=env,
    )


def test_the_suite_restamped_from_declared_models_is_green(built):
    _restamp_from_declared_models(built)
    proc = _run_lifecycle(built)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_restore_that_loses_rows_still_fails(built, tmp_path):
    import shutil

    broken = tmp_path / "broken"
    shutil.copytree(built, broken)
    _restamp_from_declared_models(broken)
    backup_py = broken / "app" / "backup.py"
    src = backup_py.read_text(encoding="utf-8")
    # The product's restore "succeeds" but brings back an empty database.
    backup_py.write_text(
        src
        + "\n\n_real_restore = restore_backup\n\n"
        "def restore_backup(archive):\n"
        "    from app.migrations import upgrade_head\n"
        "    upgrade_head()\n"
        "    return None\n",
        encoding="utf-8",
    )
    proc = _run_lifecycle(broken)
    assert proc.returncode != 0
    assert "test_restore_drill_backup_wipe_restore_rows" in proc.stdout
