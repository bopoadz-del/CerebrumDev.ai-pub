"""F16: block.json is the canonical UI contract, and divergence is named.

The check runs in one direction on purpose — a field the class declares and
block.json omits never reaches a generated UI. The reverse (block.json
richer than the class) is the normal case and is not a defect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.ui_schema import (
    CANONICAL,
    UiSchemaDivergence,
    assert_no_divergence,
    canonical_fields,
    class_fields,
    inspect_block,
    survey,
)


def _block(root: Path, name: str, ui_schema=None) -> Path:
    d = root / "block_registry" / name
    d.mkdir(parents=True, exist_ok=True)
    body = {"id": name}
    if ui_schema is not None:
        body["ui_schema"] = ui_schema
    (d / "block.json").write_text(json.dumps(body), encoding="utf-8")
    return d


def _module(root: Path, name: str, schema_src: str) -> Path:
    mods = root / "app" / "blocks"
    mods.mkdir(parents=True, exist_ok=True)
    path = mods / f"{name}.py"
    path.write_text(
        "class Block:\n"
        f"    ui_schema = {schema_src}\n",
        encoding="utf-8",
    )
    return path


def test_block_json_is_the_canonical_shape():
    assert CANONICAL == "block.json"


def test_canonical_fields_reads_the_flat_list(tmp_path):
    d = _block(tmp_path, "alpha", [{"name": "action", "widget": "select"},
                                   {"name": "input", "widget": "json"}])
    assert canonical_fields(d) == {"action", "input"}


def test_class_fields_reads_input_and_params_without_importing(tmp_path):
    """Parsed, not imported: a Store block pulls its own dependencies."""
    m = _module(
        tmp_path,
        "alpha",
        "{'input': {'type': 'json'}, "
        "'params': [{'name': 'retention_hours'}, {'name': 'window'}]}",
    )
    assert class_fields(m) == {"input", "retention_hours", "window"}


def test_a_block_json_superset_is_not_divergence(tmp_path):
    """The normal case: block.json carries config the class does not list."""
    d = _block(tmp_path, "alpha", [
        {"name": "input"}, {"name": "action"}, {"name": "retention_hours"},
    ])
    m = _module(tmp_path, "alpha", "{'input': {'type': 'json'}, 'params': []}")

    report = inspect_block(d, m)
    assert report["class_only"] == []
    assert_no_divergence(d, m)  # must not raise


def test_a_field_only_the_class_declares_is_divergence(tmp_path):
    """This direction loses information, so it is the one that is checked."""
    d = _block(tmp_path, "sandboxy", [{"name": "action"}, {"name": "code"}])
    m = _module(
        tmp_path,
        "sandboxy",
        "{'input': {'type': 'json'}, "
        "'params': [{'name': 'max_memory_mb'}, {'name': 'network_allowed'}]}",
    )

    report = inspect_block(d, m)
    assert report["class_only"] == ["input", "max_memory_mb", "network_allowed"]

    with pytest.raises(UiSchemaDivergence) as excinfo:
        assert_no_divergence(d, m)
    assert "max_memory_mb" in str(excinfo.value)
    assert "block.json omits" in str(excinfo.value)


def test_a_block_with_no_class_module_is_not_divergence(tmp_path):
    d = _block(tmp_path, "alpha", [{"name": "input"}])
    report = inspect_block(d, None)

    assert report["has_canonical"] is True
    assert report["has_class"] is False
    assert report["class_only"] == []


def test_an_unparseable_class_schema_does_not_crash(tmp_path):
    """A computed ui_schema is not literal-evaluable; report nothing, not raise."""
    d = _block(tmp_path, "alpha", [{"name": "input"}])
    mods = tmp_path / "app" / "blocks"
    mods.mkdir(parents=True, exist_ok=True)
    m = mods / "alpha.py"
    m.write_text("BASE = {}\nclass B:\n    ui_schema = dict(BASE)\n", encoding="utf-8")

    assert class_fields(m) == set()
    assert inspect_block(d, m)["class_only"] == []


def test_survey_counts_both_shapes_and_lists_divergence(tmp_path):
    _block(tmp_path, "good", [{"name": "input"}, {"name": "action"}])
    _module(tmp_path, "good", "{'input': {'type': 'json'}, 'params': []}")
    _block(tmp_path, "bad", [{"name": "action"}])
    _module(tmp_path, "bad", "{'input': {'type': 'json'}, 'params': [{'name': 'cpu'}]}")

    result = survey(tmp_path / "block_registry", tmp_path / "app" / "blocks")

    assert result["blocks"] == 2
    assert result["with_canonical"] == 2
    assert result["with_class"] == 2
    assert [d["block_id"] for d in result["diverging"]] == ["bad"]
    assert result["diverging"][0]["class_only"] == ["cpu", "input"]
