"""Build-role implementations (collector, cloner, writer, tester, store manager).

Public types and templates live in ``roles_models`` / ``roles_constants``.
``roles.py`` re-exports this module so existing imports keep working.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("cerebrumdev.factory.roles_handlers")

from app.factory.build.failure_kinds import failure_kind, record_failure_kind
from app.factory.build.authority import (
    KERNEL_ROUTE_NAMES,
    BuildRole,
    jobs_manifest,
)
from app.factory.build.offline_adapters import (
    DOCUMENT_ENGINE_PARSERS_STUB,
    emit_instantiate_ready,
    emit_runtime_module,
    needs_document_engine_parsers_package,
    package_imports_own_parsers,
)
from app.factory.build.block_inputs import (
    align_spec_to_handler_source,
    extract_capability_route_source,
    render_block_inputs_module,
    sample_channel_value,
    sanitize_python_identifier,
)
from app.factory.build.authorship import (
    AGENT_SOURCE_EXACT,
    AGENT_SOURCE_PREFIXES,
    AUTHORSHIP_MARKER,
    RENDERED_MARKER_READER,
    authorship_marker_line,
    coding_agent_artifact_ids,
    writer_contract_role_detail,
)
from app.factory.build.persist_accept import (
    FACTORY_GROUNDED_PERSIST_SOURCE,
    WRITER_REUSE_HANDLER_HALT,
    persist_handler_rel,
    persist_workspace_root,
)
from app.factory.build.reuse_accept import (
    ReuseAcceptHalt,
    apply_default_actions_to_handler,
    assert_reuse_schema_accept,
    harvest_block_default_actions,
    parse_handler_block_ids,
)
from app.factory.build.workflow_accept import (
    FACTORY_GROUNDED_EVENT_BUS_SOURCE,
    grounded_event_bus_handler_body,
    handler_satisfies_event_bus_contract,
    needs_grounded_event_bus_handler,
)
from app.factory.build.writer_fanout import (
    FanOut,
    coder_fanout,
    fan_out,
    loop_budget_too_low,
)
from app.factory.build.block_obligations import (
    BlockObligationError,
    ENVELOPE_STATUS_VALUES,
    assert_feedable,
    augment_model_spec,
    dependency_obligations,
    dependency_obligations_on_disk,
    describe_resource_obligations,
    ensure_record_envelope,
    render_preconditions_module,
    render_dependency_lines,
)
from app.factory.build.roles_constants import (
    _BLOCK_DEF_RE,
    _CONFTEST,
    CONFTEST_REL,
    _DISPATCH_RUNTIME,
    _GET_BLOCK_RE,
    _INSTANTIATE_HELPER,
    _PY_DEFAULTS,
    _REGISTRY_API_NAMES,
    _SAMPLE_VALUES,
    _STORE_FOREIGN_LAZY_RE,
    _STORE_FOREIGN_TOP_RE,
    _STORE_RUNTIME_RE,
)
from app.factory.build.roles_models import RoleContext, RoleError, RoleResult
from app.factory.build.payload_helpers import render_payload_helpers
from app.factory.build.findings import render_capability_recorder
from app.factory.build.findings import rework_targets as _rework_targets
from app.factory.build.rejection_contract import (
    ACCEPT_STATUSES,
    CROSS_TENANT_READ_STATUS,
    ERROR_KEY,
    LISTED_RECORDS_SRC,
    OK_KEY,
    RECORD_ID_KEY,
    STORED_RECORD_KEY,
    ALLOWED_VALUES_KEY,
    MISSING_REQUIRED,
    NOT_ALLOWED,
    REJECTED_FIELD_KEY,
    REJECTION_REASON_KEY,
)
from app.factory.build.supply_chain import (
    PYTHON_312_SLIM_FROM,
    SupplyChainError,
    assert_generated_dockerfile,
    emit_supply_chain_artifacts,
    redact_unpinned_images,
)

from app.factory.build.vendored_integrity import LOCK_KEY as _INTEGRITY_KEY
from app.factory.build.vendored_integrity import lock_record as _integrity_record
from app.factory.build.model_call import MODEL_CALL_STATE as _MODEL_CALL_STATE

#: The worker info fields the WRITER relay copies onto its ledger NOTE. The
#: model-call lifecycle (``model_call_state`` OPEN/CLOSED) is one of them:
#: the relay used to copy only {model_call, deadline_s, provider}, so the
#: worker's CLOSED never reached the ledger, every codewhale call read as
#: open forever, and at the build wall the inspector answered "await_cli"
#: for a CLI that had exited minutes earlier (live 2026-10-06, 01a1eed7:
#: "wall-clock budget of 2700s spent before TESTER completed; inspect wall:
#: FACTORY_CODE_CLI in-flight").
RELAYED_CALL_FIELDS: Tuple[str, ...] = (
    "model_call",
    "deadline_s",
    "provider",
    _MODEL_CALL_STATE,
)


def relayed_call_fields(info: Any) -> Dict[str, Any]:
    """The model-call fields of one worker progress record, as the ledger keeps them."""
    if not isinstance(info, dict):
        return {}
    return {key: info[key] for key in RELAYED_CALL_FIELDS if info.get(key) is not None}


def _from_facade(name: str, fallback):
    """Resolve a helper via the ``roles.py`` facade.

    Tests monkeypatch ``app.factory.build.roles._block_source_dir``. Looking
    the name up here keeps that contract after the split.
    """
    import sys

    facade = sys.modules.get("app.factory.build.roles")
    if facade is None:
        return fallback
    return getattr(facade, name, fallback)


# -- COLLECTOR -----------------------------------------------------------


def run_collector(ctx: RoleContext) -> RoleResult:
    """Binding surveyor: resolve planned capabilities into parts, name every gap.

    Read-only by contract: this writes nothing. A capability the plan could
    not back with a block is reported as a gap so the WRITER knows it must
    author that logic -- silently dropping it is the failure mode the
    COLLECTOR gate exists to catch. The coding agent may review bindings;
    it may not change them. Published on the product as ``GET /v1/catalog``.
    """
    resolved: List[str] = []
    gaps: List[str] = []
    for cap in ctx.plan.capabilities:
        if cap.block_ids:
            resolved.extend(cap.block_ids)
        else:
            gaps.append(cap.capability_id)

    if not resolved and not gaps:
        raise RoleError("plan contains no capabilities — nothing to build")

    # De-duplicate while keeping a stable order, so the manifest and every
    # downstream hash are reproducible.
    seen: Dict[str, None] = {}
    for bid in resolved:
        seen.setdefault(bid, None)

    reviews: List[Dict[str, Any]] = []
    review_model = ""
    try:
        reviews, review_model = _collector_agent_review(ctx)
    except Exception as exc:  # noqa: BLE001 -- review is advisory
        ctx.state.setdefault("coder_failures", {})["COLLECTOR.review"] = str(exc)
        ctx.note(f"coding agent skipped collector review: {exc}", stage="collector")

    mismatch_n = sum(1 for r in reviews if r.get("verdict") == "mismatch")
    detail = f"{len(seen)} block(s) resolved, {len(gaps)} gap(s)"
    if reviews:
        detail += (
            f"; coding agent reviewed {len(reviews)} binding(s)"
            f" ({mismatch_n} mismatch(es))"
        )
        ctx.note(
            f"coding agent reviewed collector bindings ({mismatch_n} mismatch(es))",
            stage="collector",
            reviews=len(reviews),
            mismatches=mismatch_n,
        )

    intake: Dict[str, Any] = {}
    try:
        from app.factory.build.brief_compiler import synthesize_domain_pack
        from app.factory.build.intake_blueprint import intake_from_product_blueprint

        pack = synthesize_domain_pack(ctx.blueprint, ctx.plan)
        turns = list((ctx.state or {}).get("chat_turns") or [])
        intake = intake_from_product_blueprint(
            ctx.blueprint, plan=ctx.plan, chat_turns=turns, domain_pack=pack
        )
        ctx.state["intake_blueprint"] = intake
        detail += "; intake_blueprint.v1 emitted"
    except Exception as exc:  # noqa: BLE001 — collector still reports gaps
        ctx.state.setdefault("coder_failures", {})["COLLECTOR.intake"] = str(exc)
        ctx.note(f"intake blueprint failed: {exc}", stage="collector")

    return RoleResult(
        ok=True,
        detail=detail,
        gaps=tuple(gaps),
        vendored_blocks=tuple(seen),
        notes={
            "resolved_blocks": list(seen),
            "gaps": gaps,
            "agent_binding_reviews": reviews,
            "agent_binding_model": review_model,
            "intake_blueprint": intake,
        },
    )


def _collector_block_meta(ctx: RoleContext, block_id: str) -> Dict[str, Any]:
    """Harvest a block.json the COLLECTOR can read (no vendor/ yet)."""
    meta: Dict[str, Any] = {"block_id": block_id}
    source = _from_facade("_block_source_dir", _block_source_dir)(
        block_id, ctx.blocks_root
    )
    if source is None:
        return meta
    path = source / "block.json"
    if not path.is_file():
        return meta
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return meta
    if isinstance(data, dict):
        if data.get("description"):
            meta["description"] = str(data["description"])[:400]
        actions = []
        for item in data.get("inputs") or []:
            if isinstance(item, dict) and item.get("name") == "action":
                if item.get("options"):
                    actions = list(item["options"])
                elif item.get("default") is not None:
                    actions = [item["default"]]
        if actions:
            meta["actions"] = actions
    return meta


def _collector_agent_review(ctx: RoleContext) -> tuple:
    """Ask the coding agent to judge bindings. Empty if unavailable."""
    from app.factory.build.coder_session import deepseek_cli_ready
    from app.factory.coder import coder_enabled, review_capability_bindings

    if not coder_enabled():
        return [], ""
    if deepseek_cli_ready():
        # C-BRIEF owns coding. An in-process OpenRouter collector review
        # on a leftover ~47s wall expires the run before WRITER can start
        # FACTORY_CODE_CLI (sess_b9fbae7 FAILED_BUDGET_SPENT).
        ctx.note(
            "skipping factory-LLM collector review — FACTORY_CODE_CLI "
            "(DeepSeek) is ready",
            stage="collector",
            source="FACTORY_CODE_CLI",
        )
        return [], ""
    if _budget_too_low(ctx, "collector review"):
        return [], ""
    caps = []
    for cap in ctx.plan.capabilities:
        bids = list(cap.block_ids or [])
        caps.append(
            {
                "id": cap.capability_id,
                "block_ids": bids,
                "description": getattr(cap, "notes", "") or cap.capability_id,
                "contracts": [_collector_block_meta(ctx, b) for b in bids],
            }
        )
    result = review_capability_bindings(
        product_name=getattr(ctx.blueprint, "product_name", "platform"),
        vertical=getattr(ctx.blueprint, "vertical", "product"),
        capabilities=caps,
    )
    return list(result.get("reviews") or []), str(result.get("model") or "")


# -- CLONER --------------------------------------------------------------


def _block_source_dir(block_id: str, blocks_root: Optional[Path]) -> Optional[Path]:
    """Real Store checkout first, factory vendor mirror second.

    A block dir counts as store-sourced only when the pinned store sha
    actually commits it — untracked working-tree leftovers in the local
    checkout must not shadow the vendor mirror (Phase 2 §0.2 lock
    determinism).
    """
    if blocks_root:
        from app.factory.blocks_lock import _block_dir_in_store

        candidate = _block_dir_in_store(Path(blocks_root), block_id)
        if candidate is not None:
            return candidate
    # No Factory-local fallback: the Store is the only source of blocks. A
    # miss is reported by the CLONER as "no source found -- do not invent".
    return None


def _content_digest(source: Path) -> str:
    """Stable digest of a block's files, for a source with no commit.

    The vendor mirror is not a git repository, so a mirror-sourced clone has
    no revision to pin. Hashing the content gives the registrar something that
    still changes when the block does, which is what staleness detection
    actually needs.
    """
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(source).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()[:40]


def _pin_source(source: Path, blocks_root: Optional[Path]) -> tuple:
    """(origin, revision) for one vendored block.

    Clones used to be recorded as "unpinned", which left the Store registrar
    unable to answer its own question -- once a platform ships, the commit a
    block came from is unrecoverable, because the vendored files look
    identical whether they are current or eight months behind.
    """
    from app.factory.generator import git_head

    if blocks_root and str(source).startswith(str(Path(blocks_root).resolve())):
        revision = git_head(Path(blocks_root))
        # A checkout that is not a git repo answers "unknown"; fall back to
        # content rather than recording a revision that means nothing.
        if revision and revision != "unknown":
            return "cerebrum-blocks", revision
        return "cerebrum-blocks", _content_digest(source)
    return "factory-vendor-mirror", _content_digest(source)


# -- runtime slice (defect 1g) -------------------------------------------
#
# Real Store blocks are shims: block.py does ``from app.blocks import
# get_block`` and the logic lives in the Store's ``app/blocks/<name>.py``,
# resting on ``app/core``. The first build against the real Store failed its
# own offline gate on every block with ``No module named 'app'`` because only
# the shim was vendored. The name ``app`` cannot be vendored as-is -- the
# delivered platform's own package is ``app`` -- so the slice lives under
# ``vendor/cerebrum/`` and every ``app.blocks``/``app.core`` import in copied
# source is mechanically rewritten to the vendored name.



#: The Store's runtime packages, as dotted-name parts.
_STORE_CORE_PKG = ("app", "core")
_STORE_BLOCKS_PKG = ("app", "blocks")


def _dotted_mentions(text: str) -> Tuple[List[str], List[str]]:
    """Dotted names in Python source: ``(code, prose)``.

    ``code`` are names the language itself references (``a.b.c`` built from
    NAME and ``.`` tokens, imports included); ``prose`` are dotted words
    inside comments and string literals. Read by the tokenizer, never by a
    pattern, so a word in a sentence is never mistaken for an import."""
    import io
    import tokenize

    code: List[str] = []
    prose: List[str] = []
    chain: List[str] = []
    state = {"expect": True}

    def _flush():
        if chain:
            code.append(".".join(chain))
        chain.clear()

    try:
        for tok in tokenize.generate_tokens(io.StringIO(text or "").readline):
            if tok.type == tokenize.NAME:
                if not state["expect"]:
                    _flush()
                chain.append(tok.string)
                state["expect"] = False
                continue
            if tok.type == tokenize.OP and tok.string == "." and chain and not state["expect"]:
                state["expect"] = True
                continue
            _flush()
            state["expect"] = True
            if tok.type in (tokenize.STRING, tokenize.COMMENT):
                word: List[str] = []
                for ch in tok.string + " ":
                    if ch.isalnum() or ch in "._":
                        word.append(ch)
                        continue
                    if word:
                        prose.append("".join(word).strip("."))
                    word = []
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    _flush()
    return code, prose


def _submodules(names: Iterable[str], package: Tuple[str, ...]) -> List[str]:
    """The first module under ``package`` each dotted name reaches."""
    out: List[str] = []
    for name in names:
        parts = [p for p in name.split(".") if p]
        if len(parts) > len(package) and tuple(parts[: len(package)]) == package:
            out.append(parts[len(package)])
    return out


def _imported_names(text: str, package: Tuple[str, ...]) -> List[str]:
    """Names bound by ``from <package> import a, b as c`` (the imported name,
    not its alias)."""
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return []
    want = ".".join(package)
    return [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == want
        for alias in node.names
    ]


def _relative_import_modules(text: str) -> List[str]:
    """``from .x import ...`` -> ``x`` (one level, a sibling module)."""
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return []
    return [
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
    ]


def _parsed(text: str):
    """Syntax tree of source; an indented fragment is read dedented."""
    import textwrap

    for src in (text or "", textwrap.dedent(text or "")):
        try:
            return ast.parse(src)
        except SyntaxError:
            continue
    return None


def _assigned_from_call(text: str, func: str) -> set:
    """Names assigned the result of calling ``func`` (``x = func(...)``)."""
    tree = _parsed(text)
    if tree is None:
        return set()
    out = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == func):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return out


def _defines_function(text: str, name: str) -> bool:
    tree = _parsed(text)
    return tree is not None and any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        for n in ast.walk(tree)
    )



def _calls_with_name_arg(text: str, func: str, arg: str) -> bool:
    """``func(arg)`` is called somewhere in the source."""
    tree = _parsed(text)
    return tree is not None and any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func
        and len(n.args) == 1 and isinstance(n.args[0], ast.Name) and n.args[0].id == arg
        for n in ast.walk(tree)
    )


def _callee(node: ast.Call) -> str:
    parts: List[str] = []
    func = node.func
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


#: The persistence calls a handler may not make itself (Phase 2 §0.2:
#: persistence is route-scoped). Callee names of the Factory's own API.
_DIRECT_PERSIST_CALLEES = frozenset({"_persist_record", "store.save", "save"})


def _persists_directly(text: str) -> bool:
    """The handler calls the persistence API itself."""
    tree = _parsed(text)
    return tree is not None and any(
        isinstance(n, ast.Call) and _callee(n) in _DIRECT_PERSIST_CALLEES
        for n in ast.walk(tree)
    )


def _harvest_block_source(source: str) -> Dict[str, List[str]]:
    """What a vendored Store block's source declares, read from its tree:

    * ``required``: the string list passed as ``required_fields=`` to the
      ``Schema(...)`` bound to ``input_schema``;
    * ``errors``: string values the source puts under an ``"error"`` key
      (literals and placeholder-free f-strings, 4-120 chars);
    * ``keys_read``: identifier keys read with ``.get("<key>"...)``.
    """
    out: Dict[str, List[str]] = {"required": [], "errors": [], "keys_read": []}
    tree = _parsed(source)
    if tree is None:
        return out
    for node in ast.walk(tree):
        value = None
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any((isinstance(tg, ast.Name) and tg.id == "input_schema")
                   or (isinstance(tg, ast.Attribute) and tg.attr == "input_schema")
                   for tg in targets):
                value = node.value
        elif isinstance(node, ast.keyword) and node.arg == "input_schema":
            value = node.value
        if isinstance(value, ast.Call) and _callee(value).split(".")[-1] == "Schema":
            for kw in value.keywords:
                if kw.arg == "required_fields" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    names = [e.value for e in kw.value.elts
                             if isinstance(e, ast.Constant) and isinstance(e.value, str)
                             and e.value.isidentifier()]
                    if names and not out["required"]:
                        out["required"] = names
        if isinstance(node, ast.Dict):
            for key, val in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "error"):
                    continue
                text = None
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    text = val.value
                elif isinstance(val, ast.JoinedStr) and all(
                    isinstance(v, ast.Constant) for v in val.values
                ):
                    text = "".join(str(v.value) for v in val.values)
                if text is not None and 4 <= len(text) <= 120 and not set(text) & set("{}\"'"):
                    out["errors"].append(text)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args
                and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                and node.args[0].value.isidentifier() and 2 <= len(node.args[0].value) <= 30):
            out["keys_read"].append(node.args[0].value)
    return out


def _code_only(text: str) -> str:
    """``text`` with comments and string literals blanked out.

    The slice scan below looks for ``app.core.X`` / ``app.blocks.X`` with a
    regex, and a regex cannot tell an import from prose. Live:
    ``app/blocks/core/action_contract/registry.py`` documents an OPTIONAL
    plugin namespace in a comment and names it in a constant --

        # ... discovered under this package as ``app.blocks.domains.<kit>``
        DOMAINS_PACKAGE = "app.blocks.domains"

    -- and the scan demanded ``app/blocks/domains/`` exist, failing the clone
    of medical_ehr_connector over a package that is discovered with pkgutil
    when present and simply absent otherwise.

    Blanking rather than deleting keeps every line and column where it was, so
    any message that quotes a line number still points at the right line.
    """
    try:
        import io
        import tokenize

        pieces: List[str] = []
        prev_end = (1, 0)
        lines = text.splitlines(keepends=True)

        def _line(row: int) -> str:
            # tokenize emits a trailing NEWLINE/ENDMARKER on the row AFTER the
            # last line of a file, so row-1 can run off the end.
            return lines[row - 1] if 1 <= row <= len(lines) else ""

        def _slice(start, end) -> str:
            (sr, sc), (er, ec) = start, end
            if sr == er:
                return _line(sr)[sc:ec]
            out = [_line(sr)[sc:]]
            out.extend(_line(r + 1) for r in range(sr, er - 1))
            out.append(_line(er)[:ec])
            return "".join(out)

        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.start > prev_end:
                pieces.append(_slice(prev_end, tok.start))
            raw = _slice(tok.start, tok.end)
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                # Keep newlines so line numbering survives a multi-line
                # docstring; blank everything else.
                pieces.append("".join(c if c == "\n" else " " for c in raw))
            else:
                pieces.append(raw)
            prev_end = tok.end
        return "".join(pieces)
    except Exception:
        # A file the tokenizer cannot read is not a reason to skip the scan;
        # fall back to the raw text, which is what this always did.
        return text


def _rewrite_runtime_imports(text: str) -> str:
    text = re.sub(r"\bapp\.blocks\b", "vendor.cerebrum.blocks", text)
    return re.sub(r"\bapp\.core\b", "vendor.cerebrum.core", text)


def _rewrite_shim_constructors(text: str) -> str:
    """Rewrite Store block construction that omits HAL/config.

    Kit shims used ``block_cls()``. Live sess_f1fe691 (VetCare Hub) PRODUCT
    then failed inside the Store *workflow* runtime, not the kit shim:

        workflow: step_0 (database): DatabaseBlock.__init__() missing 2
        required positional arguments: 'hal_block' and 'config'

    That module constructs children via ``get_block(...)()``, a name
    assigned from ``get_block``, or ``DatabaseBlock()`` after the
    Store-host ``_create_block_instance`` import fails. Only shims were
    rewritten; the runtime slice was not.
    """
    if not text:
        return text
    original = text
    text = re.sub(
        r"get_block\(([^()\n]+)\)\s*\(\s*\)",
        r"_instantiate_store_block(get_block(\1))",
        text,
    )
    assigned = _assigned_from_call(text, "get_block") - {"_instantiate_store_block", "get_block"}
    for name in assigned:
        text = re.sub(
            rf"\b{re.escape(name)}\s*\(\s*\)",
            f"_instantiate_store_block({name})",
            text,
        )
    text = re.sub(
        r"\bblock_cls\s*\(\s*\)",
        "_instantiate_store_block(block_cls)",
        text,
    )
    text = re.sub(
        r"\b([A-Z]\w*Block)\s*\(\s*\)",
        r"_instantiate_store_block(\1)",
        text,
    )
    if text == original:
        return text
    if not _defines_function(text, "_instantiate_store_block"):
        from app.factory.build.offline_adapters import insert_after_future_imports

        text = insert_after_future_imports(
            text, _INSTANTIATE_HELPER.lstrip("\n") + "\n"
        )
    return text


#: A vendoring transform turned Store source that parsed into source that
#: does not. Named at CLONER so the build stops where the damage happened.
VENDOR_TRANSFORM_BROKE_SOURCE = "vendor_transform_broke_source"


def _parses(text: str) -> bool:
    try:
        compile(text.lstrip("\ufeff"), "<vendored>", "exec")
    except SyntaxError:
        return False
    return True


def _prepare_cloned_python(text: str, *, label: str = "vendored module") -> str:
    """HAL DI + result-key access on every vendored .py (shim or runtime).

    These are regex rewrites of someone else's source, written against the
    Store's shape at the time. When the Store changes shape they can break
    it silently: FinOps (sess_065fc3eac75c4f62) shipped a notification.py
    that did not parse and still went 13/13, because nothing exercised the
    block. So the output must parse whenever the input did -- otherwise
    CLONER refuses by name instead of shipping the damage.
    """
    from app.factory.build.offline_adapters import (
        emit_result_key_access,
        emit_store_host_di,
    )

    # A leading BOM means nothing to Python, but the helper injection below
    # PREPENDS text and would bury it mid-file, where it is a SyntaxError
    # (caught by this guard on its first run). Drop it before rewriting.
    text = text.lstrip("\ufeff")
    original = text
    text = _rewrite_shim_constructors(text)
    text = emit_result_key_access(text)
    text = emit_store_host_di(text)
    text = emit_instantiate_ready(text)
    if not _parses(text) and _parses(original):
        try:
            compile(text.lstrip("\ufeff"), label, "exec")
        except SyntaxError as exc:
            where = f"line {exc.lineno}: {exc.msg}"
        raise RoleError(
            f"{VENDOR_TRANSFORM_BROKE_SOURCE}: {label} parsed as the Store "
            f"published it and does not after the factory's vendoring "
            f"rewrites ({where})",
            reason=VENDOR_TRANSFORM_BROKE_SOURCE,
            location="CLONER",
        )
    return text


def _store_block_defs(blocks_root: Path) -> Dict[str, tuple]:
    """block id -> (module name, class name), parsed from the Store registry's
    literal defs. Parsed, not imported: importing the Store's ``app`` package
    into the factory process would drag in its whole runtime."""
    init = blocks_root / "app" / "blocks" / "__init__.py"
    if not init.is_file():
        return {}
    text = init.read_text(encoding="utf-8")
    return {
        m.group("name"): (m.group("mod"), m.group("cls"))
        for m in _BLOCK_DEF_RE.finditer(text)
    }


def _candidate_store_ids(block_id: str) -> tuple:
    """Kit-shelf ids and the Store's ``_v2`` runtime names.

    Dual-registration uses ``formula_executor``; the Store registry ships
    ``formula_executor_v2``. A live tasting-room Approve died in CLONER
    because the shim was found and the kit id was not in ``_EXTENDED_BLOCK_DEFS``.
    """
    ids = [block_id]
    if block_id.endswith("_v2") and len(block_id) > 3:
        ids.append(block_id[:-3])
    else:
        ids.append(f"{block_id}_v2")
    seen: Dict[str, None] = {}
    for item in ids:
        if item:
            seen.setdefault(item, None)
    return tuple(seen)


def _class_name_from_block_module(path: Path) -> Optional[str]:
    """The block class a Store module defines, read from its syntax tree.

    A Store block declares its own id (``name = "<id>"`` in its class body),
    so the class that declares this module's id is the block. Failing that,
    the class implementing the Store block interface (``process``, the method
    the Store calls), else the first class. Never chosen by how a class is
    NAMED.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError):
        return None
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    if not classes:
        return None
    module_id = path.stem

    def _declares_id(cls: ast.ClassDef) -> bool:
        for stmt in cls.body:
            targets = stmt.targets if isinstance(stmt, ast.Assign) else (
                [stmt.target] if isinstance(stmt, ast.AnnAssign) else []
            )
            value = getattr(stmt, "value", None)
            if (
                any(isinstance(t, ast.Name) and t.id == "name" for t in targets)
                and isinstance(value, ast.Constant)
                and value.value == module_id
            ):
                return True
        return False

    def _implements_process(cls: ast.ClassDef) -> bool:
        return any(
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == "process"
            for stmt in cls.body
        )

    for predicate in (_declares_id, _implements_process):
        for cls in classes:
            if predicate(cls):
                return cls.name
    return classes[0].name


def _resolve_store_def(
    block_id: str, defs: Dict[str, tuple], blocks_root: Path
) -> Optional[tuple]:
    """``(store_key, (module, class))`` for a kit-shelf id, or None."""
    for key in _candidate_store_ids(block_id):
        if key in defs:
            return key, defs[key]
    blocks_dir = Path(blocks_root) / "app" / "blocks"
    for key in _candidate_store_ids(block_id):
        path = blocks_dir / f"{key}.py"
        if not path.is_file():
            continue
        cls = _class_name_from_block_module(path)
        if cls:
            return key, (key, cls)
    return None


def _runtime_defs_for_blocks(
    block_ids: Sequence[str],
    defs: Dict[str, tuple],
    blocks_root: Path,
    *,
    shim_texts: Optional[Dict[str, str]] = None,
) -> Dict[str, tuple]:
    """Vendored registry entries keyed by every name a shim may ``get_block``.

    The Store's native id (often ``*_v2``) and the kit-shelf id both point at
    the same module so ``get_block("formula_executor")`` resolves after clone.
    """
    registry: Dict[str, tuple] = {}
    init = Path(blocks_root) / "app" / "blocks" / "__init__.py"
    for bid in block_ids:
        resolved = _resolve_store_def(bid, defs, blocks_root)
        if resolved is None:
            tried = ", ".join(
                key for key in _candidate_store_ids(bid) if key != bid
            )
            extra = f" (also tried {tried})" if tried else ""
            raise RoleError(
                f"{bid}: shim imports the Store runtime but the Store registry "
                f"({init}) has no entry for it{extra}"
            )
        store_key, pair = resolved
        registry[bid] = pair
        registry[store_key] = pair
        if shim_texts and bid in shim_texts:
            for name in _GET_BLOCK_RE.findall(shim_texts[bid]):
                registry.setdefault(name, pair)
    return registry


def _shim_needs_runtime(source: Path) -> bool:
    return any(
        _STORE_RUNTIME_RE.search(p.read_text(encoding="utf-8", errors="replace"))
        for p in source.rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _package_source_blob(pkg_dir: Path) -> str:
    parts: List[str] = []
    for py in sorted(pkg_dir.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        parts.append(py.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _shadow_stem(mod: str) -> Optional[str]:
    """``document_engine_block`` → ``document_engine``; else None."""
    suffix = "_block"
    if not mod.endswith(suffix):
        return None
    stem = mod[: -len(suffix)]
    return stem or None


def _package_declares_sibling_wrapper(pkg_dir: Path, stem: str, synthetic_mod: Optional[str] = None) -> bool:
    """True when a shadowing package load-names the sibling ``{stem}.py``.

    The live Cerebrum-Blocks pattern (``document_engine/`` +
    ``document_engine.py``) sets ``_BLOCK_MODULE_NAME =
    "app.blocks.document_engine_block"`` and importlib-loads
    ``../document_engine.py``. Both tokens must be present so we do not
    invent a module from a coincidental filename mention.
    """
    if not (pkg_dir / "__init__.py").is_file():
        return False
    blob = _package_source_blob(pkg_dir)
    if f"{stem}.py" not in blob:
        return False
    if synthetic_mod is not None and f"app.blocks.{synthetic_mod}" not in blob:
        return False
    return True


def _resolve_shadowed_wrapper(blocks_dir: Path, mod: str) -> Optional[Path]:
    """Sibling ``{stem}.py`` a package importlib-loads as ``app.blocks.{mod}``.

    Live sess_000f9a85d339422f: CLONER scanned the ``document_engine/``
    package, regex-picked ``document_engine_block``, and failed closed
    because that path is not on disk. The wrapper lives at
    ``app/blocks/document_engine.py``. Map the synthetic name only when
    the package declares it and the sibling file exists.
    """
    if (blocks_dir / f"{mod}.py").is_file():
        return None
    if (blocks_dir / mod / "__init__.py").is_file():
        return None
    stem = _shadow_stem(mod)
    if stem is None:
        return None
    wrapper = blocks_dir / f"{stem}.py"
    pkg = blocks_dir / stem
    if not wrapper.is_file():
        return None
    if not _package_declares_sibling_wrapper(pkg, stem, mod):
        return None
    return wrapper


def _closure_over_runtime(
    blocks_root: Path, block_ids: Sequence[str], defs: Dict[str, tuple]
) -> tuple:
    """(block modules, core modules) the vendored blocks transitively need.

    A module reached by real code must exist; a reference the closure cannot
    resolve fails the clone here, with the module named, rather than passing
    the clone and surfacing as a ModuleNotFoundError on the customer's
    machine.

    HARD vs SOFT references
    -----------------------
    The scan is textual, so it cannot simply trust every ``app.core.X`` it
    sees -- nor simply ignore the ones inside strings. Both kinds are real in
    the live Store:

        _BLOCK_MODULE_NAME = "app.blocks.document_engine_block"  # load-bearing
        DOMAINS_PACKAGE     = "app.blocks.domains"               # optional

    The first is a synthetic name importlib-loads a sibling wrapper under; the
    second is a pkgutil discovery namespace that is simply absent in this
    checkout. Nothing about the strings tells them apart -- what tells them
    apart is whether anything is there.

    So a name reached by real code is HARD: it must resolve or the clone
    fails. A name that appears only in a comment or a string literal is SOFT:
    it is followed when it resolves and skipped when it does not. A SOFT
    reference that is absent was refusing clones over prose; a HARD one that
    is absent is the app.core.redline case and must still refuse.
    """
    blocks_dir = blocks_root / "app" / "blocks"
    core_dir = blocks_root / "app" / "core"
    class_to_name = {cls: name for name, (_, cls) in defs.items()}

    block_mods: Dict[str, None] = {}
    core_mods: Dict[str, None] = {}
    hard_blocks: set = set()
    hard_cores: set = set()
    todo: List[str] = []

    for bid in block_ids:
        resolved = _resolve_store_def(bid, defs, blocks_root)
        if resolved is None:
            tried = ", ".join(
                key for key in _candidate_store_ids(bid) if key != bid
            )
            extra = f" (also tried {tried})" if tried else ""
            raise RoleError(
                f"{bid}: shim imports the Store runtime but the Store registry "
                f"({blocks_dir / '__init__.py'}) has no entry for it{extra}"
            )
        _store_key, (mod, _cls) = resolved
        # A block the build asked for is hard by definition.
        hard_blocks.add(mod)
        todo.append(mod)

    while todo:
        mod = todo.pop()
        if mod in block_mods:
            continue
        if "." in mod:
            raise RoleError(
                f"runtime slice cannot vendor app.blocks.{mod}: dotted "
                "registry module names are not supported by the slice "
                "vendorer yet (package *contents* are copied separately)"
            )
        path = blocks_dir / f"{mod}.py"
        pkg_init = blocks_dir / mod / "__init__.py"
        shadow = _resolve_shadowed_wrapper(blocks_dir, mod)
        sources: List[str] = []
        if pkg_init.is_file():
            for py in sorted((blocks_dir / mod).rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                sources.append(py.read_text(encoding="utf-8", errors="replace"))
        elif path.is_file():
            sources.append(path.read_text(encoding="utf-8", errors="replace"))
        elif shadow is not None:
            sources.append(shadow.read_text(encoding="utf-8", errors="replace"))
        elif mod not in hard_blocks:
            # Named only in a comment or a string and not on disk -- an
            # optional discovery namespace, not a dependency. See the docstring.
            continue
        else:
            raise RoleError(
                f"runtime slice needs app/blocks/{mod}.py or "
                f"app/blocks/{mod}/__init__.py which does not exist in "
                "the Store checkout"
            )
        block_mods[mod] = None
        for raw_text in sources:
            # Reached by real code == HARD; named only in a comment or string
            # literal == SOFT. See the docstring for why both are followed and
            # only the first is fatal when absent.
            code_refs, prose_refs = _dotted_mentions(raw_text)
            code_cores = _submodules(code_refs, _STORE_CORE_PKG)
            code_blocks = _submodules(code_refs, _STORE_BLOCKS_PKG)
            hard_cores.update(code_cores)
            hard_blocks.update(code_blocks)
            all_cores = code_cores + _submodules(prose_refs, _STORE_CORE_PKG)
            all_blocks = code_blocks + _submodules(prose_refs, _STORE_BLOCKS_PKG)
            core_mods.update(dict.fromkeys(all_cores))
            todo.extend(all_blocks)
            if True:
                for name in _imported_names(raw_text, _STORE_BLOCKS_PKG):
                    # ``import X as Y`` binds Y; the thing to resolve is X --
                    # the AST alias carries X.
                    if not name or name in _REGISTRY_API_NAMES:
                        continue
                    # ``from app.blocks import _knowledge`` imports a SUBMODULE,
                    # not a block class -- app/blocks/_knowledge.py is a shared
                    # helper no registry entry points at. Vendor it as a module;
                    # only fall through to the class lookup when no such file
                    # exists.
                    if (blocks_dir / f"{name}.py").is_file() or (
                        blocks_dir / name / "__init__.py"
                    ).is_file():
                        hard_blocks.add(name)
                        todo.append(name)
                        continue
                    ref = class_to_name.get(name)
                    if ref is None:
                        raise RoleError(
                            f"app/blocks/{mod} imports {name} from app.blocks "
                            "but the Store registry maps no block to that "
                            f"class and app/blocks/{name}.py does not exist"
                        )
                    hard_blocks.add(defs[ref][0])
                    todo.append(defs[ref][0])

    seen_core: Dict[str, None] = {}
    core_todo = list(core_mods)
    while core_todo:
        name = core_todo.pop()
        if name in seen_core:
            continue
        # A core dependency may be a flat module OR a package -- app.core.rag
        # is app/core/rag/{__init__,retriever}.py, and demanding rag.py failed
        # the clone of every block that touches retrieval. The block loop above
        # has always accepted both; this loop did not, and one resolver
        # disagreeing with the other about what a module is IS the defect.
        pkg_dir = core_dir / name
        pkg_init = pkg_dir / "__init__.py"
        path = core_dir / f"{name}.py"
        is_pkg = pkg_init.is_file()
        if is_pkg:
            sources = [
                py.read_text(encoding="utf-8", errors="replace")
                for py in sorted(pkg_dir.rglob("*.py"))
                if "__pycache__" not in py.parts
            ]
        elif path.is_file():
            sources = [path.read_text(encoding="utf-8", errors="replace")]
        elif name not in hard_cores:
            continue  # SOFT and absent -- see the docstring.
        else:
            raise RoleError(
                f"runtime slice needs app/core/{name}.py or "
                f"app/core/{name}/__init__.py which does not exist in the "
                "Store checkout"
            )
        seen_core[name] = None
        for raw_text in sources:
            code_refs, prose_refs = _dotted_mentions(raw_text)
            hard_cores.update(_submodules(code_refs, _STORE_CORE_PKG))
            core_todo.extend(
                _submodules(code_refs, _STORE_CORE_PKG) + _submodules(prose_refs, _STORE_CORE_PKG)
            )
            # ``from .x import`` inside a PACKAGE names a sibling within that
            # package, which the package copy already carries. Following it as
            # a top-level app/core/x.py would demand a file that need not
            # exist. Only a flat module's relative import means app.core.x.
            if not is_pkg:
                relative = _relative_import_modules(raw_text)
                hard_cores.update(relative)  # a real import statement
                core_todo.extend(relative)

    return tuple(block_mods), tuple(seen_core)


def _check_foreign_app_imports(rel: str, text: str) -> List[str]:
    """Fail on a module-level foreign ``app`` import; return the lazy ones.

    A top-level import executes when the block loads, so the block cannot
    import offline at all -- the clone must fail with the module named. An
    indented one only breaks the single feature that runs it; it is returned
    so the lockfile records the limitation instead of shipping a surprise.
    """
    leftover = _STORE_FOREIGN_TOP_RE.search(text)
    if leftover:
        raise RoleError(
            f"{rel} imports the Store's app package outside blocks/core at "
            f"module level ({leftover.group(0).strip()!r}); the factory "
            "cannot vendor that"
        )
    return [
        f"{rel}: {module}"
        for module in _STORE_FOREIGN_LAZY_RE.findall(text)
    ]


def _render_vendored_registry(entries: Dict[str, tuple]) -> str:
    defs = "\n".join(
        f'    "{name}": ("vendor.cerebrum.blocks.{mod}", "{cls}"),'
        for name, (mod, cls) in sorted(entries.items())
    )
    return f'''"""Vendored Cerebrum block runtime registry.

Generated by the factory CLONER. Lists ONLY the blocks vendored into this
platform -- an entry pointing at a module that is not on disk would be a
latent ModuleNotFoundError in the customer's environment.
"""

import importlib

_BLOCK_DEFS = {{
{defs}
}}


def get_block(name):
    try:
        module_path, class_name = _BLOCK_DEFS[name]
    except KeyError:
        raise KeyError(f"block {{name!r}} is not vendored in this platform") from None
    return getattr(importlib.import_module(module_path), class_name)


class _LazyRegistry:
    """Mapping-shaped view over the vendored defs. Blocks use it for
    cross-block dispatch (``"x" in BLOCK_REGISTRY``, ``BLOCK_REGISTRY[x]``)."""

    def __contains__(self, name):
        return name in _BLOCK_DEFS

    def __getitem__(self, name):
        return get_block(name)

    def get(self, name, default=None):
        try:
            return get_block(name)
        except KeyError:
            return default

    def keys(self):
        return _BLOCK_DEFS.keys()


BLOCK_REGISTRY = _LazyRegistry()

_CLASS_TO_NAME = {{cls: name for name, (_, cls) in _BLOCK_DEFS.items()}}


def __getattr__(name):
    block = _CLASS_TO_NAME.get(name)
    if block is None:
        raise AttributeError(name)
    return get_block(block)
'''


def _vendor_runtime_slice(
    ctx: RoleContext, runtime_block_ids: Sequence[str]
) -> tuple:
    """Vendor the Store runtime the given blocks stand on.

    Returns ``(files, lazy_foreign_imports)`` -- the workspace-relative paths
    written and any function-local imports of unvendorable Store packages,
    both for the lockfile.
    """
    blocks_root = Path(ctx.blocks_root)
    defs = _store_block_defs(blocks_root)
    block_mods, core_mods = _closure_over_runtime(blocks_root, runtime_block_ids, defs)

    written: List[str] = []
    lazy_foreign: List[str] = []
    # Every vendored file's final text, keyed by workspace path. The
    # dependency obligation is read off THIS -- the bytes that ship -- not
    # off the Store checkout, which the rewrites have already diverged from.
    shipped: Dict[str, str] = {}

    def _write(rel: Path, text: str) -> None:
        lazy_foreign.extend(_check_foreign_app_imports(rel.as_posix(), text))
        ctx.workspace.write_text(rel, text)
        written.append(rel.as_posix())
        shipped[rel.as_posix()] = text

    def _emit_store_module(mod: str, source: str) -> str:
        return _prepare_cloned_python(
            emit_runtime_module(mod, _rewrite_runtime_imports(source)),
            label=f"vendor/cerebrum/blocks/{mod.rsplit('.', 1)[-1]}.py",
        )

    base = Path("vendor") / "cerebrum"
    _write(
        base / "__init__.py",
        '"""Cerebrum Store runtime, vendored at build time (see blocks.lock.json)."""\n',
    )
    _write(
        base / "core" / "__init__.py",
        '"""Vendored slice of the Store\'s app.core. Deliberately minimal."""\n',
    )
    core_src_dir = blocks_root / "app" / "core"
    for name in sorted(core_mods):
        # Packages as well as flat modules -- see _closure_over_runtime. A
        # package copied as "<name>.py" would ship an empty file and turn a
        # resolved slice back into a ModuleNotFoundError on the customer's box.
        pkg_dir = core_src_dir / name
        if (pkg_dir / "__init__.py").is_file():
            for py in sorted(pkg_dir.rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                source = py.read_text(encoding="utf-8", errors="replace")
                _write(
                    base / "core" / name / py.relative_to(pkg_dir),
                    _rewrite_runtime_imports(source),
                )
            continue
        source = (core_src_dir / f"{name}.py").read_text(
            encoding="utf-8", errors="replace"
        )
        _write(base / "core" / f"{name}.py", _rewrite_runtime_imports(source))

    shim_texts: Dict[str, str] = {}
    for bid in runtime_block_ids:
        shim = ctx.workspace.workspace / "vendor" / "blocks" / bid / "block.py"
        if shim.is_file():
            shim_texts[bid] = shim.read_text(encoding="utf-8", errors="replace")
    registry = _runtime_defs_for_blocks(
        runtime_block_ids, defs, blocks_root, shim_texts=shim_texts
    )
    _write(base / "blocks" / "__init__.py", _render_vendored_registry(registry))
    blocks_dir = blocks_root / "app" / "blocks"
    for mod in sorted(block_mods):
        pkg_src = blocks_dir / mod
        py_file = blocks_dir / f"{mod}.py"
        if (pkg_src / "__init__.py").is_file():
            package_sources: List[str] = []
            for py in sorted(pkg_src.rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                rel_inner = py.relative_to(pkg_src)
                source = py.read_text(encoding="utf-8", errors="replace")
                emitted = _emit_store_module(mod, source)
                package_sources.append(emitted)
                _write(base / "blocks" / mod / rel_inner, emitted)
            parsers_init = (
                ctx.workspace.workspace / base / "blocks" / mod / "parsers" / "__init__.py"
            )
            # Decided by what the package's own source imports: a package
            # that reaches for a ``parsers`` subpackage it does not ship gets
            # the stub, whatever the package is called.
            if not parsers_init.is_file() and package_imports_own_parsers(
                "\n".join(package_sources),
                mod,
                "\n".join(shipped.get(p, "") for p in written),
            ):
                _write(
                    base / "blocks" / mod / "parsers" / "__init__.py",
                    DOCUMENT_ENGINE_PARSERS_STUB,
                )
            sibling = blocks_dir / f"{mod}.py"
            if sibling.is_file() and _package_declares_sibling_wrapper(pkg_src, mod):
                # Keep ../{mod}.py next to the package so importlib load of
                # the shadowed wrapper still resolves after rewrite.
                source = sibling.read_text(encoding="utf-8", errors="replace")
                _write(
                    base / "blocks" / f"{mod}.py",
                    _emit_store_module(mod, source),
                )
            continue
        if not py_file.is_file():
            shadow = _resolve_shadowed_wrapper(blocks_dir, mod)
            if shadow is None:
                raise RoleError(
                    f"runtime slice needs app/blocks/{mod}.py or "
                    f"app/blocks/{mod}/__init__.py which does not exist in "
                    "the Store checkout"
                )
            emit_mod = _shadow_stem(mod) or mod
            source = shadow.read_text(encoding="utf-8", errors="replace")
            _write(
                base / "blocks" / f"{mod}.py",
                _emit_store_module(emit_mod, source),
            )
            continue
        source = py_file.read_text(encoding="utf-8", errors="replace")
        rewritten = _emit_store_module(mod, source)
        if needs_document_engine_parsers_package(rewritten):
            # A module file cannot host a .parsers submodule. Convert to a
            # package so ``vendor.cerebrum.blocks.document_engine.parsers``
            # imports (live sess_a69c8ce).
            _write(base / "blocks" / mod / "__init__.py", rewritten)
            _write(
                base / "blocks" / mod / "parsers" / "__init__.py",
                DOCUMENT_ENGINE_PARSERS_STUB,
            )
        else:
            _write(base / "blocks" / f"{mod}.py", rewritten)

    # The shims themselves still say ``from app.blocks import get_block`` --
    # rewrite them in place to point at the vendored runtime.
    for bid in runtime_block_ids:
        shim_dir = Path("vendor") / "blocks" / bid
        for py in sorted(
            (ctx.workspace.workspace / shim_dir).rglob("*.py")
        ):
            if "__pycache__" in py.parts:
                continue
            rel = shim_dir / py.relative_to(ctx.workspace.workspace / shim_dir)
            text = _prepare_cloned_python(
                _rewrite_runtime_imports(py.read_text(encoding="utf-8", errors="replace")),
                label=rel.as_posix(),
            )
            lazy_foreign.extend(_check_foreign_app_imports(rel.as_posix(), text))
            ctx.workspace.write_text(rel, text)
            shipped[rel.as_posix()] = text

    # Raises on an import with no recorded distribution. Failing the CLONER
    # is the point: a requirements.txt that omits what the source imports is
    # the same F10 defect as an import satisfied by accident, and it surfaces
    # as a dead feature in the customer's platform rather than a build error.
    ctx.state["vendored_dependencies"] = dependency_obligations(shipped)

    return written, sorted(set(lazy_foreign))


def _runtime_pin(blocks_root: Path) -> str:
    from app.factory.generator import git_head

    revision = git_head(Path(blocks_root))
    if revision and revision != "unknown":
        return revision
    return _content_digest(Path(blocks_root) / "app")


#: Stands in for the block id while adapter shapes are compared.
_ADAPTER_ID_SLOT = "<<CEREBRUM_BLOCK_ID>>"
_ADAPTER_MARKER = "Auto-generated adapter for Cerebrum block"
_adapter_shape_cache: Dict[str, str] = {}


def _store_adapter_shape(blocks_root: Path) -> str:
    """The adapter the pinned Store generates, with the block id slotted out.

    NOTHING ABOUT THE ADAPTER IS WRITTEN DOWN HERE. The Store ships one
    generated block.py per block, identical but for its id. Read them all,
    slot the id out of each, and the most common result IS the Store's
    adapter -- at whatever commit the Factory is pinned to. When the Store
    changes its generator, the next pin bump changes what is emitted, with no
    edit in this repo and no template here to drift.

    The MODAL shape rather than the first one found: a few adapters are
    hand-edited, and a short id ("pdf", "chat") can collide with ordinary
    text when slotted out. Neither can be the majority.
    """
    key = str(blocks_root)
    if key in _adapter_shape_cache:
        return _adapter_shape_cache[key]
    shapes: Dict[str, int] = {}
    for adapter in sorted((blocks_root / "block_registry").glob("*/block.py")):
        try:
            text = adapter.read_text(encoding="utf-8")
        except OSError:
            continue
        bid = adapter.parent.name
        if _ADAPTER_MARKER not in text or bid not in text:
            continue
        shape = text.replace(bid, _ADAPTER_ID_SLOT)
        shapes[shape] = shapes.get(shape, 0) + 1
    if not shapes:
        raise RoleError(
            "the Store checkout ships no generated block.py adapter to derive "
            "one from, so a manifest-only block cannot be given one"
        )
    shape = max(sorted(shapes), key=lambda candidate: shapes[candidate])
    _adapter_shape_cache[key] = shape
    return shape


def _emit_adapter_for_manifest_only_block(
    ctx: RoleContext, bid: str, source: Path
) -> bool:
    """Write the Store's standard adapter for a block registered without one.

    Live: "CLONER failed -- registered_block_missing: 3 block(s) registered
    but not on disk". Twenty Store blocks -- the domain containers among them
    (construction, insurance, finance_v2, legal_v2) -- are registered with a
    signed block.json and NO block.py. The CLONER copied the manifest,
    recorded the block as vendored, and its own gate then demanded a
    vendor/blocks/<id>/block.py that had never existed anywhere.

    The adapter cannot be added in the Store: block.py sits inside the signed
    file set and the operator's key is not in either repo. It does not need
    to be. The Store's adapter is generated boilerplate, identical for every
    block but its id, and holds no logic -- so it is DERIVED from the adapters
    the pinned Store already ships (see _store_adapter_shape), never written
    down here.

    NOT A STUB, which is the line this repo drew after the always-ok estate
    blocks: a stub answers on its own; this cannot. It only calls
    get_block(), so the runtime slice resolver still requires the Store
    runtime to exist and still fails the clone when it does not. A
    manifest-only block with NO runtime entry is refused here by name rather
    than shipped as an adapter around nothing.

    Returns True when an adapter was written.
    """
    if (source / "block.py").is_file():
        return False
    if not ctx.blocks_root:
        raise RoleError(
            f"{bid} is registered in the Store with a manifest and no "
            "block.py, and no Store checkout is available to find its runtime "
            "in. Set CEREBRUM_BLOCKS_ROOT."
        )
    blocks_root = Path(ctx.blocks_root)
    resolved = _resolve_store_def(bid, _store_block_defs(blocks_root), blocks_root)
    if resolved is None:
        raise RoleError(
            f"{bid} is registered in the Store with a manifest only: it has no "
            "block.py adapter AND no entry in the Store runtime registry "
            "(app/blocks/__init__.py), so there is nothing for an adapter to "
            "call. It cannot be attached until the Store gives it a runtime."
        )
    # The id the RUNTIME knows it by, which is not always the registry's:
    # the ``construction`` manifest is served by ``construction_v2``.
    store_key = resolved[0]
    ctx.workspace.write_text(
        Path("vendor") / "blocks" / bid / "block.py",
        _store_adapter_shape(blocks_root).replace(_ADAPTER_ID_SLOT, store_key),
    )
    return True


def run_cloner(ctx: RoleContext) -> RoleResult:
    """Block stocker: vendor each resolved block's real source into the workspace.

    Writes under ``vendor/``, ``kits/``, and the lockfile -- its whole lane.
    The lockfile records where each block (and kit pack) came from so the
    Store registrar can later tell a Store-sourced clone from a mirror stub.
    No agent. Published on the product as ``GET /v1/inventory``.
    """
    block_ids = tuple(ctx.state.get("resolved_blocks", ()))
    if not block_ids:
        # A plan of pure-GENERATE capabilities is legitimate; there is simply
        # nothing to vendor. Say so rather than inventing an empty lockfile.
        ctx.note("no blocks to vendor", stage="blocks", done=0, total=0)
        return RoleResult(ok=True, detail="no blocks to vendor", vendored_blocks=())

    lock: Dict[str, Any] = {"schema": "blocks.lock.v1", "blocks": {}}
    vendored: List[str] = []
    missing: List[str] = []
    runtime_blocks: List[str] = []
    ctx.note(
        f"cloning {len(block_ids)} block(s)",
        stage="blocks",
        done=0,
        total=len(block_ids),
    )

    for bid in block_ids:
        source = _from_facade("_block_source_dir", _block_source_dir)(
            bid, ctx.blocks_root
        )
        if source is None:
            missing.append(bid)
            continue
        from app.factory.blocks_lock import BlocksLockError, enforce_store_lock, resolve_lock

        factory_lock = (
            ctx.blocks_lock if ctx.blocks_lock is not None else resolve_lock()
        )
        try:
            enforce_store_lock(bid, source, ctx.blocks_root, factory_lock)
        except BlocksLockError as exc:
            raise RoleError(str(exc)) from exc
        # This used to swap in a Factory-local stub when the Store's runtime
        # did not register a block ("the factory stub runs offline") -- the
        # origin of the always-ok estate blocks that shipped inside customer
        # platforms. The Factory holds no blocks: a Store block whose runtime
        # is missing fails CLONER honestly instead of being replaced by a fake.
        needs_rt = _shim_needs_runtime(source)
        ctx.workspace.copy_tree(source, Path("vendor") / "blocks" / bid)
        if _emit_adapter_for_manifest_only_block(ctx, bid, source):
            # The adapter imports app.blocks, so it needs the runtime slice
            # exactly as a Store-written shim does.
            needs_rt = _shim_needs_runtime(
                ctx.workspace.workspace / "vendor" / "blocks" / bid
            )
        from app.factory.build.network_posture import apply_p1_cloned_block

        if apply_p1_cloned_block(ctx.workspace, bid):
            # P1 capture adapter has no Store shim; do not demand Blocks runtime.
            needs_rt = _shim_needs_runtime(
                ctx.workspace.workspace / "vendor" / "blocks" / bid
            )
        origin, revision = _pin_source(source, ctx.blocks_root)
        cloned_meta = ctx.workspace.workspace / "vendor" / "blocks" / bid / "block.json"
        image_pin = "none"
        if cloned_meta.is_file() and redact_unpinned_images(cloned_meta):
            image_pin = "refused_unverified"
        # Verify against the SOURCE, before the runtime-import rewrites below
        # change the bytes. Hashing the vendored copy would compare a
        # transformed file with an upstream digest and fail every build.
        integrity = _integrity_record(source)
        lock["blocks"][bid] = {
            "source": origin,
            "commit": revision,
            "path": f"vendor/blocks/{bid}",
            "image_pin": image_pin,
            _INTEGRITY_KEY: integrity,
        }
        vendored.append(bid)
        ctx.note(
            f"cloned {bid}",
            stage="blocks",
            done=len(vendored),
            total=len(block_ids),
            block_id=bid,
        )
        # A shim that reaches for the Store's runtime cannot import offline
        # on its own -- the slice it stands on must be vendored with it.
        #
        # Keyed on what the SOURCE NEEDS, never on where it came from. This
        # was gated on ``ctx.blocks_root`` and the factory's own vendor
        # mirror turned out to contain real Store shims (audit/, capture/),
        # so a build with no Store checkout vendored a shim that imports
        # app.blocks and shipped no runtime for it: the CLONER gate failed
        # with "No module named 'app'" on the production default path.
        if needs_rt:
            runtime_blocks.append(bid)

    if missing:
        raise RoleError(
            "no source found for block(s) — do not invent: "
            + ", ".join(sorted(missing))
        )

    if runtime_blocks and not ctx.blocks_root:
        # The slice can only be taken from a Store checkout. Vendoring the
        # shim without it would pass this role and fail the gate with an
        # opaque import error, so refuse here and name the fix.
        raise RoleError(
            "block(s) "
            + ", ".join(sorted(runtime_blocks))
            + " are Store shims that need the app.blocks/app.core runtime, but "
            "no Store checkout is available to vendor it from. Set "
            "CEREBRUM_BLOCKS_ROOT (or make CEREBRUM_BLOCKS_REPO cloneable) so "
            "the platform can carry the runtime it depends on."
        )

    if runtime_blocks:
        try:
            runtime_files, lazy_foreign = _vendor_runtime_slice(ctx, runtime_blocks)
        except BlockObligationError as exc:
            # Fail-closed is unchanged: an unknown import still refuses the
            # build. Convert to RoleError so RoleRunner records RUN_FAILED
            # with the missing module instead of the thread dying as
            # "build thread crashed" (live sess_d1cb9d51c5354bea /
            # CEREBRUMDEV-BACKEND-A).
            raise RoleError(str(exc)) from exc
        lock["runtime"] = {
            "source": "cerebrum-blocks",
            "commit": _runtime_pin(Path(ctx.blocks_root)),
            "path": "vendor/cerebrum",
            "for_blocks": sorted(runtime_blocks),
            "files": sorted(runtime_files),
            # Function-local imports of Store packages the factory cannot
            # vendor. Each breaks only the feature that runs it; listed so
            # the limitation ships in the artifact instead of as a surprise.
            "lazy_foreign_imports": lazy_foreign,
        }

    ctx.workspace.write_text(
        "blocks.lock.json", json.dumps(lock, indent=2, sort_keys=True) + "\n"
    )
    ctx.workspace.write_text(
        Path("vendor") / "__init__.py",
        '"""Vendored block source. Imported locally; never fetched at runtime."""\n',
    )
    ctx.workspace.write_text(
        Path("vendor") / "blocks" / "__init__.py",
        '"""Blocks vendored at build time, pinned by blocks.lock.json."""\n',
    )

    from app.factory.kit_pack import stock_kits_via_workspace

    kit_lock = stock_kits_via_workspace(
        ctx.workspace, vendored, blocks_root=ctx.blocks_root
    )
    if kit_lock:
        lock["kits"] = kit_lock
        ctx.workspace.write_text(
            "blocks.lock.json", json.dumps(lock, indent=2, sort_keys=True) + "\n"
        )

    # Freeze the compiled C-BRIEF on the workspace for the WRITER. The WRITER
    # runs on CodeWhale (DeepSeek) and reads docs/coder_brief.md back as its
    # COMPILED C-BRIEF section (_compiled_writer_brief) — without it the agent
    # authors the platform blind from the one-line blueprint summary
    # (sess_9f67681a79324fcc class). It must not crash CLONER — but a brief
    # that did not land must be VISIBLE, never swallowed: the old double
    # best-effort swallow hid a non-delivery on every build.
    handoff_notes: dict = {}
    try:
        from app.factory.build.domain_handoff import ensure_coder_brief

        ws = getattr(ctx, "workspace", None)
        dest = getattr(ws, "destination", None) or getattr(ws, "workspace", None)
        if dest is None:
            raise RuntimeError("CLONER has no workspace to stage the C-BRIEF on")
        brief = ensure_coder_brief(
            dest,
            blueprint=getattr(ctx, "blueprint", None),
            plan=getattr(ctx, "plan", None),
            blocks_root=getattr(ctx, "blocks_root", None),
        )
        handoff_notes["coder_brief"] = {
            "written": True,
            "path": "docs/coder_brief.md",
            "bytes": brief.stat().st_size,
        }
    except Exception as exc:  # noqa: BLE001 — never crash CLONER, but record it
        handoff_notes["coder_brief"] = {"written": False, "error": str(exc)}
        note = getattr(ctx, "note", None)
        if callable(note):
            note(
                f"C-BRIEF not frozen for the WRITER: {exc}",
                stage="coder_brief_unwritten",
            )

    notes = {"lock": lock}
    notes.update(handoff_notes)
    return RoleResult(
        ok=True,
        detail=f"vendored {len(vendored)} block(s), {len(kit_lock)} kit(s)",
        vendored_blocks=tuple(vendored),
        notes=notes,
    )


# -- WRITER --------------------------------------------------------------


def _render_dispatch(contracts: Optional[Dict[str, Any]] = None) -> str:
    """Dispatch runtime with this build's harvested block contracts baked in."""
    baked = repr(dict(contracts or {}))
    return _DISPATCH_RUNTIME.replace(
        "BLOCK_CONTRACTS: Dict[str, Any] = {}",
        "BLOCK_CONTRACTS: Dict[str, Any] = " + baked,
        1,
    )


def _indent_handler_body(body: str, extra: int = 4) -> str:
    """Indent a handle() body so it can sit inside a nested ``_impl``."""
    pad = " " * extra
    return "\n".join((pad + line if line.strip() else line) for line in body.splitlines())


def _ensure_handler_fails_closed(body: str) -> str:
    """Refuse ``ok: True`` when ``execute()`` returned a block error.

    Coder handlers (moonshot/kimi on the live invoice-management run) often
    ignore the envelope and return success anyway. The deterministic template
    already fail-closes; this wrapper makes every path — coder or template —
    refuse success when a block failed. Idempotent: a body that already
    returns ``ok: False`` is left alone.

    Before the call, domain records are shaped into block-acceptable inputs
    via ``prepare_block_input`` (notification channel/message, workflow
    steps, team minted ``team_id``, document_engine existing file paths,
    event_bus ``topic``, database ``table``/``sql``, analytics
    metric/value). That is the residential-lettings + veterinary-care
    pilot fix: handlers that forward the domain JSON unchanged still
    reach the block with a contract-valid payload. Soft import keeps unit
    tests that stub only ``app.dispatch`` working.

    ``action`` is lifted out of the payload and passed only as the
    ``action=`` keyword (live makerspace-management, sess_39b5fec2abd346a5:
    every capability buried the operation in the dict; dispatch answered
    ``Unknown action: None`` / ``unknown field(s): action``).
    """
    return (
        "    import app.dispatch as _dispatch\n"
        "    try:\n"
        "        from app.block_inputs import prepare_block_input as _prepare_block_input\n"
        "    except ImportError:  # pragma: no cover - unit stubs without the module\n"
        "        def _prepare_block_input(block_id, data, **_kw):\n"
        "            return data if isinstance(data, dict) else {'value': data}\n"
        "    try:\n"
        "        from app.block_inputs import default_block_action as _default_block_action\n"
        "    except ImportError:  # pragma: no cover - unit stubs / older emit\n"
        "        def _default_block_action(block_id, default_actions=None):\n"
        "            defaults = default_actions if isinstance(default_actions, dict) else {}\n"
        "            cand = defaults.get(block_id)\n"
        "            return cand if isinstance(cand, str) and cand.strip() else None\n"
        "    try:\n"
        "        from app.block_inputs import split_execute_action as _split_execute_action\n"
        "    except ImportError:  # pragma: no cover - unit stubs / older emit\n"
        "        def _split_execute_action(payload, action=None, default_action=None):\n"
        "            data = dict(payload) if isinstance(payload, dict) else (\n"
        "                {} if payload is None else {'value': payload}\n"
        "            )\n"
        "            inner = data.get('input') if isinstance(data.get('input'), dict) else {}\n"
        "            resolved = action\n"
        "            if not (isinstance(resolved, str) and resolved.strip()):\n"
        "                for cand in (data.get('action'), inner.get('action'), default_action):\n"
        "                    if isinstance(cand, str) and cand.strip():\n"
        "                        resolved = cand\n"
        "                        break\n"
        "                else:\n"
        "                    resolved = default_action\n"
        "            data.pop('action', None)\n"
        "            if isinstance(data.get('input'), dict):\n"
        "                data['input'] = dict(data['input'])\n"
        "                data['input'].pop('action', None)\n"
        "            return resolved, data\n"
        "    _block_errors = []\n"
        "    def _watched(block_id, *a, **kw):\n"
        "        data = a[0] if a else kw.get('payload', {})\n"
        "        action = kw.get('action')\n"
        "        if action is None and len(a) > 1:\n"
        "            action = a[1]\n"
        "        params = kw.get('params')\n"
        "        if params is None and len(a) > 2:\n"
        "            params = a[2]\n"
        "        action, data = _split_execute_action(\n"
        "            data,\n"
        "            action=action,\n"
        "            default_action=_default_block_action(\n"
        "                block_id, BLOCK_DEFAULT_ACTIONS\n"
        "            ),\n"
        "        )\n"
        "        prepared = _prepare_block_input(\n"
        "            block_id, data, action=action, roster=BLOCK_IDS,\n"
        "            entity=ENTITY,\n"
        "            default_actions=BLOCK_DEFAULT_ACTIONS,\n"
        "        )\n"
        "        if isinstance(prepared, dict):\n"
        "            prepared = dict(prepared)\n"
        "            prepared.pop('action', None)\n"
        "            if isinstance(prepared.get('input'), dict):\n"
        "                prepared['input'] = dict(prepared['input'])\n"
        "                prepared['input'].pop('action', None)\n"
        "        res = _dispatch.execute(\n"
        "            block_id, prepared, action=action, params=params\n"
        "        )\n"
        "        if isinstance(res, dict) and (\n"
        '            res.get("status") == "error" or "error" in res\n'
        "        ):\n"
        "            _block_errors.append(\n"
        '                "%s: %s" % (block_id, str(res.get("error") or res.get("status"))[:160])\n'
        "            )\n"
        "        return res\n"
        "    def _impl(payload, execute=_watched):\n"
        f"{_indent_handler_body(body, 4)}\n"
        "    result = _impl(payload)\n"
        "    if _block_errors and (\n"
        '        not isinstance(result, dict) or result.get("ok") is not False\n'
        "    ):\n"
        "        return {\n"
        '            "ok": False,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "error": "block failed: " + "; ".join(_block_errors),\n'
        '            "result": result,\n'
        "        }\n"
        "    if isinstance(result, dict) and result.get('ok') is False:\n"
        "        return result\n"
        "    if isinstance(result, dict):\n"
        "        result = dict(result)\n"
        "        result.setdefault('ok', True)\n"
        "        return result\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID, "
        "'result': result}"
    )


def _render_actions_init(capability_names: Sequence[str]) -> str:
    """``app/actions/__init__.py`` that does not eagerly re-export siblings.

    Live sess_8273a26a4c1b4ee7 (VetCare Hub / veterinary-care) died at
    WRITER ``writer_behaviour`` before any route honesty check:

        workspace does not import: ImportError: cannot import name
        'pet_records_management' from partially initialized module
        'app.actions' (.../app/actions/__init__.py)

    The factory used to emit ``from app.actions import <cap>`` for every
    capability and the same fromlist at module level in ``app/routes.py``.
    A CLI/Kimi handler that imports ``app.routes`` / ``app.main`` at load
    time (or a name from the still-initializing ``app.actions`` package)
    completes the cycle. PEP 562 ``__getattr__`` plus deferred route
    imports break it. Callers may still write
    ``from app.actions import pet_records_management``.
    """
    names = [
        str(name).replace("-", "_")
        for name in capability_names
        if str(name).strip()
    ]
    names = list(dict.fromkeys(names))
    all_list = ", ".join(repr(name) for name in names)
    return (
        '"""Capability handlers."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import importlib\n"
        "from typing import Any\n"
        "\n"
        f"__all__ = [{all_list}]\n"
        "\n"
        "\n"
        "def __getattr__(name: str) -> Any:\n"
        "    if name in __all__:\n"
        '        return importlib.import_module("." + name, __name__)\n'
        "    raise AttributeError("
        'f"module {__name__!r} has no attribute {name!r}")\n'
        "\n"
        "\n"
        "def __dir__() -> list:\n"
        "    return sorted(set(globals()) | set(__all__))\n"
    )


def actions_init_eager_reexports(text: str) -> List[str]:
    """Names pulled in via ``from app.actions import X`` inside the package init.

    That pattern is the circular-import class. An empty list means the
    package can finish initializing before any capability module loads.
    """
    tree = _parsed(text)
    if tree is None:
        return []
    return [
        alias.name
        for node in getattr(tree, "body", [])
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "app.actions"
        for alias in node.names
    ]


def _handler_module(
    capability_id: str,
    block_ids: Sequence[str],
    body: str,
    source: str,
    default_actions: Optional[Dict[str, str]] = None,
    field_names: Optional[Sequence[str]] = None,
    entity: Optional[str] = None,
) -> str:
    entity_name = entity or str(capability_id or "record").replace("-", "_")
    return f'''"""Handler for capability {capability_id}.

Written by the factory WRITER role ({source}). Blocks are invoked through the
local dispatch runtime -- this module makes no network call.

Persistence is route-scoped (factory-grounded persist envelope): the ROUTE's
tenant-scoped save writes the request after SUCCESS; handle() is pure
dispatch and must not persist directly (Phase 2 §0.2).
"""

from __future__ import annotations

from typing import Any, Dict

from app.dispatch import execute

CAPABILITY_ID = "{capability_id}"
{authorship_marker_line(source)}
ENTITY = {entity_name!r}
BLOCK_IDS = {list(block_ids)!r}
#: Each block's declared default action (from its block.json). Blocks are
#: action-dispatched; calling one with no action is answered with an error.
BLOCK_DEFAULT_ACTIONS = {dict(default_actions or {})!r}
#: This capability's own domain columns. The kernel strips trust-scope keys
#: from caller arguments; a column that shares one of those names (project_id
#: is the obvious one for construction) would be deleted before handle() ever
#: saw it. Declaring the names here tells the kernel they are domain data.
CAPABILITY_FIELDS = {list(field_names or [])!r}


def handle(payload: Dict[str, Any]) -> Dict[str, Any]:
{_ensure_handler_fails_closed(body)}
'''


def _checkpoint_writer_capability(ctx: RoleContext, capability_id: str) -> None:
    """Persist one landed handler onto the resume spine. Lazy import: runner→roles."""
    from app.factory.build.runner import checkpoint_landed_capability

    checkpoint_landed_capability(ctx, capability_id)


def _stage_handler_for_commit(ctx: RoleContext, handler_rel: Path) -> None:
    """Record a harvested/keep-path handler so staged WRITER commit copies it.

    Live sess_bc0527bad93f4c66: ``emit_factory_grounded_reuse_keep_path``
    wrote ``app/actions/{cap}.py`` into staging via ``Path.write_text``.
    WRITER then ratcheted/kept those files without ``workspace.write_text``,
    so ``commit()`` copied routes (which import the modules) but not the
    handlers. ``writer_behaviour`` POSTed ``ModuleNotFoundError``.
    """
    persist_root = persist_workspace_root(ctx.workspace)
    path = persist_root / handler_rel
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    bids = parse_handler_block_ids(text)
    defaults = harvest_block_default_actions(bids, persist_root, workspace=ctx.workspace)
    if defaults:
        text = apply_default_actions_to_handler(text, defaults)
    ctx.workspace.write_text(handler_rel, text)


def _capability_handler_body(
    capability_id: str,
    block_ids: Sequence[str],
) -> str:
    """Template, or the factory-grounded event_bus workflow body.

    Live tip 397e56a / #332: LLM timeouts left appointment_scheduling on
    ``execute(block_id, payload)``. #331's AST check vacuous-passed; PRODUCT
    then refused workflow step_1 (event_bus). Appointment-style capabilities
    get the prepared step in source so WRITER does not burn rework on stubs.
    """
    if needs_grounded_event_bus_handler(capability_id, block_ids):
        return grounded_event_bus_handler_body(capability_id, block_ids)
    return _templated_body(block_ids)


def _templated_body(block_ids: Sequence[str]) -> str:
    if not block_ids:
        # Phase 2 §0.2: handlers are pure dispatch. The ROUTE persists the
        # request via its tenant-scoped save(payload) after SUCCESS.
        return '    return {"ok": True, "capability": CAPABILITY_ID}'
    # Domain JSON is not block-acceptable JSON. prepare_block_input (invoked
    # inside the fail-closed execute wrapper, and explicitly here so the
    # template documents the contract) builds channel/message, steps, team
    # minted team_id, document file paths, event_bus topic, and database
    # table/sql from the capability record.
    # The nested check is the honesty line: the ninth live build shipped
    # three capabilities whose block calls all failed while the handler
    # reported ok -- the suite passed on a payload the blocks had rejected.
    return (
        "    results = {}\n"
        "    errors = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        result = execute(\n"
        "            block_id, payload, action=BLOCK_DEFAULT_ACTIONS.get(block_id)\n"
        "        )\n"
        "        results[block_id] = result\n"
        "        if isinstance(result, dict) and (\n"
        '            result.get("status") == "error" or "error" in result\n'
        "        ):\n"
        '            errors[block_id] = str(result.get("error") or result)[:200]\n'
        "    if errors:\n"
        "        return {\n"
        '            "ok": False,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "error": "; ".join(f"{b}: {e}" for b, e in sorted(errors.items())),\n'
        '            "results": results,\n'
        "        }\n"
        # Phase 2 §0.2: persistence is the ROUTE's job (tenant-scoped
        # save(payload)); the handler reports dispatch results only.
        '    return {"ok": True, "capability": CAPABILITY_ID, "results": results}'
    )


def _fallback_spec(cap: Any) -> Dict[str, Any]:
    """Deterministic schema when no coder is available.

    Deliberately minimal and honest: a generic record envelope rather than a
    guessed domain model. The template does not pretend to domain knowledge it
    does not have.
    """
    return {
        "entity": cap.capability_id.replace("-", "_"),
        "fields": [
            {"name": "reference", "type": "str", "required": True},
            # A vocabulary and a bound on the deterministic path too, so the
            # constraint contract is exercised on a keyless CI run rather than
            # only when an agent happens to declare one.
            {
                "name": "status",
                "type": "str",
                "required": True,
                "allowed_values": list(ENVELOPE_STATUS_VALUES),
            },
            {
                "name": "quantity",
                "type": "int",
                "required": False,
                "min": 0,
                "max": 10000,
            },
        ],
        "model": None,
    }


def _entity_class_name(entity: str) -> str:
    """PascalCase class name that is always a valid Python identifier."""
    raw = re.sub(r"[^0-9A-Za-z_]+", "_", str(entity or "record"))
    cls = "".join(p.title() for p in raw.split("_") if p) or "Record"
    if cls[0].isdigit() or not cls.isidentifier():
        cls = f"Record{cls}" if cls and cls[0].isdigit() else "Record"
    return cls


def _render_models(specs: Dict[str, Dict[str, Any]]) -> str:
    """dataclass per entity. Stdlib only, so the artifact needs no ORM."""
    out = [
        '"""Domain models for this platform.',
        "",
        "Plain dataclasses on purpose: the delivered platform must run with no",
        "ORM, no service, and no network. Persistence is in app/store.py.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from dataclasses import asdict, dataclass, field",
        "from typing import Any, Dict, Optional",
        "",
        "",
    ]
    class_names: Dict[str, str] = {}
    used_classes: set[str] = set()
    for cap_id, spec in sorted(specs.items()):
        cls = _entity_class_name(spec["entity"])
        if cls in used_classes:
            cls = sanitize_python_identifier(cls, used=used_classes)
        else:
            used_classes.add(cls)
        class_names[cap_id] = cls
        reserved = {
            "id",
            "self",
            "FIELDS",
            "CONSTRAINTS",
            "_FIELD_PY",
            "_FIELD_JSON",
            "to_dict",
            "from_dict",
        }
        rendered: List[tuple[Dict[str, Any], str]] = []
        for f in spec["fields"]:
            original = str(f.get("name") or "").strip()
            attr = sanitize_python_identifier(original, used=reserved)
            rendered.append((f, attr))
        json_to_py = {
            str(f["name"]): attr
            for f, attr in rendered
            if str(f.get("name") or "") != attr
        }
        py_to_json = {attr: str(f["name"]) for f, attr in rendered if str(f.get("name") or "") != attr}
        out += [
            "@dataclass",
            f"class {cls}:",
            f'    """Entity for capability {cap_id}."""',
            "",
            "    id: Optional[int] = None",
        ]
        for f, attr in rendered:
            out.append(
                f"    {attr}: {_python_annotation(f)} = {_field_default(f)}"
            )
        out += [
            "",
            "    FIELDS = " + repr([f["name"] for f in spec["fields"]]),
            # The single source of truth for value restrictions: the route
            # validates against these and the tests build payloads from them,
            # so neither side can invent a rule the other cannot satisfy.
            "    CONSTRAINTS = " + repr(_constraints_of(spec)),
            # Fields whose DECLARED type is money (storage collapses them to
            # float, so the declaration is kept here): the money contract
            # reads this, never a field's name.
            "    MONEY_FIELDS = " + repr([
                f["name"] for f in spec["fields"]
                if _semantic_field_type(f.get("type")) == MONEY_SEMANTIC_TYPE
            ]),
            "    _FIELD_PY = " + repr(json_to_py),
            "    _FIELD_JSON = " + repr(py_to_json),
            "",
            "    def to_dict(self) -> Dict[str, Any]:",
            "        raw = asdict(self)",
            "        return {self._FIELD_JSON.get(k, k): v for k, v in raw.items()}",
            "",
            "    @classmethod",
            "    def from_dict(cls, data: Dict[str, Any]) -> \"" + cls + "\":",
            "        known = {}",
            "        for k, v in (data or {}).items():",
            "            if k == \"id\":",
            "                continue",
            "            attr = cls._FIELD_PY.get(k, k)",
            "            if k in cls.FIELDS or attr in cls._FIELD_JSON:",
            "                known[attr] = v",
            "        return cls(id=(data or {}).get(\"id\"), **known)",
            "",
            "",
        ]
    out.append("MODELS = {")
    for cap_id in sorted(specs):
        out.append(f'    "{cap_id}": {class_names[cap_id]},')
    out.append("}")
    return "\n".join(out) + "\n"


def _render_store(specs: Dict[str, Dict[str, Any]]) -> str:
    """sqlite3 persistence derived from the same specs the models came from.

    Schema is versioned Alembic (S10). connect() does not CREATE TABLE.
    """
    from app.factory.build.data_lifecycle import render_store

    return render_store(specs)


def _constraint_guard(spec: Dict[str, Any]) -> str:
    """Reject payloads that violate the capability's own field rules."""
    constraints = _constraints_of(spec)
    required = [
        str(f["name"])
        for f in (spec.get("fields") or [])
        if isinstance(f, dict) and f.get("name") and f.get("required")
    ]
    return "\n".join(
        [
            "    if not isinstance(payload, dict):",
            '        return {"ok": False, "error": "payload must be an object"}',
            f"    required_fields = {required!r}",
            "    for name in required_fields:",
            "        if name not in payload or payload[name] in (None, ''):",
            '            return {"ok": False,',
            '                    "error": "Missing required field: " + name,',
            f"                    {REJECTED_FIELD_KEY!r}: name,",
            f"                    {REJECTION_REASON_KEY!r}: {MISSING_REQUIRED!r}}}",
            f"    constraints = {constraints!r}",
            "    for name, rules in constraints.items():",
            "        if name not in payload:",
            "            continue",
            "        value = payload[name]",
            '        allowed = rules.get("allowed_values")',
            "        if allowed is not None and value not in allowed:",
            '            return {"ok": False,',
            '                    "error": name + " must be one of: " + ", ".join(allowed),',
            f"                    {REJECTED_FIELD_KEY!r}: name,",
            f"                    {REJECTION_REASON_KEY!r}: {NOT_ALLOWED!r},",
            f"                    {ALLOWED_VALUES_KEY!r}: list(allowed)}}",
            '        low, high = rules.get("min"), rules.get("max")',
            "        if isinstance(value, bool) or not isinstance(value, (int, float)):",
            "            continue",
            "        if low is not None and value < low:",
            '            return {"ok": False, "error": name + " is below the minimum"}',
            "        if high is not None and value > high:",
            '            return {"ok": False, "error": name + " is above the maximum"}',
        ]
    )


def _declared_refusal_lines(
    name: str,
    sample: Dict[str, Any],
    connectors: List[str],
    capability_id: Optional[str] = None,
) -> List[str]:
    """Route-suite lines for a capability calling a DECLARED placeholder
    connector: the right answer is the typed unavailable refusal naming the
    settings it needs -- never an accepted record, whose round trip is not
    judgeable until the connector is built. Its list route still answers."""
    from app.factory.build.placeholder_connectors import (
        UNAVAILABLE_KIND,
        UNAVAILABLE_STATUS,
        setting_for,
    )

    settings = [setting_for(c) for c in connectors]
    # The route validates the payload (422) before it refuses a declared
    # placeholder (503): build and correct it with the shared helpers so
    # the refusal is what this line reaches.
    return [
        f"    payload = {sample!r}",
        f'    resp, _corr = _post_accepting("/v1/{name}", payload, AUTH, {capability_id!r})',
        "    try:",
        "        body = resp.json()",
        "    except Exception:",
        "        body = None",
        f"    if not (resp.status_code == {UNAVAILABLE_STATUS}",
        f"            and isinstance(body, dict) and body.get({OK_KEY!r}) is False",
        f"            and body.get('error_kind') == {UNAVAILABLE_KIND!r}",
        f"            and list(body.get('settings') or []) == {settings!r}):",
        f"        failures.append('{name}: declares placeholder connector(s); want HTTP"
        f" {UNAVAILABLE_STATUS} error_kind {UNAVAILABLE_KIND} naming {', '.join(settings)},"
        " got HTTP ' + str(resp.status_code) + ': ' + resp.text[:200])",
        f'    listed = client.get("/v1/{name}", headers=AUTH)',
        f"    if listed.status_code not in {tuple(ACCEPT_STATUSES)!r}:",
        f"        failures.append('{name} list: HTTP ' + str(listed.status_code))",
        "",
    ]


def _templated_route_body(spec: Dict[str, Any]) -> str:
    """Capability POST routed through ``execute_action``. Persist stays here.

    This is not a new persist-wrapper. The kernel already owns trust-scope,
    input/output validation, and ActionResult. The route persists the request
    payload only after ActionStatus.SUCCESS, using the existing store.
    """
    lines = [
        _constraint_guard(spec),
        "    result = await run_capability(CAPABILITY_ID, payload, request)",
        "    if isinstance(result, dict) and result.get('status') != 'success':",
        "        return {'ok': False,",
        "                'error': result.get('error_message') or result.get('status'),",
        "                'result': result}",
        "    try:",
        "        stored = save(payload)",
        "    except Exception as exc:",
        "        return {'ok': False,",
        "                'error': f'{type(exc).__name__}: {exc}',",
        "                'capability': CAPABILITY_ID}",
        f'    return {{{OK_KEY!r}: True, "capability": CAPABILITY_ID, "result": result,',
        f'            {STORED_RECORD_KEY!r}: stored}}',
    ]
    return "\n".join(lines)


def _ensure_route_persists_payload(body: str) -> str:
    """Coder routes often persist handle()'s envelope, not the request.

    The live winery-hospitality zip saved ``result`` / ``handled``
    (``{ok, data: {...}}``) into sqlite, so every column was NULL even
    though POST returned ``ok: true``. The record is the payload.
    """
    rewritten = re.sub(
        r"\bsave\(\s*(?!payload\s*\))([A-Za-z_][\w]*)\s*\)",
        "save(payload)",
        body,
    )
    if not _calls_with_name_arg(rewritten, "save", "payload"):
        rewritten = (
            rewritten.rstrip()
            + "\n    try:\n        stored = save(payload)\n"
            + "    except Exception as exc:\n"
            + "        return {'ok': False, "
            + "'error': f'{type(exc).__name__}: {exc}'}\n"
        )
    return rewritten


def _render_jobs_module(
    *,
    catalog: Dict[str, Any],
    capabilities: List[Dict[str, Any]],
    gates: Dict[str, Any],
) -> str:
    """Frozen kernel JDs plus live readers for lock/provenance files."""
    jobs = jobs_manifest()
    return (
        '"""Kernel job descriptions shipped with this platform.\n'
        "\n"
        "Frozen at manufacture time from Factory RoleContract. HTTP routes\n"
        "publish each kernel's job; they never re-run that job. Inventory and\n"
        "provenance read lock/provenance files live so the register stays true\n"
        "to the checkout.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, Optional\n"
        "\n"
        f"JOBS = {jobs!r}\n"
        f"CATALOG = {catalog!r}\n"
        f"CAPABILITIES = {capabilities!r}\n"
        f"GATES = {gates!r}\n"
        "\n"
        "\n"
        "def _read_workspace_json(relative: str) -> Optional[Dict[str, Any]]:\n"
        "    path = Path(__file__).resolve().parents[1] / relative\n"
        "    if not path.is_file():\n"
        "        return None\n"
        "    try:\n"
        "        data = json.loads(path.read_text(encoding='utf-8'))\n"
        "    except (OSError, ValueError):\n"
        "        return None\n"
        "    return data if isinstance(data, dict) else None\n"
        "\n"
        "\n"
        "def _job(kernel: str) -> Dict[str, Any]:\n"
        "    for item in JOBS:\n"
        "        if item['kernel'] == kernel:\n"
        "            return dict(item)\n"
        "    return {'kernel': kernel}\n"
        "\n"
        "\n"
        "def inventory() -> Dict[str, Any]:\n"
        "    payload = _job('CLONER')\n"
        "    payload['lock'] = _read_workspace_json('blocks.lock.json') or {\n"
        "        'schema': 'blocks.lock.v1',\n"
        "        'blocks': {},\n"
        "    }\n"
        "    return payload\n"
        "\n"
        "\n"
        "def provenance() -> Dict[str, Any]:\n"
        "    payload = _job('STORE_MANAGER')\n"
        "    payload['build'] = _read_workspace_json('docs/build_provenance.json') or {}\n"
        "    lock = _read_workspace_json('blocks.lock.json') or {}\n"
        "    payload['clones'] = lock.get('blocks') or {}\n"
        "    payload['store_ops'] = []\n"
        "    return payload\n"
    )


def render_routes_files(
    entries: List[Dict[str, Any]], blueprint: Any
) -> Dict[Path, str]:
    """``app/routes.py`` and every Factory module it imports, as one unit.

    The routes import ``app.placeholders``. Rendering routes alone left that
    import to a later, separate stamp, and a packaging path that wrote only
    the routes shipped an unimportable product (ModuleNotFoundError). Every
    path that emits routes writes them through here.
    """
    import ast

    from app.factory.build.store_acceptance import factory_renders

    routes = _render_routes(entries)
    imported = {
        node.module.replace(".", "/") + ".py"
        for node in ast.walk(ast.parse(routes))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    files: Dict[Path, str] = {Path("app") / "routes.py": routes}
    # Every module the routes import that the Factory stamps, rendered from
    # the same table the stamp writes -- read from the imports, never listed.
    for rel, text in factory_renders("platform", (), blueprint).items():
        if str(rel).replace("\\", "/") in imported:
            files[Path(rel)] = text
    return files


def _render_routes(entries: List[Dict[str, Any]]) -> str:
    """FastAPI router: kernel job routes, then one POST/GET/GET-id per capability."""
    out = [
        '"""HTTP surface for the platform\'s kernels and capabilities.',
        "",
        "Kernel routes publish each build role's job. Capability routes run",
        "entirely in-process: the handler dispatches to a vendored block and",
        "the result is persisted locally. No outbound call.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Any, Dict",
        "",
        "from fastapi import APIRouter, HTTPException, Request",
        "from fastapi.responses import JSONResponse",
        "",
        "from app import jobs, store",
        "from app.placeholders import UNAVAILABLE_STATUS, refusal_for",
        "from app.domain_ops import perform as perform_domain",
        "from app.kernel_bridge import run_capability",
        "",
        "router = APIRouter()",
        "",
        "#: capability id -> the store entity its POST route saves to. Rendered",
        "#: from the same entries as the routes below, so a reader of where a",
        "#: capability persists reads this, never a guess from its name.",
        "ROUTE_ENTITIES = %r" % ({e["capability_id"]: e["entity"] for e in entries},),
        "",
        "",
        '@router.get("/jobs")',
        "def list_jobs() -> Dict[str, Any]:",
        '    """Roster of every kernel job description."""',
        '    return {"jobs": jobs.JOBS}',
        "",
        "",
        '@router.get("/catalog")',
        "def catalog() -> Dict[str, Any]:",
        '    """COLLECTOR — Binding surveyor."""',
        "    return jobs.CATALOG",
        "",
        "",
        '@router.get("/inventory")',
        "def inventory() -> Dict[str, Any]:",
        '    """CLONER — Block stocker. Pinned vendor lock, read live."""',
        "    return jobs.inventory()",
        "",
        "",
        '@router.get("/capabilities")',
        "def capabilities() -> Dict[str, Any]:",
        '    """WRITER — Platform manufacturer. Capability HTTP surface."""',
        '    return {"items": jobs.CAPABILITIES}',
        "",
        "",
        '@router.get("/gates")',
        "def gates() -> Dict[str, Any]:",
        '    """TESTER — Acceptance inspector. Coverage only; does not run tests."""',
        "    return jobs.GATES",
        "",
        "",
        '@router.get("/provenance")',
        "def provenance() -> Dict[str, Any]:",
        '    """STORE_MANAGER — Store registrar. Clone register and provenance."""',
        "    return jobs.provenance()",
        "",
        "",
    ]
    for e in entries:
        name, entity = e["name"], e["entity"]
        out += [
            f"# --- {e['capability_id']} ({e['source']}) ---",
            "",
            "",
            f"def _{name}_handle(payload: Dict[str, Any]) -> Dict[str, Any]:",
            "    try:",
            # Import the sibling module, not the package. A module-level
            # ``from app.actions import {name}`` plus eager package
            # re-exports is the circular class (VetCare pet_records_management).
            f"        from app.actions.{name} import handle as _handle",
            "        result = _handle(payload)",
            "    except Exception as exc:  # Store/runtime refusal is not HTTP 500",
            '        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}',
            "    if not isinstance(result, dict):",
            '        return {"ok": False, "error": f"handle() returned {type(result).__name__}"}',
            "    return result",
            "",
            "",
            f'@router.post("/{name}")',
            f"async def {name}_create(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:",
            "    from app.auth import reject_invalid_payload, require_platform_token",
            "    tenant = require_platform_token(request)",
            f'    reject_invalid_payload("{e["capability_id"]}", payload)',
            f'    CAPABILITY_ID = "{e["capability_id"]}"',
            # A capability that calls a DECLARED placeholder connector answers
            # the typed unavailable refusal. After auth and payload checks, so
            # 401/422 still hold; before the handler, so nothing is stored.
            "    placeholder = refusal_for(CAPABILITY_ID)",
            "    if placeholder is not None:",
            "        return JSONResponse(status_code=UNAVAILABLE_STATUS, content=placeholder)",
            f"    handle = _{name}_handle",
            f'    save = lambda record: store.save("{entity}", record, tenant_id=tenant.tenant_id)',
            f'    list_all = lambda: store.list_all("{entity}", tenant_id=tenant.tenant_id)',
            e["body"],
            "",
            "",
            f'@router.get("/{name}")',
            f"def {name}_list(request: Request) -> Dict[str, Any]:",
            "    from app.auth import require_platform_token",
            "    tenant = require_platform_token(request)",
            "    # F7: filter/sort/page from the entity's own declared columns.",
            "    # An unrecognised query field is refused rather than ignored --",
            "    # silently dropping ?staus=open returns the whole table and looks",
            "    # like a match.",
            '    CONTROLS = {"limit", "offset", "sort", "order"}',
            f'    allowed = set(store.COLUMNS["{entity}"]) | CONTROLS',
            "    given = dict(request.query_params)",
            "    unknown = sorted(k for k in given if k not in allowed)",
            "    if unknown:",
            '        return {"ok": False,',
            '                "error": "unknown query field(s): " + ", ".join(unknown)}',
            "    try:",
            f'        return store.query("{entity}", tenant.tenant_id,',
            "            filters={k: v for k, v in given.items() if k not in CONTROLS},",
            '            sort=given.get("sort"),',
            '            order=given.get("order", "asc"),',
            '            limit=int(given.get("limit", 50)),',
            '            offset=int(given.get("offset", 0)))',
            "    except (store.QueryError, ValueError) as exc:",
            '        return {"ok": False, "error": str(exc)}',
            "",
            "",
            f'@router.get("/{name}/{{item_id}}")',
            f"def {name}_get(item_id: int, request: Request) -> Dict[str, Any]:",
            "    from app.auth import require_platform_token",
            "    tenant = require_platform_token(request)",
            f'    record = store.get("{entity}", item_id, tenant.tenant_id)',
            "    if record is None:",
            '        raise HTTPException(status_code=404, detail="not found")',
            "    return record",
            "",
            "",
            f'@router.put("/{name}/{{item_id}}")',
            f"async def {name}_update(item_id: int, payload: Dict[str, Any], request: Request) -> Dict[str, Any]:",
            "    from app.auth import require_platform_token",
            "    require_platform_token(request)",
            "    from app.kernel_bridge import product_context",
            f'    result = await perform_domain("update", "{e["capability_id"]}", '
            "{**(payload or {}), 'id': item_id}, context=product_context(request))",
            "    if result.get('status') != 'success':",
            "        return {'ok': False,",
            "                'error': result.get('error_message') or result.get('status'),",
            "                'result': result}",
            "    return {'ok': True, 'capability': "
            f'"{e["capability_id"]}", \'result\': result}}',
            "",
            "",
            f'@router.delete("/{name}/{{item_id}}")',
            f"async def {name}_delete(item_id: int, request: Request) -> Dict[str, Any]:",
            "    from app.auth import require_platform_token",
            "    require_platform_token(request)",
            "    from app.kernel_bridge import product_context",
            f'    result = await perform_domain("delete", "{e["capability_id"]}", '
            "{'id': item_id}, context=product_context(request))",
            "    if result.get('status') != 'success':",
            "        return {'ok': False,",
            "                'error': result.get('error_message') or result.get('status'),",
            "                'result': result}",
            "    return {'ok': True, 'capability': "
            f'"{e["capability_id"]}", \'result\': result}}',
            "",
            "",
        ]
    out += [
        "@router.post(\"/work_queue\")",
        "async def work_queue_enqueue(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:",
        "    from app.auth import require_platform_token",
        "    require_platform_token(request)",
        "    from app.kernel_bridge import product_context",
        "    result = await perform_domain(",
        '        "enqueue", str((payload or {}).get("capability_id") or ""), payload or {},',
        "        context=product_context(request),",
        "    )",
        "    if result.get('status') != 'success':",
        "        return {'ok': False,",
        "                'error': result.get('error_message') or result.get('status'),",
        "                'result': result}",
        "    return {'ok': True, 'result': result}",
        "",
        "",
        '@router.post("/work_queue/{item_id}/process")',
        "async def work_queue_process(item_id: int, request: Request) -> Dict[str, Any]:",
        "    from app.auth import require_platform_token",
        "    require_platform_token(request)",
        "    from app.kernel_bridge import product_context",
        '    result = await perform_domain("process", "", {"id": item_id}, context=product_context(request))',
        "    if result.get('status') != 'success':",
        "        return {'ok': False,",
        "                'error': result.get('error_message') or result.get('status'),",
        "                'result': result}",
        "    return {'ok': True, 'result': result}",
        "",
        "",
        '@router.get("/work_queue")',
        "def work_queue_list(request: Request) -> Dict[str, Any]:",
        "    from app.auth import require_platform_token",
        "    tenant = require_platform_token(request)",
        "    from app import work_queue as _work_queue",
        '    return {"items": _work_queue.list_all(tenant_id=tenant.tenant_id)}',
        "",
        "",
    ]
    return "\n".join(out)


def _render_main(product_name: str, vertical: str = "") -> str:
    from app.factory.build.deploy import render_main

    # Pass vertical through: the ingestion mount is gated on the
    # inventory exclusion set (Phase 2 §1).
    return render_main(product_name, vertical)


#: Distributions the factory's own runtime lane declares. Named so the
#: vendored-dependency pass can dedupe against them instead of emitting a
#: second, conflicting line for the same package.
_RUNTIME_DISTRIBUTIONS = (
    "fastapi", "uvicorn", "pydantic", "alembic", "sqlalchemy", "starlette", "psycopg",
)


def _requirement_name(line: str) -> str:
    """``python-multipart>=0.0.9`` -> ``python-multipart``."""
    return re.split(r"[<>=!~\[; ]", line.strip(), 1)[0].strip().lower().replace("_", "-")


def _render_requirements(
    vendored_deps: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> str:
    """The product's runtime declarations.

    ``root`` is the product tree. Given it, the framework features the code
    actually uses are declared too -- form parsing, templates, session cookies.
    FastAPI does not declare their backing packages and nothing imports them by
    name, so without this scan they are absent in the image and the feature
    fails at the first request rather than at install.
    """
    from app.factory.build.dependency_pins import CONSTRAINTS_REL, pin
    from app.factory.build.framework_extras import framework_lines
    from app.factory.build.network_posture import POSTURE_ID

    extras = framework_lines(Path(root)) if root is not None else []
    extra_block = ("\n".join(extras) + "\n") if extras else ""
    already = _RUNTIME_DISTRIBUTIONS + tuple(
        _requirement_name(line) for line in extras if not line.startswith("#")
    )
    return (
        "# Runtime dependencies. Persistence is stdlib sqlite3 on purpose --\n"
        f"# the platform runs with no database server and no network ({POSTURE_ID}).\n"
        "# pydantic is required by the vendored cerebrum_product_kernel contract.\n"
        "# Alembic applies versioned schema at deploy against STORAGE_PATH.\n"
        "# starlette is imported directly by app/observe.py. FastAPI pulls it in\n"
        "# transitively, but a runtime module that imports a package must declare\n"
        "# it: relying on someone else's dependency graph breaks the moment that\n"
        "# graph changes, and F10 is exactly the class of defect where an import\n"
        "# is satisfied by accident rather than by declaration.\n"
        "# Exact versions from the Factory's one pin table (dependency_pins):\n"
        "# an open range resolves to whatever was released that day, and the\n"
        f"# same build stops reproducing. {CONSTRAINTS_REL} bounds the rest.\n"
        f"{pin('fastapi')}\n"
        f"{pin('uvicorn')}\n"
        f"{pin('pydantic')}\n"
        f"{pin('alembic')}\n"
        f"{pin('sqlalchemy')}\n"
        f"{pin('starlette')}\n"
        "# The Postgres driver app/db.py uses when the operator sets DATABASE_URL.\n"
        "# Without it the variable is accepted and then cannot be dialled --\n"
        "# postgres_boot_200 on the acceptance floor measures exactly that.\n"
        f"{pin('psycopg', extras='binary')}\n"
        + extra_block
    ) + render_dependency_lines(
        vendored_deps or {}, already=already
    )


# -- deploy scaffold ------------------------------------------------------
#
# Templated, not coder-written, and deliberately so. Container and process
# config is mechanical: there is no domain judgement for an agent to
# contribute, and a hallucinated base image or start command is a deployment
# failure rather than a test failure. The dual path exists for artifacts where
# the agent knows something the template cannot.


def _render_dockerfile() -> str:
    from app.factory.build.dependency_pins import CONSTRAINTS_REL
    from app.factory.build.network_posture import NETWORK_POSTURE

    text = (
        "# Standalone image. Blocks are vendored into the repository at build\n"
        f"# time. Network posture: {NETWORK_POSTURE} — no outbound at runtime.\n"
        f"# Base image pin: Docker Hub library/python:3.12-slim (2026-08-23).\n"
        f"FROM {PYTHON_312_SLIM_FROM}\n"
        "WORKDIR /app\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
        "\n"
        f"# Installs against the Factory's pinned {CONSTRAINTS_REL} (one source:\n"
        "# dependency_pins), so every build resolves the same framework.\n"
        f"COPY requirements.txt {CONSTRAINTS_REL} ./\n"
        f"RUN pip install --no-cache-dir -c {CONSTRAINTS_REL} -r requirements.txt\n"
        "COPY requirements-dev.txt .\n"
        f"RUN pip install --no-cache-dir -c {CONSTRAINTS_REL} -r requirements-dev.txt\n"
        "\n"
        "COPY . .\n"
        "ENV PYTHONPATH=/app\n"
        "# Persistence is a sqlite file on the mounted disk (F23).\n"
        "ENV STORAGE_PATH=/app/data\n"
        "RUN mkdir -p /app/data\n"
        "\n"
        "# F19: a red suite must not produce a deployable image.\n"
        "RUN python3 scripts/release_gate.py\n"
        "\n"
        "EXPOSE 8000\n"
        "HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 "
        'CMD python3 -c "import urllib.request; urllib.request.urlopen('
        "'http://127.0.0.1:8000/health')\"\n"
        "\n"
        "# S10: migrate against the persistent disk, then serve. Failure refuses boot.\n"
        "# scripts/entrypoint.sh: alembic upgrade head && uvicorn app.main:app\n"
        'ENTRYPOINT ["sh", "/app/scripts/entrypoint.sh"]\n'
    )
    assert_generated_dockerfile(text)
    return text


def _render_dockerignore() -> str:
    return (
        "__pycache__/\n"
        "*.py[cod]\n"
        ".pytest_cache/\n"
        "data/\n"
        ".env\n"
        "build_ledger.jsonl\n"
    )


def _render_dev_requirements() -> str:
    """What scripts/release_gate.py needs in order to run.

    Kept out of requirements.txt so the delivered runtime stays lean, but it
    must EXIST: the artifact ships a clone-and-test gate and never declared
    the test runner that gate depends on. The identical omission in the
    factory's own production image made every live build fail its TESTER
    gate with "suite is red" and zero findings.
    """
    from app.factory.build.dependency_pins import CONSTRAINTS_REL, TEST_ONLY, pin

    return (
        "# Needed by scripts/release_gate.py and tests/. Pinned from the\n"
        "# Factory's one table (dependency_pins), like the runtime lines.\n"
        f"#   pip install -c {CONSTRAINTS_REL} -r requirements-dev.txt\n"
        + "".join(pin(dist) + "\n" for dist in TEST_ONLY)
    )


def _render_release_gate(product_name: str) -> str:
    """The clone-and-test contract, ported from the template path.

    A customer clones the delivered repository, runs one script, and watches
    the platform prove itself. The template generator has always shipped
    this; the runner's artifact must too, or the cutover would quietly drop
    a customer-facing promise. It additionally reports which artifacts the
    coding agent wrote, so provenance is auditable without the factory.
    """
    return f'''#!/usr/bin/env python3
"""Release gate for {product_name}.

Runs the platform's own *code-phase* suite (pytest -m "not pilot") and
prints a PASS/FAIL verdict. Store-backed execute-all lives on
@pytest.mark.pilot and is a later phase, not this gate.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

{RENDERED_MARKER_READER}

def main() -> int:
    print("== {product_name} — release gate ==")
    try:
        import pytest  # noqa: F401
    except ImportError:
        print("pytest is not installed, so the suite cannot be run.")
        print("  pip install -r requirements-dev.txt")
        print("VERDICT: CANNOT RUN")
        return 2
    # Literal sys.executable. Extra braces make {{sys.executable}} a set,
    # which compiles then TypeError's in Popen (py_compile cannot catch it).
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-m", "not pilot"],
        cwd=ROOT,
    )
    ok = result.returncode == 0

    lock = ROOT / "blocks.lock.json"
    if lock.is_file():
        data = json.loads(lock.read_text(encoding="utf-8"))
        blocks = data.get("blocks", {{}})
        print(f"vendored blocks: {{len(blocks)}}")
        for bid, meta in sorted(blocks.items()):
            print(f"  {{bid}} @ {{str(meta.get('commit'))[:16]}} ({{meta.get('source')}})")
        runtime = data.get("runtime")
        if runtime:
            print(f"store runtime slice: {{len(runtime.get('files', []))}} file(s) "
                  f"@ {{str(runtime.get('commit'))[:16]}}")
    else:
        print("blocks.lock.json: MISSING — provenance of vendored blocks is unknown")
        ok = False

    manifest = ROOT / "docs" / "build_provenance.json"
    if manifest.is_file():
        prov = json.loads(manifest.read_text(encoding="utf-8"))
        sources = prov.get("artifact_sources", {{}})
        agent = sorted(
            k
            for k, v in sources.items()
            # The Factory's one authorship vocabulary (authorship.py).
            if str(v).startswith({tuple(AGENT_SOURCE_PREFIXES)!r})
        )
        print(f"artifacts: {{len(sources)}} total, {{len(agent)}} written by the coding agent")
    else:
        # docs/build_provenance.json is the factory's own record and does not
        # ship (builds_push.FACTORY_INTERNAL_PATHS), so a delivered platform
        # never has it. Demanding it here failed the image build of every
        # platform at Dockerfile's release-gate step -- a suite with 108
        # passing tests was reported as 0/13 because acceptance never ran.
        # Report authorship from what IS in the tree: the stamp each handler
        # carries in its own source. Reported, not judged -- the acceptance
        # floor's authorship_floor check is the one place that is decided.
        actions = ROOT / "app" / "actions"
        handlers = [
            p for p in sorted(actions.glob("*.py")) if not p.name.startswith("_")
        ] if actions.is_dir() else []
        # The SAME rule the acceptance floor's authorship_floor uses: each
        # handler's module-level {AUTHORSHIP_MARKER} assignment, read from the
        # syntax tree by a reader rendered from the Factory's canonical one,
        # so the delivered tree needs no app/factory import. A docstring
        # sentence decides nothing.
        _agent_prefixes = {tuple(AGENT_SOURCE_PREFIXES)!r}
        _agent_exact = {set(AGENT_SOURCE_EXACT)!r}
        stamped = 0
        for path in handlers:
            _src = _authored_by(path.read_text(encoding="utf-8", errors="replace"))
            if _src and (_src.startswith(_agent_prefixes) or _src.lower() in _agent_exact):
                stamped += 1
        print(f"handlers: {{len(handlers)}} total, {{stamped}} stamped by the coding agent")

    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


def _render_procfile() -> str:
    return (
        "web: python -m alembic upgrade head && "
        "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}\n"
    )


def _render_platform_env_example() -> str:
    """P1_OFFLINE_STRICT: no store URL, no LLM keys, no Ollama.

    The template generator's .env.example documents CEREBRUM_API_URL because
    its handlers POST to the block store (S6 declared leftover). This
    platform's handlers import vendored blocks, so a store or cloud-LLM
    variable here would be a lie about how it runs.
    """
    from app.factory.build.network_posture import P1_ENV_EXAMPLE

    return P1_ENV_EXAMPLE


def _deploy_contract(product_id: str) -> Dict[str, Any]:
    """What this platform needs in order to run, stated once.

    Render supplied five things no config file mentioned -- PORT, DATABASE_URL,
    a persistent disk, RENDER_GIT_COMMIT and auto-linked datastores -- and the
    hosting migration found each one by breaking. A delivered platform must not
    inherit that trick, so its requirements are written down here and every
    deploy artifact is DERIVED from this dict rather than restating it. A second
    copy is how a contract goes stale while still reading as authoritative.
    """
    from app.factory.build.network_posture import NETWORK_POSTURE

    slug = re.sub(r"[^a-z0-9-]+", "-", str(product_id).lower()).strip("-") or "platform"
    return {
        "schema": "cerebrum.deploy.v1",
        "service": slug,
        "port": 8000,
        "health_path": "/health",
        "persistent_volume": {
            "required": True,
            "mount_path": "/app/data",
            "size_gb": 1,
            "reason": (
                "the platform's sqlite database and its backups live here. On a "
                "runtime with ephemeral storage -- an ECS task, a plain container "
                "-- every deploy silently discards all rows unless this path is a "
                "mounted volume."
            ),
        },
        "environment": {"STORAGE_PATH": "/app/data"},
        # No Postgres and no key-value store: persistence is a sqlite file
        # (posture: NETWORK_POSTURE). Provisioning either would bill for
        # infrastructure the platform never opens.
        "datastores": [],
        "network_posture": NETWORK_POSTURE,
    }


def _render_deploy_contract(product_id: str) -> str:
    return json.dumps(_deploy_contract(product_id), indent=2, sort_keys=True) + "\n"


def _render_aws_task_definition(product_id: str) -> str:
    """An ECS Fargate task definition, derived from the contract.

    ECS injects no PORT and gives the task no disk of its own, which is exactly
    where the Render assumptions bite. So the port is passed explicitly and the
    volume is an EFS mount at the contract's own path. Account-specific values
    are REPLACE_WITH_ placeholders: inventing an ARN or a filesystem id here
    would produce a file that looks deployable and is not.
    """
    contract = _deploy_contract(product_id)
    slug = contract["service"]
    mount = contract["persistent_volume"]["mount_path"]
    port = int(contract["port"])
    health = contract["health_path"]
    environment = [
        {"name": key, "value": value}
        for key, value in sorted(contract["environment"].items())
    ]
    # Injected by Render and by nothing else. State it.
    environment.append({"name": "PORT", "value": str(port)})
    probe = (
        "python3 -c \"import urllib.request; urllib.request.urlopen("
        "'http://127.0.0.1:{port}{health}')\" || exit 1"
    ).format(port=port, health=health)
    task = {
        "family": slug,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "512",
        "memory": "1024",
        "executionRoleArn": "REPLACE_WITH_EXECUTION_ROLE_ARN",
        "taskRoleArn": "REPLACE_WITH_TASK_ROLE_ARN",
        "volumes": [
            {
                "name": slug + "-data",
                "efsVolumeConfiguration": {
                    "fileSystemId": "REPLACE_WITH_EFS_FILESYSTEM_ID",
                    "rootDirectory": "/",
                    "transitEncryption": "ENABLED",
                },
            }
        ],
        "containerDefinitions": [
            {
                "name": slug,
                "image": "REPLACE_WITH_ECR_IMAGE_URI",
                "essential": True,
                "portMappings": [{"containerPort": port, "protocol": "tcp"}],
                "environment": environment,
                "mountPoints": [
                    {
                        "sourceVolume": slug + "-data",
                        "containerPath": mount,
                        "readOnly": False,
                    }
                ],
                "healthCheck": {
                    "command": ["CMD-SHELL", probe],
                    "interval": 10,
                    "timeout": 3,
                    "retries": 3,
                    "startPeriod": 20,
                },
            }
        ],
    }
    return json.dumps(task, indent=2, sort_keys=True) + "\n"


def _render_deploy_readme(product_id: str) -> str:
    contract = _deploy_contract(product_id)
    volume = contract["persistent_volume"]
    return (
        "# Deploying {service}\n"
        "\n"
        "`contract.json` is the source: port, health path, volume and\n"
        "environment. Every file beside it is generated from those values, so\n"
        "change the contract rather than a copy of it.\n"
        "\n"
        "## What this platform requires\n"
        "\n"
        "- **Port `{port}`.** The container reads `$PORT` and falls back to\n"
        "  `{port}`. Some runtimes inject `PORT`; most do not. Pass it.\n"
        "- **Health check `{health}`.** It is fail-closed -- 503 until the\n"
        "  database answers and the migration is at head -- so allow a start\n"
        "  period before the first probe counts.\n"
        "- **A persistent volume at `{mount}`** ({size} GiB is enough).\n"
        "  {reason}\n"
        "- **No database service and no key-value store.** Persistence is a\n"
        "  sqlite file on that volume; provisioning either would bill for\n"
        "  infrastructure this platform never opens.\n"
        "\n"
        "## AWS (ECS Fargate)\n"
        "\n"
        "`aws/task-definition.json` mounts EFS at the contract's path, because\n"
        "an ECS task has no disk of its own. Replace every `REPLACE_WITH_`\n"
        "value, register the definition, then roll the service.\n"
        "\n"
        "## Anywhere else\n"
        "\n"
        "The image is an ordinary container. Honour the four requirements above\n"
        "and it runs; miss the volume and it runs until the first redeploy.\n"
    ).format(
        service=contract["service"],
        port=contract["port"],
        health=contract["health_path"],
        mount=volume["mount_path"],
        size=volume["size_gb"],
        reason=volume["reason"],
    )


def _kernel_http_readme_section() -> str:
    rows = []
    for job in jobs_manifest():
        routes = ", ".join(f"`{r}`" for r in job["http_routes"])
        rows.append(
            f"| `{job['kernel']}` | {job['title']} | {job['agent']} | {routes} |"
        )
    table = "\n".join(rows)
    return f"""

## Kernel jobs and HTTP

`GET /v1/jobs` lists every kernel's job description. Distinctive surfaces:

| Kernel | Job | Agent | Routes |
|---|---|---|---|
{table}

These routes inspect the manufactured platform. They do not re-run the factory
and they do not execute the test suite (`GET /v1/gates` describes coverage only).
"""


def _templated_readme(product_name: str, caps: Sequence[str], blocks: Sequence[str]) -> str:
    from app.factory.build.network_posture import NETWORK_POSTURE

    cap_lines = "\n".join(f"- `{c}`" for c in caps) or "- (none)"
    block_lines = "\n".join(f"- `{b}`" for b in blocks) or "- (none)"
    return f"""# {product_name}

Generated by the CerebrumDev factory role runner.

## What this is

A standalone platform. Every capability runs in-process: handlers invoke
blocks that were vendored into `vendor/blocks/` at build time, through
`app/dispatch.py`. There is **no call back to a block store at runtime** — the
platform runs with the factory switched off (`{NETWORK_POSTURE}`).

## Capabilities

{cap_lines}

## Vendored blocks

{block_lines}

## Run it

```bash
python3 -m venv .venv
.venv/bin/pip install -c constraints.txt -r requirements.txt -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload      # GET /health -> 200; GET /v1/jobs -> kernel JDs
.venv/bin/python -m pytest tests -m "not pilot"   # factory code-phase gate
.venv/bin/python -m pytest tests                   # includes Store-backed @pytest.mark.pilot
# or: .venv/bin/python scripts/release_gate.py
```

Data lands in `$STORAGE_PATH/platform.db` (default `./data`), stdlib sqlite3.
Schema is Alembic (`alembic upgrade head` at deploy). `app/store.py` does not
`CREATE TABLE`.

## Layout

| Path | Purpose |
|---|---|
| `app/jobs.py` | kernel job descriptions (`GET /v1/jobs` and friends) |
| `app/models.py` | domain dataclasses |
| `app/store.py` | sqlite persistence (WAL; no DDL) |
| `app/migrations.py` | Alembic upgrade / downgrade |
| `app/backup.py` | backup / restore / retention |
| `alembic/` | versioned up and down revisions |
| `app/routes.py` | HTTP surface |
| `app/actions/` | capability handlers |
| `app/dispatch.py` | local block dispatch |
| `vendor/blocks/` | vendored block source, pinned by `blocks.lock.json` |
| `kits/` | kit packs for the capabilities (Factory shelf / Blocks kits) |
"""


def _vendored_manifest(ctx: RoleContext, block_id: str) -> Dict[str, Any]:
    """The block.json the CLONER vendored for ``block_id``; the Store's own
    manifest when the workspace carries none."""
    meta_rel = Path("vendor") / "blocks" / block_id / "block.json"
    if ctx.workspace.exists(meta_rel):
        try:
            meta = json.loads(ctx.workspace.read_text(meta_rel))
        except (ValueError, OSError):
            meta = None
        if isinstance(meta, dict):
            return meta
    from app.factory.store_kits import block_manifest

    return block_manifest(block_id)


def vendored_capability_classes(ctx: RoleContext, block_ids: Sequence[str]) -> Dict[str, str]:
    """block id -> the ``capability_class`` its vendored manifest declares;
    the pinned Store's manifest when the vendored copy predates the field."""
    from app.factory.store_kits import block_capability_class, capability_class

    out: Dict[str, str] = {}
    for bid in block_ids or ():
        cls = capability_class(_vendored_manifest(ctx, bid)) or block_capability_class(bid)
        if cls:
            out[bid] = cls
    return out


def _block_contract(ctx: RoleContext, block_id: str) -> Dict[str, Any]:
    """What this vendored block actually accepts, gathered at build time.

    Two sources, both already in the workspace: the block.json the CLONER
    vendored (declared inputs, default action, action options) and -- for
    Store runtime blocks -- the ``input_schema`` required fields parsed from
    the vendored module. The first live build failed precisely here: handlers
    sent raw domain payloads to blocks that validate their input, and nothing
    told either the coder or the template what the blocks wanted.
    """
    contract: Dict[str, Any] = {"block_id": block_id}
    meta_rel = Path("vendor") / "blocks" / block_id / "block.json"
    if ctx.workspace.exists(meta_rel):
        try:
            meta = json.loads(ctx.workspace.read_text(meta_rel))
        except (ValueError, OSError):
            meta = {}
        declared = []
        for item in meta.get("inputs", []):
            name = item.get("name")
            if not name:
                continue
            if name == "action":
                if item.get("default") is not None:
                    contract["default_action"] = item["default"]
                if item.get("options"):
                    contract["action_options"] = list(item["options"])
                continue
            declared.append(
                {
                    "name": name,
                    "type": item.get("type"),
                    "required": bool(item.get("required")),
                }
            )
        if declared:
            contract["declared_inputs"] = declared

    module_rel = Path("vendor") / "cerebrum" / "blocks" / f"{block_id}.py"
    if ctx.workspace.exists(module_rel):
        source = ctx.workspace.read_text(module_rel)
        harvested = _harvest_block_source(source)
        if harvested["required"]:
            contract["input_required_fields"] = harvested["required"]
        # Blocks self-document their per-action requirements in the error
        # literals they answer with ("user_id and name required", "metric and
        # value required", ...). Three live builds discovered these one 429
        # at a time; harvesting them into the contract lets the coder satisfy
        # them before the first attempt instead of after the third.
        if harvested["errors"]:
            contract["runtime_error_contracts"] = sorted(set(harvested["errors"]))[:25]
        # WORKAROUND -- removal tracked in CerebrumDev.ai#256.
        #
        # The dict keys the block's code reads from its input are its de
        # facto vocabulary. A pipeline step written as {"block_id": ...}
        # failed with "No block specified" because the workflow block reads
        # step.get("block") -- knowledge that sat in the vendored source and
        # nowhere else, which is why this harvest exists at all.
        #
        # A .get("x") anywhere in the module harvests identically whether x
        # is a payload key, a result key, a config key or an env key; a key
        # read through a variable (payload.get(field)) is invisible to it;
        # and the cap of 40 is arbitrary. Only the kit author knows which
        # keys are the contract.
        #
        # The design is Cerebrum-Blocks#90 (Lane 2 / L2.2): block.json
        # declaring requires_inputs. Until it lands AND the vendored blocks
        # fill it in -- it ships optional and empty on purpose -- a declared
        # value wins over this one. The two are never merged silently.
        if harvested["keys_read"]:
            contract["input_keys_read_by_block"] = sorted(set(harvested["keys_read"]))[:40]
    return contract


def _budget_too_low(ctx: RoleContext, what: str) -> bool:
    """True when too little build budget remains for another coder call.

    Records the skip so the artifact's provenance says the agent was not
    used and why -- an artifact quietly templated because time ran out is
    exactly the invisible degradation the factory refuses.

    A stage-inspect hard-stop must not stub remaining capabilities as a
    thin SUCCESS: raise so the run fails closed with the inspect snapshot.
    """
    halt = (ctx.state or {}).get("stage_halt")
    if isinstance(halt, dict) and halt.get("decision") == "hard_stop":
        raise RoleError(
            halt.get("reason")
            or f"stage inspect hard-stop before {what}"
        )
    # Still asked per call, on purpose. ``loop_budget_too_low`` decides once,
    # before the loop, whether the agent is affordable at all; this stays as
    # the wall a long loop can still hit mid-way. Suppressing it here would
    # turn a loop that overruns from "the tail is templated, and the artifact
    # says so" into "the phase blows its wall and the build fails".
    left = ctx.coder_time_left()
    if left is None:
        return False
    from app.factory.coder import _call_timeout_s

    # One successful post has to finish inside the remaining phase wall.
    # Requiring two full legs here skipped every handler once the
    # per-attempt timeout grew past the old 25-minute code-phase cap.
    needed = _call_timeout_s() + 30
    if left > needed:
        return False
    ctx.state.setdefault("coder_failures", {})[what] = (
        f"skipped: {int(max(left, 0))}s of build budget left, a {what} call "
        f"needs up to {int(needed)}s"
    )
    from app.factory.build.failure_kinds import BUDGET_SKIPPED, record_failure_kind

    record_failure_kind(ctx.state, what, BUDGET_SKIPPED)
    ctx.note(f"coder skipped for {what} — build budget nearly spent", stage="budget")
    return True


def _note_model_call(ctx: RoleContext, what: str, **payload: Any) -> None:
    """Ledger NOTE that a coder LLM call is in flight — not progress.

    ``done`` is left to the caller so a hung call cannot look like a
    finished handler. ``model_call`` + ``deadline_s`` let build_status
    fail the Floor once the watchdog wall is exceeded instead of climbing
    "quiet for N min — model call may still be running".
    """
    from app.factory.coder import _attempt_wall_s

    ctx.note(
        f"calling coder LLM for {what}",
        model_call=True,
        deadline_s=_attempt_wall_s(),
        **payload,
    )


def _coder_body(
    ctx: RoleContext,
    cap: Any,
    usable: Sequence[str],
    spec: Optional[Dict[str, Any]] = None,
    previous_attempt: Optional[str] = None,
) -> Optional[tuple]:
    """Ask the coding agent for this handler's body, or None if unavailable.

    Returns ``(body, source)``. None means the agent could not be used --
    disabled, unconfigured, or it failed its validation gate. The caller then
    ships the deterministic body and records *which* path produced it, so the
    artifact never implies authorship it did not have.

    ``spec`` is the capability's data model. Without it the coder invents its
    own required payload fields, and the suite -- which builds payloads from
    the spec -- rejects the handler for demanding fields no caller sends.
    """
    from app.factory.coder import (
        CoderError,
        CoderTimeout,
        coder_enabled,
        generate_platform_handler,
    )

    if not coder_enabled():
        return None
    if _budget_too_low(ctx, "handler"):
        return None
    _note_model_call(
        ctx,
        cap.capability_id,
        stage="coder",
        capability=cap.capability_id,
    )
    try:
        result = generate_platform_handler(
            capability_id=cap.capability_id,
            description=getattr(cap, "notes", "") or cap.capability_id,
            block_ids=list(usable),
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
            work_list=list(ctx.work_list),
            block_contracts={b: _block_contract(ctx, b) for b in usable},
            model_fields=(spec or {}).get("fields"),
            resource_obligations=describe_resource_obligations(usable),
            previous_attempt=previous_attempt,
            vendored_roster=sorted(ctx.state.get("vendored_blocks", ())),
        )
    except CoderTimeout as exc:
        ctx.state.setdefault("coder_failures", {})[cap.capability_id] = str(exc)
        record_failure_kind(ctx.state, cap.capability_id, failure_kind(exc))
        raise RoleError(
            f"coder LLM timed out writing handler {cap.capability_id}: {exc}"
        ) from exc
    except CoderError as exc:
        # Degraded output is acceptable; invisible degradation is not.
        ctx.state.setdefault("coder_failures", {})[cap.capability_id] = str(exc)
        return None
    return result["body"], f"coder LLM ({result['model']})"


def _record_failure(ctx: RoleContext, key: str, exc: Exception) -> None:
    ctx.state.setdefault("coder_failures", {})[key] = str(exc)
    record_failure_kind(ctx.state, key, failure_kind(exc))


def _coder_model_spec(ctx: RoleContext, cap: Any) -> Optional[Dict[str, Any]]:
    """Let the agent design the schema. None means fall back to the template."""
    from app.factory.coder import (
        CoderError,
        CoderTimeout,
        coder_enabled,
        generate_model_spec,
    )

    if not coder_enabled():
        return None
    if _budget_too_low(ctx, "model spec"):
        return None
    _note_model_call(
        ctx,
        f"model spec {cap.capability_id}",
        stage="coder",
        capability=cap.capability_id,
    )
    try:
        return generate_model_spec(
            capability_id=cap.capability_id,
            description=getattr(cap, "notes", "") or cap.capability_id,
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
        )
    except CoderTimeout as exc:
        _record_failure(ctx, f"model:{cap.capability_id}", exc)
        raise RoleError(
            f"coder LLM timed out writing model spec {cap.capability_id}: {exc}"
        ) from exc
    except CoderError as exc:
        _record_failure(ctx, f"model:{cap.capability_id}", exc)
        return None


def _coder_route_body(
    ctx: RoleContext,
    cap: Any,
    spec: Dict[str, Any],
    previous_attempt: Optional[str] = None,
) -> Optional[tuple]:
    """Capability HTTP routes go through execute_action (U12).

    An LLM-authored body would displace the kernel. Handlers and models may
    still be coder-written; the route is not. This is not a persist-wrapper.
    """
    return None


def _coder_readme(
    ctx: RoleContext, product_name: str, caps: Sequence[str], blocks: Sequence[str]
) -> Optional[tuple]:
    """Prose is the one artifact where the template is nearly as good.

    Kept on the dual path anyway so every artifact class is consistent, but a
    failure here is uninteresting and silently templated.
    """
    from app.factory.build.coder_session import deepseek_cli_ready
    from app.factory.coder import CoderError, coder_enabled

    if not coder_enabled():
        return None
    if deepseek_cli_ready():
        return None
    try:
        from app.factory.coder import _llm_code_call

        text, model_used = _llm_code_call(
            [
                {
                    "role": "system",
                    "content": (
                        "Write a concise README.md for a generated business platform. "
                        "Markdown only, no code fences around the whole document. "
                        "Cover: what it does, its capabilities, how to run it "
                        "(python3 -m venv .venv; "
                        ".venv/bin/pip install -c constraints.txt -r requirements.txt -r requirements-dev.txt; "
                        ".venv/bin/uvicorn app.main:app; GET /health and GET /v1/jobs; "
                        ".venv/bin/python -m pytest tests -m \"not pilot\"), "
                        "and that it runs fully offline with blocks vendored into "
                        "vendor/blocks/ and no call back to any store. Do not invent "
                        "an HTTP table — a kernel-jobs section is appended for you."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Platform: {product_name}\n"
                        f"Capabilities: {list(caps)!r}\n"
                        f"Vendored blocks: {list(blocks)!r}"
                    ),
                },
            ]
        )
    except CoderError as exc:
        _record_failure(ctx, "readme", exc)
        return None
    if not text.strip():
        return None
    return text, f"coder LLM ({model_used})"


def _vendor_product_kernel(ctx: RoleContext) -> None:
    """Copy the host kernel into the generated product (U12).

    Same source ProductGenerator vendors at generator.py. role_runner previously
    shipped none of it. Imports already use ``app.cerebrum_product_kernel``.
    """
    kernel_src = Path(__file__).resolve().parents[2] / "cerebrum_product_kernel"
    if not kernel_src.is_dir():
        raise RoleError(
            "cerebrum_product_kernel missing from factory host — cannot ship U12"
        )
    for item in sorted(kernel_src.rglob("*")):
        if not item.is_file():
            continue
        if "__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"}:
            continue
        rel = Path("app") / "cerebrum_product_kernel" / item.relative_to(kernel_src)
        ctx.workspace.copy_file(item, rel)



def _emit_reasoning_socket(ctx) -> None:
    """Emit the universal reasoning socket into the product under build."""
    from app.factory.build import reasoning_socket

    written = reasoning_socket.emit(ctx)
    # Ask the Store the same way ``emit`` just did, or this line names no kit for
    # a kit the alias table has no entry for — reporting "kit 'None' vendored"
    # beside the four files it vendored.
    from app.factory.blocks_source import resolve_blocks_root

    try:
        store_root = resolve_blocks_root()
    except Exception:  # noqa: BLE001 -- a log line must not fail a build
        store_root = None
    kit = reasoning_socket.kit_for_vertical(
        ctx.blueprint,
        getattr(ctx, "plan", None) and getattr(ctx.plan, "__dict__", None),
        store_root=store_root)
    vendored = [p for p in written if "/kit/" in p]
    if vendored:
        logger.info(
            "reasoning socket emitted (%d files); kit '%s' vendored: %s",
            len(written), kit, ", ".join(sorted(p.rsplit("/", 1)[-1] for p in vendored)),
        )
    else:
        # Said at ERROR, not info. A platform whose reasoning layer refuses every
        # figure is fail-closed and correct, and also gates nothing — that is not
        # something a build log should mention in passing.
        logger.error(
            "reasoning socket emitted (%d files) with NO KIT (%s). This platform will "
            "refuse every figure its rules need.",
            len(written), kit or "no kit maps to this vertical",
        )


def _render_kernel_bridge() -> str:
    """Adapt sync capability handle() to execute_action. Does not own persist."""
    return '''"""Bridge capability handle() through the vendored product kernel.

The kernel owns trust-scope, input/output validation, and ActionResult.
Persistence stays in the HTTP route after ActionStatus.SUCCESS.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List, Tuple

from app.cerebrum_product_kernel.contract.models import (
    ActionContext,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
)
from app.cerebrum_product_kernel.contract.runtime import execute_action


def _wrap_handle(handle):
    async def _handler(context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
        # Live invoice-management (2026-08-30): a coder handler returned
        # ok:True after execute() failed. Without watching the seam, this
        # wrapper treated that as ActionStatus.SUCCESS and the route
        # persisted. A capability route must not report success over a
        # failed block, whichever path wrote the handler.
        import sys
        import app.dispatch as _dispatch

        _real = _dispatch.execute
        _failed = []

        def _watched(block_id, *a, **kw):
            res = _real(block_id, *a, **kw)
            if isinstance(res, dict) and (
                res.get("status") == "error" or "error" in res
            ):
                _failed.append(str(block_id))
            return res

        _dispatch.execute = _watched
        _patched = []
        for _name, _mod in list(sys.modules.items()):
            if _name.startswith("app.actions") and hasattr(_mod, "execute"):
                _patched.append((_mod, _mod.execute))
                _mod.execute = _watched
        try:
            out = handle(arguments)
        finally:
            _dispatch.execute = _real
            for _mod, _prev in _patched:
                _mod.execute = _prev
        if not isinstance(out, dict):
            return ActionOutcome(
                status=ActionStatus.EXECUTION_ERROR,
                error_code="invalid_handler_result",
                error_message="handle() returned a non-mapping",
            )
        if out.get("ok") is False:
            return ActionOutcome(
                status=ActionStatus.VALIDATION_ERROR,
                error_code="refused",
                error_message=str(out.get("error") or "refused"),
                output=out,
            )
        if _failed:
            return ActionOutcome(
                status=ActionStatus.EXECUTION_ERROR,
                error_code="block_failed",
                error_message="block(s) failed: " + ", ".join(_failed),
                output=out,
            )
        return ActionOutcome.success(out)

    return _handler


def spec_for(capability_id: str) -> ActionSpec:
    name = capability_id.replace("-", "_")
    mod = importlib.import_module(f"app.actions.{name}")
    return ActionSpec(
        action_id=f"product.{name}",
        domain="product",
        name=name,
        description=str(getattr(mod, "CAPABILITY_ID", name)),
        # The capability's own domain columns. Without them the kernel strips
        # any column named like a trust-scope key -- project_id, user_id,
        # tenant_id -- and the handler refuses its own well-formed payload.
        input_schema={
            "properties": {
                str(f): {} for f in (getattr(mod, "CAPABILITY_FIELDS", ()) or ())
            }
        },
        output_schema={},
        required_context=[],
        permissions=[],
        read_only=False,
        handler=_wrap_handle(mod.handle),
    )


#: Tenancy speaks in roles; the kernel checks permissions. A role name is not
#: a permission: passing roles straight through left every caller holding
#: ["admin"] against operation specs that require product.write, so every
#: PUT and DELETE in every generated platform answered permission_denied
#: (13/13 in Docker -- no check wrote twice).
#: Unknown roles grant nothing.
ROLE_PERMISSIONS: Dict[str, Tuple[str, ...]] = {
    "admin": ("product.read", "product.write", "product.process"),
    "writer": ("product.read", "product.write"),
    "reader": ("product.read",),
}


def permissions_for_roles(roles) -> List[str]:
    granted: List[str] = []
    for role in roles or ():
        for permission in ROLE_PERMISSIONS.get(str(role), ()):
            if permission not in granted:
                granted.append(permission)
    return granted


def product_context(request) -> ActionContext:
    """Build the kernel ActionContext from the authenticated principal.

    The tenant is resolved from the request's bearer token by
    app.tenancy.resolve_tenant — the single resolution path shared with
    rag_routes. A caller that cannot be bound to a tenant is refused by
    name (401 authentication_required), never mapped to a default.
    """
    import app.tenancy as tenancy
    from fastapi import HTTPException

    try:
        tenant = tenancy.resolve_tenant(request.headers)
    except tenancy.TenantRefused:
        raise HTTPException(status_code=401, detail="authentication_required")
    return ActionContext(
        user_id=tenant.tenant_id,
        tenant_id=tenant.tenant_id,
        organisation_id=tenant.tenant_id,
        project_id=tenant.tenant_id,
        permissions=permissions_for_roles(getattr(tenant, "roles", ())),
        allowed_domains=["product"],
    )


async def run_capability(capability_id: str, payload: Dict[str, Any], request) -> Dict[str, Any]:
    result = await execute_action(spec_for(capability_id), product_context(request), payload or {})
    return result.to_dict()
'''


def _writer_block_roster(state: Dict[str, Any]) -> tuple:
    """Block ids this WRITER pass may emit preconditions for.

    Frozen at entry so later handler/domain/supply-chain work cannot expand
    the roster to the whole vendor mirror (CI saw analytics..workflow).
    A mapping is treated as id→path (kit_pack shape), not as 'use every key
    of RESOURCE_OBLIGATIONS / the factory shelf'.
    """
    raw = (state or {}).get("vendored_blocks") or ()
    if isinstance(raw, dict):
        raw = raw.keys()
    return tuple(sorted({str(b) for b in raw if b}))


def _dispatch_cli_keep_ids(dispatch: Any) -> Sequence[str]:
    """Handlers the CLI actually wrote — not factory-grounded hole-fill.

    An explicit empty ``cli_authored_ids`` after DeepSeek/kimi exit 0 must
    not fall back to ``kept_handler_ids`` (those may be factory fill).
    Billing keep-path still uses ``kept_handler_ids`` when the no-authorship
    blocker is unset.
    """
    from app.factory.build.coder_session import (
        NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS,
    )

    authored = list(getattr(dispatch, "cli_authored_ids", None) or [])
    if authored:
        return authored
    if getattr(dispatch, "blocker", None) in {
        NAMED_BLOCKER_CLI_NO_AUTHORSHIP,
        NAMED_BLOCKER_CLI_UNKEEPABLE_EVENT_BUS,
    }:
        return ()
    return list(getattr(dispatch, "kept_handler_ids", None) or [])


# WRITER dispatch control now lives in its own module; re-exported here so
# the existing importers of roles_handlers.writer_uses_codewhale keep working.
from app.factory.build.writer_control import writer_uses_codewhale  # noqa: F401,E402


def _compiled_writer_brief(ctx: RoleContext) -> str:
    """The full BRIEF the worker must follow — ALL sources, together:

    1. the user's own words from the Floor chat (threaded from the
       session as ``state['brief']``),
    2. the frozen compiled C-BRIEF (block contracts, REUSE inventory,
       gap list) CLONER wrote to docs/coder_brief.md,
    3. the blueprint's capability specs.

    Live builds shipped an EMPTY BRIEF section because nothing set
    ``state['brief']`` and the seam never read the compiled brief — the
    agent authored the platform blind from the one-line blueprint
    summary (sess_9f67681a79324fcc class).
    """
    sections: List[str] = []
    user_brief = str(ctx.state.get("brief") or "").strip()
    if user_brief:
        sections.append(f"USER BRIEF\n{user_brief}")
    coder_brief_rel = Path("docs") / "coder_brief.md"
    if ctx.workspace.exists(coder_brief_rel):
        text = ctx.workspace.read_text(coder_brief_rel).strip()
        if text:
            sections.append(f"COMPILED C-BRIEF\n{text}")
    cap_lines: List[str] = []
    for cap in getattr(ctx.blueprint, "capabilities", None) or []:
        cid = str(getattr(cap, "id", "") or "").strip()
        desc = str(getattr(cap, "description", "") or "").strip()
        if cid:
            cap_lines.append(f"- {cid}: {desc}".rstrip())
    if cap_lines:
        sections.append("CAPABILITIES\n" + "\n".join(cap_lines))
    if not sections:
        summary = str(getattr(ctx.blueprint, "summary", "") or "").strip()
        if summary:
            sections.append(summary)
    return "\n\n".join(sections)


def _writer_worker_timeout_s(ctx: RoleContext) -> Optional[float]:
    """The wall the WRITER subprocess is given, from the run's own budget.

    D1 (live 2026-09-29): ``run_worker_job`` was called with no ``timeout_s``,
    so ``worker_timeout_s()`` returned the flat ``DEFAULT_WORKER_TIMEOUT_S``
    (1800s) and killed the subprocess 30 minutes in -- even on a pilot run
    granted a 90-minute phase wall. Because the worker died right at the
    stage-1 boundary, the budget inspector found the CLI NOT in flight, took
    its ``else`` branch, saw ``written=0, stub_rate=1.0`` on a dead writer and
    hard-stopped: ``wall-clock budget of 1800s spent before WRITER completed``.

    The wall now tracks the phase budget (the same source the C-BRIEF path
    reads via ``cli_dispatch_timeout_s``): the remaining phase time minus a
    grace so the worker dies just BEFORE the phase wall -- keeping the writer
    in flight through the intermediate budget inspections, which is exactly
    what flips the stage-1 inspect from ``hard_stop`` to ``continue_stage_2``.
    Floored at the historical default so a code-only pass is never granted
    less than it had; ``None`` (unbounded) defers to the worker's own default.
    """
    from app.factory.build.codewhale_worker import DEFAULT_WORKER_TIMEOUT_S
    from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

    left = ctx.coder_time_left()
    if left is None:
        return None
    return max(float(DEFAULT_WORKER_TIMEOUT_S), float(left) - MODEL_CALL_GRACE_S)


def _writer_slot_wait_left(ctx: RoleContext) -> Callable[[], Optional[float]]:
    """Seconds this run may still wait for a build slot: up to its phase-wall
    CEILING (``deadline_box['ceiling_at']``, set by the runner) and never a
    separate, shorter timeout. ``None`` when the run has no ceiling."""

    def _left() -> Optional[float]:
        box = ctx.deadline_box or {}
        ceiling = box.get("ceiling_at")
        if ceiling is None:
            return None
        clock = box.get("clock")
        if not callable(clock):
            import time as _time

            clock = _time.monotonic
        return float(ceiling) - float(clock())

    return _left


def _writer_worker_live_time_left(ctx: RoleContext) -> Optional[Callable[[], Optional[float]]]:
    """The run's remaining budget as the worker reads it, live, every tick.

    ``ctx.coder_time_left()`` reads ``deadline_box['at']`` -- the one value
    the runner's budget ramp lifts (``_extend_wall``, clamped there to
    ``hard_ceiling_s``). Handing the worker a reader of that box, not a
    number taken at dispatch, is what lets a ramp reach the wait it was
    meant for. The same grace as ``_writer_worker_timeout_s`` keeps the
    worker dying just before the run's own wall. ``None`` when the run is
    unbounded: the worker keeps its dispatch wall.
    """
    from app.factory.llm_watchdog import MODEL_CALL_GRACE_S

    if ctx.coder_time_left() is None:
        return None

    def _left() -> Optional[float]:
        left = ctx.coder_time_left()
        if left is None:
            return None
        return float(left) - MODEL_CALL_GRACE_S

    return _left


def _run_writer_via_codewhale_worker(ctx: RoleContext) -> RoleResult:
    """Phase 5: the WRITER role runs headless through `codewhale exec`.

    The worker authors the checkout (the staged persist root, so the
    runner's normal commit flow owns the output); the receipt is recorded
    in the result notes. The writer-contract gate still judges the disk
    (Phase 0.5): the worker prompt instructs the agent to stamp every
    authored handler, so the disk-level agent count applies to worker
    output exactly as it applies to the in-process coder.
    """
    from app.factory.build.codewhale_worker import (
        SLOT_WAIT_CLOSED,
        SLOT_WAIT_EXHAUSTED,
        SLOT_WAIT_OPEN,
        WRITER_PROGRESS_FIELDS,
        WorkerError,
        is_narration_line,
        run_worker_job,
        writer_specialist_cap,
    )
    from app.factory.build.writer_prompt import render_writer_prompt

    dest = persist_workspace_root(ctx.workspace)
    from app.factory.build.image_sufficiency import locked_entries as image_locked_entries
    from app.factory.build.image_sufficiency import runtime_paths as image_runtime_paths

    product_tree = Path(getattr(ctx.workspace, "destination", None) or dest)
    prompt = render_writer_prompt(
        ctx.blueprint,
        brief=_compiled_writer_brief(ctx),
        resume=bool(ctx.state.get("writer_resumed")),
        # How many specialist agents this instance can carry at once. Read
        # here, not in the template: it follows the worker profile and the
        # operator override instead of going stale on the next plan.
        specialist_workers=writer_specialist_cap(),
        # What the image must carry, read off the PRODUCT tree's lock: the
        # writer's checkout is the staging tree, which does not show the
        # CLONER's stock, and two writers left it out of the image because
        # nothing named it (cycles 8 and 9). Only the ones the checkout does
        # not show: those are the ones the prompt says are absent from it.
        runtime_paths=[
            rel
            for rel in image_runtime_paths(product_tree)
            if not (Path(dest) / rel).exists()
        ],
        # Where each locked block must load from -- every one, shown in the
        # checkout or not. The WRITER gate refuses a block loaded from any
        # other copy; two cycle-9 writers did exactly that because the rule
        # was nowhere in the prompt (co-op, craft marketplace).
        locked_blocks=image_locked_entries(product_tree),
    )
    try:
        # The writer narrates itself on the Floor: CLI progress lines
        # become throttled ledger NOTEs (one per ~3s), so build-status
        # shows what the writer is doing instead of "quiet for N min".
        throttle = {"last": 0.0}

        def relay_progress(line: str, info: Any) -> None:
            import time as _time

            now = _time.monotonic()
            # The throttle exists for chatty CLI log lines. The agent's own
            # STEP lines and the factory heartbeat are the narration itself
            # and are never dropped: the agent writes its progress log in
            # bursts, and a 3s window kept the first line of each burst and
            # DISCARDED the rest (live: STEP 1 then STEP 21 -- nineteen steps
            # that happened and were never shown).
            # The worker's "CLI started -- model call in flight" note carries
            # {model_call, deadline_s, provider}. This relay used to keep the
            # text and drop the payload, and the payload is the ONLY thing
            # boot recovery (orphan_recovery) and the Floor's in-flight copy
            # look at: every CodeWhale build read "model_call: none", so a
            # server restart mid-WRITER was never auto-resumed (live:
            # FleetOps sat dead for 20+ minutes). Never throttle it either.
            call = relayed_call_fields(info)
            if not is_narration_line(line) and not call:
                if now - throttle["last"] < 3.0:
                    return
                throttle["last"] = now
            # The writer's own progress (steps reported, files on disk) rides
            # the NOTE as data: the budget ramp's "visible work" test reads
            # it, so a writer reporting steps is never killed at a stage wall
            # as if it were silent (live 2026-10-08: STEP 7 and STEP 8 landed
            # 3 minutes before the 2700s wall cut a 238-file pass).
            progress_fields = {
                key: info[key]
                for key in WRITER_PROGRESS_FIELDS
                if isinstance(info, dict) and info.get(key) is not None
            }
            ctx.note(
                line[:200],
                stage=str(info.get("tool") or "writer-cli"),
                source="codewhale_worker",
                **call,
                **progress_fields,
            )

        # No free build slot never fails the writer (owner, 2026-10-06): the
        # job QUEUES, first-in-first-out per tenant (the smoke principal is
        # served first), its place narrated as a typed NOTE the Floor and the
        # budget read. The wait is bounded only by the run's phase-wall
        # ceiling (live 2026-10-06: the post-deploy smoke died
        # "tenant_slots_exhausted" behind a second build on its account).
        slot_wait: Dict[str, Any] = {"last": 0.0, "position": None, "closed": False}

        def note_slot_wait(info: Dict[str, Any]) -> None:
            import time as _time

            now = _time.monotonic()
            if (
                info.get("position") == slot_wait["position"]
                and now - slot_wait["last"] < 60.0
            ):
                return
            slot_wait["last"] = now
            slot_wait["position"] = info.get("position")
            ctx.note(
                f"waiting for a build slot -- position {info.get('position')} "
                f"({info.get('ahead')} ahead)",
                stage="writer-slot-wait",
                source="codewhale_worker",
                slot_wait=SLOT_WAIT_OPEN,
                slot_queue={
                    "position": info.get("position"),
                    "ahead": info.get("ahead"),
                    "since_s": info.get("since_s"),
                },
            )

        def relay_after_slot(line: str, info: Any) -> None:
            # The job holds its slot once the worker is talking: close the
            # wait NOTE (only if one was opened) before the first line.
            if slot_wait["position"] is not None and not slot_wait["closed"]:
                slot_wait["closed"] = True
                ctx.note(
                    "build slot acquired",
                    stage="writer-slot-wait",
                    source="codewhale_worker",
                    slot_wait=SLOT_WAIT_CLOSED,
                )
            relay_progress(line, info)

        # Every Factory-owned file as the Factory left it, so a writer that
        # creates, edits or deletes one is caught here and sent to rework
        # by name -- not silently re-stamped or kept further down.
        from app.factory.build import factory_owned

        # The Factory's files first: a writer that finds them absent creates
        # them for a complete product (live cycle 5). Rendered, they are the
        # Factory's to keep and the writer's to leave alone.
        try:
            prestamped = factory_owned.prestamp(Path(dest), ctx.blueprint, ctx)
        except Exception as exc:  # noqa: BLE001 -- recorded; the gates still judge
            ctx.note(
                f"factory files not stamped before the writer: {type(exc).__name__}: {exc}",
                stage="factory-owned",
                source="factory",
            )
        else:
            if prestamped:
                ctx.note(
                    "factory files stamped before the writer: " + ", ".join(prestamped),
                    stage="factory-owned",
                    source="factory",
                )
        owned_before = factory_owned.snapshot(Path(dest))
        try:
            receipt = run_worker_job(
                prompt,
                dest,
                tenant_store=ctx.state.get("tenant_store"),
                # THIS build's own identity, so a concurrent build cannot hand
                # its session id to this writer child through the process env.
                session_id=str(ctx.state.get("session_id") or ""),
                product_id=str(
                    getattr(ctx.blueprint, "product_id", "") or ""
                ),
                progress=relay_after_slot,
                # D1: the wall is the run's remaining phase budget, not a flat
                # 1800s that kills the writer before the phase inspector's
                # in-flight bump can fire.
                timeout_s=_writer_worker_timeout_s(ctx),
                # ...and the wall follows the run's LIVE deadline, so the
                # budget ramp reaches this wait (live 2026-10-06: killed
                # at 1800s while producing work).
                live_time_left=_writer_worker_live_time_left(ctx),
                slot_wait_left=_writer_slot_wait_left(ctx),
                on_slot_wait=note_slot_wait,
            )
        except WorkerError as exc:
            if SLOT_WAIT_EXHAUSTED in str(exc):
                raise RoleError(
                    f"{SLOT_WAIT_EXHAUSTED}: {exc}",
                    reason=SLOT_WAIT_EXHAUSTED,
                    location="WRITER",
                ) from exc
            raise
    except WorkerError as exc:
        raise RoleError(
            f"codewhale_worker_failed: {exc}",
            reason="codewhale_worker_failed",
            location="WRITER",
        ) from exc

    # E2: the session status must reflect the leg that actually ran —
    # write the worker's own receipt where the Floor monitor reads it,
    # tagged with the seam name.
    try:
        from app.factory.build.coder_session_status import RECEIPT_REL

        receipt_path = Path(dest) / RECEIPT_REL
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(
                {"via": "codewhale_worker", **receipt.to_dict()}, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        logger.exception("writer receipt persistence failed")

    # The writer never authors a Factory-owned file (owner rule 2026-10-08;
    # live: the cycle-2 fintech writer invented docs/provenance/provenance.json
    # and the gap-filling converge kept it). Put the Factory's version back
    # and record the paths: the WRITER gate turns them into rework naming
    # each one.
    from app.factory.build import factory_owned

    touched = factory_owned.writer_touched(owned_before, factory_owned.snapshot(Path(dest)))
    if touched:
        factory_owned.restore(Path(dest), owned_before, touched)
        ctx.note(
            "the writer touched Factory-owned files (restored; sent to rework): "
            + ", ".join(f"{row['path']} ({row['change']})" for row in touched),
            stage="factory-owned",
            source="factory",
        )
    factory_owned.record(Path(dest), touched)

    # Which writer run produced app/actions/, recorded where converge can
    # stamp it into the product's provenance. The receipt carries no id of
    # its own, so its canonical hash is the identifier.
    try:
        from app.factory.build.build_provenance import receipt_hash

        digest = receipt_hash(receipt)
        if digest:
            ctx.state["writer_receipt"] = digest
    except Exception:  # noqa: BLE001 -- provenance never breaks a build
        logger.warning("could not hash the writer receipt", exc_info=True)

    # run_writer() returns at its CodeWhale branch, so emit_writer_artifacts
    # -- further down that function -- never runs in production, while
    # run_tester() still stamps tests/test_data_lifecycle.py, which opens
    # ``from app import backup, store``. The suite then imports a module no
    # production path wrote (sess_b6d51f9089e14176: "ImportError: cannot
    # import name 'backup' from 'app'"). Without this the agent has to
    # reverse-engineer the substrate contract from red tests, one rework
    # round per missing file. Fills gaps only: anything the agent wrote is
    # left exactly as it wrote it.
    from app.factory.build.data_lifecycle import backfill_platform_substrate

    substrate = backfill_platform_substrate(ctx.workspace)
    if substrate["written"]:
        ctx.note(
            "platform substrate written by the factory (the agent is not "
            "asked for these): " + ", ".join(substrate["written"]),
            stage="substrate",
            source="factory",
        )

    # data_lifecycle was only one of THREE emitters stranded below the
    # CodeWhale branch. run_tester stamps tests/test_deploy.py and
    # tests/test_domain_acceptance.py just as unconditionally, and they open
    # ``from app.health import ...`` / ``from app.observe import ...`` /
    # ``from app.revision import ...`` and ``from app.domain_ops import
    # OUTCOMES, perform_all``. None of those modules were written on this
    # path, so the suite died at COLLECTION -- before one assertion ran --
    # and the agent spent rework rounds inventing a contract the factory
    # owns. Live: sess_42d244d317f042b2 (Cerebrum VenueOps run 2),
    # "suite_red: missing module -- FAILED tests.test_domain_acceptance".
    # Gaps only, same as above: anything the agent wrote stays as written.
    from app.factory.build.deploy import (
        backfill_deploy_substrate,
        stamp_factory_deploy_modules,
    )
    from app.factory.build.declared_specs import product_root
    from app.factory.build.domain_acceptance import (
        backfill_domain_gaps,
        domain_specs,
        stamp_domain_substrate,
    )

    # The domain acceptance driver and the kernel it runs on are Factory-owned
    # (cycle 8: the kernel was never vendored on this path and the suite died
    # at collection). Rendered whole, from the specs TESTER stamps its suite
    # from; a writer that touched them was already put back above.
    domain_root = product_root(
        (getattr(ctx.workspace, "workspace", None), getattr(ctx.workspace, "destination", None))
    )
    domain_stamped = stamp_domain_substrate(
        ctx.workspace,
        domain_specs(dict(ctx.state.get("model_specs") or {}), domain_root),
    )
    if domain_stamped:
        ctx.note(
            "domain acceptance driver stamped by the factory (Factory-owned): "
            + ", ".join(domain_stamped[:6])
            + (f" (+{len(domain_stamped) - 6} kernel file(s))" if len(domain_stamped) > 6 else ""),
            stage="substrate",
            source="factory",
        )

    # health/observe/revision are Factory-owned: the stamped suite reads
    # their SHAPES (health body, log line, revision identity), not only the
    # names the backfill checks. Stamped outright, from deploy.py.
    stamped = stamp_factory_deploy_modules(ctx.workspace)
    if stamped:
        ctx.note(
            "deploy modules stamped by the factory (shape the stamped suite "
            "reads): " + ", ".join(stamped),
            stage="substrate",
            source="factory",
        )

    for label, result in (
        ("deploy", backfill_deploy_substrate(ctx.workspace)),
        (
            "domain",
            backfill_domain_gaps(
                ctx.workspace, dict(ctx.state.get("model_specs") or {})
            ),
        ),
    ):
        if result["written"]:
            ctx.note(
                f"{label} substrate written by the factory (the agent is not "
                "asked for these): " + ", ".join(result["written"]),
                stage="substrate",
                source="factory",
            )
        # D2: a stub that could not satisfy the factory's own stamped import
        # contract was replaced with the canonical module. Named on the Floor
        # so a repaired file is never mistaken for agent authorship.
        if result.get("repaired"):
            ctx.note(
                f"{label} substrate REPAIRED (a stub could not satisfy the "
                "test the factory stamps): " + ", ".join(result["repaired"]),
                stage="substrate",
                source="factory",
            )
        # A file that provides some required names but not all may be real
        # authored work; the factory must not overwrite it, and it cannot ship
        # a product whose own stamped suite will not import. This is the
        # factory's fault to resolve, so it halts FACTORY (no writer rework).
        if result.get("conflicts"):
            raise RoleError(
                f"factory_substrate_conflict: {label} substrate cannot be "
                "safely repaired without overwriting possible agent work: "
                + "; ".join(result["conflicts"]),
                reason="factory_substrate_conflict",
                location="WRITER",
            )

    # Same early return, the other half of the contract: run_writer converges
    # the ProductGenerator classes (app/agents/manifests, app/workflows,
    # app/connectors, product-dna, docs/blueprint, docs/provenance,
    # docs/certification) far below the CodeWhale branch, so production never
    # ran it and every CodeWhale build graded "missing founding files"
    # (FinOps, sess_065fc3eac75c4f62). Gaps only: this converge also owns the
    # frontend tree, and the agent writes its own App.tsx.
    from app.factory.build.converge import converge_writer_emitters

    converged = converge_writer_emitters(ctx, fill_gaps_only=True)
    if converged.get("copied"):
        ctx.note(
            "founding classes filled by the factory: "
            + ", ".join(sorted(converged["copied"])[:12]),
            stage="converge",
            source="factory",
        )

    # The published kernel roster (GET /v1/jobs, /v1/catalog, /v1/gates) is
    # the Factory's role contracts, and TESTER's stamped route suite asserts
    # it -- but it was stamped only below this branch, so the agent wrote its
    # own app/jobs.py with an empty CATALOG/GATES and the suite died on
    # KeyError: 'kernel', a fault no rework could fix (vineyard repro,
    # dd1b353d). Stamped here from the same source; the product's declared
    # CAPABILITIES manifest is kept as declared.
    from app.factory.build.kernel_publish import stamp_roster

    if stamp_roster(ctx):
        ctx.note(
            "kernel roster (app/jobs.py) stamped by the factory from the role contracts",
            stage="substrate",
            source="factory",
        )

    # The worker subprocess writes into the staging tree directly, so its
    # files are not in the workspace's tracked ``written`` list — commit()
    # would drop every authored handler (live-factory
    # sess_620b8581fb224bea: authored=8 at the role, 0 at the gate).
    record_existing = getattr(ctx.workspace, "record_existing", None)
    if callable(record_existing):
        record_existing()

    # E1 + E3: three outcomes must be distinguishable at the ROLE level —
    # dispatched-and-failed (above), dispatched-and-succeeded, and
    # dispatched-succeeded-but-wrote-nothing. A "completed" receipt with
    # zero tool calls or zero stamped handlers is the same silent success
    # the writer_contract gate refuses; the role now refuses it too, so
    # the gate is defense-in-depth rather than the only line.
    from app.factory.build.authorship import (
        agent_written_handler_ids_in_workspace,
    )

    authored = agent_written_handler_ids_in_workspace(Path(dest))
    # ``dest`` is the STAGING root, and staging is rmtree'd fresh at every
    # phase start -- so this counts handlers written in THIS round only. A
    # rework round legitimately writes no new handler: it fixes
    # app/dispatch.py, a route, a migration. Judging staging alone reads
    # "no new handler this round" as "the writer produced nothing" and
    # kills a build that already carries stamped handlers. Live
    # sess_b6d51f9089e14176: status='completed' tools=123 authored=0, on a
    # round whose own narration was "block availability made honest --
    # app.dispatch.block_is_available now resolves the wrapped Store".
    # The refusal is about a writer that produced nothing AT ALL, which is
    # a build-level property, so fall back to what is already committed.
    carried_over: List[str] = []
    if not authored:
        destination = getattr(ctx.workspace, "destination", None)
        if destination is not None and Path(destination) != Path(dest):
            carried_over = agent_written_handler_ids_in_workspace(Path(destination))
    logger.info(
        "codewhale writer result: status=%s tools=%d authored=%d carried=%d",
        receipt.status,
        len(receipt.tools),
        len(authored),
        len(carried_over),
    )
    if (
        receipt.status != "completed"
        or not receipt.tools
        or not (authored or carried_over)
    ):
        return RoleResult(
            ok=False,
            detail=(
                "codewhale_worker_failed: writer_no_output: "
                f"status={receipt.status!r} tools={len(receipt.tools)} "
                f"authored={len(authored)} carried={len(carried_over)}"
            ),
            reason="writer_no_output",
            location="WRITER",
            notes={"codewhale_worker": receipt.to_dict()},
        )
    # docs/build_provenance.json is part of WRITER's contract. It is the
    # FACTORY's record and does not ship (builds_push.FACTORY_INTERNAL_PATHS);
    # scripts/release_gate.py used to fail the image build without it, which
    # reported 0/13 on platforms whose suite passed, and no longer reads it as
    # a requirement. run_writer() writes it, but far below its CodeWhale early
    # return, so on the production path it only existed when the agent
    # happened to write one itself (live: the vet build had it and went 13/13;
    # the dental build did not and died in Docker on exactly this line).
    # Written here when absent, attributed from what is actually on disk;
    # an agent-written manifest is left alone.
    prov_rel = Path("docs") / "build_provenance.json"
    if not ctx.workspace.exists(prov_rel):
        handler_ids = list(authored or carried_over)
        ctx.workspace.write_text(
            prov_rel,
            json.dumps(
                {
                    "schema_version": "build_provenance.v1",
                    "product_id": getattr(ctx.blueprint, "product_id", "unknown"),
                    "product_name": getattr(ctx.blueprint, "product_name", ""),
                    "engine": "codewhale_worker",
                    "artifact_sources": {
                        f"app/actions/{hid}.py": "coder CLI (codewhale exec)"
                        for hid in handler_ids
                    },
                    # Read by the product's own acceptance.py (authorship_floor,
                    # the 13th check) via full_pilot_authorship_from: without
                    # these the image builds and then fails the floor.
                    "authorship": {
                        "action_py": len(handler_ids),
                        "agent_artifacts": list(handler_ids),
                        "cli_authored_ids": list(handler_ids),
                    },
                    "n_required": len(
                        list(getattr(ctx.blueprint, "capabilities", None) or [])
                    )
                    or len(handler_ids),
                    "written_by": "factory (the agent did not emit a manifest)",
                    "worker": {
                        "status": receipt.status,
                        "provider": getattr(receipt, "provider", ""),
                        "model": getattr(receipt, "model", ""),
                        "tools": len(receipt.tools),
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        ctx.note(
            "docs/build_provenance.json written by the factory (the agent did "
            "not emit one; the product image cannot build without it)",
            stage="provenance",
            source="factory",
        )
    return RoleResult(
        ok=True,
        detail=(
            f"codewhale worker {receipt.status} "
            f"({receipt.termination_reason or 'no reason'}) "
            f"authored={len(authored)}"
        ),
        notes={"codewhale_worker": receipt.to_dict()},
    )


def run_writer(
    ctx: RoleContext,
    *,
    launch: Any = None,
    env: Any = None,
) -> RoleResult:
    """Platform manufacturer: dispatch runtime plus one handler per capability.

    Executor order: the headless CodeWhale worker when
    ``FACTORY_CODEWHALE_WRITER=1`` is armed; otherwise the compiled-brief
    dispatch (``dispatch_compiled_brief``) and the deterministic template
    fallback. The retired cursor BA / kimi CLI seams are gone from this
    ladder.

    The coding agent writes each body when one is configured; otherwise the
    body is composed from the block contract deterministically. Which path ran
    is stamped into every module header and reported in the result -- CI has
    no key and must still exercise this route, so the fallback is a first-class
    path rather than an error. Also publishes every kernel's job over HTTP
    (``app/jobs.py``, kernel routes in ``app/routes.py``).

    On a rework pass ``ctx.work_list`` carries the TESTER's findings. Only the
    capabilities those findings implicate are regenerated; everything else is
    reused from the previous round (specs from state, handler files from the
    committed destination, route bodies from state), so a green capability
    cannot regress while a red one is being fixed.

    A pilot cycle without a coder key must not replace agent-written
    handlers with the deterministic template. Adapter patches already ran.

    C-BRIEF: compile ONE gated brief and dispatch it via
    FACTORY_CODE_CLI in three fail-closed phases (backend →
    frontend+RAG → integration). A keyed Floor without that binary, or a Kimi
    binary without config.toml, fail-closes (FACTORY_CODE_CLI_UNAVAILABLE
    / FACTORY_CODE_CLI_CREDENTIALS_MISSING /
    FACTORY_CODE_CLI_NO_MODEL) before WRITER progress.
    A CLI exit of "No model configured" is also FACTORY_CODE_CLI_NO_MODEL
    (still FACTORY_CODE_CLI_FAILED honesty; not a ≥2h CLI session).
    A 404 / Permission denied on the configured model is
    FACTORY_CODE_CLI_MODEL_DENIED (distinct from NO_MODEL).
    A Moonshot 429 / insufficient-balance exit is
    FACTORY_CODE_CLI_BILLING (still FACTORY_CODE_CLI_FAILED honesty).
    When verified REUSE rows remain, that billing/auth miss continues
    factory-grounded persist / event_bus emit + harvest (sess_d5789a91)
    instead of a templated SCAFFOLD. Remaining GENERATE inventory_gaps
    fall through to the factory coder LLM (compiled-brief path, not a
    per-capability handle() loop) and still persist-emit (alias-key bind
    + factory-grounded envelope) so WRITER [check:round_trip] does not
    HALT on a missing handler. Receipt stays ok=false with the CLI
    miss; inventory_gaps stay listed until artifacts land.
    HTTP oneshot stays behind FACTORY_BRIEF_HTTP_ONESHOT=1 (CI).
    Per-capability handle() shots stay behind FACTORY_BRIEF_DISPATCH=0.
    Inventory is checked against the Store registry before any handler
    is written.
    """
    # CodeWhale (DeepSeek) hosts the writer. It is checked first and, in
    # production, FACTORY_CODEWHALE_WRITER=1 makes it the only path taken.
    # The kimi CLI vehicle underneath has been removed outright (see
    # app/factory/code_cli.py), so no later branch can shell out to it.
    #
    # The locale the user declared on the Floor and the run-time money
    # settings module are stamped before ANY writer path, so the agent builds
    # against them and the money contract can read them.
    from app.factory.build.money_contract import emit_money_artifacts
    from app.factory.build.writer_behaviour import emit_self_check

    emit_money_artifacts(ctx.workspace, ctx.blueprint)
    # The WRITER gate's own probe, so the writer can run what the gate runs
    # (F1/F11/schema) before it declares done -- its own suite cannot.
    emit_self_check(ctx.workspace)
    # The Store gate's own harness, stamped before ANY writer path: the writer
    # measures itself with the N checks the gate scores (``--self-check``),
    # never a script of its own. Re-stamped before TESTER (factory_refresh).
    from app.factory.build.store_acceptance import stamp_acceptance_harness

    stamp_acceptance_harness(ctx.workspace, blueprint=ctx.blueprint)
    if writer_uses_codewhale(env):
        return _run_writer_via_codewhale_worker(ctx)
    writer_roster = _writer_block_roster(ctx.state)
    if (
        str(ctx.state.get("build_cycle") or "") == "pilot"
        and ctx.work_list
    ):
        from app.factory.coder import coder_enabled

        if not coder_enabled():
            # Do not report ok=True: that would re-enter TESTER with identical
            # handlers and burn the rework budget on a no-op loop.
            vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
            return RoleResult(
                ok=False,
                reason="pilot_rework_no_coder_key",
                location="WRITER",
                detail=(
                    "pilot rework requires a coder key; cannot regenerate failing "
                    f"handlers ({len(ctx.plan.capabilities)} capability(ies) unchanged)"
                ),
                vendored_blocks=tuple(vendored),
            )
    from app.factory.build.brief_compiler import (
        InventoryHalt,
        compile_brief_from_ctx,
        verify_inventory,
    )
    from app.factory.build.brief_lint import BriefLintError, lint_or_raise
    from app.factory.build.coder_session import (
        CLI_AUTH_BILLING_BLOCKERS,
        CLI_PREFLIGHT_BLOCKERS,
        NAMED_BLOCKER_CLI_UNUSED,
        brief_dispatch_enabled,
        brief_requires_cli,
        deepseek_cli_ready,
        dispatch_compiled_brief,
        dispatch_from_state,
        emit_factory_grounded_reuse_keep_path,
        factory_grounded_source_for,
        inventory_gap_ids,
        merge_writer_phase_dispatch,
        phase_authorship_miss,
        record_unauthored_cli_work,
        refresh_receipt_harvest,
        remaining_phase_cli_work,
        write_brief_artifacts,
        write_dispatch_receipt,
        _workspace_root,
    )

    try:
        compiled_brief = compile_brief_from_ctx(ctx)
        verify_inventory(compiled_brief)
        lint_or_raise(compiled_brief)
    except InventoryHalt as exc:
        raise RoleError(str(exc)) from exc
    except BriefLintError as exc:
        raise RoleError(str(exc)) from exc
    from app.factory.build.architect import attach_narrative

    # The architect writes the NARRATIVE around the CONTRACT just compiled and
    # linted (FACTORY_BRIEF_NARRATIVE; off by default). The CONTRACT is not
    # touched: every phase brief derived below carries it verbatim.
    compiled_brief = attach_narrative(ctx, compiled_brief)
    if compiled_brief.intake:
        ctx.state["intake_blueprint"] = compiled_brief.intake
        ctx.workspace.write_text(
            Path("docs") / "intake_blueprint.json",
            json.dumps(compiled_brief.intake, indent=2, sort_keys=True) + "\n",
        )
    write_brief_artifacts(ctx, compiled_brief)
    dispatch = None
    use_brief_dispatch = brief_dispatch_enabled() or deepseek_cli_ready()
    reuse_keep_path = False
    from app.factory.build.writer_phases import (
        PhaseAcceptHalt,
        WRITER_PHASE_BACKEND,
        WRITER_PHASE_FRONTEND_RAG,
        accept_writer_phase,
        checkpoint_landed_phase,
        compile_phase_brief,
        later_writer_phases,
        should_dispatch_writer_phase,
        should_reopen_writer_phase,
    )

    if use_brief_dispatch:
        root = _workspace_root(ctx)
        phase_work = remaining_phase_cli_work(
            compiled_brief, ctx.workspace, ctx.state
        )
        if should_reopen_writer_phase(ctx, WRITER_PHASE_BACKEND):
            # TESTER findings must reach a non-CLI brief (oneshot CI). A
            # dest-complete CLI with no remaining work must not re-open a
            # 2700s empty shot (sess_5782f226 run8).
            reopen_dispatch = bool(phase_work) or (
                bool(ctx.work_list) and not deepseek_cli_ready()
            )
            if reopen_dispatch:
                phase_brief = compile_phase_brief(
                    compiled_brief, WRITER_PHASE_BACKEND
                )
                lint_or_raise(phase_brief)
                write_brief_artifacts(ctx, phase_brief)
                dispatch = dispatch_compiled_brief(ctx, phase_brief)
                record_unauthored_cli_work(ctx.state, phase_work, dispatch)
                miss = phase_authorship_miss(
                    compiled_brief, dispatch, ctx.workspace, phase_work
                )
                if miss:
                    raise RoleError(miss)
            else:
                emit_factory_grounded_reuse_keep_path(
                    root, compiled_brief, only_missing=True
                )
                dispatch = dispatch_from_state(ctx.state)
                ctx.note(
                    "skip backend C-BRIEF — no remaining phase CLI work "
                    "(keepable handlers already landed or prior empty CLI "
                    "already recorded these gaps)",
                    stage="checkpoint",
                    phase=WRITER_PHASE_BACKEND,
                    source="writer phases",
                )
        else:
            dispatch = dispatch_from_state(ctx.state)
            ctx.note(
                "resume skips landed writer phase backend",
                stage="checkpoint",
                phase=WRITER_PHASE_BACKEND,
                source="writer phases",
            )
        if dispatch is not None:
            reuse_keep_path = bool(getattr(dispatch, "reuse_keep_path", False))
            gaps = inventory_gap_ids(compiled_brief)
            if dispatch.blocker in CLI_PREFLIGHT_BLOCKERS and brief_requires_cli():
                # Verified REUSE + named billing/auth miss: continue emit.
                # GENERATE-gap factory-LLM fallthrough: continue (honest receipt).
                # Binary-missing without that second leg still halt.
                fallthrough = bool(
                    getattr(dispatch, "factory_llm_generate_fallthrough", False)
                )
                reuse_ok = reuse_keep_path or (
                    not gaps and dispatch.blocker in CLI_AUTH_BILLING_BLOCKERS
                )
                if not fallthrough and not reuse_ok:
                    raise RoleError(dispatch.detail)
            ctx.note(
                f"brief dispatch via {dispatch.via}: {dispatch.detail}",
                stage="dispatch",
                source=f"coder {'CLI' if dispatch.via == 'cli' else 'LLM'} ({dispatch.model})"
                if dispatch.ok
                else "brief dispatch",
                done=1 if dispatch.ok else 0,
                total=1,
            )
            if deepseek_cli_ready() and dispatch.via != "cli":
                raise RoleError(
                    f"{NAMED_BLOCKER_CLI_UNUSED}: DeepSeek FACTORY_CODE_CLI is "
                    f"ready but C-BRIEF dispatched via={dispatch.via!r}, not cli. "
                    "Store-complete REUSE/COMPOSE (no GENERATE gaps) is not a skip."
                )

    ctx.workspace.write_text(Path("app") / "__init__.py", '"""Generated platform."""\n')
    _vendor_product_kernel(ctx)
    ctx.workspace.write_text(Path("app") / "kernel_bridge.py", _render_kernel_bridge())
    # The universal reasoning socket. Identical in every platform the Factory
    # builds: the kernel is one file, and the KIT vendored beside it is the only
    # thing that differs between a hotel build and an airport build. Emitted here
    # rather than hand-patched into one product, so every future build has it.
    #
    # Fail closed twice over: a kit that does not load refuses every statement,
    # and a host function the product has not wired raises instead of returning a
    # clean pass -- a platform that forgot to wire retrieval must not answer
    # ungated and look fine doing it.
    _emit_reasoning_socket(ctx)
    vendored_ids = [b for b in ctx.state.get("vendored_blocks", ()) if b]
    contracts = {b: _block_contract(ctx, b) for b in vendored_ids}
    ctx.workspace.write_text(Path("app") / "dispatch.py", _render_dispatch(contracts))
    # Shared block-input construction for every handler's execute wrapper.
    # Lives beside dispatch (not inside it) so LotDesk F18 stays clean.
    ctx.workspace.write_text(
        Path("app") / "block_inputs.py",
        render_block_inputs_module(
            harvest_block_default_actions(vendored_ids, workspace=ctx.workspace),
            vendored_capability_classes(ctx, vendored_ids),
        ),
    )

    vendored = set(ctx.state.get("vendored_blocks", ()))
    cap_ids = [cap.capability_id for cap in ctx.plan.capabilities]
    # The ratchet: regenerate only the capabilities the typed findings name
    # (build/findings.py rework_targets) -- decided by capability_id fields,
    # never by searching the finding text.
    failing = _rework_targets(ctx.work_list, cap_ids)
    previous_specs: Dict[str, Any] = dict(ctx.state.get("model_specs") or {})
    previous_sources: Dict[str, str] = dict(ctx.state.get("artifact_sources") or {})
    previous_routes: Dict[str, str] = dict(ctx.state.get("route_bodies") or {})

    written: List[str] = []
    sources: Dict[str, str] = {}
    fallback_source = "deterministic contract template"

    # --- domain models FIRST: handlers and routes are written against them --
    #
    # The coder shots go out ahead of the walk so N capabilities cost
    # ceil(N / fanout) round trips instead of N. Everything that touches
    # shared state -- specs, sources, ctx.note -- stays in the walk below, in
    # plan order, so the emitted bytes and the ledger do not depend on which
    # model answered first. Fan-out defaults to 1, which runs these inline.
    fanout = coder_fanout()
    if fanout > 1:
        # Tell the client what the WRITER is about to do. The bound is on
        # concurrency, never on retry: a 429 on a paid leg stays unretried,
        # and the way to keep that policy affordable is not to create the
        # burst. Only imported when it is needed, so a template-only build
        # still does not reach for the coder module.
        from app.factory.coder import set_inflight_limit

        set_inflight_limit(fanout)
    spec_ids = [
        cap.capability_id
        for cap in ctx.plan.capabilities
        if not use_brief_dispatch
        and (cap.capability_id in failing or cap.capability_id not in previous_specs)
    ]
    caps_by_id = {cap.capability_id: cap for cap in ctx.plan.capabilities}
    # An empty FanOut is how "the whole loop is templated" is expressed: the
    # walk below finds no value for any capability and falls back for all of
    # them, rather than funding the first and refusing the rest.
    spec_shots = FanOut()
    if not loop_budget_too_low(ctx, "model spec", count=len(spec_ids), workers=fanout):
        spec_shots = fan_out(
            ctx,
            spec_ids,
            lambda wctx, cid: _coder_model_spec(wctx, caps_by_id[cid]),
            workers=fanout,
        )

    specs: Dict[str, Dict[str, Any]] = {}
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        if cid not in failing and cid in previous_specs:
            # A ratcheted spec gets the same obligation check: the round that
            # produced it may predate the block assignment it is reused with.
            _kept = [b for b in cap.block_ids if b in vendored]
            specs[cid] = augment_model_spec(previous_specs[cid], _kept)
            specs[cid], _envelope = ensure_record_envelope(specs[cid])
            assert_feedable(cid, _kept, specs[cid])
            sources[f"model:{cid}"] = previous_sources.get(
                f"model:{cid}", "unchanged from previous round"
            )
            continue
        if use_brief_dispatch and dispatch and cid in (dispatch.specs or {}):
            spec = dispatch.specs[cid]
            specs[cid] = spec
            specs[cid], _envelope = ensure_record_envelope(specs[cid])
            _kept = [b for b in cap.block_ids if b in vendored]
            specs[cid] = augment_model_spec(specs[cid], _kept)
            specs[cid], _envelope = ensure_record_envelope(specs[cid])
            assert_feedable(cid, _kept, specs[cid])
            sources[f"model:{cid}"] = dispatch.artifact_source(cid)
            continue
        if use_brief_dispatch:
            spec = _fallback_spec(cap)
        else:
            # Replay here, where the serial call used to happen, so the ledger
            # reads the same at any fan-out.
            spec_shots.replay(ctx, cid)
            spec = spec_shots.values.get(cid) or _fallback_spec(cap)
        # IF YOU ASSIGN IT, YOU FEED IT. A block whose precondition only the
        # caller can meet obligates this spec to carry a field for it; the
        # agent still owns the design, this only closes what it left open.
        # Audited immediately after, so an unfeedable assignment fails HERE
        # with the missing field named -- not at the F11 gate, after four
        # handlers were written, with no zip.
        _assigned = [b for b in cap.block_ids if b in vendored]
        spec = augment_model_spec(spec, _assigned)
        spec, _envelope = ensure_record_envelope(spec)
        assert_feedable(cid, _assigned, spec)
        specs[cid] = spec
        sources[f"model:{cid}"] = (
            f"coder LLM ({spec['model']})" if spec.get("model") else fallback_source
        )
    if spec_shots.unreplayed():
        raise RoleError(
            "model-spec shots were made but never recorded: "
            + ", ".join(spec_shots.unreplayed())
        )
    ctx.note(
        f"designed {len(specs)} data model(s)",
        stage="models",
        done=len(specs),
        total=len(cap_ids),
    )
    ctx.workspace.write_text(Path("app") / "models.py", _render_models(specs))

    # Persistence + versioned Alembic from the same specs. connect() does
    # not CREATE TABLE — deploy applies revisions against STORAGE_PATH.
    from app.factory.build.data_lifecycle import emit_writer_artifacts
    from app.factory.build.deploy import emit_writer_artifacts as emit_deploy_artifacts
    from app.factory.build.domain_acceptance import (
        emit_writer_artifacts as emit_domain_artifacts,
    )

    emit_writer_artifacts(ctx.workspace, specs)
    emit_deploy_artifacts(ctx.workspace)
    emit_domain_artifacts(ctx.workspace, specs)
    from app.factory.build.persist_accept import wipe_workspace_runtime_db

    wipe_workspace_runtime_db(ctx.workspace)
    sources["persistence"] = (
        "derived from coder-designed models"
        if any(s.get("model") for s in specs.values())
        else fallback_source
    )
    sources["migrations"] = "alembic revisions emitted from unused-kit pattern"
    sources["deploy_observe"] = "S11 fail-closed health, rollback drill, JSON request logs"
    sources["domain_acceptance"] = (
        "S12 ten outcomes through execute_action; LotDesk-class fixtures fail"
    )

    # --- capability handlers ------------------------------------------------
    #
    # Same shape as the model loop: the shots go out ahead, the walk below
    # decides, writes and notes in plan order. Only capabilities that would
    # actually reach the coder are shot -- a ratcheted handler is kept, not
    # regenerated, and the brief path already answered for all of them in one
    # dispatch.
    _persist_root = persist_workspace_root(ctx.workspace)
    body_ids = [
        cap.capability_id
        for cap in ctx.plan.capabilities
        if not use_brief_dispatch
        and not (
            cap.capability_id not in failing
            and (_persist_root / persist_handler_rel(cap.capability_id)).is_file()
        )
    ]
    # Reading the previous attempt is a workspace read, so it belongs on the
    # calling thread; the coder needs it because eight live rounds proved that
    # regenerating from the same prompt converges to the same wrong code.
    _previous_attempts = {
        cid: (
            ctx.workspace.read_text(persist_handler_rel(cid))
            if ctx.work_list and ctx.workspace.exists(persist_handler_rel(cid))
            else None
        )
        for cid in body_ids
    }
    body_shots = FanOut()
    if not loop_budget_too_low(ctx, "handler", count=len(body_ids), workers=fanout):
        body_shots = fan_out(
            ctx,
            body_ids,
            lambda wctx, cid: _coder_body(
                wctx,
                caps_by_id[cid],
                [b for b in caps_by_id[cid].block_ids if b in vendored],
                specs[cid],
                _previous_attempts.get(cid),
            ),
            workers=fanout,
        )

    action_names: List[str] = []
    landed = {
        str(item)
        for item in (ctx.state.get("landed_capabilities") or ())
        if item
    }
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        name = cid.replace("-", "_")
        handler_rel = persist_handler_rel(cid)
        action_names.append(name)
        written.append(name)

        persist_root = persist_workspace_root(ctx.workspace)
        if cid in landed and cid not in failing:
            sources[cid] = previous_sources.get(
                cid, "unchanged from previous round"
            )
            if ctx.workspace.exists(handler_rel):
                ctx.workspace.write_text(
                    handler_rel, ctx.workspace.read_text(handler_rel)
                )
            continue
        if cid not in failing and (persist_root / handler_rel).is_file():
            # Ratchet only when the persist-check tree has the file.
            # RoleWorkspace.exists also sees destination / store_root;
            # a leftover destination GENERATE handler is how staging
            # then fails [check:round_trip] with handler missing.
            sources[cid] = previous_sources.get(cid, "unchanged from previous round")
            _stage_handler_for_commit(ctx, handler_rel)
            _checkpoint_writer_capability(ctx, cid)
            continue

        usable = [b for b in cap.block_ids if b in vendored]
        # The previous attempt was read before the shots went out; see
        # _previous_attempts above for why the coder is given it at all.
        if use_brief_dispatch and dispatch and cid in (dispatch.handlers or {}):
            authored = (
                dispatch.handlers[cid],
                dispatch.artifact_source(cid),
            )
        elif (
            use_brief_dispatch
            and dispatch
            and cid in _dispatch_cli_keep_ids(dispatch)
            and (persist_root / handler_rel).is_file()
        ):
            kept_text = ctx.workspace.read_text(handler_rel)
            if needs_grounded_event_bus_handler(cid, usable) and not (
                handler_satisfies_event_bus_contract(
                    kept_text, require_prepared_step=True
                )
            ):
                authored = None
            elif _persists_directly(kept_text):
                # Phase 2 §0.2: persistence is route-scoped. A keepable CLI
                # handler that still persists directly collides with the
                # tenant-required store signature (handler has no tenant);
                # regenerate the pure-dispatch envelope.
                authored = None
            else:
                # FACTORY_CODE_CLI / oneshot harvest already wrote a PREPARED
                # brief-driven module. Overwriting a prepared handler with the
                # fallback envelope is how workflow steps died; keeping an
                # unprepared {'block': 'event_bus', 'input': payload} is how
                # PRODUCT step_N (event_bus) locked in after #318.
                # Empty-gap billing/auth keep-path is factory-grounded emit,
                # not a ≥2h CLI session — do not credit FACTORY_CODE_CLI.
                if reuse_keep_path and not dispatch.ok:
                    sources[cid] = factory_grounded_source_for(cid, usable)
                else:
                    sources[cid] = (
                        f"coder CLI ({dispatch.model})" if dispatch.model
                        else (
                            "FACTORY_CODE_CLI" if dispatch.via == "cli"
                            else "harvested workspace handler"
                        )
                    )
                ctx.note(
                    f"kept CLI handler {cid} ({sources[cid]})",
                    stage="handlers",
                    capability=cid,
                    source=sources[cid],
                    done=len([k for k in sources if k in set(cap_ids)]),
                    total=len(cap_ids),
                )
                _stage_handler_for_commit(ctx, handler_rel)
                _checkpoint_writer_capability(ctx, cid)
                continue
        elif use_brief_dispatch:
            authored = None
        else:
            body_shots.replay(ctx, cid)
            authored = body_shots.values.get(cid)
        written_ids = set(
            getattr(dispatch, "factory_llm_written_ids", None) or ()
            if dispatch is not None
            else ()
        )
        persist_ids = set(
            getattr(dispatch, "generate_persist_ids", None) or ()
            if dispatch is not None
            else ()
        )
        if authored:
            body, source = authored
        elif (
            dispatch is not None
            and cid in persist_ids
            and cid not in written_ids
            and cid not in set(getattr(dispatch, "cli_authored_ids", None) or ())
            and (persist_root / handler_rel).is_file()
        ):
            # Persist-emitted GENERATE gap (billing fallthrough or empty
            # DeepSeek CLI harvest). Stage it; do not skip or credit CLI.
            sources[cid] = factory_grounded_source_for(cid, usable)
            ctx.note(
                f"kept factory-grounded GENERATE persist {cid}",
                stage="handlers",
                capability=cid,
                source=sources[cid],
                done=len([k for k in sources if k in set(cap_ids)]),
                total=len(cap_ids),
            )
            _stage_handler_for_commit(ctx, handler_rel)
            _checkpoint_writer_capability(ctx, cid)
            continue
        else:
            body = _capability_handler_body(cid, usable)
            if needs_grounded_event_bus_handler(cid, usable):
                source = FACTORY_GROUNDED_EVENT_BUS_SOURCE
            elif reuse_keep_path or (
                dispatch is not None and cid in persist_ids
            ):
                source = FACTORY_GROUNDED_PERSIST_SOURCE
            else:
                source = fallback_source
        if needs_grounded_event_bus_handler(cid, usable) and not (
            handler_satisfies_event_bus_contract(
                body, require_prepared_step=True
            )
        ):
            body = grounded_event_bus_handler_body(cid, usable)
            source = FACTORY_GROUNDED_EVENT_BUS_SOURCE

        default_actions = {
            b: contract["default_action"]
            for b in usable
            if (contract := _block_contract(ctx, b)).get("default_action")
        }
        harvested = harvest_block_default_actions(
            usable, persist_workspace_root(ctx.workspace), workspace=ctx.workspace
        )
        default_actions = {**harvested, **default_actions}
        ctx.workspace.write_text(
            handler_rel,
            _handler_module(
                cid, usable, body, source, default_actions,
                field_names=[
                    str(f.get("name"))
                    for f in ((specs.get(cid) or {}).get("fields") or [])
                    if isinstance(f, dict) and f.get("name")
                ],
                entity=str((specs.get(cid) or {}).get("entity") or name),
            ),
        )
        sources[cid] = source
        ctx.note(
            f"wrote handler {cid} ({source})",
            stage="handlers",
            capability=cid,
            source=source,
            done=len([k for k in sources if k in set(cap_ids)]),
            total=len(cap_ids),
        )
        _checkpoint_writer_capability(ctx, cid)

    if body_shots.unreplayed():
        raise RoleError(
            "handler shots were made but never recorded: "
            + ", ".join(body_shots.unreplayed())
        )

    reuse_miss: List[str] = []
    inv_by_cid = {
        item.capability_id: item
        for item in getattr(compiled_brief, "inventory", ()) or ()
    }
    persist_root = persist_workspace_root(ctx.workspace)
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        handler_rel = persist_handler_rel(cid)
        path = persist_root / handler_rel
        if path.is_file():
            if handler_rel.as_posix() not in ctx.workspace.written:
                _stage_handler_for_commit(ctx, handler_rel)
            continue
        item = inv_by_cid.get(cid)
        if item is not None and getattr(item, "is_reuse", False):
            reuse_miss.append(cid)
    if reuse_miss:
        raise RoleError(WRITER_REUSE_HANDLER_HALT + ": " + ", ".join(reuse_miss))
    try:
        assert_reuse_schema_accept(persist_root, compiled_brief)
    except ReuseAcceptHalt as exc:
        raise RoleError(str(exc)) from exc

    ctx.workspace.write_text(
        Path("app") / "actions" / "__init__.py",
        _render_actions_init(action_names),
    )
    if dispatch is not None and (
        reuse_keep_path
        or getattr(dispatch, "factory_llm_generate_fallthrough", False)
        or getattr(dispatch, "generate_persist_ids", None)
    ):
        refresh_receipt_harvest(ctx, compiled_brief, dispatch)
        ctx.state["brief_dispatch"] = dispatch.to_dict()

    from app.factory.build.workflow_accept import (
        EventBusWorkflowHalt,
        assert_event_bus_workflow_handlers,
        declares_event_bus_workflow,
    )

    if declares_event_bus_workflow(compiled_brief):
        try:
            assert_event_bus_workflow_handlers(
                ctx.workspace.workspace, compiled_brief
            )
        except EventBusWorkflowHalt as exc:
            raise RoleError(str(exc)) from exc

    # Handlers may validate domain fields the model_specs omitted (live:
    # property_reference_code) or enforce a vocabulary/type the spec left as
    # bare str (live VetConnect role/channel/is_active/login_count). Align
    # specs so _sample_payload and the route guard agree with the handler
    # before routes/tests are written.
    aligned_specs = False
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        name = cid.replace("-", "_")
        handler_rel = Path("app") / "actions" / f"{name}.py"
        if not ctx.workspace.exists(handler_rel):
            continue
        specs[cid], changed = align_spec_to_handler_source(
            specs[cid], ctx.workspace.read_text(handler_rel)
        )
        specs[cid], env_added = ensure_record_envelope(specs[cid])
        changed = list(changed) + list(env_added)
        if not changed:
            continue
        aligned_specs = True
        field_names = [
            str(f.get("name"))
            for f in (specs[cid].get("fields") or [])
            if isinstance(f, dict) and f.get("name")
        ]
        text = ctx.workspace.read_text(handler_rel)
        text = re.sub(
            r"^CAPABILITY_FIELDS = .*$",
            f"CAPABILITY_FIELDS = {field_names!r}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        ctx.workspace.write_text(handler_rel, text)
        ctx.note(
            f"aligned model_specs for {cid} with handler-required fields: "
            + ", ".join(changed),
            stage="models",
            capability=cid,
            added=changed,
        )
    if aligned_specs:
        ctx.workspace.write_text(Path("app") / "models.py", _render_models(specs))
        emit_writer_artifacts(ctx.workspace, specs)
        emit_domain_artifacts(ctx.workspace, specs)

    # --- API surface -------------------------------------------------------
    #
    # Not fanned out, and not by oversight: ``_coder_route_body`` returns None
    # unconditionally under U12 -- capability routes go through execute_action
    # and an LLM-authored body would displace the kernel. There is no network
    # call in this loop to overlap. If U12 is ever reversed, this loop takes
    # the same prefetch-then-walk shape as the two above.
    entries: List[Dict[str, Any]] = []
    route_bodies: Dict[str, str] = {}
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        spec = specs[cid]
        if cid not in failing and cid in previous_routes:
            body = previous_routes[cid]
            route_source = previous_sources.get(
                f"route:{cid}", "unchanged from previous round"
            )
        else:
            authored = _coder_route_body(
                ctx,
                cap,
                spec,
                previous_attempt=previous_routes.get(cid) if ctx.work_list else None,
            )
            raw_body, route_source = authored or (
                None,
                "kernel execute_action template",
            )
            if authored and raw_body is not None:
                # #311 mined the handler only. Live sess_1fd1d54c: the
                # refuse was the route (coder-invented factory status
                # after an LLM scheduled list). Align from handler +
                # raw route BEFORE baking ``_constraint_guard`` so the
                # prepended vocab and the schema sample agree.
                handler_rel = Path("app") / "actions" / f"{cid.replace('-', '_')}.py"
                runtime_src = raw_body
                if ctx.workspace.exists(handler_rel):
                    runtime_src = (
                        ctx.workspace.read_text(handler_rel) + "\n" + raw_body
                    )
                specs[cid], _changed = align_spec_to_handler_source(
                    specs[cid], runtime_src
                )
                specs[cid], _env = ensure_record_envelope(specs[cid])
                spec = specs[cid]
                # Coder bodies skip field constraints; the tasting-room
                # TESTER then failed agent "reject" cases the route accepted.
                body = _constraint_guard(spec) + "\n" + raw_body
            else:
                body = _templated_route_body(spec)
        body = _ensure_route_persists_payload(body)
        route_bodies[cid] = body
        name = cid.replace("-", "_")
        if name in KERNEL_ROUTE_NAMES:
            raise RoleError(
                f"capability {cid} collides with kernel HTTP route /v1/{name}"
            )
        entries.append(
            {
                "capability_id": cid,
                "name": name,
                "entity": spec["entity"],
                "body": body,
                "source": route_source,
            }
        )
        sources[f"route:{cid}"] = route_source
        ctx.note(
            f"wrote route {cid} ({route_source})",
            stage="routes",
            capability=cid,
            source=route_source,
            done=len(entries),
            total=len(cap_ids),
        )
    # One source for the published kernel roster (build/kernel_publish.py):
    # the CodeWhale path stamps it from the same functions.
    from app.factory.build.kernel_publish import (
        JOBS_REL,
        capabilities_from_entries,
        render_roster,
    )

    ctx.workspace.write_text(
        JOBS_REL,
        render_roster(ctx.state, ctx.plan, capabilities_from_entries(entries)),
    )
    sources["jobs"] = fallback_source
    for rel, text in render_routes_files(entries, ctx.blueprint).items():
        ctx.workspace.write_text(rel, text)
    from app.factory.build.persist_accept import (
        PersistRoundTripHalt,
        assert_persist_round_trip_ready,
        wipe_workspace_runtime_db,
    )

    wipe_workspace_runtime_db(ctx.workspace)
    try:
        assert_persist_round_trip_ready(persist_workspace_root(ctx.workspace), specs)
    except PersistRoundTripHalt as exc:
        raise RoleError(str(exc)) from exc
    try:
        accept_writer_phase(ctx, WRITER_PHASE_BACKEND, compiled_brief)
    except PhaseAcceptHalt as exc:
        raise RoleError(str(exc)) from exc
    checkpoint_landed_phase(ctx, WRITER_PHASE_BACKEND)

    # --- run scaffold ------------------------------------------------------
    product_name = getattr(ctx.blueprint, "product_name", "Generated Platform")
    vertical = getattr(ctx.blueprint, "vertical", "")
    ctx.workspace.write_text(Path("app") / "main.py", _render_main(product_name, vertical))
    # Platform preconditions as CODE, not as prose in a coder prompt (R1c).
    # The prompt told every handler to create the team first; on
    # residential-lettings three of four did not, and the one that used the
    # correct calling convention still answered "Team access denied".
    ctx.workspace.write_text(
        Path("app") / "preconditions.py",
        render_preconditions_module(
            list(writer_roster),
            product_name,
        ),
    )
    # A vendored block's imports are a precondition exactly like a schema
    # field: assigned means declared. Derived from the source the CLONER
    # actually wrote, so it cannot drift from what ships.
    #
    # Read off the DISK, not ``ctx.state``. The CLONER leaves these in the
    # runner's memory, which dies with the process: a build that timed out and
    # resumed rendered this file from None, shipped seven packages for 23
    # vendored blocks, and failed its Docker image on ``No module named
    # 'numpy'`` -- see block_obligations.dependency_obligations_on_disk.
    try:
        vendored_deps = dependency_obligations_on_disk(ctx.workspace.workspace)
    except BlockObligationError as exc:
        raise RoleError(str(exc)) from exc
    # Scanned against the tree the coder just wrote: a route that parses a form
    # needs python-multipart declared, and FastAPI does not declare it.
    ctx.workspace.write_text(
        "requirements.txt",
        _render_requirements(vendored_deps, root=ctx.workspace.workspace),
    )
    ctx.workspace.write_text(
        "requirements-dev.txt", _render_dev_requirements()
    )
    from app.factory.build.dependency_pins import CONSTRAINTS_REL, constraints_for_tree

    ctx.workspace.write_text(
        CONSTRAINTS_REL,
        constraints_for_tree(ctx.workspace.workspace),
    )
    if ctx.work_list and ctx.workspace.exists("README.md"):
        # The README does not fail tests; regenerating it on rework spends
        # coder budget for churn.
        sources["readme"] = previous_sources.get("readme", "unchanged from previous round")
    else:
        readme = _coder_readme(ctx, product_name, written, sorted(vendored))
        body = (
            readme[0]
            if readme
            else _templated_readme(product_name, written, sorted(vendored))
        )
        from app.factory.build.network_posture import readme_section

        ctx.workspace.write_text(
            "README.md", body + _kernel_http_readme_section() + readme_section()
        )
        sources["readme"] = readme[1] if readme else fallback_source
    sources["entrypoint"] = fallback_source
    sources["requirements"] = fallback_source

    # Deploy scaffold: without these the artifact can only be run locally.
    product_id = getattr(ctx.blueprint, "product_id", "platform")
    ctx.workspace.write_text("Dockerfile", _render_dockerfile())
    ctx.workspace.write_text(".dockerignore", _render_dockerignore())
    ctx.workspace.write_text("Procfile", _render_procfile())
    ctx.workspace.write_text(
        Path("scripts") / "release_gate.py", _render_release_gate(product_name)
    )
    sources["release_gate"] = fallback_source
    from app.factory.build.store_acceptance import stamp_acceptance_artifacts

    stamp_acceptance_artifacts(
        ctx.workspace,
        product_name=product_name,
        cap_ids=[
            str(getattr(c, "capability_id", "") or getattr(c, "id", "") or "")
            for c in (getattr(ctx.plan, "capabilities", None) or [])
        ],
        blueprint=ctx.blueprint,
    )
    sources["acceptance"] = fallback_source
    ctx.workspace.write_text(".env.example", _render_platform_env_example())
    # Deploy artifacts, all derived from one contract. No Render blueprint: that
    # platform is gone, and a blueprint for a suspended account is the most
    # deploy-shaped file in the delivery, so it is the one a customer reaches
    # for first.
    ctx.workspace.write_text(
        Path("deploy") / "contract.json", _render_deploy_contract(product_id)
    )
    ctx.workspace.write_text(
        Path("deploy") / "README.md", _render_deploy_readme(product_id)
    )
    ctx.workspace.write_text(
        Path("deploy") / "aws" / "task-definition.json",
        _render_aws_task_definition(product_id),
    )
    from app.factory.build.network_posture import POSTURE_ID, declaration_json

    ctx.workspace.write_text(Path("docs") / "network_posture.json", declaration_json())
    try:
        emit_supply_chain_artifacts(
            ctx.workspace,
            product_id=str(product_id),
            product_name=str(product_name),
            vendored_blocks=sorted(vendored),
        )
    except SupplyChainError as exc:
        raise RoleError(str(exc)) from exc
    from app.factory.build.domain_pack import DomainPackError, emit_domain_pack

    try:
        emit_domain_pack(ctx.workspace, blueprint=ctx.blueprint)
    except DomainPackError as exc:
        raise RoleError(str(exc)) from exc
    sources["deploy_scaffold"] = fallback_source
    sources["network_posture"] = POSTURE_ID
    sources["sbom"] = fallback_source
    sources["permissions"] = fallback_source
    sources["domain_pack"] = fallback_source

    from app.factory.build.converge import converge_writer_emitters

    converged = converge_writer_emitters(ctx)
    if converged.get("ok"):
        sources["emitter_parity"] = "ProductGenerator class emitters (converge)"

    from app.factory.build.coder_session import BRIEF_REL
    from app.factory.build.writer_phases import phase_acceptance_errors

    for phase_id in later_writer_phases():
        owed = phase_acceptance_errors(ctx, phase_id, compiled_brief)
        later_work = remaining_phase_cli_work(
            compiled_brief, ctx.workspace, ctx.state
        )
        if owed and should_dispatch_writer_phase(
            phase_id, dispatch, remaining_work=later_work
        ):
            later_brief = compile_phase_brief(compiled_brief, phase_id)
            lint_or_raise(later_brief)
            from app.factory.build.architect import dispatch_text

            later_text = dispatch_text(later_brief)
            ctx.workspace.write_text(
                BRIEF_REL,
                later_text if later_text.endswith("\n") else later_text + "\n",
            )
            later = dispatch_compiled_brief(ctx, later_brief)
            record_unauthored_cli_work(ctx.state, later_work, later)
            miss = phase_authorship_miss(
                compiled_brief,
                later,
                ctx.workspace,
                later_work,
            )
            dispatch = merge_writer_phase_dispatch(dispatch, later)
            write_dispatch_receipt(ctx, compiled_brief, dispatch)
            ctx.state["brief_dispatch"] = dispatch.to_dict()
            ctx.note(
                f"brief dispatch phase {phase_id} via {later.via}: {later.detail}",
                stage="dispatch",
                phase=phase_id,
                source="writer phases",
                done=1 if later.ok else 0,
                total=1,
            )
            if miss:
                raise RoleError(miss)
        elif owed and not later_work:
            ctx.note(
                f"skip {phase_id} C-BRIEF — same gaps already landed or "
                "a prior empty CLI recorded them; plant/reuse keep-path only",
                stage="checkpoint",
                phase=phase_id,
                source="writer phases",
            )
        if phase_id == WRITER_PHASE_FRONTEND_RAG:
            from app.factory.build.rag_surface import (
                FACTORY_GROUNDED_RAG_SOURCE,
                emit_factory_grounded_rag_surface,
            )

            ws = Path(getattr(ctx.workspace, "workspace", ctx.workspace))
            planted = emit_factory_grounded_rag_surface(ws, compiled_brief)
            if planted:
                sources["rag_surface"] = FACTORY_GROUNDED_RAG_SOURCE
                ctx.note(
                    "factory-grounded RAG ingest/query keep-path "
                    + ",".join(planted),
                    stage="dispatch",
                    phase=phase_id,
                    source=FACTORY_GROUNDED_RAG_SOURCE,
                    done=1,
                    total=1,
                )
        try:
            accept_writer_phase(ctx, phase_id, compiled_brief)
        except PhaseAcceptHalt as exc:
            raise RoleError(str(exc)) from exc
        checkpoint_landed_phase(ctx, phase_id)

    by_coder = len(coding_agent_artifact_ids(sources))
    if not by_coder:
        # 0.5.2: an in-process WRITER pass with zero agent-authored
        # artifacts is the hollow green the artifact gate exists to stop.
        # Refuse here, at the entry point -- the gate is the second line.
        WRITER_NO_OUTPUT = "writer_no_output"

        raise RoleError(
            f"{WRITER_NO_OUTPUT}: zero agent-authored artifacts "
            f"({len(sources) - by_coder} templated) -- refusing to report "
            "WRITER success"
        )
    ctx.workspace.write_text(
        Path("docs") / "build_provenance.json",
        json.dumps(
            {
                "schema_version": "build_provenance.v1",
                "product_id": getattr(ctx.blueprint, "product_id", "unknown"),
                "product_name": product_name,
                "engine": "role_runner",
                "network_posture": POSTURE_ID,
                "artifact_sources": sources,
                "coder_failures": dict(ctx.state.get("coder_failures", {})),
                "coder_failure_kinds": dict(ctx.state.get("coder_failure_kinds", {})),
                "brief_dispatch": dict(ctx.state.get("brief_dispatch") or {}),
                "n_required": ctx.state.get("n_required"),
                "n_required_capabilities": ctx.state.get("n_required_capabilities"),
                "kernel_agents": {
                    "COLLECTOR": {
                        "reviews": list(ctx.state.get("agent_binding_reviews") or []),
                        "model": ctx.state.get("agent_binding_model") or "",
                    },
                    "WRITER": {"artifacts": by_coder},
                    "TESTER": "harness acceptance — not an LLM role",
                    "CLONER": "deterministic — no agent",
                    "STORE_MANAGER": "deterministic — no agent",
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    # The provenance manifest is part of WRITER's contract, not a nicety: it
    # is the FACTORY's record of who wrote what (the download route, the
    # authorship gates and adopt-green all read it). Verify the write landed
    # instead of assuming it did.
    #
    # History, because this guard once protected something else: the product's
    # release gate used to FAIL the image build without this file, and the
    # Store gate reported 0/12 on a platform whose suite passed 46 tests. This
    # guard was the answer -- but it checks the WORKSPACE, and the file is
    # withheld from what ships (builds_push.FACTORY_INTERNAL_PATHS), so the
    # same failure came back as 0/13 on a suite passing 108. The release gate
    # no longer requires the file; tests/factory/
    # test_release_gate_survives_the_export.py runs it on a tree that has been
    # through the real export filter, which is the check this never was.
    WRITER_PROVENANCE_MISSING = "writer_provenance_missing"
    if not ctx.workspace.exists(Path("docs") / "build_provenance.json"):
        raise RoleError(
            f"{WRITER_PROVENANCE_MISSING}: docs/build_provenance.json was not "
            "written to the workspace -- the factory has no record of who "
            "authored this platform's artifacts"
        )

    from app.factory.build.network_posture import PostureError, assert_workspace_posture

    try:
        assert_workspace_posture(
            ctx.workspace.workspace,
            fallback=(
                ctx.workspace.destination if ctx.workspace.staged else None
            ),
        )
    except PostureError as exc:
        raise RoleError(str(exc)) from exc
    detail = writer_contract_role_detail(written, sources)
    if ctx.work_list:
        detail += f"; reworking {len(ctx.work_list)} finding(s)"
    return RoleResult(
        ok=True,
        detail=detail,
        notes={
            "handlers": written,
            "artifact_sources": sources,
            "coder_artifacts": by_coder,
            "model_specs": specs,
            # Kept for the next rework round's ratchet: bodies of routes that
            # passed are reused verbatim instead of regenerated.
            "route_bodies": route_bodies,
        },
    )


# -- TESTER --------------------------------------------------------------


def _looks_like_email_field(field: Dict[str, Any]) -> bool:
    """The field DECLARES an address: type ``email`` or format ``email``.

    The live winery-hospitality export failed its own pilot suite because
    TESTER sent guest_email='sample' and WRITER (correctly) required '@'.
    The fix is the declaration (type/format), never the field's name: a
    name implies nothing the spec did not say.
    """
    resolved = _resolve_known_field_type(field.get("type") or "str")
    if resolved == "email":
        return True
    if resolved not in (None, "str"):
        return False
    fmt = str(field.get("format") or "").lower().replace("-", "")
    return fmt == "email"


_TYPE_ALIASES = {
    "str": "str",
    "text": "str",
    "string": "str",
    "int": "int",
    "integer": "int",
    "float": "float",
    "real": "float",
    "number": "float",
    "bool": "bool",
    "boolean": "bool",
    "datetime": "datetime",
    "timestamp": "datetime",
    "date": "date",
    "time": "time",
    "uuid": "uuid",
    "email": "email",
    # Spellings with exactly one right answer. This table is judged against
    # whatever the writer chose to call a field, and a miss refuses the whole
    # build AFTER the writer has already spent its pass -- the same shape as
    # block_obligations.DISTRIBUTIONS refusing a clone over 'bcrypt'. SQL and
    # annotation spellings of the scalars above are not new types, only new
    # names for them. Structural types (list, dict, json) are deliberately
    # NOT here: the model emitter stores every field as a scalar, so sampling
    # one would send a payload the generated model cannot hold.
    "varchar": "str",
    "char": "str",
    "bigint": "int",
    "smallint": "int",
    "long": "int",
    "double": "float",
    "decimal": "float",
    "numeric": "float",
    "money": "float",
    "currency": "float",
}

_TEMPORAL_SAMPLES = {
    "datetime": "2026-09-03T10:00:00",
    "date": "2026-09-03",
    "time": "10:00:00",
}


def _resolve_known_field_type(raw: Any) -> Optional[str]:
    """Canonical type if WRITER/LLM spelling is known; None if unknown.

    TESTER sample emission must distinguish unknown types from ``str``.
    Mapping them to ``str`` hid ``datetime`` only because that alias is
    listed; a four-key literal map then KeyError'd (sess_f48f60b / BACKEND-S).
    """
    kind = str(raw or "str").strip().lower()
    kind = (
        kind.replace("datetime.", "")
        .replace("uuid.", "")
        .replace("decimal.", "")
        .replace("optional[", "")
        .replace("]", "")
    )
    # ``str | None`` is ``Optional[str]`` in the newer spelling; only the
    # older one was unwrapped, so the modern annotation refused the build.
    kind = " | ".join(
        part for part in (p.strip() for p in kind.split("|")) if part and part != "none"
    )
    # VARCHAR(255) / NUMERIC(10, 2): the width is not part of the type.
    kind = kind.split("(", 1)[0].strip()
    if not kind:
        return "str"
    return _TYPE_ALIASES.get(kind)


#: Declared types that carry meaning beyond their storage type. A money field
#: is stored as float; its declaration is what the money contract reads.
MONEY_SEMANTIC_TYPE = "money"
_SEMANTIC_TYPES = {"money": MONEY_SEMANTIC_TYPE, "currency": MONEY_SEMANTIC_TYPE}


def _semantic_field_type(raw: Any) -> Optional[str]:
    """The declared type's meaning (e.g. money), or None for plain types."""
    kind = str(raw or "").strip().lower().split("(", 1)[0]
    kind = kind.replace("optional[", "").replace("]", "").strip()
    return _SEMANTIC_TYPES.get(kind)


def _normalize_field_type(raw: Any) -> str:
    """Map LLM/SQL/annotation spellings onto the emitter's type set."""
    return _resolve_known_field_type(raw) or "str"


def _python_annotation(field: Dict[str, Any]) -> str:
    """Compilable dataclass annotation. datetime/date/time persist as ISO str."""
    ftype = _normalize_field_type(field.get("type") or "str")
    return {
        "str": "str",
        "int": "int",
        "float": "float",
        "bool": "bool",
        "datetime": "str",
        "date": "str",
        "time": "str",
        "uuid": "str",
        "email": "str",
    }.get(ftype, "str")


def _assert_fields_sampleable(specs: Dict[str, Any]) -> None:
    """Refuse unknown field types as RoleError, never an uncaught KeyError."""
    unknown: List[str] = []
    for cap_id, spec in specs.items():
        if not isinstance(spec, dict):
            continue
        for field in spec.get("fields") or []:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            raw = field.get("type") or "str"
            if _resolve_known_field_type(raw) is None:
                unknown.append(f"{cap_id}.{field['name']}: {raw!r}")
    if unknown:
        raise RoleError(
            "TESTER cannot sample field type(s): "
            + "; ".join(unknown)
            + ". Known types: str, int, float, bool, datetime, date, time, "
            "uuid, email (and aliases text/string/integer/boolean/timestamp/"
            "real/number)"
        )


def _model_roundtrip_literal(field: Dict[str, Any]) -> str:
    """Python source fragment for ``tests/test_models.py`` sample records.

    The live residential-lettings crash used a four-key map
    ``str/int/float/bool`` and KeyError'd on ``type: datetime``. Common
    WRITER types get a constraint-aware literal; anything else is
    RoleError so RoleRunner records RUN_FAILED instead of a thread crash.
    """
    raw = field.get("type") or "str"
    if _resolve_known_field_type(raw) is None:
        name = field.get("name")
        raise RoleError(
            f"TESTER cannot sample field {name!r}: unknown type {raw!r}"
        )
    return repr(_sample_value(field))


def _temporal_sample(field: Dict[str, Any]) -> str | None:
    """ISO sample for a field DECLARED temporal (type or format), else None.

    Live veterinary-care: scheduled_time / duration_minutes / service_type.
    The word "sample" is type-valid as str and rejected as a time -- so the
    spec declares the type; the name decides nothing.
    """
    fmt = str(field.get("format") or "").lower().replace("-", "")
    ftype = _normalize_field_type(field.get("type") or "str")
    if ftype == "datetime" or fmt in ("datetime", "timestamp", "iso8601"):
        return _TEMPORAL_SAMPLES["datetime"]
    if ftype == "date" or fmt == "date":
        return _TEMPORAL_SAMPLES["date"]
    if ftype == "time" or fmt == "time":
        return _TEMPORAL_SAMPLES["time"]
    return None


def _sample_value(field: Dict[str, Any]) -> Any:
    """A value that satisfies every constraint the field DECLARES.

    Typing alone is not enough. An agent-designed field often carries a
    vocabulary or a range, and a generic "sample" is type-valid but
    domain-invalid -- the route rejects it, the gate fails, and the rework
    loop cannot recover because the only way for the writer to pass would be
    to delete its own validation. What the field is CALLED decides nothing:
    a vocabulary, a type, a format or a bound must be declared (the spec
    miner aligns the spec to what the handler enforces), and a field that
    declares nothing gets its type's neutral sample.
    """
    if field.get("allowed_values"):
        # A Store-deliverable value when the vocabulary holds one (a
        # notification channel), else the first declared value.
        return sample_channel_value(field["allowed_values"])
    if _looks_like_email_field(field):
        return "guest@example.com"
    temporal = _temporal_sample(field)
    if temporal is not None:
        return temporal
    ftype = _normalize_field_type(field.get("type") or "str")
    if ftype in ("int", "float"):
        lo, hi = field.get("min"), field.get("max")
        if lo is not None:
            # min=0 is a valid bound; 0 is also falsy. LLM handlers often
            # write ``if not payload.get("capacity")`` so prefer a positive
            # sample that still satisfies the bound.
            if lo <= 0 and (hi is None or hi >= 1):
                return 1 if ftype == "int" else 1.0
            return lo
        if hi is not None:
            return hi if hi < _SAMPLE_VALUES[ftype] else _SAMPLE_VALUES[ftype]
    return _SAMPLE_VALUES.get(ftype, "sample")


def _sample_payload(spec: Dict[str, Any]) -> Dict[str, Any]:
    """A valid instance of the entity, built from its own spec.

    Includes every declared field (required and optional) so pilot
    ``test_every_capability_route_accepts_payload`` never omits a column the
    route/handler guard demands. Values honour vocabulary, bounds, and
    email-shaped names via ``_sample_value``.
    """
    out: Dict[str, Any] = {}
    for field in spec.get("fields", []) or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        if "type" not in field:
            field = {**field, "type": "str"}
        out[str(field["name"])] = _sample_value(field)
    return out


def _field_default(field: Dict[str, Any]) -> str:
    """Python literal for the dataclass default, valid under the constraints."""
    if field.get("allowed_values"):
        return repr(field["allowed_values"][0])
    ftype = _normalize_field_type(field.get("type") or "str")
    if ftype in ("int", "float") and field.get("min") is not None:
        return repr(field["min"])
    temporal = _temporal_sample(field)
    if temporal is not None:
        return repr(temporal)
    return _PY_DEFAULTS.get(ftype, '""')


def _constraints_of(spec: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for f in spec.get("fields", []):
        c = {
            k: f[k]
            for k in ("allowed_values", "min", "max", "format", "required")
            if f.get(k) is not None
        }
        if c:
            out[f["name"]] = c
    return out


def run_tester(ctx: RoleContext) -> RoleResult:
    """Acceptance inspector: write the code-phase suite. tests/ only.

    Factory-gated tests judge the coder's 20–30 minute pass: imports,
    dispatch load, handle() returning a mapping, model round-trip, routes
    answering HTTP 200 JSON. Store-backed ``ok: True`` and nested-error
    scans are ``@pytest.mark.pilot`` — a complete platform as designed is
    a later phase, not this gate.

    The harness's acceptance IS the tester. TESTER is not an LLM role.
    GET /v1/gates describes this coverage; it does not run the suite.

    On a pilot cycle the suite is already on disk. Rewriting it against an
    empty in-memory spec (worker restart / first pilot TESTER pass) would
    change payloads and hide the agent's own cases — keep the files then.

    On pilot *rework* (``work_list`` set after a red PRODUCT suite) WRITER
    may have regenerated handlers and ``model_specs``. Keeping the frozen
    code-phase payloads then burns the rework budget against stale probes.
    Re-emit the suite from the current specs so ``pytest -m pilot`` matches
    the workspace under test.
    """
    # The roster the route suite asserts comes from the same role contracts
    # that stamp app/jobs.py (build/kernel_publish.py) -- one source.
    from app.factory.build.deploy import render_health_route_test as _render_health_route_test
    from app.factory.build.kernel_publish import render_roster_test as _render_roster_test

    # G1 ownership, the honest half: remember which tests THIS function
    # stamps, as a snapshot delta on the workspace's written-list. The
    # runner's factory_test_files is "written under tests/ during the TESTER
    # phase" -- which also catches files a wrapper or a broken TESTER injects
    # after this function returns (that is the point: the writer may not
    # edit any of them). behavior_test_files is strictly what the canonical
    # emitters produced, so an assertion failing in one of THESE is the
    # product failing a behavior check by construction, while an assertion
    # in an injected file stays the factory's own fault.
    _stamped_before = {
        str(rel).replace("\\", "/")
        for rel in (getattr(ctx.workspace, "written", None) or [])
    }
    if str(ctx.state.get("build_cycle") or "") == "pilot":
        existing = Path("tests") / "test_smoke.py"
        if ctx.workspace.exists(existing) and not ctx.work_list:
            return RoleResult(
                ok=True,
                detail=(
                    "pilot cycle: existing suite kept; gate will run pytest -m pilot"
                ),
                vendored_blocks=tuple(sorted(set(ctx.state.get("vendored_blocks", ())))),
            )
    caps = [c.capability_id.replace("-", "_") for c in ctx.plan.capabilities]
    vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
    specs = {
        cid: dict(spec) if isinstance(spec, dict) else spec
        for cid, spec in dict(ctx.state.get("model_specs") or {}).items()
    }
    # The declared schema is the product's own app/models.py, read on EVERY
    # pass -- the same FIELDS/CONSTRAINTS the WRITER gate's probe builds its
    # payload from. Sampling only the captured state spec meant a field the
    # writer declared in a rework round never reached these suites, and the
    # same test failed again (live sess_1fbea5094c2a4a57).
    from app.factory.build.product_suites import DOMAIN_SUITE, ROUTES_SUITE, SMOKE_SUITE
    from app.factory.build.declared_specs import (
        merge_declared_specs,
        product_root,
        specs_from_product_models,
    )

    _root = product_root(
        (getattr(ctx.workspace, "workspace", None), getattr(ctx.workspace, "destination", None))
    )
    if _root is not None:
        specs = merge_declared_specs(specs, specs_from_product_models(_root))
    # Late align: coder rework may have rewritten handlers after WRITER's
    # first align, or left vocab/type on the handler that the spec never
    # declared. Re-mine on-disk handlers AND the route (``_constraint_guard``
    # + coder body) so _sample_payload matches the body TESTER is about
    # to exercise. #311 mined the handler only; sess_1fd1d54c's refuse
    # lived on the route.
    previous_routes = dict(ctx.state.get("route_bodies") or {})
    routes_text = ""
    routes_rel = Path("app") / "routes.py"
    if ctx.workspace.exists(routes_rel):
        routes_text = ctx.workspace.read_text(routes_rel)
    for cap in ctx.plan.capabilities:
        cid = cap.capability_id
        handler_rel = Path("app") / "actions" / f"{cid.replace('-', '_')}.py"
        parts: List[str] = []
        if ctx.workspace.exists(handler_rel):
            parts.append(ctx.workspace.read_text(handler_rel))
        route_src = previous_routes.get(cid) or extract_capability_route_source(
            routes_text, cid
        )
        if route_src:
            parts.append(route_src)
        if not parts:
            existing = specs.get(cid)
            if isinstance(existing, dict) and existing:
                specs[cid], _env = ensure_record_envelope(existing)
            continue
        specs[cid], _changed = align_spec_to_handler_source(
            specs.get(cid) or {},
            "\n".join(parts),
        )
        specs[cid], _env = ensure_record_envelope(specs[cid])
    # A mined or state-carried spec can lack ``entity`` (the worker's
    # handler format does not always carry it). Every suite emitter below
    # indexes ``spec["entity"]`` (data_lifecycle, domain_acceptance,
    # deploy) — a missing key there crashed the TESTER thread with
    # KeyError 'entity' (live sess_620b8581fb224bea run3). Default it to
    # the capability id, the same fallback the entities map uses.
    for cid, spec in list(specs.items()):
        if isinstance(spec, dict) and not spec.get("entity"):
            spec["entity"] = cid.replace("-", "_")
    _assert_fields_sampleable(specs)
    entities = {
        cap.capability_id.replace("-", "_"): specs.get(cap.capability_id, {}).get(
            "entity", cap.capability_id.replace("-", "_")
        )
        for cap in ctx.plan.capabilities
    }

    # The Factory's bootstrap at its own path -- never over the product's
    # tests/conftest.py (roles_constants.CONFTEST_REL).
    ctx.workspace.write_text(CONFTEST_REL, _CONFTEST)

    # -- capability + offline dispatch ------------------------------------
    smoke = [
        '"""The platform runs, and it runs without the store."""',
        "",
        "import os",
        "",
        "import pytest",
        "",
        "",
        "def test_capabilities_import():",
    ]
    for name in caps:
        smoke.append(f"    from app.actions import {name}")
        smoke.append(f"    assert {name}.CAPABILITY_ID")
    if not caps:
        smoke.append("    pass")
    default_actions = {
        b: contract["default_action"]
        for b in vendored
        if (contract := _block_contract(ctx, b)).get("default_action")
    }
    smoke += [
        "",
        "",
        "def test_dispatch_runs_offline():",
        '    """No store env, no network: every block must LOAD and EXECUTE from',
        "    vendor/. A block-level refusal of this bare probe is still local",
        "    execution; an import failure is the store dependency this test",
        '    exists to catch."""',
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):',
        "        os.environ.pop(var, None)",
        "    from app.dispatch import execute, load_block",
        f"    for block_id in {vendored!r}:",
        "        load_block(block_id)",
        f"    actions = {default_actions!r}",
        f"    for block_id in {vendored!r}:",
        "        try:",
        "            result = execute(block_id, {}, action=actions.get(block_id))",
        "        except RuntimeError as exc:",
        "            # The block ran and refused the empty probe -- fine here;",
        "            # the pilot test below demands Store-backed success.",
        "            # An import error is never fine.",
        '            assert "No module named" not in str(exc), (block_id, exc)',
        '            assert "cannot import" not in str(exc), (block_id, exc)',
        "        else:",
        "            assert isinstance(result, dict), block_id",
    ]
    if vendored:
        smoke += [
            "",
            "",
            "def test_kit_packs_present():",
            '    """The download is a product tree: kits/ next to vendor/blocks."""',
            "    from pathlib import Path as _Path",
            '    kits = _Path(__file__).resolve().parents[1] / "kits"',
            '    assert kits.is_dir(), "kits/ missing from the delivered platform"',
            '    assert list(kits.glob("*/manifest.json")), "no kit pack manifests"',
        ]
    smoke += ["", "", *render_capability_recorder()]
    smoke += [
        "",
        "",
        "def test_every_capability_handle_returns_mapping():",
        '    """Code-phase: the coder wired handle() and it returns a dict.',
        "    Store ok: False or a Store exception is not this gate.\"\"\"",
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY"):',
        "        os.environ.pop(var, None)",
        "    failures = []",
    ]
    for cap in ctx.plan.capabilities:
        name = cap.capability_id.replace("-", "_")
        sample = _sample_payload(specs.get(cap.capability_id, {}))
        smoke += [
            f"    from app.actions import {name}",
            "    try:",
            f"        out = {name}.handle({sample!r})",
            "    except Exception as exc:",
            "        out = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}",
            "    if not isinstance(out, dict):",
            f"        failures.append('{name} handle() must return a dict, got '"
            " + type(out).__name__)",
            f"        _record_capability_failure({cap.capability_id!r})",
        ]
    if caps:
        smoke.append('    assert not failures, "; ".join(failures)')
    else:
        smoke.append("    pass")
    smoke += [
        "",
        "",
        "@pytest.mark.pilot",
        "def test_every_capability_executes_end_to_end():",
        '    """Pilot: each handler runs its blocks on a spec payload and',
        "    Store must accept it. Not the factory code-phase gate.\"\"\"",
        '    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY"):',
        "        os.environ.pop(var, None)",
        "    import json as _json",
        "    failures = []",
    ]
    for cap in ctx.plan.capabilities:
        name = cap.capability_id.replace("-", "_")
        sample = _sample_payload(specs.get(cap.capability_id, {}))
        # Collect, never abort: a run that stops at the first failing
        # capability hides the rest. The nested scan is the fake-green
        # stop: a handler that reports ok around a failed block call is
        # caught here no matter who wrote it.
        smoke += [
            f"    from app.actions import {name}",
            f"    out = {name}.handle({sample!r})",
            "    if not isinstance(out, dict):",
            f"        failures.append('{name} returned a non-dict: ' + repr(out)[:120])",
            f"        _record_capability_failure({cap.capability_id!r})",
            f"    elif out.get({OK_KEY!r}) is False:",
            f"        failures.append('{name} rejected a payload built from its own "
            "schema: ' + str(out.get(" + repr(ERROR_KEY) + ")))",
            f"        _record_capability_failure({cap.capability_id!r})",
            # A failed block call nested in an ok answer. The statuses are the
            # DECLARED block-result contract: the Factory's own error envelope
            # (roles_constants._error_envelope, "status": "error") and the
            # workflow step results ("failed"). default=str keeps a handler
            # that returns bytes (a rendered document) serialisable.
            "    elif '\\\"status\\\": \\\"error\\\"' in "
            "_json.dumps(out, default=str) or "
            "'\\\"status\\\": \\\"failed\\\"' in _json.dumps(out, default=str):",
            f"        failures.append('{name} reported ok around a failed block "
            "call: ' + _json.dumps(out, default=str)[:300])",
            f"        _record_capability_failure({cap.capability_id!r})",
        ]
    if caps:
        smoke.append('    assert not failures, "; ".join(failures)')
    else:
        smoke.append("    pass")
    ctx.workspace.write_text(Path(SMOKE_SUITE), "\n".join(smoke) + "\n")

    # -- models round-trip through persistence -----------------------------
    model_lines = [
        '"""Models must survive a round trip through sqlite."""',
        "",
        "from app import store",
        "from app.models import MODELS",
        "",
        "",
        "#: Tenant the generated tests act as (the platform token's tenant).",
        "TENANT = 'local'",
        "",
        "",
        "def test_every_model_round_trips():",
    ]
    # `entities` can be non-empty while `specs` is empty: no on-disk
    # handler means no sampleable fields, and the loop below then emits
    # zero record lines — leaving `def test_every_model_round_trips():`
    # bare and the generated file an IndentationError the suite reports
    # as "missing module" noise. A body must always be emitted.
    if entities and specs:
        for cap_id, spec in sorted(specs.items()):
            entity = spec.get("entity", cap_id)
            sample = {
                f["name"]: _model_roundtrip_literal(f)
                for f in spec.get("fields", [])
                if isinstance(f, dict) and f.get("name")
            }
            payload = "{" + ", ".join(f"'{k}': {v}" for k, v in sample.items()) + "}"
            model_lines += [
                f"    record = {payload}",
                f"    saved = store.save('{entity}', record, tenant_id=TENANT)",
                f"    assert saved[{RECORD_ID_KEY!r}] is not None, 'no id assigned for {entity}'",
                f"    fetched = store.get('{entity}', saved[{RECORD_ID_KEY!r}], tenant_id=TENANT)",
                f"    assert fetched is not None, '{entity} did not persist'",
                "    for key, value in record.items():",
                "        assert fetched[key] == value, (key, fetched[key], value)",
                f"    assert any(r[{RECORD_ID_KEY!r}] == saved[{RECORD_ID_KEY!r}] for r in store.list_all('{entity}', tenant_id=TENANT))",
            ]
        model_lines += [
            "",
            "",
            "def test_models_expose_their_fields():",
            "    assert MODELS, 'no models were generated'",
            "    for cap_id, cls in MODELS.items():",
            "        instance = cls.from_dict({})",
            f"        assert instance.to_dict()[{RECORD_ID_KEY!r}] is None",
            "        assert cls.FIELDS, cap_id",
        ]
    else:
        model_lines.append("    pass")
    ctx.workspace.write_text(
        Path("tests") / "test_models.py", "\n".join(model_lines) + "\n"
    )
    from app.factory.build.data_lifecycle import render_product_tests

    ctx.workspace.write_text(
        Path("tests") / "test_data_lifecycle.py", render_product_tests(specs)
    )
    from app.factory.build.deploy import render_product_tests as render_deploy_tests
    from app.factory.build.domain_acceptance import (
        render_product_tests as render_domain_tests,
    )

    ctx.workspace.write_text(
        Path("tests") / "test_deploy.py", render_deploy_tests(specs)
    )
    ctx.workspace.write_text(
        Path(DOMAIN_SUITE), render_domain_tests(specs)
    )

    # Eight suites were stamped and none touched the UI, so the agent could
    # not see a UI problem until it had yielded and ui_end_to_end rejected the
    # build -- a full rework round for something a red test shows in the same
    # pass (sess_42d244d317f042b2 lost a 10m22s writer pass to
    # ui_not_wired_end_to_end). The prompt already tells the agent to run
    # pytest -m "not pilot" before yielding; this puts the UI in that run.
    # Same bar as the gate, detected earlier: it asserts only what the gate
    # treats as fatal, never its advisories.
    from app.factory.build.ui_e2e import render_ui_tests

    ctx.workspace.write_text(
        Path("tests") / "test_ui_operator_flow.py", render_ui_tests(specs)
    )

    # negative_floor asks each capability for four counter-cases. Asking the
    # coder and grading it afterwards costs a rework round for the
    # difference; the Factory knows the shape of all four from the spec, so
    # it writes the harness and the agent extends it with the domain
    # judgement only it has.
    # tests/ is TESTER's lane: the writer may not author the tests that
    # judge it, so the backup-restore roundtrip is emitted here rather than
    # with the rest of the ops substrate.
    from app.factory.build.observability import render_backup_restore_test

    ctx.workspace.write_text(
        Path("tests") / "test_backup_restore.py", render_backup_restore_test()
    )

    from app.factory.build.negative_floor import render_negative_tests

    ctx.workspace.write_text(
        Path("tests") / "test_negative_floor.py",
        render_negative_tests(
            specs,
            {cid: _sample_payload(specs.get(cid) or {}) for cid in specs},
        ),
    )

    # The authority on a capability calling a DECLARED placeholder connector:
    # stamped here, so it is a behavior test the writer may not edit, and a
    # writer test that disagrees with it is that test's defect.
    from app.factory.build.placeholder_connectors import (
        CONTRACT_TEST,
        render_contract_tests,
    )

    contract = render_contract_tests(
        ctx.blueprint,
        {cid: _sample_payload(specs.get(cid) or {}) for cid in specs},
    )
    if contract:
        ctx.workspace.write_text(Path(CONTRACT_TEST), contract)

    # -- routes return their documented shape ------------------------------
    route_lines = [
        '"""The HTTP surface answers, and what it answers has the right shape."""',
        "",
        "import pytest",
        "from fastapi.testclient import TestClient",
        "",
        "from app.main import app",
        "",
        "client = TestClient(app)",
        "AUTH = {'Authorization': 'Bearer ' + __import__('os').environ.get('PLATFORM_TOKEN', 'dev-local-token')}",
        "",
        "",
        # The list reader every Factory check shares (rejection_contract): by
        # shape, never a guessed key -- the brief declares no list key.
        *LISTED_RECORDS_SRC.strip("\n").splitlines(),
        "",
        "",
        *render_payload_helpers(),
        "",
        "",
        *_render_health_route_test(),
        "",
        "",
        *_render_roster_test(),
        "",
        "",
        "def test_every_capability_route_answers():",
        '    """Code-phase: each capability POST answers HTTP 200 JSON.',
        "    Store ok: False is allowed here — acceptance is the pilot test.\"\"\"",
    ]
    from app.factory.build.placeholder_connectors import placeholder_connectors

    declared = placeholder_connectors(ctx.blueprint)
    if caps:
        route_lines.append("    failures = []")
        for cap in ctx.plan.capabilities:
            name = cap.capability_id.replace("-", "_")
            spec = specs.get(cap.capability_id, {})
            sample = _sample_payload(spec)
            if cap.capability_id in declared:
                route_lines += _declared_refusal_lines(
                    name, sample, declared[cap.capability_id], cap.capability_id
                )
                continue
            route_lines += [
                f"    payload = {sample!r}",
                f'    resp, _corr = _post_accepting("/v1/{name}", payload, AUTH, {cap.capability_id!r})',
                f"    if resp.status_code not in {tuple(ACCEPT_STATUSES)!r}:",
                f"        failures.append('{name}: HTTP ' + str(resp.status_code)"
                " + ': ' + resp.text[:200] + (' [tester already tried the values the"
                " route named: ' + '; '.join(_corr) + ']' if _corr else ''))",
                "    else:",
                "        try:",
                "            body = resp.json()",
                "        except Exception:",
                f"            failures.append('{name}: response is not JSON')",
                "        else:",
                "            if not isinstance(body, dict):",
                f"                failures.append('{name}: JSON body is not a dict')",
                "            # A connector with no credentials answers as a DECLARED",
                "            # STUB -- ok:true, naming what is unavailable. That IS the",
                "            # right answer before the operator configures it. Judged",
                "            # only when THIS response declares itself a stub at the top",
                "            # level; a stubbed block nested inside a capability's result",
                "            # is that capability's business, and ok:false stays allowed.",
                "            elif 'blocks_unavailable' in body:",
                "                unavailable = body.get('blocks_unavailable')",
                f"                if body.get({OK_KEY!r}) is not True or not unavailable:",
                f"                    failures.append('{name}: declared stub must be ok:true'"
                " + ' and name blocks_unavailable')",
                f'            listed = client.get("/v1/{name}", headers=AUTH)',
                f"            if listed.status_code not in {tuple(ACCEPT_STATUSES)!r}:",
                f"                failures.append('{name} list: HTTP '"
                " + str(listed.status_code))",
                "",
            ]
        route_lines.append('    assert not failures, "; ".join(failures)')
    else:
        route_lines.append("    pass")
    route_lines += [
        "",
        "",
        "@pytest.mark.pilot",
        "def test_every_capability_route_accepts_payload():",
        '    """Pilot: spec payload is accepted (ok is not False) and persisted.',
        "    Not the factory code-phase gate.\"\"\"",
    ]
    if caps:
        route_lines.append("    failures = []")
        for cap in ctx.plan.capabilities:
            name = cap.capability_id.replace("-", "_")
            spec = specs.get(cap.capability_id, {})
            sample = _sample_payload(spec)
            if cap.capability_id in declared:
                route_lines += _declared_refusal_lines(
                    name, sample, declared[cap.capability_id], cap.capability_id
                )
                continue
            route_lines += [
                f"    payload = {sample!r}",
                f'    resp, _corr = _post_accepting("/v1/{name}", payload, AUTH, {cap.capability_id!r})',
                f"    if resp.status_code not in {tuple(ACCEPT_STATUSES)!r}:",
                f"        failures.append('{name}: HTTP ' + str(resp.status_code)"
                " + ': ' + resp.text[:200] + (' [tester already tried the values the"
                " route named: ' + '; '.join(_corr) + ']' if _corr else ''))",
                f'    elif resp.json().get({OK_KEY!r}) is False:',
                f"        failures.append('{name} rejected a payload built from its "
                "own schema: ' + str(resp.json().get(" + repr(ERROR_KEY) + ")) + (' [tester already"
                " tried the values the route named: ' + '; '.join(_corr) + ']' if _corr"
                " else ''))",
                "    else:",
                f'        listed = client.get("/v1/{name}", headers=AUTH)',
                "        listed_body = listed.json() if listed.content else {}",
                f"        if listed.status_code not in {tuple(ACCEPT_STATUSES)!r}:",
                f"            failures.append('{name} list: HTTP '"
                " + str(listed.status_code))",
                '        elif isinstance(listed_body, dict) and '
                f'listed_body.get({OK_KEY!r}) is False:',
                f"            failures.append('{name} list refused: '"
                " + str(listed_body.get(" + repr(ERROR_KEY) + ") or listed_body)[:200])",
                "        else:",
                "            rows = _listed(listed_body)",
                "            if not rows:",
                f"                failures.append('{name} accepted a record but "
                "persisted nothing')",
                "            else:",
                f'                item_id = rows[0].get({RECORD_ID_KEY!r})',
                f'                got = client.get(f"/v1/{name}/{{item_id}}", headers=AUTH)',
                f"                if got.status_code not in {tuple(ACCEPT_STATUSES)!r}:",
                f"                    failures.append('{name} get: HTTP '"
                " + str(got.status_code))",
                f'                missing = client.get("/v1/{name}/999999", headers=AUTH)',
                f"                if missing.status_code != {CROSS_TENANT_READ_STATUS}:",
                f"                    failures.append('{name} missing id: HTTP '"
                f" + str(missing.status_code) + ' (expected {CROSS_TENANT_READ_STATUS})')",
                "",
            ]
        route_lines.append('    assert not failures, "; ".join(failures)')
    else:
        route_lines.append("    pass")
    ctx.workspace.write_text(
        Path(ROUTES_SUITE), "\n".join(route_lines) + "\n"
    )

    admitted: List[Dict[str, Any]] = []
    case_model = ""

    if str(ctx.state.get("build_cycle") or "") == "pilot" and ctx.work_list:
        detail = (
            f"pilot rework: suite refreshed from model_specs for {len(caps)} "
            "capability(ies); gate will run pytest -m pilot"
        )
    else:
        detail = (
            f"code-phase suite written for {len(caps)} capability(ies): import, "
            "dispatch load, handle() mapping, persistence, route JSON; "
            "Store-backed execute-all is @pytest.mark.pilot"
        )
    if admitted:
        detail += f"; coding agent added {len(admitted)} domain case(s)"
    ctx.state["behavior_test_files"] = sorted(
        rel
        for rel in {
            str(r).replace("\\", "/")
            for r in (getattr(ctx.workspace, "written", None) or [])
        }
        - _stamped_before
        if rel.startswith("tests/")
    )
    return RoleResult(
        ok=True,
        detail=detail,
        notes={
            "capabilities": caps,
            "vendored": vendored,
            "entities": entities,
            "agent_domain_cases": admitted,
            "agent_domain_model": case_model,
            "model_specs": specs,
        },
    )


def _payload_constraint_violations(
    payload: Dict[str, Any], spec: Dict[str, Any]
) -> List[str]:
    """Field-constraint breaks, used to admit TESTER agent cases."""
    hits: List[str] = []
    for name, rules in _constraints_of(spec).items():
        if name not in payload:
            continue
        value = payload[name]
        allowed = rules.get("allowed_values")
        if allowed is not None and value not in allowed:
            hits.append(name)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        low, high = rules.get("min"), rules.get("max")
        if low is not None and value < low:
            hits.append(name)
        elif high is not None and value > high:
            hits.append(name)
    return hits


def _is_payload_mutation(sample: Dict[str, Any], proposed: Dict[str, Any]) -> bool:
    """True when proposed is a mutation of the spec-derived sample, not a replacement."""
    if not isinstance(proposed, dict) or not sample:
        return False
    extra = set(proposed) - set(sample)
    if extra:
        return False
    if not proposed:
        return False
    return proposed != sample


def _tester_agent_cases(
    ctx: RoleContext, specs: Dict[str, Any]
) -> tuple:
    """Ask the coding agent for extra domain cases; admit only valid mutations."""
    from app.factory.coder import CoderError, coder_enabled, propose_domain_test_cases

    if not coder_enabled():
        return [], ""
    if _budget_too_low(ctx, "tester domain cases"):
        return [], ""
    samples = []
    sample_by_id: Dict[str, Dict[str, Any]] = {}
    for cap in ctx.plan.capabilities:
        spec = specs.get(cap.capability_id, {})
        sample = _sample_payload(spec)
        sample_by_id[cap.capability_id] = sample
        samples.append(
            {
                "id": cap.capability_id,
                "description": getattr(cap, "notes", "") or cap.capability_id,
                "sample_payload": sample,
            }
        )
    try:
        result = propose_domain_test_cases(
            product_name=getattr(ctx.blueprint, "product_name", "platform"),
            vertical=getattr(ctx.blueprint, "vertical", "product"),
            capabilities=samples,
        )
    except (CoderError, Exception) as exc:  # noqa: BLE001 -- extras are optional
        ctx.state.setdefault("coder_failures", {})["TESTER.domain_cases"] = str(exc)
        ctx.note(f"coding agent skipped tester domain cases: {exc}", stage="tester")
        return [], ""

    admitted: List[Dict[str, Any]] = []
    for case in result.get("cases") or []:
        cap_id = case.get("capability_id")
        sample = sample_by_id.get(cap_id) or {}
        payload = case.get("payload") or {}
        if not _is_payload_mutation(sample, payload):
            continue
        spec = specs.get(cap_id) or {}
        violations = _payload_constraint_violations(payload, spec)
        expect = case.get("expect")
        # Only admit reject-cases the kernel can actually enforce, and
        # accept-cases that satisfy the spec. Live TESTER burned three
        # rework rounds on "revenue cannot be negative" when no field min
        # existed, so the route accepted the payload.
        if expect == "reject" and not violations:
            continue
        if expect != "reject" and violations:
            continue
        admitted.append(case)
    return admitted, str(result.get("model") or "")


def _render_agent_domain_tests(cases: List[Dict[str, Any]]) -> str:
    """Kernel-owned file of agent-proposed cases. TESTER writes this, not WRITER."""
    lines = [
        '"""Additional domain cases proposed by the coding agent under TESTER.',
        "",
        "These are mutations of spec-derived payloads. They cannot replace the",
        "kernel suite in test_routes.py / test_models.py. The file is named",
        "agent_domain_cases.py (no test_ prefix) so pytest does not collect it",
        "into the gated suite.",
        '"""',
        "",
        "from fastapi.testclient import TestClient",
        "",
        "from app.main import app",
        "",
        "client = TestClient(app)",
        "AUTH = {'Authorization': 'Bearer ' + __import__('os').environ.get('PLATFORM_TOKEN', 'dev-local-token')}",
        "",
        "",
        "def test_agent_domain_cases():",
        "    failures = []",
    ]
    for i, case in enumerate(cases):
        name = str(case["capability_id"]).replace("-", "_")
        payload = case["payload"]
        expect = case["expect"]
        reason = (case.get("reason") or "").replace("\\", "\\\\").replace("'", "\\'")
        lines += [
            f"    payload_{i} = {payload!r}",
            f'    resp_{i} = client.post("/v1/{name}", json=payload_{i}, headers=AUTH)',
        ]
        if expect == "reject":
            lines += [
                f"    if resp_{i}.status_code in {tuple(ACCEPT_STATUSES)!r} and resp_{i}.json().get({OK_KEY!r}) is not False:",
                f"        failures.append({reason!r} or 'case {i} should have been rejected')",
            ]
        else:
            lines += [
                f"    if resp_{i}.status_code not in {tuple(ACCEPT_STATUSES)!r} or resp_{i}.json().get({OK_KEY!r}) is False:",
                f"        failures.append({reason!r} or 'case {i} should have been accepted')",
            ]
    lines += [
        "    assert not failures, '; '.join(failures)",
        "",
    ]
    return "\n".join(lines) + "\n"


# -- STORE_MANAGER -------------------------------------------------------


def run_store_manager(ctx: RoleContext) -> RoleResult:
    """Store registrar: register what this build took from the Store.

    Code-phase 5/5 records the clone register and applies no Store op
    (historical). The pilot cycle applies authorised ``STORE_READ`` for
    every clone. A live Store URL additionally runs
    ``STORE_RUN_COMPATIBILITY``. When ``CEREBRUM_API_URL`` is unset the
    gate still passes with an honest ``store_unwired`` flag — local reads
    only. No agent. Published on the product as ``GET /v1/provenance``.
    """
    from app.factory.store_manager import StoreOp, assert_store_op_allowed

    vendored = sorted(set(ctx.state.get("vendored_blocks", ())))
    cycle = str(ctx.state.get("build_cycle") or "code")
    ops: list = []
    store_unwired = False
    if cycle == "pilot":
        for bid in vendored:
            assert_store_op_allowed(StoreOp.STORE_READ)
            ops.append(
                {
                    "op": StoreOp.STORE_READ.value,
                    "block_id": bid,
                    "source": "clone_register",
                }
            )
        store_unwired = not (os.getenv("CEREBRUM_API_URL") or "").strip()
        if not store_unwired:
            assert_store_op_allowed(StoreOp.STORE_RUN_COMPATIBILITY)
            ops.append(
                {
                    "op": StoreOp.STORE_RUN_COMPATIBILITY.value,
                    "source": "store_url",
                }
            )
        detail = f"registered {len(vendored)} clone(s); applied {len(ops)} store op(s)"
        if store_unwired:
            detail += "; store unwired — local STORE_READ only"
    else:
        detail = f"registered {len(vendored)} clone(s); no store op applied"
    return RoleResult(
        ok=True,
        detail=detail,
        vendored_blocks=tuple(vendored),
        notes={
            "registered": vendored,
            "store_ops": ops,
            "store_unwired": store_unwired,
        },
    )


ROLE_IMPLEMENTATIONS = {
    BuildRole.COLLECTOR: run_collector,
    BuildRole.CLONER: run_cloner,
    BuildRole.WRITER: run_writer,
    BuildRole.TESTER: run_tester,
    BuildRole.STORE_MANAGER: run_store_manager,
}
