"""F-8a -- Layer-1 function grader.

For a built product workspace and its blueprint:

1. derive the functions to grade from the SAME plan capability list TESTER
   iterates (``app/actions/<capability_id with - as _>.py``), each probed
   whatever its module holds (a missing ``handle`` fails on import);
2. build its inputs from the SAME spec TESTER samples
   (``declared_specs.tester_specs``: the state spec merged under the
   product's declared models, the late align over its handler and route, the
   record envelope). Input one is byte-identical to TESTER's completed sample
   (``roles_handlers._sample_payload`` completed by the product's own model,
   ``payload_helpers.completed_samples``); input two differs in every field
   the declared constraints let differ. A rejection the handle states as
   data is corrected exactly as TESTER's route suites correct it
   (``payload_helpers._corrected``) before anything is judged;
3. compare the two answers with echo and noise removed -- a leaf that copies
   the same input path in every run, or that changes when the SAME input is
   sent twice (a timestamp, a fresh id), is not a result;
4. field dependence: re-run input two with one declared field set back to
   input one's value, per field. A handle whose answer changes for no single
   field is a stub; the fields it depends on are reported.

A capability with no field that can vary has one input. It is judged by
TESTER's own one-input pilot rule: the answer is a dict, it is not
``ok: False``, and it has no failed block call nested in it. A pass counts
toward certification with detail :data:`ONE_INPUT`. A fail quotes TESTER's
failure string.

A function whose test does not pass, or whose mutant survives, makes the
platform NOT handover-ready and is named in the grade. Fewer than
``MIN_FUNCTIONS`` gradeable functions is itself a FAIL.

Each function runs in a fresh subprocess with the workspace on sys.path, so
a product's imports never leak into the Factory process.
"""

from __future__ import annotations

import datetime as _dt
import json
import secrets
import subprocess
import sys
import tempfile
import uuid as _uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.deploy_time_settings import SNIPPET as _DEPLOY_TIME_SNIPPET
from app.factory.build.image_sufficiency import GATE_RUN_ENV
from app.factory.build.payload_helpers import render_payload_helpers
from app.factory.build.rejection_contract import (
    ERROR_KEY,
    OK_KEY,
    PILOT_FAILED_BLOCK,
    PILOT_FAILED_STATUSES,
    PILOT_NON_DICT,
    PILOT_REJECTED,
)
from app.factory.build.product_grader import vault

#: ORDERS_1710Z-B: "graded on at least 4-5 of its functions".
MIN_FUNCTIONS = 4
#: The one mutant family: per declared field, input two with that field set
#: back to input one's value. Killed when the answer changes for at least one
#: field (the handle reads its input); survived when it changes for none.
MUTANTS: Sequence[str] = ("field_dependence",)
RUN_TIMEOUT_S = 60
#: The probe artifact keeps at most this much of each observed answer.
ANSWER_CHARS = 2000
#: The detail a handle earns when its two inputs gave one result (it answers
#: like a stub). The reason-class derivation reads this constant, never prose.
ONE_RESULT = "two inputs gave one result"
#: The detail a handle earns when both inputs answered ONE honest-error
#: envelope (``ok`` is False with a non-empty ``error``): the handle failed
#: closed -- its blocks did not answer -- which is a handler_error, never a
#: stub. A stub answers a fixed result; a fail-closed handle answers its
#: error. Neither is ok, so the bar is unchanged; only the class differs.
BLOCK_UNAVAILABLE = "block_unavailable: two inputs gave one honest-error envelope"
#: The detail of a capability with no field that can take a second value and
#: that PASSED TESTER's one-input pilot rule. It counts as a pass and is
#: recorded for information. One that fails the rule carries TESTER's
#: failure string instead.
ONE_INPUT = "one_input"


@dataclass(frozen=True)
class GradedFunction:
    capability_id: str
    module: str
    #: TESTER's completed sample, then a second valid instance of the same
    #: spec when one of its fields can vary (else the sample alone).
    inputs: List[Dict[str, Any]]


@dataclass
class FunctionResult:
    capability_id: str
    two_input_passed: bool
    mutants_killed: List[str] = field(default_factory=list)
    mutants_survived: List[str] = field(default_factory=list)
    detail: str = ""
    #: The declared fields whose single change moved the answer.
    depends_on: List[str] = field(default_factory=list)
    #: Internal grader artifact for the handback, kept on a failing handle
    #: only: the exact inputs sent and the answers observed. The public
    #: timeline reads reason classes, never this.
    probe: Dict[str, Any] = field(default_factory=dict)
    #: One input only (no field can vary), and TESTER's pilot rule passed it.
    one_input_passed: bool = False

    @property
    def ok(self) -> bool:
        return (self.two_input_passed or self.one_input_passed) and not self.mutants_survived


@dataclass
class FunctionGrade:
    functions: List[FunctionResult]
    min_functions: int = MIN_FUNCTIONS
    errors: List[str] = field(default_factory=list)
    #: F-8d writer-blind vault for this run: {seed, entries, gaps}. None when no
    #: grader root was given (the vault is a Factory-owned, grader-root artifact).
    vault: "Dict[str, Any] | None" = None
    #: Functions the grader did not run. Not counted, not in ``failing`` (the
    #: writer is not handed them); they keep the grade from being ok.
    ungraded: List[FunctionResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        # MIN_FUNCTIONS is a floor, not a default: a caller can ask for more
        # gradeable functions, never fewer (0, negatives, bools, non-ints).
        m = self.min_functions
        if isinstance(m, bool) or not isinstance(m, int):
            raise TypeError(f"min_functions must be an int, got {type(m).__name__}")
        self.min_functions = max(m, MIN_FUNCTIONS)

    @property
    def ok(self) -> bool:
        return (
            not self.errors
            and not self.ungraded
            and len(self.functions) >= self.min_functions
            and all(f.ok for f in self.functions)
        )

    @property
    def failing(self) -> List[str]:
        names = [f.capability_id for f in self.functions if not f.ok]
        if len(self.functions) < self.min_functions:
            names.append(
                f"too few gradeable functions: {len(self.functions)} < {self.min_functions}"
            )
        return names + list(self.errors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "min_functions": self.min_functions,
            "graded": len(self.functions),
            "failing": self.failing,
            "functions": [dict(asdict(f), ok=f.ok) for f in self.functions],
            "ungraded": [asdict(f) for f in self.ungraded],
            **({"vault": self.vault} if self.vault is not None else {}),
        }


def _in_bounds(value: Any, field_spec: Mapping[str, Any]) -> bool:
    lo, hi = field_spec.get("min"), field_spec.get("max")
    try:
        return (lo is None or value >= lo) and (hi is None or value <= hi)
    except TypeError:
        return False


def _other_number(first: Any, field_spec: Mapping[str, Any], kind: str) -> Any:
    """The nearest value to ``first`` inside the declared min/max that differs
    from it; ``first`` when the bounds allow only one value."""
    step = 1 if kind == "int" else 1.0
    candidates: List[Any] = [first + step, first - step]
    lo, hi = field_spec.get("min"), field_spec.get("max")
    if lo is not None and hi is not None and kind != "int":
        candidates.append((lo + hi) / 2)
    for cand in candidates:
        if cand != first and _in_bounds(cand, field_spec):
            return cand
    return first


def _shift_temporal(first: str) -> str:
    """The ISO sample shifted by one unit (a day for a date or datetime, an
    hour for a time); the sample itself when it does not parse."""
    try:
        if "T" in first or " " in first.strip():
            return (_dt.datetime.fromisoformat(first) + _dt.timedelta(days=1)).isoformat()
        if ":" in first:
            moved = _dt.datetime.combine(_dt.date(2000, 1, 1), _dt.time.fromisoformat(first))
            return (moved + _dt.timedelta(hours=1)).time().isoformat()
        return (_dt.date.fromisoformat(first) + _dt.timedelta(days=1)).isoformat()
    except ValueError:
        return first


def _other_value(field_spec: Mapping[str, Any], first: Any) -> Any:
    """A second valid value for a declared field, different from ``first``
    when the declared constraints allow one. Reads the declared type, format,
    vocabulary and bounds only -- never the field's name beyond what
    ``_sample_value`` already reads."""
    from app.factory.build.roles_handlers import (
        _looks_like_email_field,
        _normalize_field_type,
        _temporal_sample,
    )

    allowed = field_spec.get("allowed_values")
    if allowed:
        return next((v for v in allowed if v != first), first)
    if isinstance(first, bool):
        return not first
    if _looks_like_email_field(dict(field_spec)):
        return next(a for a in ("second@example.com", "third@example.com") if a != first)
    if isinstance(first, str) and _temporal_sample(dict(field_spec)) is not None:
        return _shift_temporal(first)
    kind = _normalize_field_type(field_spec.get("type") or "str")
    if kind in ("int", "float") and isinstance(first, (int, float)):
        return _other_number(first, field_spec, kind)
    if kind == "uuid" and isinstance(first, str):
        try:
            return str(_uuid.UUID(int=(_uuid.UUID(first).int + 1) % (1 << 128)))
        except ValueError:
            return first
    if isinstance(first, str):
        return first + " 2"
    return first


def contract_inputs(spec: Any, first: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """``first`` (default: TESTER's sample of ``spec``), then a second valid
    instance that differs in every declared field the constraints allow to
    differ -- or ``[first]`` alone when no declared field can vary."""
    from app.factory.build.roles_handlers import _sample_payload

    spec = dict(spec) if isinstance(spec, Mapping) else {}
    first = dict(_sample_payload(spec) if first is None else first)
    fields = [f for f in spec.get("fields") or [] if isinstance(f, dict) and f.get("name")]
    second = {
        **first,
        **{str(f["name"]): _other_value({"type": "str", **f}, first[str(f["name"])]) for f in fields},
    }
    return [first, second] if second != first else [first]


def derive_functions(
    capability_ids: Sequence[str],
    workspace: Path,
    *,
    state_specs: Optional[Mapping[str, Any]] = None,
    route_bodies: Optional[Mapping[str, Any]] = None,
) -> List[GradedFunction]:
    """Every plan capability (TESTER's list and module naming), its inputs
    built from TESTER's spec (``declared_specs.tester_specs``) and TESTER's
    completed sample."""
    from app.factory.build.declared_specs import product_root, read_under, tester_specs
    from app.factory.build.payload_helpers import completed_samples
    from app.factory.build.roles_handlers import _sample_payload

    root = Path(workspace)
    ids = [str(cid) for cid in capability_ids]
    specs = tester_specs(
        ids, dict(state_specs or {}), product_root((root,)), read_under(root), dict(route_bodies or {})
    )
    firsts = completed_samples(root, {cid: _sample_payload(specs.get(cid, {})) for cid in ids})
    return [
        GradedFunction(cid, "app.actions." + cid.replace("-", "_"), contract_inputs(specs.get(cid), firsts[cid]))
        for cid in ids
    ]


#: F-G1: the grader probes a handle in the SAME environment the WRITER gate
#: (writer_behaviour's probe) and the stamped test harness give it -- the
#: workspace's vendored blocks on the path, a scratch store, the gate's
#: test-context token, deploy-time settings stood in, the product's own
#: migrations applied, and no outbound network. Graded bare, a correct
#: handle that fails closed when its blocks fail answered one honest error
#: for both inputs and was read as a stub.
_HARNESS = r"""
import os, socket, sys
os.environ["STORAGE_PATH"] = spec["storage"]
for _k, _v in spec["harness_env"].items():
    os.environ.setdefault(_k, _v)
_stand_in_for_deploy_time_settings(os.getcwd())
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_real_connect = socket.socket.connect
def _offline_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("utf-8", "replace")
    if str(host) not in _LOCAL_HOSTS:
        raise OSError(f"grader: outbound connection to {host!r} refused (no network)")
    return _real_connect(self, address)
socket.socket.connect = _offline_connect
try:
    from app.migrations import upgrade_head
except ImportError:
    upgrade_head = None
if upgrade_head is not None:
    try:
        upgrade_head()
    except BaseException as exc:
        emit({"two_input": False, "mutants": {m: "not_run" for m in spec["mutants"]},
              "error": "harness migration failed: " + why(exc)})
        sys.exit(0)
"""

#: TESTER's one-input pilot rule (rejection_contract.PILOT_*), and the loop
#: that corrects one input with the payload builder's own correction step
#: (payload_helpers._body_rejection + _corrected) before it is judged.
_PILOT_RULE = (
    "_PILOT = "
    + repr((OK_KEY, ERROR_KEY, PILOT_NON_DICT, PILOT_REJECTED, PILOT_FAILED_BLOCK, PILOT_FAILED_STATUSES))
    + r"""
def pilot_failure(name, answer):
    ok_key, error_key, non_dict, rejected, failed_block, statuses = _PILOT
    if not isinstance(answer, dict):
        return name + non_dict + repr(answer)[:120]
    if answer.get(ok_key) is False:
        return name + rejected + str(answer.get(error_key))
    dumped = json.dumps(answer, default=str)
    if any('"status": ' + json.dumps(s) in dumped for s in statuses):
        return name + failed_block + dumped[:300]
    return None
def corrected_input(handle, payload, capability_id):
    # A rejection the handle states as data is corrected as TESTER's route
    # suites correct it: each (field, value) once, so the loop converges.
    payload, tried, corrections = dict(payload), set(), []
    for _ in range(len(payload) + 2):
        try:
            answer = handle(dict(payload))
        except BaseException:
            break
        if not (isinstance(answer, dict) and answer.get(_PILOT[0]) is False):
            break
        rejection = _body_rejection(answer)
        correction = None if rejection is None else _corrected(rejection, capability_id, tried)
        if correction is None:
            break
        name, value = correction
        corrections.append(str(name) + "=" + repr(payload.get(name)) + "->" + repr(value))
        payload[name] = value
    return payload, corrections
"""
)

_RUNNER = _DEPLOY_TIME_SNIPPET + r"""
import importlib, json, sys
spec = json.loads(sys.stdin.read())
def emit(out):
    # The result goes to a file named by the parent with a per-run nonce --
    # never stdout, which the product's own code can print to.
    out["nonce"] = spec["nonce"]
    with open(spec["out"], "w", encoding="utf-8") as fh:
        json.dump(out, fh)
def why(exc):
    kind = "exited" if isinstance(exc, SystemExit) else "raised"
    return f"handle {kind}: {type(exc).__name__}: {exc}"[:300]
""" + "\n".join(render_payload_helpers()) + "\n" + _PILOT_RULE + _HARNESS + r"""
try:
    original = importlib.import_module(spec["module"]).handle
except BaseException as exc:
    emit({"two_input": False, "mutants": {m: "not_run" for m in spec["mutants"]},
          "error": "import failed: " + why(exc)})
    sys.exit(0)
def canon(r):
    return json.dumps(r, sort_keys=True, default=str)
def leaves(obj, path=""):
    # path -> canonical scalar; an empty container is one leaf of its own.
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out.update(leaves(v, path + "/" + json.dumps(str(k))))
        return out or {path: "{}"}
    if isinstance(obj, (list, tuple)):
        out = {}
        for i, v in enumerate(obj):
            out.update(leaves(v, path + "[" + str(i) + "]"))
        return out or {path: "[]"}
    return {path: canon(obj)}
def honest_error(r):
    # The fail-closed envelope every handle answers when a block failed:
    # ok is False and the error is named. Typed fields, never the words.
    return (isinstance(r, dict) and r.get("ok") is False
            and isinstance(r.get("error"), str) and bool(r["error"].strip()))
def echo_sources(answer, payload):
    # Per answer leaf: the input paths whose value it copies.
    inp = leaves(payload)
    return {p: {q for q, w in inp.items() if w == v} for p, v in leaves(answer).items()}
sent, corrections = [], []
for given in spec["inputs"]:
    payload, fixed = corrected_input(original, given, spec["capability"])
    sent.append(payload)
    corrections += fixed
if len(sent) == 1 or canon(sent[0]) == canon(sent[1]):
    # No field can vary: TESTER's one-input pilot rule, nothing more.
    try:
        answer = original(dict(sent[0]))
    except BaseException as exc:
        emit({"two_input": False, "mutants": {m: "not_run" for m in spec["mutants"]},
              "error": why(exc), "inputs": sent[:1], "corrections": corrections})
        sys.exit(0)
    failure = pilot_failure(spec["capability"].replace("-", "_"), answer)
    emit({"one_input": failure is None, "failure": failure, "mutants": {},
          "answers": [canon(answer)], "inputs": sent[:1], "corrections": corrections})
    sys.exit(0)
first, second = sent
out = {"two_input": None, "mutants": {}, "depends_on": [], "answers": [],
       "inputs": sent, "corrections": corrections}
def variant_of(name):
    v = dict(second)
    v[name] = first.get(name)
    return v
varied = [n for n in first if canon(first.get(n)) != canon(second.get(n))]
runs, variants = [], {}
try:
    runs = [(first, original(dict(first))), (first, original(dict(first))),
            (second, original(dict(second)))]
    for name in varied:
        try:
            variants[name] = original(variant_of(name))
        except BaseException:
            variants[name] = None  # it refused this combination: the answer changed
    # Each input again AFTER the variants, so a coarse clock has moved on.
    runs += [(first, original(dict(first))), (second, original(dict(second)))]
except BaseException as exc:
    # A handle that raises or exits answered nothing: never ok, mutants prove nothing.
    out["two_input"] = False
    out["error"] = why(exc)
if not out.get("error"):
    a1, a2 = runs[0][1], runs[2][1]
    out["answers"] = [canon(a1), canon(a2)]
    # Noise: a leaf that differs when the SAME input is sent again.
    drop = set()
    for group in ([runs[0][1], runs[1][1], runs[3][1]], [runs[2][1], runs[4][1]]):
        seen = [leaves(g) for g in group]
        for p in set().union(*seen):
            if len({s.get(p) for s in seen}) > 1:
                drop.add(p)
    # Echo: a leaf that copies the SAME input path in every run.
    sources = [echo_sources(ans, inp) for inp, ans in runs]
    for p in set().union(*[set(leaves(ans)) for _inp, ans in runs]):
        if set.intersection(*[src.get(p, set()) for src in sources]):
            drop.add(p)
    def signal(answer):
        return {p: v for p, v in leaves(answer).items() if p not in drop}
    out["two_input"] = signal(a1) != signal(a2)
    out["honest_error"] = (not out["two_input"]) and honest_error(a1)
    base = signal(a2)
    out["depends_on"] = [n for n in varied
                         if variants[n] is None or signal(variants[n]) != base]
for name in spec["mutants"]:
    if out.get("error"):
        out["mutants"][name] = "not_run"
    else:
        out["mutants"][name] = "killed" if out["depends_on"] else "survived"
emit(out)
"""


def _probe(
    fn: GradedFunction, answers: Sequence[Any], data: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    """The inputs actually sent (after any stated-rejection correction), the
    corrections made, and the answers observed."""
    data = data or {}
    return {
        "inputs": data.get("inputs") or fn.inputs,
        "corrections": list(data.get("corrections") or []),
        "answers": [str(a)[:ANSWER_CHARS] for a in answers],
    }


def harness_process_env(workspace: Path | str) -> Dict[str, str]:
    """The test-context environment every grader process starts from, built
    here once: the product workspace as the only import root, a fixed system
    PATH, and the test context flag. Nothing is copied from the Factory's own
    process, so its keys and settings never reach model-written code. The
    function harness runs in exactly this; the grading boot (FG-2) adds only
    what booting needs on top of it."""
    return {"PYTHONPATH": str(workspace), "PATH": "/usr/bin:/bin", "ENV": "test"}


def run_function(fn: GradedFunction, workspace: Path, *, python: str = sys.executable) -> FunctionResult:
    nonce = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="f8a-") as tmp:
        out_path = Path(tmp) / "grade.json"
        storage = Path(tmp) / "store"
        storage.mkdir()
        spec = json.dumps({"module": fn.module, "capability": fn.capability_id,
                           "inputs": fn.inputs, "mutants": list(MUTANTS),
                           "nonce": nonce, "out": str(out_path), "storage": str(storage),
                           "harness_env": dict(GATE_RUN_ENV)}, default=str)
        try:
            proc = subprocess.run(
                [python, "-c", _RUNNER],
                input=spec,
                capture_output=True,
                text=True,
                cwd=str(workspace),
                timeout=RUN_TIMEOUT_S,
                env=harness_process_env(workspace),
            )
        except subprocess.TimeoutExpired:
            return FunctionResult(fn.capability_id, False, [], list(MUTANTS), "timeout",
                                  probe=_probe(fn, []))
        data = _read_result(out_path, nonce)
    if data is None or proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no result"]
        return FunctionResult(fn.capability_id, False, [], list(MUTANTS),
                              f"handle exited the grader (rc={proc.returncode}): {tail[0]}"[:300],
                              probe=_probe(fn, []))
    killed = [m for m, v in data["mutants"].items() if v == "killed"]
    survived = [m for m, v in data["mutants"].items() if v != "killed"]
    if "one_input" in data:
        passed = data["one_input"] is True
        one = FunctionResult(fn.capability_id, False, killed, survived,
                             ONE_INPUT if passed else str(data.get("failure")),
                             one_input_passed=passed)
        if not one.ok:
            one.probe = _probe(fn, data.get("answers") or [], data)
        return one
    detail = data.get("error") or (
        "" if data["two_input"]
        else BLOCK_UNAVAILABLE if data.get("honest_error") is True
        else ONE_RESULT
    )
    result = FunctionResult(fn.capability_id, data["two_input"] is True, killed, survived, detail,
                            depends_on=[str(n) for n in data.get("depends_on") or []])
    if not result.ok:
        result.probe = _probe(fn, data.get("answers") or [], data)
    return result


def _read_result(path: Path, nonce: str) -> Dict[str, Any] | None:
    """The runner's result, or None when it never wrote one (the process was
    exited from under it) or the nonce does not match (not the runner's)."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text("utf-8"))
    return data if isinstance(data, dict) and data.get("nonce") == nonce else None


def _input_schemas(blueprint: Mapping[str, Any]) -> Dict[str, Any]:
    """Each capability's declared ActionSpec ``input_schema`` (for the vault's
    per-input ranges), keyed by capability id."""
    out: Dict[str, Any] = {}
    for cap in blueprint.get("capabilities") or []:
        cid = str(cap.get("id") or "")
        if cid:
            out[cid] = cap.get("input_schema") or {}
    return out


def _store_formula(cid: str, store_index: Sequence[Mapping[str, Any]]) -> Dict[str, Any] | None:
    """The Store pack formula for ``cid`` with ``expression`` + ``inputs``, read
    from the pack it was indexed from; None when the pack row carries no
    computable expression (then it is a kernel gap, never hand-oracled)."""
    for row in [r for r in store_index if str(r.get("id")) == str(cid)]:
        try:
            data = json.loads(Path(str(row.get("source") or "")).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        for spec in data.get("formulas") or []:
            if str((spec or {}).get("formula_id")) == str(cid) and (spec or {}).get("expression"):
                return {
                    "id": cid,
                    "key": f"store:{cid}",
                    "expression": spec["expression"],
                    "inputs": list(spec.get("inputs") or []),
                }
    return None


def _definition_resolver(vertical: str, store_root: Any):
    """A kernel -> Store -> nothing lookup: capability id -> a definition with
    ``expression`` + ``inputs``, or None. No hard-wired ids; the layer is decided
    by ``formula_resolution.resolve_layer`` over the live kernel/Store indexes."""
    from app.cerebrum_product_kernel.formulas import definition_index, resolve_definitions
    from app.factory.build.formula_resolution import (
        LAYER_KERNEL,
        LAYER_STORE,
        resolve_layer,
        store_coverage_index,
    )

    resolved = resolve_definitions()
    kernel_index = definition_index(resolved)
    store_index = store_coverage_index(vertical, store_root)

    def resolve(cid: str) -> Dict[str, Any] | None:
        layer = resolve_layer(cid, kernel_index, store_index)["layer"]
        if layer == LAYER_KERNEL:
            return resolved.get(cid)
        if layer == LAYER_STORE:
            return _store_formula(cid, store_index)
        return None

    return resolve


def _build_vault(
    blueprint: Mapping[str, Any], workspace: Path, grader_root: Path | str, store_root: Any
) -> Dict[str, Any]:
    """F-8d: the writer-blind vault for this run -- seeded inputs + kernel/Store
    computed expected per gradeable capability -- stored under the grader root
    (isolated from the writer), with kernel gaps recorded as candidate rows."""
    cids = [str(c.get("id") or "") for c in (blueprint.get("capabilities") or []) if c.get("id")]
    resolve = _definition_resolver(str(blueprint.get("vertical") or ""), store_root)
    seed = vault.new_seed()
    entries, gaps = vault.build_run(cids, resolve, _input_schemas(blueprint), seed)
    vault.write_vault(entries, grader_root, product_workspace=workspace)
    # record_kernel_gaps no-ops on an empty list, so the call is unconditional
    # (no redundant guard to drop).
    vault.record_kernel_gaps(
        gaps, Path(grader_root) / vault.VAULT_REL / "new_domain_candidates.jsonl"
    )
    return {
        "seed": seed,
        "entries": [e.to_dict() for e in entries],
        "gaps": [g.to_dict() for g in gaps],
    }


def grade_functions(
    blueprint: Mapping[str, Any],
    workspace: Path | str,
    *,
    capability_ids: Sequence[str],
    state_specs: Optional[Mapping[str, Any]] = None,
    route_bodies: Optional[Mapping[str, Any]] = None,
    python: str = sys.executable,
    min_functions: int = MIN_FUNCTIONS,
    grader_root: Path | str | None = None,
    store_root: Any = None,
) -> FunctionGrade:
    """Grade every plan capability (``capability_ids``) over TESTER's spec.
    ``state_specs`` and ``route_bodies`` are the build state TESTER reads."""
    ws = Path(workspace)
    fns = derive_functions(capability_ids, ws, state_specs=state_specs, route_bodies=route_bodies)
    grade = FunctionGrade([run_function(f, ws, python=python) for f in fns], min_functions=min_functions)
    # F-8d: when a grader root is given, also build the writer-blind vault
    # (seeded inputs + kernel-computed expected) and record it on the grade so
    # grade_record carries the seed + set into grade.json under layer1.functions.
    if grader_root is not None:
        grade.vault = _build_vault(blueprint, ws, grader_root, store_root)
    return grade
