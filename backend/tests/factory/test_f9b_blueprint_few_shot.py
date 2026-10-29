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
    block = user.split("BLUEPRINT FEW-SHOT", 1)[1].split("COMPILED BRIEF", 1)[0]
    return json.loads(block[block.index("{"): block.rindex("}") + 1])


@pytest.mark.parametrize("caps,other", [(BLUEPRINT_A, BLUEPRINT_B), (BLUEPRINT_B, BLUEPRINT_A)])
def test_few_shot_is_generated_from_this_blueprint(monkeypatch, caps, other):
    seen = _prompt(monkeypatch, caps, "P", "v")
    roster = re.search(r"Roster \(exactly these ids\): (.*)", seen["user"]).group(1)
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
    with pytest.raises(coder.CoderError):
        coder.blueprint_few_shot([" ", ""], "P", "v")
