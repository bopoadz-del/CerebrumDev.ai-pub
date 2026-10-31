"""F-8d -- writer-blind vault: seeded inputs + kernel-computed expected.

The definition below is invented (kernel-row shape: expression + declared
inputs); nothing here names a real product, client or vertical.
"""
from __future__ import annotations

import json

import pytest

from app.factory.build.product_grader import vault

DEF = {
    "id": "net_total",
    "key": "universal:net_total_v1",
    "expression": "base * rate - discount",
    "inputs": ["base", "rate", "discount"],
}
SCHEMA = {
    "base": {"minimum": 10, "maximum": 500},
    "rate": {"minimum": 1, "maximum": 3},
    "discount": {"minimum": 0, "maximum": 50},
}


@pytest.mark.parametrize("seed_a, seed_b", [(1, 2), (7, 99)])
def test_two_seeds_give_two_input_sets(seed_a, seed_b):
    """The per-run seed drives the draw: two seeds give two input sets (kills a
    mutant that ignores the seed)."""
    a = vault.generate_inputs(DEF, SCHEMA, seed_a)
    b = vault.generate_inputs(DEF, SCHEMA, seed_b)
    assert set(a) == set(b) == {"base", "rate", "discount"}
    assert a != b


@pytest.mark.parametrize("seed", [3, 404])
def test_expected_is_computed_by_the_kernel_not_a_literal(seed):
    """Expected = safe_eval(expression, inputs), recomputed from the same inputs
    (kills a mutant that returns a constant/literal)."""
    inputs = vault.generate_inputs(DEF, SCHEMA, seed)
    got = vault.expected_value(DEF, inputs)
    want = inputs["base"] * inputs["rate"] - inputs["discount"]
    assert got == pytest.approx(want)


@pytest.mark.parametrize("seed", [5, 55])
def test_generated_values_stay_in_the_declared_range(seed):
    inputs = vault.generate_inputs(DEF, SCHEMA, seed)
    assert 10 <= inputs["base"] <= 500
    assert 1 <= inputs["rate"] <= 3
    assert 0 <= inputs["discount"] <= 50


@pytest.mark.parametrize("cid", ["cap_one", "cap_two"])
def test_no_definition_is_a_kernel_gap_never_oracled(cid, tmp_path):
    """A function the kernel/Store do not define is a gap, recorded as a
    candidate row -- never a VaultEntry, never a hand-oracled pass."""
    entry = vault.build_entry(cid, None, SCHEMA, seed=1)
    assert isinstance(entry, vault.KernelGap)
    assert entry.to_dict()["gradable"] is False
    cands = tmp_path / "new_domain_candidates.jsonl"
    vault.record_kernel_gaps([entry], cands)
    rows = [json.loads(ln) for ln in cands.read_text("utf-8").splitlines()]
    assert rows[-1]["capability_id"] == cid and rows[-1]["gradable"] is False


@pytest.mark.parametrize("seed", [2, 22])
def test_build_entry_carries_seed_inputs_and_kernel_expected(seed):
    entry = vault.build_entry("net_total_cap", DEF, SCHEMA, seed)
    assert isinstance(entry, vault.VaultEntry)
    assert entry.seed == seed
    assert entry.expected == pytest.approx(
        entry.inputs["base"] * entry.inputs["rate"] - entry.inputs["discount"]
    )
    assert entry.definition_id == "net_total"


@pytest.mark.parametrize("seed", [1, 8])
def test_vault_is_written_under_the_grader_root_only(seed, tmp_path):
    """The seed + set live under the grader root's vault dir, nowhere else."""
    grader_root = tmp_path / "grader"
    entry = vault.build_entry("c", DEF, SCHEMA, seed)
    out = vault.write_vault([entry], grader_root)
    assert out == grader_root / vault.VAULT_REL / "vault.json"
    assert out.is_file()
    payload = json.loads(out.read_text("utf-8"))
    assert payload[0]["seed"] == seed
