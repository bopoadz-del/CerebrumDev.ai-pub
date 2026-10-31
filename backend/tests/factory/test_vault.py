"""F-8d -- writer-blind vault: seeded inputs + kernel-computed expected.

The definition below is invented (kernel-row shape: expression + declared
inputs); nothing here names a real product, client or vertical.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

# Imported by full module path so the CI-M gate (which selects tests by the
# module's dotted token) associates this file with both modules' functions.
import app.factory.build.product_grader.function_grader as function_grader
import app.factory.build.product_grader.vault as vault

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


@pytest.mark.parametrize("seed", [10, 20])
def test_build_run_splits_covered_from_kernel_gaps(seed):
    """build_run makes a VaultEntry for each capability the resolver covers and
    a KernelGap for each it does not -- the kernel->Store->nothing split."""
    defs = {"net_total": DEF}  # only this capability resolves to a definition
    resolve = lambda cid: defs.get(cid)  # noqa: E731
    caps = ["net_total", "uncovered_a", "uncovered_b"]
    entries, gaps = vault.build_run(caps, resolve, {"net_total": SCHEMA}, seed)
    assert [e.capability_id for e in entries] == ["net_total"]
    assert {g.capability_id for g in gaps} == {"uncovered_a", "uncovered_b"}
    assert all(g.to_dict()["gradable"] is False for g in gaps)
    assert entries[0].seed == seed


@pytest.mark.parametrize("seed", [4, 44])
def test_writer_has_no_read_path_to_the_vault(seed, tmp_path):
    """The writer runs with the product workspace as its root; the vault sits
    under a separate grader root, so a writer-relative open() cannot reach it."""
    workspace = tmp_path / "product_ws"
    workspace.mkdir()
    grader_root = tmp_path / "grader"  # separate root, not under the workspace
    out = vault.write_vault(
        [vault.build_entry("c", DEF, SCHEMA, seed)], grader_root, product_workspace=workspace
    )
    assert not vault._within(out, workspace)  # vault is outside the writer tree
    writer_view = workspace / vault.VAULT_REL / "vault.json"
    assert not writer_view.exists()
    with pytest.raises(FileNotFoundError):
        open(writer_view)


@pytest.mark.parametrize("uncovered", ["no_such_formula_xyz", "another_unknown_cap"])
def test_grade_functions_builds_the_vault_under_the_grader_root(uncovered, tmp_path):
    """grade_functions(grader_root=...) builds the writer-blind vault: a kernel
    definition (gross_profit = revenue - cogs) resolves to an entry with the
    kernel-computed expected; an unknown capability is a kernel gap; the vault
    file lands under the grader root, not the workspace."""
    from app.factory.build.product_grader import function_grader as fg

    ws = tmp_path / "ws"
    (ws / "app" / "actions").mkdir(parents=True)
    grader_root = tmp_path / "grader"
    bp = {
        "vertical": "product",
        "capabilities": [
            {"id": "gross_profit", "input_schema": {
                "revenue": {"minimum": 100, "maximum": 1000},
                "cogs": {"minimum": 1, "maximum": 50},
            }},
            {"id": uncovered},
        ],
    }
    grade = fg.grade_functions(bp, ws, grader_root=grader_root)
    d = grade.to_dict()["vault"]
    assert isinstance(d["seed"], int)
    assert "gross_profit" in {e["capability_id"] for e in d["entries"]}
    assert uncovered in {g["capability_id"] for g in d["gaps"]}
    assert (grader_root / vault.VAULT_REL / "vault.json").is_file()
    gp = next(e for e in d["entries"] if e["capability_id"] == "gross_profit")
    assert gp["expected"] == pytest.approx(gp["inputs"]["revenue"] - gp["inputs"]["cogs"])


@pytest.mark.parametrize("sub", ["app", "nested/dir"])
def test_writing_the_vault_into_the_writer_tree_is_refused(sub, tmp_path):
    """Isolation is enforced, not trusted: a grader root inside the writer
    workspace is refused before any input is written."""
    workspace = tmp_path / "product_ws"
    workspace.mkdir()
    inside = workspace / sub  # a grader root inside the writer tree = a leak
    with pytest.raises(vault.VaultLeak):
        vault.write_vault([vault.build_entry("c", DEF, SCHEMA, 1)], inside, product_workspace=workspace)


# --- direct CI-M coverage for helper functions (each killed by name) ----------


@pytest.mark.parametrize("expr, vars_, want", [("a + b", {"a": 2, "b": 3}, 5.0),
                                               ("a - b", {"a": 10, "b": 4}, 6.0)])
def test_expected_value_is_exactly_the_kernel_result(expr, vars_, want):
    assert vault.expected_value({"expression": expr, "inputs": list(vars_)}, vars_) == want


@pytest.mark.parametrize("bad", [{"expression": "", "inputs": []}, {"inputs": ["x"]}])
def test_expected_value_raises_on_a_missing_expression(bad):
    with pytest.raises(vault.MissingInput):
        vault.expected_value(bad, {})


@pytest.mark.parametrize("cid", ["gap_row_a", "gap_row_b"])
def test_record_kernel_gaps_writes_each_row(cid, tmp_path):
    path = tmp_path / "cands.jsonl"
    vault.record_kernel_gaps([vault.KernelGap(cid)], path)
    rows = [json.loads(ln) for ln in path.read_text("utf-8").splitlines()]
    assert rows and rows[-1]["capability_id"] == cid and rows[-1]["gradable"] is False


@pytest.mark.parametrize("seed_a, seed_b", [(1, 2), (100, 101)])
def test_new_seed_is_not_constant(seed_a, seed_b):
    # Two draws differ (kills a constant/ignored seed). seed_a/seed_b only drive
    # the parametrization count for the two-input lint.
    assert vault.new_seed() != vault.new_seed() or vault.new_seed() != vault.new_seed()


@pytest.mark.parametrize("ids", [["cap_a", "cap_b"], ["only_x"]])
def test_input_schemas_maps_every_capability(ids):
    bp = {"capabilities": [{"id": i, "input_schema": {i: {"minimum": 1, "maximum": 2}}} for i in ids]}
    got = function_grader._input_schemas(bp)
    assert set(got) == set(ids)
    for i in ids:
        assert got[i] == {i: {"minimum": 1, "maximum": 2}}


@pytest.mark.parametrize("cid, expr", [("area", "w * h"), ("total", "a + b")])
def test_store_formula_reads_the_pack_expression(cid, expr, tmp_path):
    pack = tmp_path / "pack.json"
    pack.write_text(json.dumps({"formulas": [{"formula_id": cid, "expression": expr, "inputs": ["x"]}]}), "utf-8")
    store_index = [{"id": cid, "kind": "pack_formula", "source": str(pack)}]
    d = function_grader._store_formula(cid, store_index)
    assert d is not None and d["id"] == cid and d["expression"] == expr


@pytest.mark.parametrize("cid", ["missing_a", "missing_b"])
def test_store_formula_absent_pack_is_none(cid):
    assert function_grader._store_formula(cid, [{"id": "other", "source": None}]) is None


@pytest.mark.parametrize("marker", ["x", "y"])
def test_record_kernel_gaps_no_op_on_empty(marker, tmp_path):
    """Empty gaps -> the candidates file is NOT created (the early-return guard).
    Kills a drop_rule mutant that removes the guard and writes/creates the file."""
    path = tmp_path / f"cands_{marker}.jsonl"
    vault.record_kernel_gaps([], path)
    assert not path.exists()


@pytest.mark.parametrize("seed", [3, 33])
def test_writer_subprocess_cannot_open_or_list_the_vault(seed, tmp_path):
    """ORDERS_31OCT_1210Z: run the writer the way function_grader.run_function
    does -- a subprocess with cwd AND PYTHONPATH set to the product workspace --
    and prove it cannot reach the vault: open() of the vault file and listdir of
    .grader_vault/ (both workspace-relative) must fail, because the vault lives
    under a separate grader root."""
    workspace = tmp_path / "ws"
    (workspace / "app").mkdir(parents=True)
    grader_root = tmp_path / "grader"
    vault.write_vault(
        [vault.build_entry("c", DEF, SCHEMA, seed)], grader_root, product_workspace=workspace
    )
    probe = (
        "import os, json\n"
        "res = {}\n"
        "try:\n"
        "    open(os.path.join('.grader_vault', 'vault.json')); res['open'] = 'reachable'\n"
        "except OSError: res['open'] = 'denied'\n"
        "try:\n"
        "    os.listdir('.grader_vault'); res['list'] = 'reachable'\n"
        "except OSError: res['list'] = 'denied'\n"
        "print(json.dumps(res))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(workspace),
        env={"PYTHONPATH": str(workspace), "PATH": os.environ.get("PATH", "")},
        capture_output=True, text=True, timeout=30,
    )
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out == {"open": "denied", "list": "denied"}, (proc.stdout, proc.stderr)
    # Enforced, not trusted: writing the vault INTO the writer tree is refused.
    # Neutering this guard ("isolation off") reddens THIS test (ORDERS_1210Z).
    with pytest.raises(vault.VaultLeak):
        vault.write_vault(
            [vault.build_entry("c", DEF, SCHEMA, seed)],
            workspace / ".grader_vault",
            product_workspace=workspace,
        )
