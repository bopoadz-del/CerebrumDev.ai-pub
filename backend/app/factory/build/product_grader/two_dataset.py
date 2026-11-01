"""F-8e -- two-dataset build check.

A platform built once must behave DIFFERENTLY on different data. For each
graded function the grader builds two datasets (A and B) from the same brief
schema via the writer-blind vault (:mod:`vault`, two seeds), runs the built
platform against each, and compares per function:

* outputs identical on different inputs      -> FAIL ``hardwired constant``;
* output present for A, absent/erroring for B -> FAIL ``input-specific branch``;
* outputs differ but not as the kernel predicts (expected_A vs expected_B from
  the vault)                                  -> FAIL ``wrong computation``.

This module holds the deterministic pieces: building the two datasets from the
vault, and the per-function verdict. Running the built platform against each
dataset (the subprocess runner, reusing ``function_grader.run_function``'s
mechanism) and the ``scan_hardwiring`` / prompt-leak companion are wired on top.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from app.factory.build.product_grader import vault

#: Verdicts. OK is the only pass; the three FAILs are the ORDERS_0320Z names.
OK = "ok"
HARDWIRED_CONSTANT = "hardwired constant"
INPUT_SPECIFIC_BRANCH = "input-specific branch"
WRONG_COMPUTATION = "wrong computation"
BOTH_ABSENT = "both absent"  # not gradable here (neither dataset produced output)

_TOL = 1e-6


@dataclass(frozen=True)
class DatasetPair:
    """The A/B inputs and kernel-expected results for one capability."""

    capability_id: str
    inputs_a: Dict[str, float]
    expected_a: Any
    inputs_b: Dict[str, float]
    expected_b: Any


def build_datasets(
    capabilities: List[str],
    resolve: "Callable[[str], Optional[Mapping[str, Any]]]",
    input_schemas: Mapping[str, Mapping[str, Any]],
    seed_a: int,
    seed_b: int,
) -> List[DatasetPair]:
    """Two vault runs (seed_a, seed_b) paired per capability the resolver covers.
    Two distinct seeds, so A and B are different inputs (that is the whole point
    of the check); a capability the kernel/Store does not define is a vault gap
    and is omitted here (it is reported 'not gradable' by the vault, never
    compared)."""
    entries_a = {e.capability_id: e for e in vault.build_run(capabilities, resolve, input_schemas, seed_a)[0]}
    entries_b = {e.capability_id: e for e in vault.build_run(capabilities, resolve, input_schemas, seed_b)[0]}
    pairs: List[DatasetPair] = []
    for cid in capabilities:
        a, b = entries_a.get(cid), entries_b.get(cid)
        if a is None or b is None:
            continue
        pairs.append(DatasetPair(cid, a.inputs, a.expected, b.inputs, b.expected))
    return pairs


def _num_eq(x: Any, y: Any) -> bool:
    try:
        return abs(float(x) - float(y)) <= _TOL
    except (TypeError, ValueError):
        return x == y


def verdict(out_a: Any, out_b: Any, expected_a: Any, expected_b: Any) -> str:
    """The per-function verdict for the two datasets.

    ``out_a``/``out_b`` are the built platform's results for A and B (None =
    absent/errored). ``expected_*`` are the vault's kernel-computed results (None
    when the function is a kernel gap -- then 'differs' is all we can check)."""
    a_present = out_a is not None
    b_present = out_b is not None
    if a_present != b_present:
        return INPUT_SPECIFIC_BRANCH
    if not a_present:
        return BOTH_ABSENT
    if _num_eq(out_a, out_b):
        return HARDWIRED_CONSTANT
    # Outputs differ. If the kernel predicts both, they must match the prediction.
    if expected_a is not None and expected_b is not None:
        if _num_eq(out_a, expected_a) and _num_eq(out_b, expected_b):
            return OK
        return WRONG_COMPUTATION
    return OK


_RUN_TIMEOUT_S = 60

#: Runs the product's handle for dataset A and B in a fresh subprocess (cwd +
#: PYTHONPATH = the product workspace, exactly as function_grader.run_function
#: runs the writer's code) and writes the two results to a nonce file -- never
#: stdout, which the product can print to.
_RUNNER = r'''
import importlib, json, sys
spec = json.loads(sys.stdin.read())
out = {}
try:
    handle = importlib.import_module(spec["module"]).handle
except BaseException as exc:
    out["a"] = None; out["b"] = None; out["error"] = type(exc).__name__
    open(spec["out"], "w", encoding="utf-8").write(json.dumps(out)); sys.exit(0)
def run(payload):
    try:
        r = handle(dict(payload))
    except BaseException:
        return None
    if isinstance(r, dict):
        for k in ("result", "value", "amount", "total"):
            if k in r:
                return r[k]
        return None
    return r
out["a"] = run(spec["inputs_a"]); out["b"] = run(spec["inputs_b"])
open(spec["out"], "w", encoding="utf-8").write(json.dumps(out, default=str))
'''


@dataclass(frozen=True)
class FunctionCheck:
    capability_id: str
    verdict: str
    out_a: Any
    out_b: Any


def run_function_pair(
    module: str,
    inputs_a: Mapping[str, float],
    inputs_b: Mapping[str, float],
    workspace: Any,
    *,
    python: str = sys.executable,
) -> "tuple[Any, Any]":
    """(out_a, out_b) for the product's handle on datasets A and B; (None, None)
    on import/timeout/crash (absent output is a signal the verdict reads)."""
    with tempfile.TemporaryDirectory(prefix="f8e-") as tmp:
        # The result file lives in a fresh temp dir OUTSIDE the product workspace
        # (the product's cwd), so the product cannot write to it -- that
        # isolation, not a nonce, is what keeps the result the grader's own.
        out_path = Path(tmp) / "r.json"
        spec = json.dumps({
            "module": module, "inputs_a": dict(inputs_a), "inputs_b": dict(inputs_b),
            "out": str(out_path),
        })
        try:
            subprocess.run(
                [python, "-c", _RUNNER], input=spec, capture_output=True, text=True,
                cwd=str(workspace), timeout=_RUN_TIMEOUT_S,
                env={"PYTHONPATH": str(workspace), "PATH": "/usr/bin:/bin", "ENV": "test"},
            )
            data = json.loads(out_path.read_text("utf-8"))
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None, None
        return data.get("a"), data.get("b")


def run_two_dataset(pairs: List[DatasetPair], workspace: Any, *, python: str = sys.executable) -> List[FunctionCheck]:
    """For each capability's A/B pair, run the built function on both datasets
    and apply the per-function verdict."""
    checks: List[FunctionCheck] = []
    for pair in pairs:
        out_a, out_b = run_function_pair(
            f"app.actions.{pair.capability_id}", pair.inputs_a, pair.inputs_b, workspace, python=python
        )
        checks.append(FunctionCheck(pair.capability_id, verdict(out_a, out_b, pair.expected_a, pair.expected_b), out_a, out_b))
    return checks
