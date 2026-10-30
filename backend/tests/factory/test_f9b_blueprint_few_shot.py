"""F-9b: no photographed roster in the coder's system prompt; the roster and
worked example reaching the coder are generated from the current blueprint.
Two blueprints give two prompts, each naming only its own capabilities."""
from __future__ import annotations

import json
import re

import pytest

import app.factory.coder as coder

BLUEPRINT_A = ["stock_ledger", "supplier_orders", "reorder_alerts"]
BLUEPRINT_B = ["tenant_leases", "rent_collection"]


def _prompt(monkeypatch, caps, name, vertical):
    seen = {}

    def fake(messages, label):
        seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
        return {"specs": {c: {"entity": c} for c in caps}, "handlers": {}}, "stub"

    monkeypatch.setattr(coder, "_llm_json_object", fake)
    coder.generate_from_compiled_brief(brief="b", capabilities=caps,
                                       product_name=name, vertical=vertical)
    return seen


def _few_shot_json(user):
    assert "BLUEPRINT FEW-SHOT" in user, "few-shot missing from the user message"
    block = user.split("BLUEPRINT FEW-SHOT", 1)[1].split("COMPILED BRIEF", 1)[0]
    return json.loads(block[block.index("{"): block.rindex("}") + 1])


@pytest.mark.parametrize("caps,other", [(BLUEPRINT_A, BLUEPRINT_B), (BLUEPRINT_B, BLUEPRINT_A)])
def test_few_shot_is_generated_from_this_blueprint(monkeypatch, caps, other):
    seen = _prompt(monkeypatch, caps, "P", "v")
    found = re.search(r"Roster \(exactly these ids\): (.*)", seen["user"])
    assert found is not None, "roster line missing from the user message"
    roster = found.group(1)
    assert roster.split(", ") == caps
    shot = _few_shot_json(seen["user"])
    assert set(shot["specs"]) == {caps[0]} and set(shot["handlers"]) == {caps[0]}
    for cid in other:
        assert cid not in seen["user"] and cid not in seen["system"]


def test_system_prompt_carries_no_photographed_roster(monkeypatch):
    a = _prompt(monkeypatch, BLUEPRINT_A, "P", "v")["system"]
    assert "Photographed roster" not in a
    # every snake_case id the static system prompt names must be a contract
    # term, not a capability: none of the identifiers listed as capability
    # roster entries in an enumerated "roster:" line may remain.
    assert not re.search(r"roster:\s*\n?\s*[a-z]+_[a-z_]+,", a, re.I)
    assert "appointment_scheduling /" not in a


def test_empty_blueprint_refuses():
    for blank in ([" ", ""], ["\t"]):
        err = None
        try:
            coder.blueprint_few_shot(blank, "P", "v")
        except Exception as exc:  # noqa: BLE001 - the type is the assertion
            err = exc
        assert isinstance(err, coder.CoderError), f"{blank!r} -> {err!r}"


# --- repair 1/2: foreign capability ids in the static system prompt --------
import ast  # noqa: E402
import pathlib  # noqa: E402

from app.factory.build.block_obligations import ENVELOPE_STATUS_VALUES  # noqa: E402

_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_APP = pathlib.Path(coder.__file__).resolve().parents[1]
_SHELF = _APP / "factory" / "shelves" / "factory_blocks.json"


def _code_vocabulary():
    """Snake_case terms the Factory's own code defines: identifiers, attribute
    and argument names, non-docstring string literals (outside coder.py) and
    shelf block ids. A capability id belongs to a blueprint, never to code."""
    vocab = {b["id"] for b in json.loads(_SHELF.read_text())["blocks"]}
    skip = pathlib.Path(coder.__file__).resolve()
    for path in _APP.rglob("*.py"):
        if path.resolve() == skip:
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Name):
                vocab.add(n.id)
            elif isinstance(n, ast.Attribute):
                vocab.add(n.attr)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                vocab.add(n.name)
            elif isinstance(n, ast.arg):
                vocab.add(n.arg)
            elif isinstance(n, ast.keyword) and n.arg:
                vocab.add(n.arg)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                vocab |= set(_SNAKE.findall(n.value))
    return vocab


_VOCAB = _code_vocabulary()


def foreign_ids(system):
    return sorted({t for t in _SNAKE.findall(system) if t not in _VOCAB})


@pytest.mark.parametrize("caps", [BLUEPRINT_A, BLUEPRINT_B])
def test_system_prompt_names_no_foreign_capability(monkeypatch, caps):
    system = _prompt(monkeypatch, caps, "P", "v")["system"]
    assert foreign_ids(system) == []


@pytest.mark.parametrize("injected", ["patient_records_management", "stock_ledger"])
def test_foreign_id_detector_catches_reinjection(injected):
    assert foreign_ids(coder._WHOLE_JOB_SYSTEM + f", e.g. {injected}") == [injected]


@pytest.mark.parametrize("caps,name,vertical,entity", [
    (BLUEPRINT_A, "Stock Desk", "retail", "stock"),
    (BLUEPRINT_B, "Lease Hub", "lettings", "tenant"),
])
def test_few_shot_shape_pinned(monkeypatch, caps, name, vertical, entity):
    user = _prompt(monkeypatch, caps, name, vertical)["user"]
    assert f"BLUEPRINT FEW-SHOT ({name}, vertical {vertical}):" in user
    shot = _few_shot_json(user)
    assert set(shot["specs"]) == {caps[0]} and set(shot["handlers"]) == {caps[0]}
    spec = shot["specs"][caps[0]]
    assert spec["entity"] == entity
    assert [f["name"] for f in spec["fields"]] == [f"{entity}_id", "status"]
    assert spec["fields"][1].get("allowed_values") == list(ENVELOPE_STATUS_VALUES)
    handler = shot["handlers"][caps[0]]
    assert "execute(BLOCK_IDS[0]" in handler
    assert "return {'ok': True, 'capability': CAPABILITY_ID, 'result': out}" in handler
