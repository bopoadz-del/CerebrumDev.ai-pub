"""Workflow + event_bus accept-payload contract (C-BRIEF).

A capability that binds both ``workflow`` and ``event_bus`` must hand every
event_bus child the prepared contract (topic, payload dict, message, channel,
MCP target, action), never the raw schema sample. Which capabilities carry the
contract is read from the blocks each one binds. Nothing here depends on what
a capability is called.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from app.factory.build.schema_accept import (
    CHANNEL_SAMPLE,
    DATETIME_SAMPLE,
    DATE_SAMPLE,
    ENVELOPE_STATUS_SAMPLE,
    GENERIC_STR_SAMPLE,
    TIME_SAMPLE,
)
from app.factory.store_kits import EVENTS, ORCHESTRATION, blocks_of_class, capability_classes

PRODUCT_ACCEPT_TEST = "test_every_capability_route_accepts_payload"
PRODUCT_ACCEPT_CHECK = "event_bus_workflow"
PRODUCT_EVENT_BUS_STEP_HALT = "workflow: step_N (event_bus): error"
PRODUCT_EVENT_BUS_STEP_CLASS = "schema sample refused (event_bus workflow step)"
PRODUCT_ACCEPT_EMPTY_CLASS = "accept-payload persisted nothing"
#: Live sess_07dff0eaf8f64186 after #348: Unknown action cleared; PRODUCT
#: then refused appointment_scheduling because Store workflow / kit shim
#: read ``out['result']`` (schema-sample POST has no such key).
PRODUCT_WORKFLOW_RESULT_HALT = "workflow: RuntimeError: 'result'"
WORKFLOW_RESULT_KEY = "result"
WRITER_EVENT_BUS_WORKFLOW_HALT = (
    "WRITER [check:event_bus_workflow] failed — plan binds "
    "workflow+event_bus without the prepared contract"
)

#: PRODUCT ``_sample_value`` for email-shaped names (not writer_behaviour).
PRODUCT_EMAIL_SAMPLE = "guest@example.com"

#: Store event_bus / notification channel after prepare_block_input.
EVENT_BUS_STEP_CHANNEL = "mcp"
EVENT_BUS_STEP_ACTION = "publish"

#: Keys PRODUCT / prepare map onto event_bus.topic when the sample has none.
EVENT_BUS_TOPIC_KEYS = ("topic", "event", "event_type", "event_name", "reminder_type")

#: Live PRODUCT class after #323: the wall moved from step_1 to step_2.
#: Live tip d72b97f / #330: the wall is step_1 again (appointment_scheduling).
#: Live sess_d5789a91 (2026-09-05, after #333): Store 0-indexes the first
#: child — automated_reminders failed as step_0 (event_bus) when event_bus
#: was the first workflow child.
PRODUCT_EVENT_BUS_STEP_0_HALT = "workflow: step_0 (event_bus): error"
PRODUCT_EVENT_BUS_STEP_1_HALT = "workflow: step_1 (event_bus): error"
PRODUCT_EVENT_BUS_STEP_2_HALT = "workflow: step_2 (event_bus): error"


#: MCP notify target on the prepared input (notification requires block/tool).
#: Use ``tool`` — ``input.block`` is also the workflow child discriminator,
#: so AST would treat the inner dict as a second unprepared event_bus step.
#: The target is the events block itself; which block that is, is what the
#: Store's manifests declare (``capability_class: events``), never an id
#: spelled here. The prompt examples below show the Store's current one.
EVENT_BUS_MCP_TARGET_KEY = "tool"


def events_block_ids() -> FrozenSet[str]:
    """Every Store block that declares ``capability_class: events``."""
    return blocks_of_class(EVENTS)


def orchestrator_block_ids() -> FrozenSet[str]:
    """Every Store block that declares ``capability_class: orchestration``."""
    return blocks_of_class(ORCHESTRATION)


def _example_id(ids: FrozenSet[str], what: str) -> str:
    return sorted(ids)[0] if ids else f"<the {what} block>"


def events_block_example() -> str:
    """The events block's id as the prompts show it (read when asked, never
    at import)."""
    return _example_id(events_block_ids(), "events")


FACTORY_GROUNDED_EVENT_BUS_SOURCE = "factory-grounded event_bus workflow"


def prepared_event_bus_step_example() -> str:
    """Exact step FACTORY_CODE_CLI must emit (or let prepare_block_input shape)."""
    target = events_block_example()
    return (
        "{\n"
        f'  "block": "{target}",\n'
        f'  "action": "{EVENT_BUS_STEP_ACTION}",\n'
        '  "input": {\n'
        '    "topic": "<non-empty str from event / reminder_type / record summary>",\n'
        '    "payload": {"reference": "<domain scalar — not the raw schema sample>"},\n'
        '    "message": "<non-empty str>",\n'
        f'    "channel": "{EVENT_BUS_STEP_CHANNEL}",\n'
        f'    "{EVENT_BUS_MCP_TARGET_KEY}": "{target}"\n'
        "  }\n"
        "}"
    )


class EventBusWorkflowHalt(ValueError):
    """WRITER must not claim done: a bound handler is still unprepared."""


def _capability_block_ids(item: Any) -> set:
    return {str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip()}


def event_bus_workflow_capability_ids(compiled_or_inventory: Any) -> List[str]:
    """Capability ids that must receive the prepared event_bus step contract.

    Structural only: a row binds the contract when its own bound blocks
    include both workflow and event_bus. A capability's NAME decides nothing.
    """
    inventory = (
        getattr(compiled_or_inventory, "inventory", None)
        if not isinstance(compiled_or_inventory, (list, tuple))
        else compiled_or_inventory
    )
    ids: List[str] = []
    for item in inventory or ():
        bids = _capability_block_ids(item)
        cid = str(getattr(item, "capability_id", "") or "")
        if not cid:
            continue
        if _binds_orchestrated_events(bids):
            ids.append(cid)
    return ids


def _binds_orchestrated_events(block_ids: Iterable[str]) -> bool:
    """The bound blocks include one that declares orchestration and one that
    declares events -- read from their manifests, whatever their ids."""
    declared = set(capability_classes(block_ids).values())
    return {ORCHESTRATION, EVENTS} <= declared


def declares_event_bus_workflow(compiled_or_inventory: Any) -> bool:
    return bool(event_bus_workflow_capability_ids(compiled_or_inventory))


def _parse_handler(text: str) -> Optional[ast.AST]:
    blob = text or ""
    for src in (blob, "def handle(payload):\n" + blob):
        try:
            return ast.parse(src)
        except SyntaxError:
            continue
    return None


def _source_tokens(text: str) -> set:
    """Identifiers and string-literal values in source, by the tokenizer.

    Used only when the source does not parse as a whole: a token is a unit
    of the language, so a word inside a comment or a larger string is not
    one of them."""
    import io
    import tokenize

    out: set = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text or "").readline):
            if tok.type == tokenize.NAME:
                out.add(tok.string)
            elif tok.type == tokenize.STRING:
                try:
                    value = ast.literal_eval(tok.string)
                except (ValueError, SyntaxError):
                    continue
                if isinstance(value, str):
                    out.add(value)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def _names_block(text: str, block_ids: FrozenSet[str]) -> bool:
    """The source names one of ``block_ids`` as a string constant (a block
    reference)."""
    tree = _parse_handler(text)
    if tree is None:
        return bool(block_ids & _source_tokens(text))
    return any(_ast_str(n) in block_ids for n in ast.walk(tree))


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _executes(node: ast.AST, block_ids: FrozenSet[str]) -> bool:
    """``execute("<block_id>", ...)`` -- the dispatch call naming one of
    ``block_ids``."""
    return (isinstance(node, ast.Call) and _call_name(node) == "execute" and bool(node.args)
            and _ast_str(node.args[0]) in block_ids)


def handler_constructs_event_bus_step(text: str) -> bool:
    """The handler builds an event_bus step: a dict naming the block, or a
    dispatch call to it. Read from the AST, never from spellings; source
    that does not parse is assumed to (fail closed)."""
    blob = text or ""
    events = events_block_ids()
    if not _names_block(blob, events):
        return False
    tree = _parse_handler(blob)
    if tree is None:
        return True
    for node in ast.walk(tree):
        pairs = _ast_dict_map(node)
        if pairs and _ast_is_event_bus_block(pairs):
            return True
        if _executes(node, events):
            return True
        # step["block"] = <events id>: the same dict, built one key at a time.
        if isinstance(node, ast.Assign) and _ast_str(node.value) in events:
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and _ast_is_event_bus_block({_ast_str(target.slice) or "": node.value})):
                    return True
    return False


def handler_forwards_raw_sample(text: str) -> bool:
    """A step's ``input`` is the raw payload/sample (unprepared). Source that
    does not parse is assumed to (fail closed)."""
    tree = _parse_handler(text)
    if tree is None:
        return bool((text or "").strip())
    for node in ast.walk(tree):
        pairs = _ast_dict_map(node)
        if pairs and "input" in pairs and _ast_is_raw_sample(pairs["input"]):
            return True
    return False


def handler_has_factory_event_bus_wrap(text: str) -> bool:
    """True when the factory execute wrap prepares every workflow child: a
    ``_watched(block_id, ...)`` wrapper that calls ``_prepare_block_input``."""
    tree = _parse_handler(text)
    if tree is None:
        return False
    for fn in ast.walk(tree):
        if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name == "_watched"
                and fn.args.args and fn.args.args[0].arg == "block_id"):
            if any(isinstance(c, ast.Call) and _call_name(c) == "_prepare_block_input"
                   for c in ast.walk(fn)):
                return True
    return False


def handler_builds_workflow_children(text: str) -> bool:
    """The handler builds workflow children: a ``steps`` collection (dict key,
    assignment or keyword) or a dispatch call to the workflow block."""
    tree = _parse_handler(text)
    if tree is None:
        # Unparseable: decide on the source's identifier and string tokens.
        names = _source_tokens(text)
        return "steps" in names or bool(names & orchestrator_block_ids())
    for node in ast.walk(tree):
        pairs = _ast_dict_map(node)
        if pairs and "steps" in pairs:
            return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(tg, ast.Name) and tg.id == "steps" for tg in targets):
                return True
        if isinstance(node, ast.Call) and any(k.arg == "steps" for k in node.keywords):
            return True
        if _executes(node, orchestrator_block_ids()):
            return True
    return False


def _ast_str(node: Any) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ast_dict_map(node: Any) -> Optional[dict]:
    if not isinstance(node, ast.Dict):
        return None
    out: dict = {}
    for key, value in zip(node.keys, node.values):
        name = _ast_str(key)
        if name and value is not None:
            out[name] = value
    return out or None


def _ast_is_raw_sample(node: Any) -> bool:
    if isinstance(node, ast.Name) and node.id in {"payload", "sample"}:
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "dict" and node.args:
            return _ast_is_raw_sample(node.args[0])
    return False


def _ast_is_payload_get_without_fallback(node: Any) -> bool:
    """True when topic/message is ``payload.get(...)`` with no literal fallback.

    PRODUCT ``_sample_payload`` for appointment_scheduling has pet/date
    fields — not topic / event. ``payload.get('event')`` is None at
    accept-payload time and Store records step_1 (event_bus): error.
    """
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
        if any(_ast_str(value) for value in node.values):
            return False
        return any(_ast_is_payload_get_without_fallback(value) for value in node.values)
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "get":
        return False
    return isinstance(func.value, ast.Name) and func.value.id in {"payload", "sample"}


def _ast_is_event_bus_block(pairs: dict) -> bool:
    events = events_block_ids()
    return any(
        _ast_str(pairs.get(key)) in events
        for key in ("block", "block_id", "name")
    )


def _ast_inner_prepared(inner: dict) -> bool:
    topic = inner.get("topic")
    has_topic = (
        topic is not None
        and not _ast_is_raw_sample(topic)
        and not _ast_is_payload_get_without_fallback(topic)
    )
    has_payload = isinstance(inner.get("payload"), ast.Dict)
    message = inner.get("message")
    has_message = (
        message is not None
        and not _ast_is_raw_sample(message)
        and not _ast_is_payload_get_without_fallback(message)
    )
    has_channel = _ast_str(inner.get("channel")) == EVENT_BUS_STEP_CHANNEL
    has_tool = _ast_str(inner.get(EVENT_BUS_MCP_TARGET_KEY)) in events_block_ids()
    return bool(
        has_topic and has_payload and has_message and has_channel and has_tool
    )


def _ast_step_is_prepared(pairs: dict) -> bool:
    if _ast_str(pairs.get("action")) != EVENT_BUS_STEP_ACTION:
        return False
    inp = pairs.get("input")
    if inp is None or _ast_is_raw_sample(inp):
        return False
    inner = _ast_dict_map(inp)
    if not inner:
        return False
    return _ast_inner_prepared(inner)


def event_bus_steps_from_handler(text: str) -> List[Tuple[int, bool]]:
    """``(step_N, prepared)`` for each event_bus child in source.

    ``step_N`` matches PRODUCT ``workflow: step_N (event_bus)`` when the
    children live in one steps list (database + event_bus → step_2).
    """
    blob = text or ""
    try:
        tree = ast.parse(blob)
    except SyntaxError:
        try:
            tree = ast.parse("def handle(payload):\n" + blob)
        except SyntaxError:
            return []
    seen: set = set()
    out: List[Tuple[int, bool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.List):
            continue
        for idx, elt in enumerate(node.elts, start=1):
            pairs = _ast_dict_map(elt)
            if not pairs or not _ast_is_event_bus_block(pairs):
                continue
            key = id(elt)
            if key in seen:
                continue
            seen.add(key)
            out.append((idx, _ast_step_is_prepared(pairs)))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict) or id(node) in seen:
            continue
        pairs = _ast_dict_map(node)
        if not pairs or not _ast_is_event_bus_block(pairs):
            continue
        seen.add(id(node))
        out.append((len(out) + 1, _ast_step_is_prepared(pairs)))
    return out


def _key_values(tree: ast.AST) -> dict:
    """Every value the source binds to a string key, from dict literals,
    keyword arguments, ``x["key"] = value`` and ``key = value``."""
    out: dict = {}
    for node in ast.walk(tree):
        for key, value in (_ast_dict_map(node) or {}).items():
            out.setdefault(key, []).append(value)
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg:
                    out.setdefault(kw.arg, []).append(kw.value)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append(node.value)
                elif isinstance(target, ast.Subscript) and _ast_str(target.slice):
                    out.setdefault(_ast_str(target.slice), []).append(node.value)
    return out


def _reads_input_attr(tree: ast.AST, attr: str) -> bool:
    """``input.<attr>`` -- the prepared input read as an object."""
    return any(
        isinstance(n, ast.Attribute) and n.attr == attr
        and isinstance(n.value, ast.Name) and n.value.id == "input"
        for n in ast.walk(tree)
    )


def _is_dict_value(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) or (
        isinstance(node, ast.Call) and _call_name(node) == "dict"
    )


def handler_has_prepared_event_bus_step(text: str) -> bool:
    """True when source binds the PRODUCT-prepared event_bus step keys: a
    topic, a payload dict, a message, the event_bus channel and the publish
    action. Read from the syntax tree; source that does not parse names
    nothing it can be credited for."""
    blob = text or ""
    if not _names_block(blob, events_block_ids()):
        return False
    tree = _parse_handler(blob)
    if tree is None:
        return False
    kv = _key_values(tree)
    has_topic = "topic" in kv or _reads_input_attr(tree, "topic")
    has_payload_dict = any(_is_dict_value(v) for v in kv.get("payload", ()))
    has_message = "message" in kv or _reads_input_attr(tree, "message")
    has_channel = any(_ast_str(v) == EVENT_BUS_STEP_CHANNEL for v in kv.get("channel", ()))
    has_action = any(_ast_str(v) == EVENT_BUS_STEP_ACTION for v in kv.get("action", ()))
    return bool(has_topic and has_payload_dict and has_message and has_channel and has_action)


def handler_builds_unparsed_event_bus_workflow(text: str) -> bool:
    """True when source invents workflow children with event_bus but AST
    cannot see those dicts. Fail closed — dynamic step_1 construction is
    the live appointment_scheduling class after #325.
    """
    blob = text or ""
    if not _names_block(blob, events_block_ids()):
        return False
    if not handler_builds_workflow_children(blob):
        return False
    if event_bus_steps_from_handler(blob):
        return False
    return handler_constructs_event_bus_step(blob)


def handler_satisfies_event_bus_contract(
    text: str,
    *,
    require_prepared_step: bool = False,
) -> bool:
    """Every event_bus child is prepared in source. Wrap is not keep/done.

    Importing ``prepare_block_input`` or emitting the factory execute wrap
    is not enough — CLI often forwards the schema sample as step_1
    (appointment_scheduling) or as step_2 (appointment_booking). A
    prepared sibling plus any unprepared event_bus child must fail.
    Unparsed dynamic construction must fail.

    ``require_prepared_step`` is the #331 hole: a templated
    ``execute(block_id, payload)`` loop has no event_bus dicts, so the
    AST check used to vacuous-pass. WRITER then claimed done; PRODUCT
    executed workflow with the schema sample and refused step_1.
    Appointment / booking / reminder handlers must ship the prepared
    step in source (factory-grounded emit, not an LLM stub).
    """
    steps = event_bus_steps_from_handler(text)
    if steps:
        return all(prepared for _idx, prepared in steps)
    if handler_builds_unparsed_event_bus_workflow(text):
        return False
    if require_prepared_step:
        return False
    if not handler_constructs_event_bus_step(text):
        return True
    if handler_forwards_raw_sample(text):
        return False
    return handler_has_prepared_event_bus_step(text)


def needs_grounded_event_bus_handler(
    capability_id: str,
    block_ids: Optional[Sequence[str]] = None,
) -> bool:
    """True when WRITER must emit the prepared event_bus step in source.

    Structural: the capability binds both workflow and event_bus. A bound
    event_bus alone stays on the generic template, and inventing an event_bus
    child for a block that is not vendored is how a build went red.
    """
    bids = {str(b) for b in (block_ids or ()) if str(b).strip()}
    return _binds_orchestrated_events(bids)


def event_bus_step_is_store_ready(data: Any) -> bool:
    """True when a workflow child input should not become step_N (event_bus): error.

    Live Store notify (after #314 channel=mcp) still refuses a schema sample
    that has no topic / payload / message, uses channel=email without ``to``,
    or omits MCP ``block``/``tool``. Domain columns at the top level are not
    an event_bus contract.
    """
    if not isinstance(data, dict):
        return False
    topic = data.get("topic")
    if not (isinstance(topic, str) and topic.strip()):
        return False
    if not isinstance(data.get("payload"), dict):
        return False
    message = data.get("message")
    if not (isinstance(message, str) and str(message).strip()):
        return False
    channel = str(data.get("channel") or "").strip().lower()
    if channel != EVENT_BUS_STEP_CHANNEL:
        return False
    target = data.get("block") or data.get("tool")
    if not (isinstance(target, str) and target.strip()):
        return False
    return True


def grounded_event_bus_topic(capability_id: str) -> str:
    """The event topic is the capability's own id; nothing is read from its name."""
    blob = str(capability_id or "record").lower().replace("-", "_")
    return f"{blob or 'record'}.recorded"


def grounded_event_bus_handler_body(
    capability_id: str,
    block_ids: Optional[Sequence[str]] = None,
) -> str:
    """Deterministic handle() body: prepared event_bus first child, no LLM.

    The live #332 VetCare halt (stub_rate≈0.833) used ``_templated_body``
    ``execute(block_id, payload)``. That vacuous-passed #331's AST check,
    then PRODUCT ran workflow with the schema sample and refused
    appointment_scheduling step_1. Live sess_d5789a91 then refused
    automated_reminders at Store ``step_0`` (event_bus first child).
    This body is the factory-grounded path for every step_N.
    """
    topic = grounded_event_bus_topic(capability_id)
    message = topic.replace(".", " ")
    bids = [str(b) for b in (block_ids or ()) if str(b).strip()]
    # The orchestrator and the events block are whichever bound blocks
    # DECLARE those classes; their ids only flow into the emitted source.
    classes = capability_classes(bids)
    wf = next((b for b in bids if classes.get(b) == ORCHESTRATION), "")
    ev = next((b for b in bids if classes.get(b) == EVENTS), "")
    others = [b for b in bids if b not in {wf, ev}]
    other_loop = ""
    if others:
        other_loop = (
            "    for block_id in BLOCK_IDS:\n"
            f"        if block_id in ({wf!r}, {ev!r}):\n"
            "            continue\n"
            "        result = execute(\n"
            "            block_id, payload, "
            "action=BLOCK_DEFAULT_ACTIONS.get(block_id)\n"
            "        )\n"
            "        results[block_id] = result\n"
            "        if isinstance(result, dict) and (\n"
            '            result.get("status") == "error" or "error" in result\n'
            "        ):\n"
            "            errors[block_id] = str("
            "result.get(\"error\") or result)[:200]\n"
        )
    return (
        "    results = {}\n"
        "    errors = {}\n"
        "    steps = [{\n"
        f'        "block": {json.dumps(ev)},\n'
        f'        "action": "{EVENT_BUS_STEP_ACTION}",\n'
        "        \"input\": {\n"
        f'            "topic": {topic!r},\n'
        '            "payload": {"reference": payload.get("reference") '
        'or payload.get("pet_name") or "record"},\n'
        f'            "message": {message!r},\n'
        f'            "channel": "{EVENT_BUS_STEP_CHANNEL}",\n'
        f'            "tool": {json.dumps(ev)},\n'
        "        },\n"
        "    }]\n"
        f"{other_loop}"
        f"    if {wf!r} in BLOCK_IDS:\n"
        "        result = execute(\n"
        f"            {wf!r}, {{'steps': steps, 'result': ("
        "steps[0].get('input') if steps else payload)}, "
        f"action=BLOCK_DEFAULT_ACTIONS.get({wf!r}) or 'run',\n"
        "        )\n"
        f"        results[{wf!r}] = result\n"
        "        if isinstance(result, dict) and (\n"
        '            result.get("status") == "error" or "error" in result\n'
        "        ):\n"
        f"            errors[{wf!r}] = str("
        "result.get(\"error\") or result)[:200]\n"
        f"    if {ev!r} in BLOCK_IDS:\n"
        "        result = execute(\n"
        f"            {ev!r}, steps[0]['input'], "
        f"action=BLOCK_DEFAULT_ACTIONS.get({ev!r}) or "
        f"'{EVENT_BUS_STEP_ACTION}',\n"
        "        )\n"
        f"        results[{ev!r}] = result\n"
        "        if isinstance(result, dict) and (\n"
        '            result.get("status") == "error" or "error" in result\n'
        "        ):\n"
        f"            errors[{ev!r}] = str("
        "result.get(\"error\") or result)[:200]\n"
        "    if errors:\n"
        "        return {\n"
        '            "ok": False,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "error": "; ".join('
        'f"{b}: {e}" for b, e in sorted(errors.items())),\n'
        '            "results": results,\n'
        "        }\n"
        # Phase 2 §0.2: persistence is the ROUTE's job (tenant-scoped
        # save(payload)); the handler reports dispatch results only.
        '    return {"ok": True, "capability": CAPABILITY_ID, "results": results}'
    )


def _action_module_ids(root: Path) -> List[str]:
    actions = Path(root) / "app" / "actions"
    if not actions.is_dir():
        return []
    return [
        path.stem
        for path in sorted(actions.glob("*.py"))
        if path.name != "__init__.py" and not path.name.startswith("_")
    ]


def handler_ids_for_event_bus_check(
    root: Path,
    compiled_or_inventory: Any,
) -> List[str]:
    """Bound inventory ids plus every on-disk handler whose own source builds
    an event_bus step, so a handler written under a different id than the
    plan's is still checked. Decided by what the code does, never its name."""
    ids = list(event_bus_workflow_capability_ids(compiled_or_inventory))
    seen = {str(cid).replace("-", "_") for cid in ids}
    base = Path(root)
    for cid in _action_module_ids(base):
        if cid in seen:
            continue
        path = base / "app" / "actions" / f"{cid}.py"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if handler_constructs_event_bus_step(text):
            ids.append(cid)
            seen.add(cid)
    return ids


def event_bus_workflow_handler_errors(
    root: Path,
    compiled_or_inventory: Any,
) -> List[str]:
    """Scan written handlers. Empty = the WRITER check is green."""
    ids = handler_ids_for_event_bus_check(root, compiled_or_inventory)
    inventory = (
        getattr(compiled_or_inventory, "inventory", None)
        if not isinstance(compiled_or_inventory, (list, tuple))
        else compiled_or_inventory
    )
    bids_by_cid = {
        str(getattr(item, "capability_id", "") or ""): list(
            getattr(item, "block_ids", None) or []
        )
        for item in inventory or ()
    }
    errors: List[str] = []
    base = Path(root)
    for cid in ids:
        path = base / "app" / "actions" / f"{str(cid).replace('-', '_')}.py"
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        require = (
            needs_grounded_event_bus_handler(cid, bids_by_cid.get(cid, ()))
            or handler_constructs_event_bus_step(text)
            or handler_builds_unparsed_event_bus_workflow(text)
        )
        if handler_satisfies_event_bus_contract(
            text, require_prepared_step=require
        ):
            continue
        steps = event_bus_steps_from_handler(text)
        bad: List[str] = []
        for idx, prepared in steps:
            if prepared:
                continue
            store_idx = idx - 1
            if store_idx == 0:
                bad.append("step_0")
            bad.append(f"step_{idx}")
        seen_bad: List[str] = []
        for label in bad:
            if label not in seen_bad:
                seen_bad.append(label)
        where = (
            f"workflow: {', '.join(seen_bad)} (event_bus): error"
            if seen_bad
            else PRODUCT_EVENT_BUS_STEP_HALT
        )
        errors.append(
            f"{cid}: unprepared event_bus workflow step "
            f"({where}; {PRODUCT_EVENT_BUS_STEP_CLASS})"
        )
    return errors


def assert_event_bus_workflow_handlers(
    root: Path,
    compiled_or_inventory: Any,
) -> None:
    """Fail closed before WRITER claims done."""
    errors = event_bus_workflow_handler_errors(root, compiled_or_inventory)
    if errors:
        raise EventBusWorkflowHalt(
            WRITER_EVENT_BUS_WORKFLOW_HALT + ": " + "; ".join(errors)
        )


def workflow_accept_rules_text(
    capability_ids: Optional[Sequence[str]] = None,
) -> str:
    """BUILD cut: what PRODUCT will POST and the exact prepared step."""
    topic_keys = " / ".join(EVENT_BUS_TOPIC_KEYS[1:])
    named = [str(c) for c in (capability_ids or ()) if str(c).strip()]
    bound_lines: List[str] = []
    if named:
        bound_lines = [
            "These planned capabilities bind workflow and event_bus and MUST",
            "use the prepared step on EVERY event_bus child, whether it is",
            "Store step_0 (first child), step_1 or step_2+:",
            *[f"- {cid}" for cid in named],
            "",
        ]
    return "\n".join(
        [
            "PRODUCT gate (after WRITER writer_behaviour) — accept-payload:",
            f"The harness runs tests/test_routes.py::{PRODUCT_ACCEPT_TEST}.",
            "It POSTs /v1/{capability_id} with a payload built from that",
            "capability's own FIELDS + CONSTRAINTS (same idea as",
            "writer_behaviour; PRODUCT then executes the bound blocks).",
            "A route that returns ok:false fails with:",
            f"  {{capability}} rejected a payload built from its own schema: "
            f"{PRODUCT_EVENT_BUS_STEP_HALT}",
            f"Named class: {PRODUCT_EVENT_BUS_STEP_CLASS}; "
            f"{PRODUCT_ACCEPT_EMPTY_CLASS}; {PRODUCT_WORKFLOW_RESULT_HALT}.",
            "",
            *bound_lines,
            "PRODUCT schema-sample rules (roles_handlers._sample_payload):",
            "- CONSTRAINTS.allowed_values[0] when declared",
            f"- status / *_status → {ENVELOPE_STATUS_SAMPLE}",
            f"- channel / *_channel → {CHANNEL_SAMPLE} (never the word "
            f"{GENERIC_STR_SAMPLE})",
            f"- datetime / *_at / *_datetime → {DATETIME_SAMPLE}",
            f"- date / *_date → {DATE_SAMPLE}",
            f"- time / *_time → {TIME_SAMPLE}",
            f"- email-shaped names → {PRODUCT_EMAIL_SAMPLE}",
            f"- otherwise the word {GENERIC_STR_SAMPLE}",
            "",
            "That schema sample is NOT an event_bus input. When a capability",
            "binds workflow AND event_bus, do NOT set ANY step input to",
            "payload. Store 0-indexes children: an event_bus-first child",
            f"fails PRODUCT as {PRODUCT_EVENT_BUS_STEP_0_HALT}. An unprepared first factory",
            f"child also fails as {PRODUCT_EVENT_BUS_STEP_1_HALT}. step_1 prepared +",
            f"step_2 raw still fails as {PRODUCT_EVENT_BUS_STEP_2_HALT}.",
            "The Store workflow records a child refusal as status=error — often",
            "only the banner string, no inner message.",
            "Store kit shims also read input['result'] / out['result'] and wrap",
            f"a missing key as {PRODUCT_WORKFLOW_RESULT_HALT}. The schema sample",
            "does not include that key — prepare_block_input / keep-path emit",
            "MUST attach result from the first prepared step so accept-payload",
            "can persist. CLONER must not rewrite assignment targets:",
            "name['result'] = becoming a .get() call fails as",
            "SyntaxError: cannot assign to function call (queue.py ~189 /",
            "formula_executor ~242).",
            "fail-closed keep original must still rewrite reads — keeping",
            "the whole Store workflow.py leaves envelope['result'] as",
            f"{PRODUCT_WORKFLOW_RESULT_HALT} (<capability> rejected a payload",
            "built from its own schema).",
            "WRITER emits a factory-grounded prepared event_bus step for",
            "every capability that binds workflow + event_bus — do not burn",
            "rework on execute(block_id, payload) stubs, and do not",
            'execute("workflow", payload) with the raw schema sample.',
            "Construct each event_bus step (every child, including Store",
            "step_0, step_1, and step_2+) in source. The factory execute wrap is a safety",
            "net, not permission to keep/done an unprepared child:",
            "- step['block'] = 'event_bus' (workflow reads block, not block_id)",
            f"- action={EVENT_BUS_STEP_ACTION} (BLOCK_DEFAULT_ACTIONS, keyword only)",
            f"- input.topic = non-empty str ({topic_keys} or a record summary)",
            "- input.payload = dict of domain scalars (not the raw sample alone)",
            "- input.message = non-empty str",
            f"- input.channel = {EVENT_BUS_STEP_CHANNEL!r} "
            f"(never {GENERIC_STR_SAMPLE!r}; {CHANNEL_SAMPLE!r} without `to` is not notify-ready)",
            f"- input.{EVENT_BUS_MCP_TARGET_KEY} = {events_block_example()!r} "
            "(MCP notify requires block/tool; the schema sample has neither)",
            "Exact prepared event_bus workflow step (copy this shape on",
            "EVERY event_bus child — step_0, step_1, step_2, and later):",
            prepared_event_bus_step_example(),
            "Do not invent a second, unprepared event_bus child after a",
            "prepared step. Do not invent a stricter workflow the spec",
            "cannot express.",
        ]
    )


def workflow_accept_acceptance_line(
    capability_ids: Optional[Sequence[str]] = None,
) -> str:
    """ACCEPTANCE cut: PRODUCT harness check, not a coder decorative test."""
    named = [str(c) for c in (capability_ids or ()) if str(c).strip()]
    who = f" ({', '.join(named)})" if named else ""
    return (
        f"- PRODUCT accept-payload{who}: every event_bus step including "
        f"step_0, step_1 and step_2+ accepts the prepared "
        f"contract (topic, payload dict, message, "
        f"channel={EVENT_BUS_STEP_CHANNEL}, "
        f"action={EVENT_BUS_STEP_ACTION}) — never the raw schema sample "
        f"({PRODUCT_ACCEPT_TEST}; {PRODUCT_EVENT_BUS_STEP_HALT}; "
        f"{PRODUCT_EVENT_BUS_STEP_0_HALT}; "
        f"{PRODUCT_EVENT_BUS_STEP_1_HALT}; "
        f"{PRODUCT_EVENT_BUS_STEP_2_HALT})  "
        f"[check:{PRODUCT_ACCEPT_CHECK}]"
    )


def workflow_accept_forbidden_lines() -> str:
    """FORBIDDEN cut: the live CLI inventions that PRODUCT then refuses."""
    return "\n".join(
        [
            "- forwarding the PRODUCT schema sample as an event_bus workflow step input",
            "- setting an event_bus workflow step to 'input': payload or "
            '"input": payload (or input=dict(payload))',
            "- an unprepared step_0 (event_bus) — Store 0-index first child; "
            f"fails PRODUCT as {PRODUCT_EVENT_BUS_STEP_0_HALT}",
            "- an unprepared step_1 (event_bus) — fails PRODUCT as "
            f"{PRODUCT_EVENT_BUS_STEP_1_HALT}",
            "- a prepared step_1 plus an unprepared step_2 (event_bus) — "
            f"still fails PRODUCT as {PRODUCT_EVENT_BUS_STEP_2_HALT}",
            "- treating one prepared event_bus child, a prepare_block_input "
            "import, or the factory execute wrap as keep/done while any "
            "child (including step_1) is still raw",
            f"- channel={GENERIC_STR_SAMPLE} or channel={CHANNEL_SAMPLE} without "
            f"`to` on an event_bus step (use channel={EVENT_BUS_STEP_CHANNEL})",
            f"- inventing {PRODUCT_EVENT_BUS_STEP_HALT} / "
            f"{PRODUCT_EVENT_BUS_STEP_0_HALT} / "
            f"{PRODUCT_EVENT_BUS_STEP_1_HALT} / "
            f"{PRODUCT_EVENT_BUS_STEP_2_HALT} without the prepared "
            f"contract on EVERY event_bus child (topic, payload dict, message, "
            f"channel={EVENT_BUS_STEP_CHANNEL}, action={EVENT_BUS_STEP_ACTION})",
            "- execute(block_id, payload) stubs for a capability that binds "
            "workflow + event_bus (WRITER must emit the factory-grounded "
            "prepared event_bus step)",
            '- execute("workflow", payload) with the raw schema sample',
            "- omitting workflow input['result'] so PRODUCT fails as "
            f"{PRODUCT_WORKFLOW_RESULT_HALT} (schema-sample POST has no "
            "result key; Store kit shim wraps KeyError as RuntimeError)",
            "- rewriting name['result'] = into name.get(...) = so PRODUCT "
            "fails as SyntaxError: cannot assign to function call "
            "(queue / formula_executor Store shims)",
            "- fail-closed keeping the whole original module so PRODUCT "
            f"fails as {PRODUCT_WORKFLOW_RESULT_HALT} (fail-closed keep "
            "original must still rewrite reads)",
        ]
    )


def workflow_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        f"PRODUCT {PRODUCT_ACCEPT_TEST} POSTs a schema-sample payload then "
        f"runs bound blocks. A capability that binds workflow + event_bus must "
        f"prepare EACH event_bus step including Store step_0, step_1 and "
        f"step_2+ "
        f"(block=event_bus, action={EVENT_BUS_STEP_ACTION}, topic, "
        f"payload dict, message, channel={EVENT_BUS_STEP_CHANNEL}, "
        f"{EVENT_BUS_MCP_TARGET_KEY}={events_block_example()}) — never "
        f"forward the raw sample as 'input': payload. Unprepared steps fail as "
        f"{PRODUCT_EVENT_BUS_STEP_HALT!r}. An event_bus-first child fails as "
        f"{PRODUCT_EVENT_BUS_STEP_0_HALT!r}. An unprepared first factory "
        f"child also fails as {PRODUCT_EVENT_BUS_STEP_1_HALT!r}. A prepared step_1 plus "
        f"an unprepared step_2 still fails as {PRODUCT_EVENT_BUS_STEP_2_HALT!r} "
        f"({PRODUCT_EVENT_BUS_STEP_CLASS}). The factory wrap is not keep/done. "
        f"WRITER emits a factory-grounded prepared event_bus step — do not "
        f'execute("workflow", payload) with the raw schema sample. '
        "Store workflow / kit shim reads input['result'] or out['result'] "
        "— a schema-sample POST that omits it fails as "
        f"{PRODUCT_WORKFLOW_RESULT_HALT!r}. prepare_block_input and the "
        f"keep-path emit MUST attach result from the first prepared step. "
        "CLONER must not rewrite assignment targets: name['result'] = "
        "becoming a .get() call fails as "
        "'SyntaxError: cannot assign to function call'. "
        "fail-closed keep original must still rewrite reads or TESTER "
        "refuses <capability> rejected a payload built from "
        f"its own schema: {PRODUCT_WORKFLOW_RESULT_HALT!r}. "
        f"Exact shape: "
        f'{{"block": "event_bus", "action": "{EVENT_BUS_STEP_ACTION}", '
        f'"input": {{"topic": "<str>", "payload": {{}}, "message": "<str>", '
        f'"channel": "{EVENT_BUS_STEP_CHANNEL}", '
        f'"{EVENT_BUS_MCP_TARGET_KEY}": "{events_block_example()}"}}}}.'
    )


def inventory_block_ids(inventory: Iterable[Any]) -> List[str]:
    """Flat claimed block ids (tests / lint helpers)."""
    out: List[str] = []
    for item in inventory or ():
        out.extend(str(b) for b in (getattr(item, "block_ids", None) or []) if str(b).strip())
    return out
