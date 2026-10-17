"""Re-stamping a declared file never erases the declaration.

Cycle 8 (#718, efe7cbc0) made ``docs/declared_locale.json`` a Factory-owned
whole-file stamp. Its registry entry rendered it with NO blueprint, so every
re-render outside the writer path wrote ``{"schema": ...}`` and nothing else.
The gate replay of PR #719 (cerebrum-builds run 38031528519) re-rendered the
Factory onto build/plt_7056c46ae14f4c4b -- certified 22/22, its committed
record declaring AE / AED -- and the image then failed at ``RUN python
frontend/build.py``: "docs/declared_locale.json declares no country or
currency". 22/22 became 0/22 on a Factory change.

The pair the stamp writes comes from the build's own declared record: the
blueprint it was compiled from (the session's typed intake, copied onto it).
A stamp handed none reads the tree's own copy of that blueprint; with the
declaration genuinely absent there, the pair the tree already declares is
kept -- never blanked, never invented.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.factory.build import gate_replay
from app.factory.build.money_contract import (
    DECLARED_LOCALE_REL,
    declared_locale,
    emit_money_artifacts,
    render_declared_locale,
)
from app.factory.build.stamp_registry import stamps

REPO = Path(__file__).resolve().parents[3]
DECLARED = declared_locale("ae", "aed")
OTHER = declared_locale("gb", "gbp")


def _pair(root: Path):
    raw = json.loads((root / DECLARED_LOCALE_REL).read_text(encoding="utf-8"))
    return declared_locale(raw.get("country"), raw.get("currency"))


def _declared_tree(root: Path) -> Path:
    path = root / DECLARED_LOCALE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_declared_locale(DECLARED).encode("utf-8"))
    return root


def _certified_tree(root: Path, *, locale=DECLARED, record: bool = True) -> Path:
    """A certified build as the Store gate holds it: its own blueprint (the
    session's declared intake on it) and, when ``record``, the declared record
    the build committed from that blueprint."""
    for rel, text in {
        "app/__init__.py": "",
        "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "requirements.txt": "fastapi\nuvicorn\n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    dna = root / "product-dna"
    dna.mkdir()
    blueprint = dna / "product_blueprint.yaml"
    shutil.copy(REPO / "blueprints" / "examples" / "basic_product.yaml", blueprint)
    if locale:
        with blueprint.open("a", encoding="utf-8") as fh:
            fh.write(f"\nlocale:\n  country: {locale['country']}\n  currency: {locale['currency']}\n")
    if record:
        path = root / DECLARED_LOCALE_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render_declared_locale(locale).encode("utf-8"))
    return root


# -- the stamp ------------------------------------------------------------------


def test_a_restamp_without_a_blueprint_never_removes_a_declared_pair(tmp_path):
    root = _declared_tree(tmp_path)
    emit_money_artifacts(root, None)
    assert _pair(root) == DECLARED


def test_a_restamp_from_a_blueprint_that_declares_nothing_keeps_the_declared_pair(tmp_path):
    root = _declared_tree(tmp_path)
    emit_money_artifacts(root, {"locale": None})
    assert _pair(root) == DECLARED


def test_every_registry_stamp_keeps_a_declared_pair(tmp_path):
    """Enumerated off the one registry: no stamp, re-run on a declared tree,
    leaves it declaring less."""
    for stamp in stamps():
        if stamp.apply is None:
            continue
        root = _declared_tree(tmp_path / stamp.name.replace(" ", "_").replace("/", "_"))
        stamp.apply(root)
        assert _pair(root) == DECLARED, stamp.name


def test_the_builds_declaration_is_followed_not_frozen(tmp_path):
    root = _declared_tree(tmp_path)
    emit_money_artifacts(root, {"locale": OTHER})
    assert _pair(root) == OTHER


def test_nothing_declared_anywhere_stays_undeclared(tmp_path):
    emit_money_artifacts(tmp_path, None)
    assert _pair(tmp_path) is None


# -- the replay ------------------------------------------------------------------


def test_replay_render_keeps_a_certified_builds_declared_locale(tmp_path):
    root = _certified_tree(tmp_path)
    before = (root / DECLARED_LOCALE_REL).read_bytes()

    changed = gate_replay.render_onto(root)

    assert DECLARED_LOCALE_REL.as_posix() not in changed
    assert (root / DECLARED_LOCALE_REL).read_bytes() == before
    assert _pair(root) == DECLARED


def test_replay_render_takes_the_pair_from_the_trees_own_blueprint(tmp_path):
    root = _certified_tree(tmp_path, record=False)
    gate_replay.render_onto(root)
    assert _pair(root) == DECLARED


def test_replay_render_of_an_undeclared_build_invents_nothing(tmp_path):
    root = _certified_tree(tmp_path, locale=None, record=False)
    gate_replay.render_onto(root)
    assert _pair(root) is None
