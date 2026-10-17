"""The Factory holds no kits (owner rule); the gate recognises one by shape."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_factory_kit_free.py"


def _gate():
    spec = importlib.util.spec_from_file_location("check_factory_kit_free", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_the_real_factory_is_kit_free():
    gate = _gate()
    assert gate.violations(gate.tracked()) == []


def test_a_kits_directory_is_refused_whatever_it_is_called():
    gate = _gate()
    found = gate.violations(["backend/app/factory/kits/zorblat_yards/router.py"])
    assert found and "kits/" in found[0]


def test_a_vertical_runtime_directory_is_refused():
    gate = _gate()
    found = gate.violations(["backend/app/factory/zorblat_runtime/api.py"])
    assert found and "_runtime" in found[0]


def test_an_embedded_product_tree_is_refused_whatever_it_is_called():
    gate = _gate()
    found = gate.violations(["backend/app/zorblat_gen/overlays/zorblat_core/app/routers/x.py"])
    assert found and "embedded" in found[0]


def test_a_kit_shaped_manifest_is_refused(tmp_path):
    gate = _gate()
    rel = "backend/app/factory/zorblat/manifest.json"
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text(json.dumps({"id": "zorblat", "blocks": ["database"]}), encoding="utf-8")
    assert gate.violations([rel], root=tmp_path)
    (tmp_path / rel).write_text(json.dumps({"id": "zorblat", "routes": []}), encoding="utf-8")
    assert gate.violations([rel], root=tmp_path) == []
