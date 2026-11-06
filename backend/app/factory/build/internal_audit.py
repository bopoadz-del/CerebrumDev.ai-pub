"""Writer-side internal audit: the F-8 product grader runs on the writer's
tree BEFORE the TESTER judges it.

The WRITER phase gate passes, and instead of handing straight to TESTER the
runner asks this module to grade what the writer just produced:

* Layer-1 function grade (deterministic, subprocess-isolated);
* the F-8e two-dataset check (hardwired-constant / input-specific-branch /
  wrong-computation per gradeable capability);
* the F-8e hardwiring scan + prompt-leak diff;
* Layer-2, the separate-context grader (fresh call, never the writer's
  transcript);
* Layer-1 UI grade (Playwright) when the product can be booted here -- an
  environment that cannot run a layer records it as NOT RUN, never as green.

The record (grade.json + GRADE.md) lands in the workspace root whatever the
outcome, so the build carries its grade and the N3 certification gate reads
a real record instead of "missing".

Fail-closed, and honest about its own availability:

* a grader that CANNOT run at all (the function grade crashed, Layer 2 never
  answered) is an audit OUTAGE -- the runner stops the build with the named
  reason instead of sending the writer on a phantom rework;
* a grader that ran and found RED (failing functions, two-dataset FAILs,
  hardwiring hits, prompt leaks, Layer-2 FAILs, failing UI steps) returns
  writer-work findings -- the runner sends the writer back round before
  TESTER ever judges;
* a layer the environment could not run (no booted product for the UI
  grade, no playwright) is recorded in the grade as NOT RUN: it does not
  block the writer-side gate, and the certification gate still refuses the
  final handoff on it -- an unaudited layer is never a silent pass.

NO HARDWIRING. Every input is derived: capability ids, input schemas, UI
plans and Layer-2 questions come from the blueprint; expected values come
from the kernel/Store via the writer-blind vault; the forbidden-literal set
for the hardwiring scan is derived from the brief text and the writer
prompt; the vault and every grader scratch path live OUTSIDE the product
workspace (vault.write_vault enforces this and raises VaultLeak on any
attempt to put the vault inside the writer's tree). Nothing in this module
names a product, client, vertical, capability or path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

logger = logging.getLogger("cerebrumdev.factory.internal_audit")

#: The gate name this verdict travels under in the ledger and on the Floor.
#: A mechanism name, never a domain literal.
AUDIT_GATE = "internal_audit"

#: Disable switch, shaped like FACTORY_INCREMENTAL_REWORK: any truthy value
#: keeps the audit on; 0/off/false/no turns it off. Default ON.
DISABLE_ENV = "FACTORY_INTERNAL_AUDIT"

#: Per-file and total caps on the rendered product Layer 2 may see, so one
#: huge generated file cannot crowd out the rest of the tree.
_FILE_CHARS = 4000
_TOTAL_CHARS = 60_000

#: How long the audit waits for a booted product to answer /health.
_BOOT_READY_TIMEOUT_S = 60.0

#: Structured brief fields whose VALUES are domain data (client, project,
#: vertical, product names). The field NAMES are schema, not domain literals.
_STRUCTURED_FIELDS = ("client", "project", "vertical", "name", "product_id")

#: Directory names that never belong in the rendered product (grader and
#: environment scaffolding, not product source).
_RENDER_EXCLUDED_DIRS = ("__pycache__", ".venv", "node_modules", ".git")

#: Document-shaped tokens (a basename with a document extension) in the
#: brief. Exact-token membership against this closed vocabulary is a contract
#: check (the suffix IS the token), not a vocabulary scan over text.
_DOC_EXTENSIONS = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".tsv", ".txt", ".png", ".jpg", ".jpeg",
})


def audit_enabled() -> bool:
    """The audit is ON unless the disable switch says otherwise."""
    val = os.environ.get(DISABLE_ENV, "").strip().lower()
    return val not in ("0", "off", "false", "no")


def _as_mapping(blueprint: Any) -> Dict[str, Any]:
    """The blueprint as a plain mapping, whatever the caller handed in: a
    dict stays itself, a model object is dumped by its own dumper (model_dump
    first, else __dict__). No field is named here -- the object declares its
    own shape."""
    if isinstance(blueprint, Mapping):
        return dict(blueprint)
    dump = getattr(blueprint, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump())
        except TypeError:
            pass
    return dict(getattr(blueprint, "__dict__", {}) or {})


def _structured_literals(blueprint: Mapping[str, Any]) -> set:
    """The blueprint's own structured domain values (client, project,
    vertical, product name): data the brief dictated, read off the schema
    fields -- nothing hand-listed."""
    out = set()
    if not isinstance(blueprint, Mapping):
        blueprint = dict(getattr(blueprint, "__dict__", {}) or {})
    for key in _STRUCTURED_FIELDS:
        value = blueprint.get(key)
        if isinstance(value, str) and value.strip():
            out.add(value.strip())
    return out


def _brief_text(blueprint: Mapping[str, Any], compiled_brief: Any) -> str:
    """The brief text the forbidden set and Layer 2 derive from: the
    compiled brief's own string values verbatim (never a JSON re-encode,
    which would escape the quotes the forbidden set must read), else the
    blueprint's own narrative fields."""
    if isinstance(compiled_brief, Mapping):
        parts: List[str] = []
        for value in compiled_brief.values():
            if isinstance(value, str) and value.strip():
                parts.append(value)
            elif isinstance(value, (Mapping, list)):
                parts.append(json.dumps(value, sort_keys=True, default=str))
        if parts:
            return "\n".join(parts)
    if isinstance(compiled_brief, str) and compiled_brief.strip():
        return compiled_brief
    if isinstance(blueprint, Mapping):
        parts = []
        for key in ("description", "narrative", "notes", "summary"):
            value = blueprint.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        for cap in blueprint.get("capabilities") or []:
            if isinstance(cap, Mapping) and isinstance(cap.get("description"), str):
                parts.append(cap["description"])
        for mod in blueprint.get("ui_modules") or []:
            if isinstance(mod, str):
                parts.append(mod)
        return "\n".join(parts)
    return ""


def _tokens(text: str) -> List[str]:
    """Whitespace tokens with trailing punctuation stripped -- the structural
    tokenization the derivations read; no vocabulary decides anything here."""
    return [t.strip(".,;:()[]{}\"'`") for t in (text or "").split()]


def derive_forbidden(
    blueprint: Mapping[str, Any],
    brief_text: str,
    prompt_text: str,
) -> FrozenSet[str]:
    """The hardwiring scan's forbidden set, DERIVED: structured blueprint
    values, document references, quoted spans, amounts, proper-noun-shaped
    tokens and file-shaped tokens from the brief. Generic lowercase prose
    and stop-word-shaped noise never enter it. The prompt text is carried
    for the prompt-leak diff, not for vocabulary mining."""
    out: set = _structured_literals(blueprint)
    text = brief_text or ""
    for token in _tokens(text):
        # A folded-upload marker carries the file name after the protocol
        # token; the equality on the marker half is a contract check on the
        # platform's own schema, never a scan for domain vocabulary.
        marker, sep, rest = token.partition(":")
        if sep and marker == "document" and rest:
            name = rest.strip()
            if name:
                out.add(name)
                stem = Path(name).stem
                if stem:
                    out.add(stem)
            continue
        suffix = Path(token).suffix.lower()
        if suffix and suffix in _DOC_EXTENSIONS:
            out.add(token)
            stem = Path(token).stem
            if stem:
                out.add(stem)
    out.update(re.findall(r'"([^"]{3,60})"', text))
    out.update(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    out.update(re.findall(r"\b[A-Z][a-z]{2,}(?:[A-Z][a-z]+)*\b", text))
    # The prompt itself is not a source of forbidden literals: the leak
    # check compares the prompt against the product separately.
    return frozenset(sorted(v for v in out if len(v) <= 80))


def render_product(workspace: Path | str) -> str:
    """A bounded, deterministic render of the product source for Layer 2:
    sorted relative paths, per-file and total caps, scaffold directories
    excluded. Ordering and caps are structural -- no file is named."""
    root = Path(workspace)
    blocks: List[str] = []
    budget = _TOTAL_CHARS
    for py in sorted(root.rglob("*")):
        # Non-files (directories) reach read_text and raise OSError, which
        # the except skips -- one exclusion path, no redundant guard.
        rel_parts = py.relative_to(root).parts
        if set(rel_parts) & set(_RENDER_EXCLUDED_DIRS):
            continue
        top = rel_parts[0] if len(rel_parts) > 1 else ""
        if py.suffix != ".py" and not (
            top in ("app", "frontend") and py.suffix in (".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".json", ".md")
        ):
            continue
        try:
            text = py.read_text("utf-8", "replace")
        except OSError:
            continue
        rel = py.relative_to(root).as_posix()
        if len(text) > _FILE_CHARS:
            text = text[:_FILE_CHARS] + "\n# ... truncated by the grader\n"
        block = f"## file: {rel}\n{text}\n"
        if len(block) > budget:
            continue
        budget -= len(block)
        blocks.append(block)
    return "\n".join(blocks)


def _free_port() -> int:
    """An OS-assigned free port for the scratch boot -- never a fixed one."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _boot_env(scratch: Path, root: Path) -> Dict[str, str]:
    """Scratch environment for the booted product: its own storage and
    database under a fresh temp dir, never the runner's live state -- and the
    PRODUCT workspace first on PYTHONPATH, so the booted process imports the
    product's app, never the factory backend this audit runs inside."""
    env = dict(os.environ)
    env["STORAGE_PATH"] = str(scratch)
    env["DATABASE_URL"] = f"sqlite:///{scratch / 'gate.db'}"
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def boot_product(workspace: Path | str) -> Tuple[Optional[subprocess.Popen], Optional[str]]:
    """Boot the product on a private port with a scratch STORAGE_PATH, the
    same shape the writer's own self-check uses, and wait for /health.
    Returns (process, base_url) or (None, reason). The caller stops the
    process; a product that will not boot is a real finding, recorded
    honestly by the caller -- never a skipped grade."""
    root = Path(workspace)
    port = _free_port()
    scratch = root.parent / (root.name + ".audit-boot")
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--host", "127.0.0.1"],
            cwd=str(root),
            env=_boot_env(scratch, root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return None, f"product boot did not start: {type(exc).__name__}"
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + _BOOT_READY_TIMEOUT_S
    try:
        import urllib.request

        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return None, f"product boot exited early with code {proc.returncode}"
            try:
                with urllib.request.urlopen(base_url + "/health", timeout=2.0) as resp:
                    if resp.status == 200:
                        return proc, base_url
            except OSError:
                pass
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001 -- an unreadable health is "did not boot"
        return None, f"product boot health check failed: {type(exc).__name__}"
    proc.terminate()
    return None, "product boot did not answer /health in time"


def _complete_via_chat_config(messages: List[Dict[str, str]]) -> str:
    """Layer 2's completion call: one fresh LLM call through the factory
    chat config, returned as the raw JSON text run_layer2 parses. The
    grader's own system prompt travels in ``messages`` -- this helper adds
    nothing and knows nothing about the product."""
    from app.factory.product_architect import _llm_json_call

    return json.dumps(_llm_json_call(messages, use_chat_config=False), default=str)


def _layer2_layer1_view(functions: Mapping[str, Any], ui: Mapping[str, Any]) -> Dict[str, Any]:
    """The Layer-1 view Layer 2 may see: the recorded grades, nothing else
    (LAYER2_INPUTS is enforced by layer2_messages anyway)."""
    return {"ok": functions.get("ok") is True and ui.get("ok") is True, "functions": functions, "ui": ui}


@dataclass(frozen=True)
class _UiNotRun:
    """The UI layer when it could not run at all: the honest not-run shape
    ``ui_grader.UiGrade`` reports when its dependencies are unavailable."""

    reason: str = ""

    @property
    def ran(self) -> bool:
        return False

    @property
    def failing(self) -> List[str]:
        return []

    @property
    def errors(self) -> List[str]:
        return [self.reason] if self.reason else []

    @property
    def ok(self) -> bool:
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": False, "ran": False, "failing": [], "steps": []}


@dataclass(frozen=True)
class InternalAuditResult:
    """What the audit did. ``grade`` is the product_grade.v1 record that was
    written to the workspace root (always written). Exactly one of
    ``writer_refusal`` / ``unavailable`` is non-None on a red audit; both
    None means green."""

    grade: Mapping[str, Any]
    writer_refusal: Optional[str]
    unavailable: Optional[str]
    findings: Tuple[str, ...] = ()
    checks: Tuple[str, ...] = ()
    ui_ran: bool = False
    ui_not_run_reason: str = ""


def run_internal_audit(
    *,
    blueprint: Mapping[str, Any],
    workspace: Path | str,
    product_id: str,
    compiled_brief: Any = None,
    prompt_text: str = "",
    store_root: Any = None,
    complete_l2: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    base_url: Optional[str] = None,
    python: str = sys.executable,
    grader_root: Optional[Path | str] = None,
) -> InternalAuditResult:
    """Run the whole writer-side audit on ``workspace`` and write the grade
    record into it. Returns the structured outcome; raises nothing (an
    outage is an outcome, never an exception the runner must guess about)."""
    root = Path(workspace)
    blueprint = _as_mapping(blueprint)
    gradroot = Path(grader_root) if grader_root else root.parent / (root.name + ".grader")
    brief = _brief_text(blueprint, compiled_brief)
    vertical = str(blueprint.get("vertical") or "")
    cids = [
        str(cap.get("id") or "")
        for cap in (blueprint.get("capabilities") or [])
        if isinstance(cap, Mapping) and cap.get("id")
    ]

    from app.factory.build.product_grader import (
        function_grader as fg,
        grade_record,
        grade_writer,
        layer2,
        scan_generated,
        two_dataset,
        vault,
    )
    from app.factory.build.product_grader.grade_md import render_md as render_grade_md

    # --- Layer 1 functions + the writer-blind vault -----------------------
    functions: Any = None
    outage: Optional[str] = None
    try:
        functions = fg.grade_functions(
            blueprint, root, python=python, grader_root=gradroot, store_root=store_root
        )
    except Exception as exc:  # noqa: BLE001 -- an outage is named, never silent
        outage = f"function grade could not run: {type(exc).__name__}: {exc}"
        logger.exception("internal audit function grade failed")

    # --- F-8e two-dataset + hardwiring scan + prompt leak -----------------
    hardwiring: Optional[Mapping[str, Any]] = None
    rendered = render_product(root)
    if functions is not None:
        resolve = fg._definition_resolver(vertical, store_root)
        schemas = fg._input_schemas(blueprint)
        # The A/B seeds derive from the SAME vault the function grade built
        # (writer-blind, kernel-computed) -- the deterministic checks own
        # nothing of their own, so they cannot exist without a function
        # grade to hang off.
        vault_record = functions.vault or {}
        seed_a = int(vault_record.get("seed") or vault.new_seed())
        pairs = two_dataset.build_datasets(cids, resolve, schemas, seed_a, seed_a + 1)
        checks = two_dataset.run_two_dataset(pairs, root, python=python)
        verdicts = {check.capability_id: check.verdict for check in checks}
        forbidden = derive_forbidden(blueprint, brief, prompt_text)
        hits = scan_generated.forbidden_hits(root, forbidden)
        leaks = scan_generated.prompt_leak(prompt_text, rendered, forbidden) if prompt_text else []
        hardwiring = grade_record.hardwiring_section(verdicts, hits, leaks)

    # --- Layer 1 UI -------------------------------------------------------
    # The UI layer is imported lazily: its optional dependencies (playwright,
    # openpyxl) are not part of the audit's core, and an environment without
    # them records the layer as NOT RUN -- a certification blocker, never an
    # outage and never a fabricated green.
    ui: Any
    ui_ran = False
    ui_not_run = ""
    boot_proc: Optional[subprocess.Popen] = None
    try:
        from app.factory.build.product_grader import ui_grader
    except Exception as exc:  # noqa: BLE001 -- a missing optional dependency
        ui_grader = None
        ui_not_run = f"UI run did not happen: {type(exc).__name__}: {exc}"
    if ui_grader is None:
        ui = _UiNotRun(ui_not_run or "UI run did not happen: the UI grader is unavailable")
    else:
        if base_url is None:
            try:
                boot_proc, booted = boot_product(root)
            except Exception as exc:  # noqa: BLE001 -- recorded below, not raised
                boot_proc = None
                booted = None
                ui_not_run = f"UI run did not happen: boot failed: {type(exc).__name__}"
            # boot_product returns (process, url) on success and (None, reason)
            # on failure -- only a live process's url is a base_url.
            if boot_proc is None:
                if not ui_not_run:
                    ui_not_run = booted or "UI run did not happen: product did not boot"
                base_url = None
            else:
                base_url = booted
        try:
            if base_url:
                ui = ui_grader.grade_ui(blueprint, gradroot / "ui", base_url=base_url)
            else:
                ui = ui_grader.UiGrade(
                    [], [ui_not_run or "UI run did not happen: no base URL for the rendered product"]
                )
            ui_ran = bool(ui.ran)
        finally:
            if boot_proc is not None and boot_proc.poll() is None:
                boot_proc.terminate()
    if not ui_ran and not ui_not_run:
        ui_not_run = "; ".join(str(e) for e in ui.errors) or "UI run did not happen"

    # --- Layer 2 ----------------------------------------------------------
    l2: Mapping[str, Any]
    # A function grade that never ran is written as its honest RED shape, so
    # the record carries the outage instead of a missing layer (which the
    # record builder cannot express).
    if functions is not None:
        fn_dict = functions.to_dict()
    else:
        fn_dict = {
            "ok": False,
            "graded": 0,
            "min_functions": None,
            "failing": [outage or "function grade did not run"],
            "functions": [],
        }
    ui_dict = ui.to_dict()
    layer1_view = _layer2_layer1_view(fn_dict or {}, ui_dict)
    l2_inputs = {"compiled_brief": brief, "rendered_product": rendered, "layer1": layer1_view}
    if complete_l2 is None:
        complete_l2 = _complete_via_chat_config
    l2 = layer2.run_layer2(l2_inputs, blueprint, complete_l2)

    # --- the record --------------------------------------------------------
    grade = grade_record.build_grade(
        product_id=product_id,
        functions=fn_dict,
        ui=ui_dict,
        layer2=l2,
        hardwiring=hardwiring,
    )
    try:
        grade_writer.write_grade_json(root, grade)
        (root / grade_writer.GRADE_MD).write_text(render_grade_md(grade), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 -- named, and the grade still returns
        logger.exception("internal audit could not write the grade record")
        if outage is None:
            outage = f"grade record could not be written: {type(exc).__name__}: {exc}"

    # --- the verdict -------------------------------------------------------
    if outage:
        return InternalAuditResult(grade=grade, writer_refusal=None, unavailable=outage)

    # A grader layer that could not run at all (no function grade, or Layer
    # 2 that never answered) is an outage -- the writer cannot fix the
    # grader, so this is never writer work.
    if l2.get("ran") is not True:
        return InternalAuditResult(
            grade=grade, writer_refusal=None,
            unavailable="Layer 2 did not run: " + "; ".join(str(q) for q in l2.get("open_fails") or []),
        )

    fn_failing = list(functions.failing)
    ui_failing = list(ui.failing) if ui_ran else []
    # Findings and their per-finding check ids are built TOGETHER from the
    # structured grade pieces (never by classifying a finding's text), so
    # the same-failure-twice rule keys on the DATA: capability id, question
    # ordinal, file path, step name.
    reds: List[str] = []
    red_checks: List[str] = []
    if not functions.ok:
        for name in fn_failing:
            reds.append(f"function grade FAIL: {name}")
            red_checks.append("audit.function.count" if ":" in str(name) else f"audit.function.{name}")
    if ui_ran:
        for name in ui_failing:
            reds.append(f"UI step FAIL: {name}")
            red_checks.append(f"audit.ui.{name}")
    for index, question in enumerate(l2.get("open_fails") or []):
        reds.append(f"re-run writer on: {question}")
        red_checks.append(f"audit.layer2.{index}")
    for finding in (hardwiring or {}).get("findings") or []:
        kind = finding.get("kind") if isinstance(finding, Mapping) else ""
        if kind == "function":
            cid = str(finding.get("capability") or "")
            reds.append(f"de-hardwire {cid}: {finding.get('verdict')}")
            red_checks.append(f"audit.two_dataset.{cid}")
        elif kind == "forbidden_literal":
            rel = str(finding.get("file") or "")
            reds.append(f"remove baked literals from {rel}: {finding.get('literals')}")
            red_checks.append(f"audit.forbidden.{rel}")
        elif kind == "prompt_leak":
            reds.append(f"stop leaking the prompt into the source: {finding.get('literals')}")
            red_checks.append("audit.prompt_leak")

    if reds:
        findings = tuple(reds)
        refusal = "; ".join(reds)[:500] or "internal audit RED"
        return InternalAuditResult(
            grade=grade,
            writer_refusal=refusal,
            unavailable=None,
            findings=findings,
            checks=tuple(red_checks),
            ui_ran=ui_ran,
            ui_not_run_reason=ui_not_run,
        )
    return InternalAuditResult(
        grade=grade, writer_refusal=None, unavailable=None, ui_ran=ui_ran, ui_not_run_reason=ui_not_run
    )
