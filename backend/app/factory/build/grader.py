"""The GRADER: the writer's tree is graded BEFORE the TESTER judges it.

The grader is never the thing it grades (The_Level #6). The deterministic
layers are the KERNEL's -- function grade, two-dataset check, hardwiring
scan, prompt-leak diff, UI walk: nothing in them shares the writer's
weights. The model layer (Layer 2) runs only under an INDEPENDENT grader
identity: its provider:model:version must differ from the writer's,
compared case- and whitespace-insensitively. When the only configured
model IS the writer's model, the layer is REFUSED and the refusal is
quoted in the record -- self-grading produces a score that means nothing
and looks exactly like one that does, which is worse than no score at all.

Verdicts, not rework. The grader issues a tier:

* CERTIFIED      -- every layer ran and is green;
* NOT_CERTIFIED  -- a verdict about the tree: failing items quoted, and
                    every layer that could not run is quoted too ("the
                    report says what it did not check" -- a question never
                    asked is an ERROR, never a FAIL);
* ERROR          -- the grader itself could not run (an outage is a
                    verdict about the run, never about the tree).

The grader never feeds a rework loop and never shares a budget with the
TESTER: the runner proceeds on CERTIFIED and ends the run with the quoted
verdict otherwise.

grade.json + GRADE.md land in the workspace whatever the outcome, so the
build carries its grade and the N3 certification gate reads a real record.

NO HARDWIRING. Capability ids, input schemas, UI plans and Layer-2
questions come from the blueprint; expected values come from the
kernel/Store via the writer-blind vault; the forbidden set derives from
the brief; per-item check ids derive from data. The vault and every
grader scratch path live OUTSIDE the product workspace (vault.write_vault
enforces this and raises VaultLeak on any attempt to put the vault inside
the writer's tree).
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
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("cerebrumdev.factory.grader")

#: The gate name this verdict travels under in the ledger and on the Floor.
#: A mechanism name, never a domain literal.
GRADER_GATE = "grader"

#: Disable switch, shaped like FACTORY_INCREMENTAL_REWORK: any truthy value
#: keeps the grader on; 0/off/false/no turns it off. Default ON.
DISABLE_ENV = "FACTORY_GRADER"

#: The verdict tiers. NOT_CERTIFIED is a verdict about the tree; ERROR is a
#: verdict about the run; CERTIFIED is the only pass.
CERTIFIED = "CERTIFIED"
NOT_CERTIFIED = "NOT_CERTIFIED"
ERROR = "ERROR"

#: The dedicated grader-model config (deliberately separate from the
#: writer's coder config -- independence is chosen by configuration, never
#: assumed). All three must be present for Layer 2 to run.
GRADER_LLM_KEY_ENV = "CEREBRUM_GRADER_LLM_API_KEY"
GRADER_LLM_BASE_URL_ENV = "CEREBRUM_GRADER_LLM_BASE_URL"
GRADER_LLM_MODEL_ENV = "CEREBRUM_GRADER_LLM_MODEL"
GRADER_LLM_VERSION_ENV = "CEREBRUM_GRADER_LLM_VERSION"

#: Per-file and total caps on the rendered product Layer 2 may see, so one
#: huge generated file cannot crowd out the rest of the tree.
_FILE_CHARS = 4000
_TOTAL_CHARS = 60_000

#: How long the grader waits for a booted product to answer /health.
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


def grader_enabled() -> bool:
    """The grader is ON unless the disable switch says otherwise."""
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
    product's app, never the factory backend this grader runs inside."""
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
    scratch = root.parent / (root.name + ".grader-boot")
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


# --- independence (The_Level #6: the grader is never the thing it grades) ---


def _provider_token(host: str) -> str:
    """The provider token from a host: the registrable-domain root label.
    ``api.deepseek.com`` -> ``deepseek``, ``openrouter.ai`` -> ``openrouter``.
    No provider list is consulted -- the token is structural."""
    labels = [part for part in (host or "").strip().split(".") if part]
    return (labels[-2] if len(labels) >= 2 else (labels[0] if labels else "")).strip().lower()


def _norm_identity(provider: str, model: str, version: str = "") -> str:
    """The identity triple, normalised for comparison: provider, model and
    version, case- and whitespace-insensitive (every part stripped) -- a
    check defeated by capitalisation is not a check (The_Level #6)."""
    return ":".join(str(part).strip() for part in (provider, model, version)).lower()


def _model_family(model_id: str) -> str:
    """The vendor family of a model id, structurally: the vendor prefix
    before the first ``/``, else the leading alphabetic run of the name.
    ``anthropic/claude-sonnet-4.5`` -> ``anthropic``; ``deepseek-v3`` ->
    ``deepseek``. No model list is consulted."""
    text = str(model_id or "").strip().lower()
    if "/" in text:
        return text.split("/", 1)[0].strip()
    run = []
    for ch in text:
        if ch.isalpha():
            run.append(ch)
        else:
            break
    return "".join(run)


def writer_identity(env: Optional[Mapping[str, str]] = None) -> str:
    """The WRITER's identity -- the target fingerprint the grader may never
    equal. The provider and model come from the coder's own configuration
    (never spelled here); version is carried when the coder declares one."""
    from app.factory import code_cli

    values = env if env is not None else os.environ
    provider = code_cli.factory_code_provider() or "deepseek"
    model = values.get(code_cli.DEEPSEEK_CODE_MODEL_ENV, "").strip() or code_cli.DEFAULT_DEEPSEEK_MODEL
    version = values.get("DEEPSEEK_CODE_VERSION", "").strip()
    return _norm_identity(provider, model, version)


def grader_llm_config(env: Optional[Mapping[str, str]] = None) -> Optional[Dict[str, str]]:
    """The dedicated grader-model config, or None when it is not complete.
    Independence is chosen by configuration, never assumed: an incomplete
    config is an unconfigured grader, not a fallback to the writer's key."""
    values = env if env is not None else os.environ
    key = values.get(GRADER_LLM_KEY_ENV, "").strip()
    base_url = values.get(GRADER_LLM_BASE_URL_ENV, "").strip()
    model = values.get(GRADER_LLM_MODEL_ENV, "").strip()
    if not (key and base_url and model):
        return None
    return {
        "api_key": key,
        "base_url": base_url,
        "model": model,
        "version": values.get(GRADER_LLM_VERSION_ENV, "").strip(),
    }


def independence_refusal(writer_id: str, grader_cfg: Optional[Mapping[str, str]]) -> Optional[str]:
    """Why Layer 2 may not run, or None. An unconfigured grader is refused;
    a grader whose identity equals the writer's is refused -- self-grading
    produces a score that means nothing and looks exactly like one that
    does. The refusal is quoted, never swallowed."""
    if grader_cfg is None:
        return (
            "layer 2 not graded: no independent grader configured "
            "(CEREBRUM_GRADER_LLM_*); the writer's model may not grade the writer's tree"
        )
    host = urlparse(grader_cfg["base_url"]).hostname or ""
    grader_id = _norm_identity(
        _provider_token(host), grader_cfg["model"], grader_cfg.get("version", "")
    )
    if grader_id == writer_id:
        return (
            f"layer 2 refused: grader identity {grader_id} equals the writer's identity -- "
            "the grader may never be the thing it grades"
        )
    # Family-level independence (F5): refuse when the grader's model VENDOR
    # equals the writer's, even when the provider:model:version triple differs
    # (e.g. OpenRouter serving deepseek-v3 against a deepseek writer).
    writer_model = writer_id.split(":")[1] if ":" in (writer_id or "") else ""
    writer_family = _model_family(writer_model)
    grader_family = _model_family(grader_cfg["model"])
    if grader_family and writer_family and grader_family == writer_family:
        return (
            f"layer 2 refused: grader model family {grader_family!r} equals the "
            "writer's model family -- the grader may never share the writer's "
            "model vendor"
        )
    return None


def _grader_complete(messages: List[Dict[str, str]]) -> str:
    """Layer 2's completion call against the DEDICATED grader endpoint: one
    fresh call, returned as the raw content text run_layer2 parses. The
    grader's own system prompt travels in ``messages`` -- this helper adds
    nothing and knows nothing about the product."""
    import httpx

    cfg = grader_llm_config()
    if cfg is None:
        raise RuntimeError("no independent grader configured (CEREBRUM_GRADER_LLM_*)")
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            url,
            json={"model": cfg["model"], "messages": messages},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def _layer2_layer1_view(functions: Mapping[str, Any], ui: Mapping[str, Any]) -> Dict[str, Any]:
    """The Layer-1 view Layer 2 may see: the recorded grades, nothing else
    (LAYER2_INPUTS is enforced by layer2_messages anyway)."""
    return {"ok": functions.get("ok") is True and ui.get("ok") is True, "functions": functions, "ui": ui}


@dataclass(frozen=True)
class _UiNotRun:
    """The UI layer when it could not run at all: the honest not-run shape
    ``ui_grader.UiGrade`` reports when its dependencies are unavailable.
    Degenerate UiGrade: every property derives from the (empty) steps and
    the reason, so a reader sees WHY it is not a pass."""

    reason: str = ""
    steps: Tuple[Dict[str, Any], ...] = ()

    @property
    def ran(self) -> bool:
        return bool(self.steps)

    @property
    def errors(self) -> List[str]:
        return [self.reason] if self.reason else []

    @property
    def failing(self) -> List[str]:
        return [str(step.get("name") or "") for step in self.steps if not step.get("passed")] + list(
            self.errors
        )

    @property
    def ok(self) -> bool:
        return self.ran and not self.failing

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "ran": self.ran, "failing": self.failing, "steps": list(self.steps)}


@dataclass(frozen=True)
class GraderVerdict:
    """The grader's verdict. ``tier`` is CERTIFIED / NOT_CERTIFIED / ERROR;
    ``items`` quotes every failing or refused item; ``ungraded`` quotes every
    layer that could not run (the report says what it did not check)."""

    tier: str
    items: Tuple[str, ...] = ()
    ungraded: Tuple[str, ...] = ()
    grade: Optional[Mapping[str, Any]] = None

    @property
    def ok(self) -> bool:
        return self.tier == CERTIFIED


def _grade_ui(
    blueprint: Mapping[str, Any],
    root: Path,
    gradroot: Path,
    base_url: Optional[str],
) -> Tuple[Any, bool, str]:
    """The UI layer, honestly: it runs when the product boots and the
    playwright toolchain exists; otherwise it is recorded as NOT RUN -- a
    quoted gap, never a fabricated green."""
    ui_not_run = ""
    boot_proc: Optional[subprocess.Popen] = None
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
        from app.factory.build.product_grader import ui_grader
    except Exception as exc:  # noqa: BLE001 -- a missing optional dependency
        ui_grader = None
        ui_not_run = ui_not_run or f"UI run did not happen: {type(exc).__name__}: {exc}"
    if ui_grader is None:
        ui: Any = _UiNotRun(ui_not_run or "UI run did not happen: the UI grader is unavailable")
        return ui, False, ui_not_run
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
    return ui, ui_ran, ui_not_run


def run_grader(
    *,
    blueprint: Mapping[str, Any],
    workspace: Path | str,
    product_id: str,
    compiled_brief: Any = None,
    prompt_text: str = "",
    store_root: Any = None,
    base_url: Optional[str] = None,
    python: str = sys.executable,
    grader_root: Optional[Path | str] = None,
) -> GraderVerdict:
    """Grade the writer's tree and write the record into it. Returns the
    verdict; raises nothing (an outage is an ERROR verdict, never an
    exception the runner must guess about)."""
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
        logger.exception("grader function grade failed")

    # --- two-dataset + hardwiring scan + prompt leak ----------------------
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
    ui, ui_ran, ui_not_run = _grade_ui(blueprint, root, gradroot, base_url)

    # --- Layer 2, under an independent grader identity --------------------
    l2: Mapping[str, Any]
    ungraded: List[str] = []
    if ui_not_run:
        ungraded.append(ui_not_run)
    refusal = independence_refusal(writer_identity(), grader_llm_config())
    if refusal:
        l2 = {"ran": False, "verdicts": [], "open_fails": [refusal]}
        ungraded.append(refusal)
    else:
        # A function grade that never ran is written as its honest RED shape,
        # so the record carries the outage instead of a missing layer.
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
        layer1_view = _layer2_layer1_view(fn_dict, ui_dict)
        l2_inputs = {"compiled_brief": brief, "rendered_product": rendered, "layer1": layer1_view}
        l2 = layer2.run_layer2(l2_inputs, blueprint, _grader_complete)

    # --- the record --------------------------------------------------------
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
    grade = grade_record.build_grade(
        product_id=product_id,
        functions=fn_dict,
        ui=ui.to_dict(),
        layer2=l2,
        hardwiring=hardwiring,
    )
    try:
        grade_writer.write_grade_json(root, grade)
        (root / grade_writer.GRADE_MD).write_text(render_grade_md(grade), encoding="utf-8")
    except OSError as exc:  # noqa: BLE001 -- named, and the verdict still returns
        logger.exception("grader could not write the grade record")
        if outage is None:
            outage = f"grade record could not be written: {type(exc).__name__}: {exc}"

    # --- the verdict --------------------------------------------------------
    if outage:
        return GraderVerdict(tier=ERROR, items=(outage,), ungraded=tuple(ungraded), grade=grade)

    # Deterministic reds, computed once and quoted whatever the tier.
    items: List[str] = []
    fn_failing = list(functions.failing)
    if not functions.ok:
        for name in fn_failing:
            items.append(f"function grade FAIL: {name}")
    ui_failing = list(ui.failing) if ui_ran else []
    if ui_ran:
        for name in ui_failing:
            items.append(f"UI step FAIL: {name}")
    if not refusal:
        for question in l2.get("open_fails") or []:
            items.append(f"re-run writer on: {question}")
    for finding in (hardwiring or {}).get("findings") or []:
        kind = finding.get("kind") if isinstance(finding, Mapping) else ""
        if kind == "function":
            cid = str(finding.get("capability") or "")
            items.append(f"de-hardwire {cid}: {finding.get('verdict')}")
        elif kind == "forbidden_literal":
            rel = str(finding.get("file") or "")
            items.append(f"remove baked literals from {rel}: {finding.get('literals')}")
        elif kind == "prompt_leak":
            items.append(f"stop leaking the prompt into the source: {finding.get('literals')}")

    # A refused layer is a quoted NOT_CERTIFIED gap -- the report says what
    # it did not check, and no tier is issued over the gap.
    if refusal:
        return GraderVerdict(
            tier=NOT_CERTIFIED,
            items=(refusal,) + tuple(items),
            ungraded=tuple(ungraded),
            grade=grade,
        )

    # A Layer 2 that never answered is an outage -- a verdict about the run
    # (the grader could not grade), never about the tree.
    if l2.get("ran") is not True:
        return GraderVerdict(
            tier=ERROR,
            items=(f"Layer 2 did not run: {'; '.join(str(q) for q in l2.get('open_fails') or [])}",),
            ungraded=tuple(ungraded),
            grade=grade,
        )

    # A refused or unrun layer blocks certification: the report says what it
    # did not check, and no tier is issued over the gap.
    if ungraded or items:
        return GraderVerdict(
            tier=NOT_CERTIFIED,
            items=tuple(items) + tuple(ungraded),
            ungraded=tuple(ungraded),
            grade=grade,
        )
    return GraderVerdict(tier=CERTIFIED, grade=grade)
