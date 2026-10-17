"""REUSE keep-path schema-sample accept contract (C-BRIEF).

A REUSE handler calls Store blocks with ``execute(block_id, payload,
action=...)``. The action each block expects is the BLOCK's contract, so it is
read from the block itself -- ``inputs[name=action|operation].default`` in its
block.json, else the default its own code declares
(``params.get("action", "<default>")``), else the first action its code
compares against. The Factory keeps no table of answers: a hand-kept map once
told every coder to call ``capture`` with ``extract`` and ``validation`` with
``validate``, both of which the Store answers with "Unknown action".

A block whose code dispatches on no action needs none; the emitted dispatch
omits ``action`` when it is None.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.persist_accept import persist_handler_rel, persist_workspace_root
from app.factory.build.product_gate import GATE_SCOPES
from app.factory.build.workspace import relpath_exists, relpath_read_text
from app.factory.build.reuse_lookup import (
    load_local_block_json,
    local_block_json_candidates,
)
from app.factory.build.workflow_accept import (
    PRODUCT_EVENT_BUS_STEP_0_HALT,
    PRODUCT_EVENT_BUS_STEP_CLASS,
    PRODUCT_WORKFLOW_RESULT_HALT,
)

PRODUCT_UNKNOWN_ACTION_HALT = "Unknown action"
PRODUCT_UNKNOWN_ACTION_NONE_HALT = "Unknown action: None"
#: A result-key rewrite that turns ``name['result'] =`` into a call.
PRODUCT_ASSIGN_TO_CALL_HALT = "SyntaxError: cannot assign to function call"
#: What TESTER prints for any capability whose schema-sample POST is refused.
PRODUCT_SCHEMA_SAMPLE_REJECT = "rejected a payload built from its own schema"
FAIL_CLOSED_MUST_REWRITE_READS = (
    "fail-closed keep original must still rewrite reads"
)
REUSE_ACCEPT_CHECK = "reuse_accept"
WRITER_REUSE_ACCEPT_HALT = (
    "WRITER [check:reuse_accept] failed — REUSE handler cannot accept "
    "its own schema sample (Unknown action / action=None)"
)
REUSE_ACCEPT_MISS = "reuse/accept miss"

_ACTION_INPUT_NAMES = frozenset({"action", "operation"})
def _tree(source: str) -> Optional[ast.Module]:
    try:
        return ast.parse(source or "")
    except SyntaxError:
        return None


def _names_action(node: ast.AST) -> bool:
    """``action`` / ``operation`` as the thing being read: a bare name, an
    attribute, ``x["action"]`` or ``x.get("action", ...)``."""
    if isinstance(node, ast.Name):
        return node.id in _ACTION_INPUT_NAMES
    if isinstance(node, ast.Attribute):
        return node.attr in _ACTION_INPUT_NAMES
    if isinstance(node, ast.Subscript):
        key = node.slice
        return isinstance(key, ast.Constant) and key.value in _ACTION_INPUT_NAMES
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (
            node.func.attr == "get"
            and bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _ACTION_INPUT_NAMES
        )
    return False


def _identifier(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
        return node.value
    return None


def _assignment(tree: ast.Module, name: str) -> Optional[ast.stmt]:
    """The first assignment to ``name`` in the module, in source order."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            found.append(node)
    return min(found, key=lambda n: (n.lineno, n.col_offset)) if found else None


def _assigned_literal(text: str, name: str) -> Any:
    tree = _tree(text)
    node = _assignment(tree, name) if tree is not None else None
    if node is None:
        return None
    try:
        return ast.literal_eval(node.value)
    except (ValueError, SyntaxError, TypeError):
        return None



class ReuseAcceptHalt(ValueError):
    """WRITER must not claim done: REUSE schema-sample would Unknown action."""


def _passes_action_none(text: str) -> bool:
    """A call to ``execute`` / ``<x>.execute`` with ``action=None``, read
    from the syntax tree (never a pattern over the text)."""
    import ast

    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "execute":
            continue
        for kw in node.keywords:
            if kw.arg == "action" and isinstance(kw.value, ast.Constant) and kw.value.value is None:
                return True
    return False


def _harvest_candidate_ids(block_id: str) -> List[str]:
    """Exact id plus the Store ``_v2`` / kit-shelf alias (formula_executor)."""
    bid = str(block_id or "").strip()
    if not bid:
        return []
    ids = [bid]
    if bid.endswith("_v2") and len(bid) > 3:
        ids.append(bid[:-3])
    else:
        ids.append(f"{bid}_v2")
    seen: Dict[str, None] = {}
    for item in ids:
        if item:
            seen.setdefault(item, None)
    return list(seen)


def default_block_action(
    block_id: str,
    default_actions: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    """Keyword action for ``execute(..., action=)``. Never invent from payload.

    The handler's own ``BLOCK_DEFAULT_ACTIONS`` first (exact id, then the
    ``_v2`` alias), then what the Store block itself declares.
    """
    for cand_id in _harvest_candidate_ids(block_id):
        if isinstance(default_actions, Mapping):
            cand = default_actions.get(cand_id)
            if isinstance(cand, str) and cand.strip():
                return cand.strip()
    return _harvest_from_factory_vendor(str(block_id or "").strip())


def default_action_from_block_json(meta: Any) -> Optional[str]:
    """``inputs[].name == action`` (or ``operation``) default, else first option."""
    if not isinstance(meta, Mapping):
        return None
    found_operation: Optional[str] = None
    for item in meta.get("inputs") or ():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        if name not in _ACTION_INPUT_NAMES:
            continue
        harvested: Optional[str] = None
        raw = item.get("default")
        if isinstance(raw, str) and raw.strip():
            harvested = raw.strip()
        else:
            options = item.get("options") or ()
            if isinstance(options, (list, tuple)):
                for opt in options:
                    if isinstance(opt, str) and opt.strip():
                        harvested = opt.strip()
                        break
        if not harvested:
            continue
        if name == "action":
            return harvested
        if found_operation is None:
            found_operation = harvested
    return found_operation


def default_action_from_source(source: str) -> Optional[str]:
    """The action a block's code declares as its default, else the first one
    it compares against. An ``action`` default beats an ``operation`` one.
    Read from the syntax tree: ``<mapping>.get("action", "x")`` declares a
    default; ``action not in ("x", ...)`` and ``action == "x"`` compare."""
    tree = _tree(source)
    if tree is None:
        return None
    nodes = sorted(
        (n for n in ast.walk(tree) if hasattr(n, "lineno")),
        key=lambda n: (n.lineno, n.col_offset),
    )
    declared: Dict[str, str] = {}
    for node in nodes:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _ACTION_INPUT_NAMES
        ):
            value = _identifier(node.args[1])
            if value:
                declared.setdefault(node.args[0].value, value)
    if declared:
        return declared.get("action") or declared.get("operation")
    for node in nodes:
        if (
            isinstance(node, ast.Compare)
            and isinstance(node.ops[0], ast.NotIn)
            and _names_action(node.left)
            and isinstance(node.comparators[0], (ast.List, ast.Tuple, ast.Set))
        ):
            for elt in node.comparators[0].elts:
                value = _identifier(elt)
                if value:
                    return value
    for node in nodes:
        if isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.Eq):
            left, right = node.left, node.comparators[0]
            if _names_action(left) and _identifier(right):
                return _identifier(right)
            if _names_action(right) and _identifier(left):
                return _identifier(left)
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    raw = _read_text(path)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _workspace_roots(
    *roots: Any,
    workspace: Any = None,
) -> List[Path]:
    out: List[Path] = []
    if workspace is not None:
        for attr in ("workspace", "destination", "store_root"):
            raw = getattr(workspace, attr, None)
            if raw:
                out.append(Path(raw))
        if not out:
            out.append(persist_workspace_root(workspace))
    for root in roots:
        if root is None:
            continue
        out.append(Path(getattr(root, "workspace", root)))
    seen: set[str] = set()
    unique: List[Path] = []
    for path in out:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _harvest_from_factory_vendor(block_id: str) -> Optional[str]:
    """The Store's own declaration: block.json, then the block's code.

    ``block_registry/<id>/block.py`` is usually a thin wrapper; the code that
    dispatches on the action lives in the Store's ``app/blocks/<id>.py``, two
    levels above the block.json. Reading only the wrapper is why a hand-kept
    table of answers grew to fill the gap.
    """
    for cand in _harvest_candidate_ids(block_id):
        harvested = default_action_from_block_json(load_local_block_json(cand))
        if harvested:
            return harvested
        for path in local_block_json_candidates(cand):
            for code in (path.with_name("block.py"), path.parents[2] / "app" / "blocks" / f"{cand}.py"):
                harvested = default_action_from_source(_read_text(code))
                if harvested:
                    return harvested
    return None


def harvest_block_default_action(
    block_id: str,
    *roots: Any,
    workspace: Any = None,
) -> Optional[str]:
    """Vendored block.json / source, then the factory-known Store map."""
    bid = str(block_id or "").strip()
    if not bid:
        return None
    candidates = _harvest_candidate_ids(bid)
    # Path.has exists(), but exists(rel) is the RoleWorkspace protocol.
    # getattr(..., "exists") treated Path as that duck-type and crashed
    # (CEREBRUMDEV-BACKEND-W). Protocol detection requires exists(rel) /
    # read_text(rel); Path-like bound methods fall through to _workspace_roots.
    for cand in candidates:
        meta_rel = Path("vendor") / "blocks" / cand / "block.json"
        if relpath_exists(workspace, meta_rel):
            try:
                meta = json.loads(relpath_read_text(workspace, meta_rel))
            except (ValueError, TypeError):
                meta = None
            harvested = default_action_from_block_json(meta)
            if harvested:
                return harvested
        for rel in (
            Path("vendor") / "blocks" / cand / "block.py",
            Path("vendor") / "cerebrum" / "blocks" / f"{cand}.py",
        ):
            if relpath_exists(workspace, rel):
                harvested = default_action_from_source(
                    relpath_read_text(workspace, rel)
                )
                if harvested:
                    return harvested
    for root in _workspace_roots(*roots, workspace=workspace):
        for cand in candidates:
            meta = _load_json(root / "vendor" / "blocks" / cand / "block.json")
            harvested = default_action_from_block_json(meta)
            if harvested:
                return harvested
            for rel in (
                Path("vendor") / "blocks" / cand / "block.py",
                Path("vendor") / "cerebrum" / "blocks" / f"{cand}.py",
            ):
                harvested = default_action_from_source(_read_text(root / rel))
                if harvested:
                    return harvested
    return _harvest_from_factory_vendor(bid)


def harvest_block_default_actions(
    block_ids: Sequence[str],
    *roots: Any,
    workspace: Any = None,
) -> Dict[str, str]:
    """capability BLOCK_IDS → keyword actions for keep-path emit."""
    out: Dict[str, str] = {}
    for raw in block_ids or ():
        bid = str(raw or "").strip()
        if not bid:
            continue
        action = harvest_block_default_action(bid, *roots, workspace=workspace)
        if action:
            out[bid] = action
    return out


def parse_handler_block_ids(text: str) -> List[str]:
    value = _assigned_literal(text, "BLOCK_IDS")
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def parse_handler_default_actions(text: str) -> Dict[str, str]:
    value = _assigned_literal(text, "BLOCK_DEFAULT_ACTIONS")
    if not isinstance(value, dict):
        return {}
    return {
        str(key): str(val)
        for key, val in value.items()
        if str(key).strip() and isinstance(val, str) and val.strip()
    }


def apply_default_actions_to_handler(
    text: str, default_actions: Mapping[str, str]
) -> str:
    """Rewrite ``BLOCK_DEFAULT_ACTIONS = …`` so keep-path source is honest.

    The assignment is located on the syntax tree and replaced by its line
    span; absent, it goes right after ``BLOCK_IDS = …``; with neither (or
    source that does not parse), it leads the module.
    """
    blob = text or ""
    assignment = f"BLOCK_DEFAULT_ACTIONS = {dict(default_actions or {})!r}"
    tree = _tree(blob)
    if tree is None:
        return assignment + "\n" + blob
    lines = blob.splitlines(keepends=True)
    existing = _assignment(tree, "BLOCK_DEFAULT_ACTIONS")
    if existing is not None:
        start, end = existing.lineno - 1, existing.end_lineno
        indent = lines[start][: existing.col_offset]
        newline = "\n" if lines[end - 1].endswith("\n") else ""
        return "".join(lines[:start] + [indent + assignment + newline] + lines[end:])
    ids = _assignment(tree, "BLOCK_IDS")
    if ids is not None:
        end = ids.end_lineno
        head = "".join(lines[:end])
        if not head.endswith("\n"):
            head += "\n"
        return head + assignment + "\n" + "".join(lines[end:])
    return assignment + "\n" + blob


def block_takes_action(block_id: str) -> Optional[bool]:
    """Does this Store block dispatch on an action at all?

    True when its block.json declares an ``action``/``operation`` input or its
    code reads one; False when its code was found and reads neither; None when
    no code was found (unknown -- the caller fails closed).
    """
    for cand in _harvest_candidate_ids(block_id):
        meta = load_local_block_json(cand)
        if isinstance(meta, Mapping) and any(
            isinstance(i, Mapping) and i.get("name") in _ACTION_INPUT_NAMES
            for i in meta.get("inputs") or ()
        ):
            return True
        found_code = False
        for path in local_block_json_candidates(cand):
            for code in (
                path.with_name("block.py"),
                path.parents[2] / "app" / "blocks" / f"{cand}.py",
            ):
                text = _read_text(code)
                tree = _tree(text) if text else None
                if tree is None:
                    continue
                found_code = True
                if any(
                    isinstance(n, ast.Constant) and n.value in _ACTION_INPUT_NAMES
                    for n in ast.walk(tree)
                ):
                    return True
        if found_code:
            return False
    return None


def reuse_accept_handler_errors(
    text: str,
    block_ids: Optional[Sequence[str]] = None,
    *,
    capability_id: str = "",
) -> List[str]:
    """Empty = this handler can keyword-dispatch its bound blocks."""
    bids = [
        str(b).strip()
        for b in (block_ids if block_ids is not None else parse_handler_block_ids(text))
        if str(b).strip()
    ]
    if not bids:
        return []
    defaults = parse_handler_default_actions(text)
    errors: List[str] = []
    prefix = f"{capability_id}: " if capability_id else ""
    for bid in bids:
        action = default_block_action(bid, defaults)
        if action:
            continue
        if block_takes_action(bid) is False:
            continue
        errors.append(
            f"{prefix}{bid}: {REUSE_ACCEPT_MISS} — no BLOCK_DEFAULT_ACTIONS "
            f"entry ({PRODUCT_UNKNOWN_ACTION_NONE_HALT})"
        )
    if _passes_action_none(text):
        errors.append(
            f"{prefix}execute() passes action=None "
            f"({PRODUCT_UNKNOWN_ACTION_NONE_HALT})"
        )
    return errors


def reuse_accept_workspace_errors(
    root: Path,
    compiled_or_inventory: Any,
) -> List[str]:
    """Scan REUSE handlers. Empty = WRITER may continue to TESTER."""
    inventory = (
        getattr(compiled_or_inventory, "inventory", None)
        if not isinstance(compiled_or_inventory, (list, tuple))
        else compiled_or_inventory
    )
    base = persist_workspace_root(root)
    errors: List[str] = []
    for item in inventory or ():
        if getattr(item, "is_gap", False):
            continue
        if not getattr(item, "is_reuse", False) and not getattr(
            item, "handler_source", ""
        ):
            if not getattr(item, "verified_present", None):
                continue
        cid = str(getattr(item, "capability_id", "") or "").strip()
        if not cid:
            continue
        path = base / persist_handler_rel(cid)
        if not path.is_file():
            errors.append(
                f"{cid}: {REUSE_ACCEPT_MISS} — missing {persist_handler_rel(cid)}"
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"{cid}: handler unreadable: {exc}")
            continue
        bids = [
            str(b)
            for b in (
                getattr(item, "verified_present", None)
                or getattr(item, "block_ids", None)
                or ()
            )
            if str(b).strip()
        ]
        errors.extend(
            reuse_accept_handler_errors(text, bids, capability_id=cid)
        )
    return errors


def assert_reuse_schema_accept(root: Path, compiled_or_inventory: Any) -> None:
    """Fail closed before TESTER / PRODUCT accept-payload."""
    errors = reuse_accept_workspace_errors(root, compiled_or_inventory)
    if errors:
        raise ReuseAcceptHalt(
            WRITER_REUSE_ACCEPT_HALT + ": " + "; ".join(errors[:8])
        )


def reuse_accept_rules_text(
    capability_ids: Optional[Sequence[str]] = None,
) -> str:
    """BUILD cut: schema-sample POSTs must not yield Unknown action."""
    named = [str(c) for c in (capability_ids or ()) if str(c).strip()]
    lines = [
        "PRODUCT / writer_behaviour schema-sample accept (REUSE keep-path):",
        "The harness POSTs /v1/{capability_id} with a payload built from",
        "that capability's own FIELDS + CONSTRAINTS, then runs bound",
        "blocks. A keep-path handler that calls execute() with no action=",
        "keyword (or action=None) on a block that dispatches on one fails as",
        f"{PRODUCT_UNKNOWN_ACTION_HALT!r} / {PRODUCT_UNKNOWN_ACTION_NONE_HALT!r}.",
        f"Workflow children without step.action fail as {PRODUCT_EVENT_BUS_STEP_0_HALT}",
        f"({PRODUCT_EVENT_BUS_STEP_CLASS}). Store workflow / kit shim",
        "reads input['result'] / out['result']; a schema-sample POST",
        f"that omits it fails as {PRODUCT_WORKFLOW_RESULT_HALT}.",
        "prepare_block_input and keep-path emit MUST attach result from",
        "the first prepared step so accept-payload can persist.",
        "CLONER emit_result_key_access rewrites reads of name['result']",
        "only — assignment targets must stay subscripts. Rewriting",
        f"name['result'] = into a .get() call fails as {PRODUCT_ASSIGN_TO_CALL_HALT}.",
        f"{FAIL_CLOSED_MUST_REWRITE_READS} — a whole-module keep of the",
        "original Store workflow.py leaves envelope['result'] /",
        f"input['result'] as KeyError → {PRODUCT_WORKFLOW_RESULT_HALT}",
        f"({PRODUCT_SCHEMA_SAMPLE_REJECT}).",
        "",
        "Each block's action is the block's own contract. Populate",
        "BLOCK_DEFAULT_ACTIONS by reading every bound block: its block.json",
        "inputs[name=action|operation].default, else the default its code",
        'declares (params.get("action", ...)), else the first action its',
        "code compares against. Never type an action from memory or from",
        "another product. A block whose code reads no action takes none.",
        "Pass action= as a keyword — never inside the payload dict.",
        "Prefer action=BLOCK_DEFAULT_ACTIONS.get(block_id).",
    ]
    if named:
        lines += ["", "This build's REUSE keep-path capabilities:"]
        lines += [f"- {cid}" for cid in named]
    lines += [
        f"A miss is {REUSE_ACCEPT_MISS}: HALT before TESTER, do not burn",
        "three PRODUCT reworks on Unknown action.",
    ]
    return "\n".join(lines)


def reuse_accept_acceptance_line() -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    return (
        "- every REUSE keep-path handler accepts a schema-sample POST "
        f"without {PRODUCT_UNKNOWN_ACTION_HALT} / "
        f"{PRODUCT_UNKNOWN_ACTION_NONE_HALT} "
        f"({GATE_SCOPES['PRODUCT']})  "
        f"[check:{REUSE_ACCEPT_CHECK}]"
    )


def reuse_accept_forbidden_lines() -> str:
    """FORBIDDEN cut: the live keep-path inventions PRODUCT then refuses."""
    return "\n".join(
        [
            f"- execute(block_id, payload) or action=None "
            f"({PRODUCT_UNKNOWN_ACTION_NONE_HALT})",
            "- empty BLOCK_DEFAULT_ACTIONS on a REUSE handler that binds Store blocks",
            "- burying action inside the payload dict",
            f"- reaching TESTER PRODUCT with {PRODUCT_UNKNOWN_ACTION_HALT} "
            f"or {PRODUCT_EVENT_BUS_STEP_0_HALT} after keep-path emit",
            "- omitting workflow input['result'] so PRODUCT fails as "
            f"{PRODUCT_WORKFLOW_RESULT_HALT} (accept-payload persisted nothing)",
            "- rewriting name['result'] = into name.get(...) = so PRODUCT "
            f"fails as {PRODUCT_ASSIGN_TO_CALL_HALT} (queue / "
            "formula_executor Store shims assign that key)",
            "- fail-closed keeping the whole original module so PRODUCT "
            f"fails as {PRODUCT_WORKFLOW_RESULT_HALT} "
            f"({FAIL_CLOSED_MUST_REWRITE_READS})",
        ]
    )


def reuse_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        "REUSE keep-path handlers must accept a schema-sample POST. "
        "Populate BLOCK_DEFAULT_ACTIONS from each bound block's own contract "
        "(its block.json action/operation default, else the default its code "
        "declares) and pass action= as a keyword "
        "(action=BLOCK_DEFAULT_ACTIONS.get(block_id)). "
        f"execute() with action=None is {PRODUCT_UNKNOWN_ACTION_NONE_HALT!r}. "
        f"Workflow step_0 without step.action is {PRODUCT_EVENT_BUS_STEP_0_HALT}. "
        "Store workflow reads input['result'] — a schema-sample POST that "
        f"omits it fails as {PRODUCT_WORKFLOW_RESULT_HALT!r}. "
        "CLONER must not rewrite assignment targets: name['result'] = "
        f"becoming a .get() call fails as {PRODUCT_ASSIGN_TO_CALL_HALT!r} "
        "(queue / formula_executor). "
        f"{FAIL_CLOSED_MUST_REWRITE_READS} or TESTER refuses "
        f"{PRODUCT_SCHEMA_SAMPLE_REJECT}: {PRODUCT_WORKFLOW_RESULT_HALT!r}. "
        f"That miss is {REUSE_ACCEPT_MISS}: HALT before TESTER."
    )
