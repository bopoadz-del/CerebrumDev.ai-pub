"""The roster stamp never rewrites the product's own capability manifest.

Live 2026-10-07 (9de69276, vineyard repro): the product's ``app/jobs.py``
declared ``CAPABILITIES`` as a roster computed from its own models, and its
capability routes were registered from it. ``stamp_roster`` re-rendered the
whole module, kept only a *literal* manifest, and wrote ``CAPABILITIES = []``
-- every capability route vanished, all eight baseline POSTs answered 404,
and writer_behaviour stopped the build (SAME_FAILURE_TWICE). The writer
could not converge on a manifest the Factory kept erasing.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from app.factory.build.factory_block import begin_line, outside
from app.factory.build.kernel_publish import (
    JOBS_REL,
    render_roster,
    roster_titles,
    stamp_roster,
)

# The live shape: a manifest computed from the product's own models module.
COMPUTED_JOBS = '''"""The platform's kernel roster and capability manifest."""
from __future__ import annotations

from tankapp.models import MODELS

JOBS = []
CATALOG = {}
GATES = {}


def _entity_of(cap_id):
    return getattr(MODELS[cap_id], "ENTITY", None) or cap_id


CAPABILITIES = [
    {"id": cap_id, "entity": _entity_of(cap_id), "source": "agent"}
    for cap_id in sorted(MODELS)
]
'''

MODELS_SRC = '''
class TankLog:
    ENTITY = "tank_reading"


class BarrelCount:
    pass


MODELS = {"tank_log": TankLog, "barrel_count": BarrelCount}
'''


class _Workspace:
    def __init__(self, root: Path):
        self.workspace = root

    def write_text(self, rel, text):
        path = self.workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


def _ctx(root: Path):
    plan = SimpleNamespace(
        capabilities=[SimpleNamespace(capability_id="tank_log", block_ids=["store"])]
    )
    return SimpleNamespace(workspace=_Workspace(root), state={}, plan=plan)


def _product(tmp_path: Path) -> Path:
    pkg = tmp_path / "tankapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "models.py").write_text(MODELS_SRC, encoding="utf-8")
    (tmp_path / JOBS_REL).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / JOBS_REL).write_text(COMPUTED_JOBS, encoding="utf-8")
    return tmp_path


def _load(root: Path) -> dict:
    path = root / JOBS_REL
    sys.path.insert(0, str(root))
    try:
        ns: dict = {"__file__": str(path)}
        exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
        return ns
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("tankapp", None)
        sys.modules.pop("tankapp.models", None)


def test_control_a_full_rerender_erases_the_computed_manifest(tmp_path):
    """The pre-fix behaviour, reproduced: the live failure's shape."""
    root = _product(tmp_path)
    rendered = render_roster({}, _ctx(root).plan, [])
    (root / JOBS_REL).write_text(rendered, encoding="utf-8")
    assert _load(root)["CAPABILITIES"] == []


def test_stamp_keeps_a_computed_manifest_and_makes_the_roster_the_factorys(tmp_path):
    root = _product(tmp_path)
    assert stamp_roster(_ctx(root)) is True
    ns = _load(root)
    assert ns["CAPABILITIES"] == [
        {"id": "barrel_count", "entity": "barrel_count", "source": "agent"},
        {"id": "tank_log", "entity": "tank_reading", "source": "agent"},
    ]
    assert {j["kernel"] for j in ns["JOBS"]} == set(roster_titles())
    assert ns["CATALOG"]["kernel"] and ns["GATES"]["kernel"]
    assert callable(ns["inventory"])
    text = (root / JOBS_REL).read_text(encoding="utf-8")
    assert outside(text) == COMPUTED_JOBS  # every product byte, unchanged
    assert text.count(begin_line()) == 1


def test_stamp_is_idempotent(tmp_path):
    root = _product(tmp_path)
    stamp_roster(_ctx(root))
    first = (root / JOBS_REL).read_text(encoding="utf-8")
    assert stamp_roster(_ctx(root)) is False
    assert (root / JOBS_REL).read_text(encoding="utf-8") == first


def test_a_literal_manifest_is_kept_too(tmp_path):
    root = tmp_path
    (root / JOBS_REL).parent.mkdir(parents=True)
    (root / JOBS_REL).write_text(
        'JOBS = []\nCAPABILITIES = [{"id": "a", "entity": "b", "source": "agent"}]\n',
        encoding="utf-8",
    )
    stamp_roster(_ctx(root))
    ns = _load(root)
    assert ns["CAPABILITIES"] == [{"id": "a", "entity": "b", "source": "agent"}]
    assert {j["kernel"] for j in ns["JOBS"]} == set(roster_titles())


def test_no_file_gets_the_roster_and_an_empty_manifest_in_the_block(tmp_path):
    root = tmp_path
    stamp_roster(_ctx(root))
    ns = _load(root)
    assert ns["CAPABILITIES"] == []
    text = (root / JOBS_REL).read_text(encoding="utf-8")
    assert outside(text) == ""  # the whole file is the Factory's block


def test_product_code_around_the_block_is_byte_identical(tmp_path):
    root = _product(tmp_path)
    existing = COMPUTED_JOBS + "\n\ndef helper():\n    return 7\n"
    (root / JOBS_REL).write_text(existing, encoding="utf-8")
    stamp_roster(_ctx(root))
    text = (root / JOBS_REL).read_text(encoding="utf-8")
    assert outside(text) == existing
    # The block carries no manifest of its own: the product declared one.
    assert text.count("CAPABILITIES = ") == 1
