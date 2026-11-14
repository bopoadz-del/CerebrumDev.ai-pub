"""F-C1: the function grader is one more reader of TESTER's mechanism.

It grades the plan's capability list with TESTER's module naming, samples
the spec TESTER samples (``declared_specs.tester_specs``: the state spec
merged under the product's models, the late align over handler and route),
sends TESTER's completed sample as input one, and corrects a rejection the
handle states as data the way TESTER's route suites do. The fixtures are
invented; the names decide nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Imported by full module path so the CI-M mutation gate associates this file
# with the modules it exercises.
import app.factory.build.declared_specs as declared_specs
import app.factory.build.payload_helpers as payload_helpers
import app.factory.build.product_grader.function_grader as fg
from app.factory.build.roles_handlers import _sample_payload

MODEL = (
    "class Entry:\n"
    "    ENTITY = 'entry'\n"
    "    FIELDS = ['owner', 'amount', 'tier']\n"
    "    CONSTRAINTS = {'amount': {'min': 0, 'max': 50}}\n"
    "    owner: str\n"
    "    amount: int\n"
    "    tier: str\n"
)

#: Reads every declared field; enforces a tier vocabulary the MODELS never
#: declare (the late align mines it off this body).
GUARDED = (
    "def handle(payload):\n"
    "    if payload.get('tier') not in {vocab!r}:\n"
    "        return {{'ok': False, 'error': 'tier refused'}}\n"
    "    return {{'ok': True, 'n': payload['amount'] * 2, 'who': payload['owner'][::-1],\n"
    "            'state': payload['tier'].upper()}}\n"
)
COMPUTES = (
    "def handle(payload):\n"
    "    return {'ok': True, 'n': payload['amount'] * 3, 'who': payload['owner'].upper(),\n"
    "            'state': len(payload['tier'])}\n"
)
BARE_OK = "def handle(payload):\n    return {'ok': True, 'count': len(payload) + 3}\n"


def _product(root: Path, modelled, bodies) -> Path:
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    (actions / "__init__.py").write_text("")
    (root / "app" / "models.py").write_text(
        MODEL + "\nMODELS = {" + ", ".join(f"{c!r}: Entry" for c in modelled) + "}\n"
    )
    for cid, body in bodies.items():
        (actions / f"{cid.replace('-', '_')}.py").write_text(body)
    return root


@pytest.mark.parametrize("dashed,vocab,owners", [
    ("entry-log", ("open", "shut"), ["ann", "bo"]),
    ("rate-card", ("draft", "sent"), ["cy", "di"]),
])
def test_grader_input_one_is_testers_completed_sample(tmp_path, dashed, vocab, owners):
    ids = [dashed, "plain_cap", "bare_cap"]
    ws = _product(tmp_path, [dashed, "plain_cap"], {
        dashed: GUARDED.format(vocab=vocab), "plain_cap": COMPUTES, "bare_cap": BARE_OK,
    })
    # A vocabulary only the build state carries (mined in an earlier round).
    state = {"plain_cap": {"fields": [{"name": "owner", "allowed_values": owners}]}}

    fns = fg.derive_functions(ids, ws, state_specs=state)
    specs = declared_specs.tester_specs(ids, state, ws, declared_specs.read_under(ws))
    tester = payload_helpers.completed_samples(ws, {c: _sample_payload(specs.get(c, {})) for c in ids})

    assert [f.capability_id for f in fns] == ids
    assert [f.inputs[0] for f in fns] == [tester[c] for c in ids]
    assert fns[0].module == "app.actions." + dashed.replace("-", "_")
    assert fns[0].inputs[0]["tier"] == vocab[0]       # mined off the handler (not an envelope field)
    assert fns[1].inputs[0]["owner"] == owners[0]       # carried by the state spec
    assert fns[2].inputs == [{}]                        # no field: one input
    assert [fg.run_function(f, ws).ok for f in fns] == [True, True, True]


#: Refuses ``zone`` unless it is in a vocabulary no syntax check states, and
#: says so AS DATA (rejection_contract keys) -- only a correction reaches it.
STATES_ITS_REJECTION = (
    "ALLOWED = tuple(sorted(set({vocab!r})))\n"
    "def handle(payload):\n"
    "    if payload['zone'] not in ALLOWED:\n"
    "        return {{'ok': False, 'error': 'zone refused', 'rejected_field': 'zone',\n"
    "                'rejection_reason': 'not_allowed', 'allowed_values': list(ALLOWED)}}\n"
    "    return {{'ok': True, 'total': payload['amount'] * 2, 'zone': payload['zone'].upper()}}\n"
)
ZONE_MODEL = (
    "class Item:\n"
    "    FIELDS = ['zone', 'amount']\n"
    "    CONSTRAINTS = {'amount': {'min': 0, 'max': 9}}\n"
    "    zone: str\n"
    "    amount: int\n"
    "MODELS = {'zone_cap': Item}\n"
)


@pytest.mark.parametrize("vocab", [("north", "south"), ("east", "west")])
def test_stated_rejection_is_corrected_before_judging(tmp_path, vocab):
    ws = _product(tmp_path, [], {"zone_cap": STATES_ITS_REJECTION.format(vocab=vocab)})
    (ws / "app" / "models.py").write_text(ZONE_MODEL)
    (fn,) = fg.derive_functions(["zone_cap"], ws)
    assert fn.inputs[0]["zone"] not in vocab           # the sample alone is refused
    r = fg.run_function(fn, ws)
    assert r.ok, r
    assert r.depends_on == ["amount"]
