"""K-F7 PR A: the writer's formula findings file is a Factory-internal record.

builds_push.FACTORY_INTERNAL_PATHS is what withholds a file from the export and
excludes it from the writer-overwrite probe; the findings file must be in it,
pinned to the literal path (not just the constant).
"""
from __future__ import annotations

import pytest

LITERAL = ".factory/writer_formula_findings.json"


def _bp():
    import app.factory.build.builds_push as builds_push

    return builds_push


def _fr():
    import app.factory.build.formula_resolution as formula_resolution

    return formula_resolution


@pytest.mark.parametrize("path, owned", [
    (LITERAL, True),
    (".factory/writer_formula_findings.jsonl", False),
])
def test_findings_file_is_factory_internal(path, owned):
    assert (path in _bp().FACTORY_INTERNAL_PATHS) is owned


@pytest.mark.parametrize("attr, expect", [
    ("FORMULA_FINDINGS_REL", LITERAL),
    ("WRITER_DEFINITIONS_REL", None),
])
def test_findings_constant_is_the_owned_literal(attr, expect):
    value = getattr(_fr(), attr)
    if expect is None:
        assert value not in _bp().FACTORY_INTERNAL_PATHS
    else:
        assert value == expect
        assert value in _bp().FACTORY_INTERNAL_PATHS
