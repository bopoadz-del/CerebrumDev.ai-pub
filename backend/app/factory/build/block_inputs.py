"""Construct block-shaped inputs from a capability's domain record.

Handlers must never require block-specific fields (``channel``, ``steps``,
``team_id``, path strings) from the caller — the coder prompt already says
so. Live residential-lettings (sess_6400b6c / hash b36090a424db) still
shipped handlers that forwarded the domain JSON unchanged, so pilot
``test_every_capability_route_accepts_payload`` failed on:

* notification — missing ``channel`` / ``message``
* workflow — missing ``steps``
* team — ``NoneType.lower`` on empty name/slug
* document_engine — dict handed to a path-like opener

Live veterinary-care (sess_a4aa977d2dff4c55, 2026-09-04) then halted
WRITER on four *different* refusals — required record fields, not
``Unknown action: None``:

* event_bus — ``RuntimeError: topic required``
* document_engine — ``No input files provided (pdf/docx/xlsx)``
* database — ``Query failed: missing sql or table``
* team — ``Team access denied`` (domain ``team_id`` / missing minted id)

This module is the shared construction rule. WRITER emits it into every
generated platform as ``app/block_inputs.py`` and the fail-closed execute
wrapper calls ``prepare_block_input`` before ``dispatch.execute``. That is
handler-layer adaptation, not F18 fabrication inside ``dispatch.py``
(``_default_block_field`` / ``_ALWAYS_FILL`` stay forbidden there).

Only missing constructible keys are filled. Values the handler already
supplied are left alone, except a domain-looking ``team_id`` (not minted
by ``create_team``) which is replaced by the platform precondition id,
and a reminder ``channel`` that is not a Store notification channel
(``sample`` / ``sms`` / ``in_process``) which is rewritten to ``mcp``.
"""

from __future__ import annotations

import ast
import json
import keyword
import os
import re
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.factory.build.block_obligations import (
    ENVELOPE_STATUS_VALUES,
    is_envelope_status_field,
    is_envelope_status_vocab,
)
from app.factory.build.reuse_accept import default_block_action
from app.factory.store_kits import (
    DOCUMENT_EXTRACTION,
    EVENTS,
    JOBS,
    LAYOUT,
    MEMBERSHIP,
    MESSAGING,
    METRICS,
    ORCHESTRATION,
    RECORDS,
    block_capability_class,
)

#: Path-like keys document_engine (and SCHEMA_OBLIGATIONS) accept.
_DOC_PATH_KEYS = (
    "file_path",
    "pdf_path",
    "docx_path",
    "xlsx_path",
    "attachment_path",
    "document_path",
    "path",
)

#: Team fields the Store block lowercases; None must never reach them.
_TEAM_STRING_KEYS = ("user_id", "name", "slug", "role", "email", "plan", "permission")

#: Envelope keys that are never domain columns (return-shape / dispatch).
_ENVELOPE_NAMES = frozenset(
    {
        "ok",
        "error",
        "capability",
        "id",
        "result",
        "results",
        "payload",
        "action",
        "block",
        "blocks",
    }
)

#: Block-contract keys ``prepare_block_input`` constructs. Still skipped when
#: building notification summaries, but alignment MAY treat ``channel`` /
#: ``message`` as domain fields when a handler validates them as such
#: (live VetConnect: reminder ``channel`` ∈ email/sms/…).
_BLOCK_CONTRACT_NAMES = frozenset({"channel", "message", "steps", "team_id"})

#: Cerebrum-Blocks ``NotificationBlock._send`` handlers. Anything else
#: (including the schema-sample word ``sample``) is
#: ``Unknown channel: …``. Offline email/webhook/slack also need extra
#: fields the domain record does not have (``to`` / ``url``).
STORE_NOTIFICATION_CHANNELS = frozenset({"mcp", "email", "webhook", "slack"})
_OFFLINE_NOTIFICATION_CHANNEL = "mcp"
_DOMAIN_CHANNEL_SAMPLE = "email"

#: Summary / prepare_block_input skip set (envelope + block-contract).
_SKIP_REQUIRED_NAMES = _ENVELOPE_NAMES | _BLOCK_CONTRACT_NAMES | {"status"}

#: Alignment never copies envelope keys or workflow/team construction keys.
#: ``status`` and ``channel`` stay eligible — they are common domain columns.
_ALIGN_SKIP_NAMES = _ENVELOPE_NAMES | frozenset({"steps", "team_id"})

def split_execute_action(
    payload: Any,
    *,
    action: Optional[str] = None,
    default_action: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Lift ``action`` out of the payload; return ``(keyword, clean_record)``.

    Live makerspace-management (sess_39b5fec2abd346a5, 2026-09-04): every
    capability called ``execute(block, {..., "action": ...})`` with no
    ``action=`` keyword. ``app/dispatch.py`` routes payload keys into the
    block's record and reads the operation only from ``action=``, so the
    blocks answered ``Unknown action: None`` or ``unknown field(s): action``
    and WRITER halted on ``every capability wrote a payload its blocks
    refuse``.

    Preference: explicit ``action=`` / positional, then a string buried in
    the payload (or its ``input`` envelope), then the block's default.
    ``action`` is never a block record field — always strip it.
    """
    data = dict(payload) if isinstance(payload, dict) else (
        {} if payload is None else {"value": payload}
    )
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}

    def _usable(value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value
        return None

    resolved = (
        _usable(action)
        or _usable(data.get("action"))
        or _usable(inner.get("action"))
        or _usable(default_action)
    )
    data.pop("action", None)
    if isinstance(data.get("input"), dict):
        cleaned = dict(data["input"])
        cleaned.pop("action", None)
        data["input"] = cleaned
    return resolved, data


def prepare_block_input(
    block_id: str,
    domain: Any,
    *,
    action: Optional[str] = None,
    roster: Sequence[str] = (),
    product_name: str = "platform",
    entity: Optional[str] = None,
    default_actions: Optional[Dict[str, str]] = None,
    capability_classes: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Return a payload the named block can accept for ``action``.

    ``domain`` is the caller's capability record (or an already-built block
    input). Missing block-contract keys are derived; existing keys win.
    ``entity`` is the capability's store table — used so database query
    does not invent a ``records`` table Alembic never created.
    ``default_actions`` is each block's harvested default (from block.json);
    workflow children receive it as ``step.action`` because the Store
    workflow calls ``execute(input, {})`` and otherwise drops the action
    factory dispatch would have passed.

    Which shaping applies is what the block DECLARES: its signed
    ``capability_class`` (``capability_classes`` maps id -> class for this
    build; absent, the Store manifest is read). The id decides nothing, so a
    block republished under a new id keeps its shaping and a block that
    declares no class is passed through untouched.
    """
    _resolved, data = split_execute_action(domain, action=action)
    bid = str(block_id or "")
    merged_actions = dict(default_actions or {})
    cls = _class_of(bid, capability_classes)
    if cls == MESSAGING:
        return _for_notification(data, roster, self_id=bid)
    if cls == ORCHESTRATION:
        return _for_workflow(
            data,
            roster,
            product_name=product_name,
            entity=entity,
            default_actions=merged_actions,
            self_id=bid,
            capability_classes=capability_classes,
        )
    if cls == MEMBERSHIP:
        return _for_team(data, product_name=product_name)
    if cls == DOCUMENT_EXTRACTION:
        return _for_document_engine(data)
    if cls == METRICS:
        return _for_analytics(data)
    if cls == EVENTS:
        return _for_event_bus(data, roster, self_id=bid)
    if cls == RECORDS:
        return _for_database(data, entity=entity)
    if cls == JOBS:
        return _for_queue(data)
    if cls == LAYOUT:
        return _for_dashboard(data)
    return data


def _class_of(block_id: str, classes: Optional[Mapping[str, str]]) -> Optional[str]:
    """The block's declared ``capability_class``: from this build's map when
    one is given, else from the Store manifest."""
    if classes is not None:
        return classes.get(block_id)
    return block_capability_class(block_id)


def _summary_message(data: Dict[str, Any]) -> str:
    parts = [
        f"{key}={value}"
        for key, value in data.items()
        if isinstance(value, (str, int, float, bool)) and key not in _SKIP_REQUIRED_NAMES
    ]
    text = "; ".join(parts).strip()
    return (text or "platform notification")[:500]


def _for_dashboard(data: Dict[str, Any]) -> Dict[str, Any]:
    """Satisfy dashboard ``status`` from the factory envelope.

    Live sess_f1fe691 clinic_dashboard: schema-sample omitted ``status``
    and the route/block answered ``Missing required field: status``.
    """
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    status = out.get("status") if out.get("status") is not None else inner.get("status")
    if not (isinstance(status, str) and status.strip()):
        out["status"] = "open"
    elif not out.get("status"):
        out["status"] = status
    return out


def sample_channel_value(allowed: Optional[Sequence[Any]] = None) -> str:
    """Schema-sample for a reminder / notification ``channel`` field.

    A bare str field used to sample as ``"sample"``. Live VetCare Hub
    ``automated_reminders`` (sess_67fe60f7) forwarded that into
    notification / event_bus and the Store raised
    ``RuntimeError: Unknown channel: sample``. Prefer a Store-known
    value from ``allowed_values``; otherwise ``email`` (domain-typical
    and Store-listed). ``prepare_block_input`` still rewrites placeholders
    and delivery channels that lack their extra fields onto ``mcp``.
    """
    if allowed:
        for cand in allowed:
            raw = str(cand).strip()
            if raw.lower() in STORE_NOTIFICATION_CHANNELS:
                return raw
        first = allowed[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return _DOMAIN_CHANNEL_SAMPLE


def notification_channel(
    value: Any, data: Optional[Dict[str, Any]] = None
) -> str:
    """Map a domain/reminder channel onto a Store-accepted notification channel.

    ``sample``, ``sms``, ``push``, ``in_process`` are not Store channels.
    ``email`` / ``webhook`` / ``slack`` are listed but fail closed offline
    without ``to`` / ``url`` / a Slack webhook — those become ``mcp``.
    """
    raw = str(value or "").strip().lower()
    payload = data if isinstance(data, dict) else {}
    if raw == "mcp":
        return "mcp"
    if raw == "email":
        to = payload.get("to") or payload.get("email")
        if isinstance(to, str) and to.strip() and "@" in to:
            return "email"
        return _OFFLINE_NOTIFICATION_CHANNEL
    if raw == "webhook":
        url = payload.get("url") or payload.get("webhook_url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return "webhook"
        return _OFFLINE_NOTIFICATION_CHANNEL
    if raw == "slack":
        if payload.get("webhook_url") or payload.get("slack_webhook_url"):
            return "slack"
        return _OFFLINE_NOTIFICATION_CHANNEL
    return _OFFLINE_NOTIFICATION_CHANNEL


def _for_notification(
    data: Dict[str, Any], roster: Sequence[str], *, self_id: str
) -> Dict[str, Any]:
    out = dict(data)
    out["channel"] = notification_channel(out.get("channel"), out)
    if not out.get("message"):
        body = out.get("body")
        out["message"] = body if isinstance(body, str) and body.strip() else _summary_message(data)
    if str(out.get("channel")).lower() == "mcp" and not out.get("block") and not out.get("tool"):
        peers = [b for b in roster if b and b != self_id]
        out["block"] = peers[0] if peers else self_id
    return out


_STEP_BLOCK_KEYS = ("block", "block_id", "name")


def _workflow_step_block(step: Dict[str, Any]) -> str:
    """Store workflow reads ``step.get("block")``; coder steps use block_id."""
    for key in _STEP_BLOCK_KEYS:
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _step_action(block_id: str, default_actions: Optional[Dict[str, str]]) -> Optional[str]:
    """Keyword action for a workflow child — never leave Store blocks at None."""
    return default_block_action(block_id, default_actions)


def _shape_workflow_steps(
    steps: Sequence[Any],
    roster: Sequence[str],
    *,
    product_name: str,
    entity: Optional[str],
    default_actions: Optional[Dict[str, str]],
    fallback_domain: Dict[str, Any],
    capability_classes: Optional[Mapping[str, str]] = None,
) -> List[Any]:
    """Prepare each existing step's input. Do not return coder steps as-is.

    Live sess_4fba2a2865044a82 (VetCare Hub / appointment_scheduling):
    PRODUCT ``test_every_capability_route_accepts_payload`` refused

        workflow: step_1 (event_bus): error

    after #314. The handler (coder) built ``steps`` from the schema sample
    and ``_for_workflow`` used to return that list unchanged, so event_bus
    never received topic / mcp channel / payload. The Store workflow
    records a child refusal as status=error (often without the inner
    message), which is exactly the live banner string.
    """
    shaped: List[Any] = []
    for step in steps:
        if not isinstance(step, dict):
            shaped.append(step)
            continue
        item = dict(step)
        bid = _workflow_step_block(item)
        if not bid:
            shaped.append(item)
            continue
        raw = item.get("input")
        payload = raw if isinstance(raw, dict) else fallback_domain
        item["block"] = bid
        item["input"] = prepare_block_input(
            bid,
            payload,
            roster=roster,
            product_name=product_name,
            entity=entity,
            default_actions=default_actions,
            capability_classes=capability_classes,
        )
        if not item.get("action"):
            action = _step_action(bid, default_actions)
            if action:
                item["action"] = action
        shaped.append(item)
    return shaped


def workflow_result_payload(
    data: Dict[str, Any],
    steps: Optional[Sequence[Any]] = None,
) -> Any:
    """Value Store workflow / kit shims read as ``input['result']``.

    Live sess_07dff0eaf8f64186 (VetCare Hub ALL-REUSE, tip da7cd2b / #348):
    PRODUCT schema-sample POST has pet/date/status — no ``result`` key.
    The Store kit shim then did ``out['result']`` (or wrapped that KeyError
    as ``RuntimeError: 'result'``). Keep-path prepare must supply this key
    so ``appointment_scheduling`` accept-payload can persist.
    """
    if isinstance(data, dict):
        existing = data.get("result")
        if existing not in (None, ""):
            return existing
    chain = steps
    if chain is None and isinstance(data, dict):
        chain = data.get("steps")
    for step in chain or ():
        if not isinstance(step, dict):
            continue
        inner = step.get("input")
        if isinstance(inner, dict) and inner:
            return dict(inner)
        if inner not in (None, ""):
            return inner
    if not isinstance(data, dict):
        return {"reference": "record"}
    scalars = {
        key: value
        for key, value in data.items()
        if key
        not in {
            "steps",
            "action",
            "result",
            "results",
            "input",
            "ok",
            "error",
            "block",
            "blocks",
        }
        and value not in (None, "")
    }
    return scalars or {"reference": data.get("reference") or "record"}


def ensure_workflow_result(
    data: Dict[str, Any],
    steps: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Attach ``result`` so Store workflow does not RuntimeError: 'result'."""
    out = dict(data) if isinstance(data, dict) else {}
    if steps is not None:
        out["steps"] = list(steps)
    if out.get("result") not in (None, ""):
        return out
    out["result"] = workflow_result_payload(out, out.get("steps"))
    return out


def _for_workflow(
    data: Dict[str, Any],
    roster: Sequence[str],
    *,
    product_name: str = "platform",
    entity: Optional[str] = None,
    default_actions: Optional[Dict[str, str]] = None,
    self_id: str,
    capability_classes: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    out = dict(data)
    steps = out.get("steps")
    if isinstance(steps, list) and steps:
        out["steps"] = _shape_workflow_steps(
            steps,
            roster,
            product_name=product_name,
            entity=entity,
            default_actions=default_actions,
            fallback_domain=data,
            capability_classes=capability_classes,
        )
        return ensure_workflow_result(out)
    peers = [b for b in roster if b and b != self_id]
    built: List[Dict[str, Any]] = []
    for block in peers[:3]:
        # workflow reads step.get("block"); "block_id" is ignored (live miss).
        # Nested prepare: raw domain JSON forwarded as step input is how
        # live veterinary-care PRODUCT failed (database/table, event_bus
        # notify, document_engine parser) inside the workflow result.
        step: Dict[str, Any] = {
            "block": block,
            "input": prepare_block_input(
                block,
                data,
                roster=roster,
                product_name=product_name,
                entity=entity,
                default_actions=default_actions,
                capability_classes=capability_classes,
            ),
        }
        action = _step_action(block, default_actions)
        if action:
            step["action"] = action
        built.append(step)
    if not built:
        # Capability bound only to the orchestrator: still supply a
        # well-formed step list so its required-field check is not the
        # failure mode.
        built.append({"block": self_id, "input": dict(data)})
    out["steps"] = built
    return ensure_workflow_result(out)


def _looks_minted_team_id(value: Any) -> bool:
    """True when ``value`` is a create_team id, not a domain label.

    Live veterinarian_availability forwarded a domain string as ``team_id``
    and the Store answered ``Team access denied``. Minted ids are
    ``team_`` + hex (see block_obligations.create_team measurement).
    """
    if not isinstance(value, str):
        return False
    body = value[5:] if value.startswith("team_") else ""
    return len(body) >= 8 and all(c in "0123456789abcdefABCDEF" for c in body)


def _platform_team_id() -> Optional[str]:
    """Id the R1c startup step minted, creating the team if startup missed it."""
    try:
        from app.preconditions import resource_id  # type: ignore
    except Exception:  # noqa: BLE001 — generated workspace may lack module
        return None
    tid = resource_id("team")
    if tid:
        return str(tid)
    try:
        from app.preconditions import ensure_all  # type: ignore

        ensure_all()
    except Exception:  # noqa: BLE001 — boot must not die inside prepare
        return None
    tid = resource_id("team")
    return str(tid) if tid else None


def _for_team(data: Dict[str, Any], *, product_name: str = "platform") -> Dict[str, Any]:
    out = dict(data)
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
    slug_base = re.sub(r"[^a-z0-9-]+", "-", (product_name or "platform").lower()).strip("-")
    slug_base = slug_base or "platform"
    out.setdefault("user_id", "system")
    out.setdefault("name", f"{product_name or 'platform'} team")
    out.setdefault("slug", f"{slug_base}-team")
    minted = _platform_team_id()
    current = out.get("team_id")
    if minted and (not current or not _looks_minted_team_id(current)):
        out["team_id"] = minted
    # Final guard: never leave a None on a lowercased key.
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
        elif key in out and not isinstance(out[key], str):
            out[key] = str(out[key])
    return out


#: Smallest PDF the Store document_engine will open. ``text`` alone is not
#: enough: the live default parse action answers "No input files provided
#: (pdf/docx/xlsx). Pass file_path as pdf_path, docx_path, or xlsx_path."
_MINIMAL_PDF = (
    b"%PDF-1.1\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
)

_SYNTH_DOC_PATH: Optional[str] = None


def _existing_file_path(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return value if os.path.isfile(value) else None
    except OSError:
        return None


def _synthesized_document_path() -> str:
    """Write one reusable temp PDF so path keys point at a real file."""
    global _SYNTH_DOC_PATH
    if _SYNTH_DOC_PATH and os.path.isfile(_SYNTH_DOC_PATH):
        return _SYNTH_DOC_PATH
    fd, path = tempfile.mkstemp(prefix="platform-doc-", suffix=".pdf")
    with os.fdopen(fd, "wb") as handle:
        handle.write(_MINIMAL_PDF)
    _SYNTH_DOC_PATH = path
    return path


def _for_document_engine(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    # Drop dict values on path keys — that is the live TypeError.
    for key in _DOC_PATH_KEYS:
        if isinstance(out.get(key), dict):
            out.pop(key)
        elif key not in out and _existing_file_path(inner.get(key)):
            out[key] = inner[key]
    existing = None
    for key in _DOC_PATH_KEYS:
        existing = _existing_file_path(out.get(key))
        if existing:
            out.setdefault("file_path", existing)
            out.setdefault("pdf_path", existing)
            break
    text = out.get("text")
    if not (isinstance(text, str) and text.strip()):
        lines = [
            f"{key}: {value}"
            for key, value in data.items()
            if isinstance(value, (str, int, float, bool))
        ]
        text = "\n".join(lines) if lines else json.dumps(data, default=str)
        out["text"] = text
    if existing:
        return out
    # A placeholder path ("sample") or text-only payload still fails the
    # live parse action. Materialize a real PDF and point the contract keys
    # at it — do not invent success over a path the block cannot open.
    path = _synthesized_document_path()
    out["file_path"] = path
    out["pdf_path"] = path
    return out


def _for_analytics(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("metric", "value"):
        if key not in out and key in inner:
            out[key] = inner[key]
    if not out.get("metric"):
        for key, value in data.items():
            if key != "input" and isinstance(value, str) and value:
                out["metric"] = key
                break
        out.setdefault("metric", "event")
    if out.get("value") is None:
        for key, value in data.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out["value"] = value
                break
        out.setdefault("value", 1)
    return out


_TOPIC_KEYS = ("topic", "event", "event_type", "event_name", "reminder_type")


def _topic_from_domain(data: Dict[str, Any]) -> str:
    """A short event_bus topic derived from the capability record."""
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", ".", _summary_message(data)).strip(".")
    return (slug or "platform.event")[:80]


def _for_event_bus(
    data: Dict[str, Any],
    roster: Sequence[str] = (),
    *,
    self_id: str,
) -> Dict[str, Any]:
    """Satisfy Store event_bus notify — not a copy of the schema sample.

    Live automated_reminders forwarded the capability JSON; the Store
    event_bus raised ``RuntimeError: topic required``. Map a domain name
    (reminder_type / event / …) or a record summary onto ``topic``.
    Live PRODUCT then failed notify: topic alone is not a notification
    payload — also supply ``payload`` / ``data`` / ``message``.

    After #331 the wall stayed at appointment_scheduling step_1 because
    ``out = dict(data)`` kept pet/date/channel=email on the child and
    omitted MCP ``block``/``tool``. Workflow children bypass factory
    dispatch, so those extras reach Store as-is.
    """
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}
    topic = data.get("topic") or inner.get("topic")
    if not (isinstance(topic, str) and topic.strip()):
        for key in _TOPIC_KEYS:
            if key == "topic":
                continue
            cand = data.get(key) if data.get(key) is not None else inner.get(key)
            if isinstance(cand, str) and cand.strip():
                topic = cand.strip()
                break
        else:
            topic = _topic_from_domain(data)
    topic = str(topic).strip()
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else None
    if payload is None and isinstance(inner.get("payload"), dict):
        payload = inner["payload"]
    if not isinstance(payload, dict):
        payload = {
            key: value
            for key, value in data.items()
            if key not in _SKIP_REQUIRED_NAMES
            and isinstance(value, (str, int, float, bool))
        } or {"topic": topic}
    message = data.get("message") if isinstance(data.get("message"), str) else ""
    if not message.strip():
        inner_msg = inner.get("message")
        message = inner_msg if isinstance(inner_msg, str) and inner_msg.strip() else _summary_message(data)
    channel = notification_channel(
        data.get("channel") if data.get("channel") is not None else inner.get("channel"),
        data,
    )
    # MCP notify target is always the events block itself. A peer id
    # (database) as block/tool is the live automated_reminders Store step_0
    # refuse: workflow's first child is event_bus but notify looked for the
    # peer.
    return {
        "topic": topic,
        "payload": dict(payload),
        "data": dict(payload),
        "event": topic,
        "message": str(message).strip() or _summary_message(data),
        "channel": channel,
        "block": self_id,
        "tool": self_id,
    }


_QUEUE_INT_KEYS = (
    "id",
    "priority",
    "delay",
    "delay_seconds",
    "timeout",
    "visibility_timeout",
    "attempts",
    "max_attempts",
    "retry_count",
    "item_id",
)


def _coerce_int(value: Any) -> Optional[int]:
    """Coerce digit strings / floats to int. Leave domain labels alone."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


_QUEUE_COMPARE_KEYS = (
    "priority",
    "delay",
    "delay_seconds",
    "timeout",
    "visibility_timeout",
    "attempts",
    "max_attempts",
    "retry_count",
)


def _for_queue(data: Dict[str, Any]) -> Dict[str, Any]:
    """Store queue blocks refuse str where they declared int (live PRODUCT)."""
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in _QUEUE_INT_KEYS:
        raw = out[key] if key in out else inner.get(key)
        if raw is None:
            continue
        coerced = _coerce_int(raw)
        if coerced is not None:
            out[key] = coerced
    # FastAPI work_queue_process(item_id: int) and Store queue.get(id: int)
    # both refuse the domain label "id-1"; a digit string becomes the item id.
    if out.get("item_id") is None:
        aliased = _coerce_int(out.get("id"))
        if aliased is not None:
            out["item_id"] = aliased
    elif out.get("id") is None:
        aliased = _coerce_int(out.get("item_id"))
        if aliased is not None:
            out["id"] = aliased
    # Live sess_a69c8ce: Store queue does ``priority > n``; ``"sample"`` /
    # ``"id-1"`` from ``_sample_value`` is not a digit string so it survived
    # coerce and raised TypeError. Comparison keys become 0; non-numeric
    # ids are dropped from the queue record (kept only in payload).
    for key in _QUEUE_COMPARE_KEYS:
        if key in out and _coerce_int(out[key]) is None:
            out[key] = 0
    for key in ("id", "item_id"):
        if key in out and _coerce_int(out[key]) is None:
            out.pop(key)
    if "payload" not in out and "item" not in out:
        out["payload"] = {
            key: value
            for key, value in data.items()
            if key not in _SKIP_REQUIRED_NAMES
            and key not in {"payload", "item", "input"}
            and isinstance(value, (str, int, float, bool))
        }
    out.setdefault("priority", 0)
    return out


def _usable_table_name(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip().isidentifier():
        return value.strip()
    return None


def _for_database(
    data: Dict[str, Any], *, entity: Optional[str] = None
) -> Dict[str, Any]:
    """Satisfy ``missing sql or table`` from the domain record.

    The factory's Store-unwired query adapter
    (``offline_adapters.emit_database_query``) returns exactly
    ``Query failed: missing sql or table`` when the handler omitted both.
    A domain record is not SQL; map it onto ``table`` + ``values`` the way
    notification maps onto channel/message.

    The table is the DECLARED schema's: the capability entity when the
    handler wrapper supplies it (Alembic creates exactly that table), else
    the table the handler named. SQL a handler passes is its own and is
    never rewritten from its text -- the emitted store builds statements as
    SQLAlchemy Core from the declared tables, so there is no leftover
    default table to retarget. With neither, no table is invented: the block
    answers ``missing sql or table`` and the gate shows why.
    """
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("sql", "table", "table_name", "values"):
        if key not in out and key in inner:
            out[key] = inner[key]
    sql = out.get("sql")
    if isinstance(sql, str) and sql.strip():
        return out
    table = _usable_table_name(entity) or _usable_table_name(
        out.get("table") or out.get("table_name") or inner.get("table") or inner.get("table_name")
    )
    if not table:
        return out
    out["table"] = table
    if not isinstance(out.get("values"), dict):
        values = {
            key: value
            for key, value in data.items()
            if key not in _SKIP_REQUIRED_NAMES
            and key not in {"sql", "table", "table_name", "values", "input", "entity"}
            and isinstance(value, (str, int, float, bool))
        }
        if values:
            out["values"] = values
    return out


_NON_IDENT_CHARS = re.compile(r"[^0-9A-Za-z_]+")


def sanitize_python_identifier(
    name: Any,
    *,
    used: Optional[Set[str]] = None,
    reserved: Optional[Iterable[str]] = None,
) -> str:
    """Return a unique valid Python identifier derived from ``name``.

    Keywords (``and``, ``class``, ``for``, …) get a trailing underscore.
    Illegal characters are remapped to ``_``. Leading digits are prefixed.
    ``id`` / ``self`` collide with the generated dataclass primary key and
    the instance name, so they are suffixed too.
    """
    extra = set(reserved or ())
    cleaned = _NON_IDENT_CHARS.sub("_", str(name or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "field"
    if cleaned[0].isdigit():
        cleaned = f"field_{cleaned}"
    if (
        keyword.iskeyword(cleaned)
        or cleaned in extra
        or cleaned in {"id", "self"}
    ):
        cleaned = f"{cleaned}_"
    if not cleaned.isidentifier():
        cleaned = "field"
    taken = used if used is not None else set()
    candidate = cleaned
    n = 2
    while candidate in taken:
        candidate = f"{cleaned}_{n}"
        n += 1
    taken.add(candidate)
    return candidate


def _usable_align_name(name: Optional[str]) -> Optional[str]:
    """Accept only real domain identifiers — never keywords or junk tokens."""
    name = str(name or "").strip()
    if not name or name in _ALIGN_SKIP_NAMES:
        return None
    if not name.isidentifier() or keyword.iskeyword(name):
        return None
    # An ALL_CAPS identifier is a constant or a configuration key -- by the
    # convention every Python file already follows -- and never a record field.
    #
    # Live: TESTER failed ``test_every_model_round_trips`` with
    # ``KeyError: 'GOOGLE_CLIENT_ID'``. A connector checked its own required
    # SETTINGS with the same loop shape a handler uses to check required
    # FIELDS, so the roster miner put the operator's credentials into the
    # entity's spec. The generated test then saved them as a record, the model
    # had no such column, and ``fetched[key]`` raised -- a whole rework round
    # spent on the miner fabricating the very thing it exists to discover.
    #
    # The shape is the rule; no credential or setting is named here. What a
    # caller posts is lower snake_case in every spec this factory compiles;
    # what an operator configures is upper-case.
    if name.isupper():
        return None
    return name


def _assign_allowed_values(slot: Dict[str, Any], values: Sequence[str]) -> None:
    """Copy mined vocab onto a contract. Envelope status wins over LLM lists."""
    incoming = list(values)
    if not incoming:
        return
    current = slot.get("allowed_values")
    if is_envelope_status_field(slot.get("name")):
        if is_envelope_status_vocab(current) and not is_envelope_status_vocab(incoming):
            return
        if is_envelope_status_vocab(incoming):
            incoming = list(ENVELOPE_STATUS_VALUES)
    slot["allowed_values"] = incoming
    slot.setdefault("type", "str")


def extract_capability_route_source(routes_text: str, capability_id: str) -> str:
    """Slice of ``app/routes.py`` for one capability (guard + coder body)."""
    text = routes_text or ""
    cid = str(capability_id or "")
    name = cid.replace("-", "_")
    start = -1
    for marker in (f"# --- {cid} ", f"# --- {name} "):
        start = text.find(marker)
        if start >= 0:
            break
    if start < 0:
        match = re.search(
            rf"async def {re.escape(name)}_create\(",
            text,
        )
        if not match:
            return ""
        start = match.start()
    rest = text[start:]
    nxt = re.search(r"\n# --- ", rest[4:])
    if nxt:
        return rest[: nxt.start() + 4]
    return rest


def _merge_field_contract(
    field: Dict[str, Any],
    contract: Optional[Dict[str, Any]],
) -> bool:
    """Copy mined type / vocab / bounds onto ``field``. Returns True if changed."""
    if not contract:
        return False
    changed = False
    ctype = contract.get("type")
    if ctype and (
        not field.get("type")
        or (str(field.get("type")) in {"str", "text", "string"} and ctype != "str")
    ):
        field["type"] = ctype
        changed = True
    allowed = contract.get("allowed_values")
    # Handler/route-mined vocab is the runtime guard. Live sess_5dfb4a3
    # and sess_1fd1d54c: appointment_scheduling LLM spec said
    # scheduled/completed (or bare str → sample="sample") while the
    # route ``_constraint_guard`` / coder handler enforced the factory
    # envelope ``open, in_progress, closed``. Envelope status already on
    # the field must not be overwritten by a later LLM list.
    if allowed and list(field.get("allowed_values") or []) != list(allowed):
        current = field.get("allowed_values")
        fname = str(field.get("name") or "")
        if is_envelope_status_field(fname) and is_envelope_status_vocab(current):
            if not is_envelope_status_vocab(allowed):
                allowed = None
        if allowed:
            field["allowed_values"] = list(allowed)
            changed = True
    if contract.get("approval") is True and not field.get("approval"):
        # F4: the approval marker rides the same channel as the vocabulary.
        field["approval"] = True
        changed = True
    if contract.get("min") is not None and field.get("min") is None:
        field["min"] = contract["min"]
        changed = True
    if contract.get("max") is not None and field.get("max") is None:
        field["max"] = contract["max"]
        changed = True
    if contract.get("required") and not field.get("required"):
        field["required"] = True
        changed = True
    return changed


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def settings_names(source: str) -> set:
    """Names this source reads from the ENVIRONMENT -- settings, not fields.

    The rule, whatever the spelling: what code reads from ``os.environ`` is
    something an operator configures, and is never something a caller posts.
    ALL_CAPS (see ``_usable_align_name``) is only the conventional shape of
    that; a handler that reads ``client_id`` from the environment has made it
    a setting just as surely.

    Two ways a name gets here:
    * literally -- ``os.environ["X"]``, ``os.environ.get("X")``, ``os.getenv("X")``
    * through a roster -- ``for key in ROSTER: os.environ.get(key)``, the live
      shape: a connector listed its credentials in one constant, checked them
      against the environment in ``configured()``, and listed them AGAIN among
      its record fields. The miner promoted the second list into the spec.

    AST, so prose and docstrings cannot contribute. Unparseable source
    contributes nothing, which errs toward mining as before.
    """
    import ast

    try:
        tree = ast.parse(source or "")
    except (SyntaxError, ValueError):
        return set()

    def _is_environ(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            return isinstance(node.value, ast.Name) and node.value.id == "os"
        return isinstance(node, ast.Name) and node.id == "environ"

    def _env_read_arg(node: ast.AST) -> Optional[ast.AST]:
        """The key expression of an environment read, else None."""
        if isinstance(node, ast.Subscript) and _is_environ(node.value):
            return node.slice
        if isinstance(node, ast.Call) and node.args:
            fn = node.func
            if isinstance(fn, ast.Attribute):
                if fn.attr in ("get", "pop") and _is_environ(fn.value):
                    return node.args[0]
                if fn.attr == "getenv" and isinstance(fn.value, ast.Name) and fn.value.id == "os":
                    return node.args[0]
            if isinstance(fn, ast.Name) and fn.id == "getenv":
                return node.args[0]
        return None

    rosters: Dict[str, List[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
                names = [
                    e.value for e in value.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                ]
                for target in targets:
                    if isinstance(target, ast.Name) and names:
                        rosters[target.id] = names

    found: set = set()
    loop_vars: Dict[str, str] = {}  # loop variable -> the roster it walks
    for node in ast.walk(tree):
        iters = []
        if isinstance(node, (ast.For, ast.AsyncFor)):
            iters.append((node.target, node.iter))
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            iters.extend((g.target, g.iter) for g in node.generators)
        for target, iterable in iters:
            if isinstance(target, ast.Name) and isinstance(iterable, ast.Name):
                if iterable.id in rosters:
                    loop_vars[target.id] = iterable.id

    for node in ast.walk(tree):
        key = _env_read_arg(node)
        if key is None:
            continue
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            found.add(key.value)
        elif isinstance(key, ast.Name) and key.id in loop_vars:
            found.update(rosters[loop_vars[key.id]])
    return found


def _parse_source(text: str) -> Optional[ast.Module]:
    """Parse a module, a route slice, or an indented handler-body fragment."""
    import textwrap

    source = text or ""
    dedented = textwrap.dedent(source)
    for candidate in (
        source,
        dedented,
        "def _fragment():\n" + textwrap.indent(dedented, "    "),
    ):
        try:
            return ast.parse(candidate)
        except (SyntaxError, ValueError):
            continue
    return None


def _builtin_type_name(node: ast.AST) -> Optional[str]:
    """``bool`` / ``int`` / ``float`` / ``str``: a name that IS a builtin type.
    The spec's type vocabulary is Python's own, so the name is the type."""
    import builtins

    if isinstance(node, ast.Name) and isinstance(getattr(builtins, node.id, None), type):
        return node.id
    return None


def _read_key(node: ast.AST, aliases: Mapping[str, str]) -> Optional[str]:
    """The record key an expression reads: ``m["k"]``, ``m.get("k", ...)``, a
    name bound to one of those, or any of them passed through a builtin type
    conversion or a no-argument method (``int(m["k"])``, ``m.get("k").strip()``)."""
    while True:
        if isinstance(node, ast.Call) and _builtin_type_name(node.func) and node.args:
            node = node.args[0]
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and not node.args
            and not node.keywords
            and not (node.func.attr == "get")
        ):
            node = node.func.value
            continue
        break
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return key.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    return None


def _aliases(tree: ast.AST) -> Dict[str, str]:
    """Names bound to a record read: ``value = payload.get("k")``."""
    out: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = _read_key(node.value, {})
            if key:
                out.setdefault(node.targets[0].id, key)
    return out


def _string_constants(node: ast.AST) -> Optional[List[str]]:
    """The string elements of a list / tuple / set literal (None otherwise)."""
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    values = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return values


def _roster_bindings(tree: ast.AST) -> Dict[str, List[str]]:
    """Names bound to a list/tuple literal of string constants."""
    out: Dict[str, List[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            values = _string_constants(node.value) if node.value is not None else None
            if not values:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, values)
    return out


def _mined_contracts(text: str) -> Dict[str, Dict[str, Any]]:
    """The type / vocabulary / bounds the code enforces on a field.

    Read only from the syntax tree -- the checks themselves, never the words of
    the refusal they raise:

    * ``payload.get("k") not in (...)`` (or a name bound to a literal) -> its
      vocabulary;
    * ``isinstance(payload.get("k"), int)`` -> its type;
    * ``payload.get("k") < n`` (refused) -> its lower bound, ``> n`` its upper;
    * the Factory's own ``constraints = {...}`` literal -> as declared.

    NEVER a required field (owner ruling 2026-10-05): which fields a request
    must carry comes only from the declared schema (the spec's ``fields`` /
    the model's FIELDS and CONSTRAINTS). A function parameter, a dict-key
    read, a membership test or a roster in code is not a declaration -- live,
    the generated block wrapper's ``kw.get("params")`` made every route demand
    a ``params`` field. These contracts only ENRICH fields the schema already
    declares (see ``align_spec_to_handler_fields``).
    """
    tree = _parse_source(text)
    contracts: Dict[str, Dict[str, Any]] = {}
    if tree is None:
        return contracts
    aliases = _aliases(tree)
    rosters = _roster_bindings(tree)

    def slot(name: Optional[str]) -> Optional[Dict[str, Any]]:
        usable = _usable_align_name(name)
        if not usable:
            return None
        return contracts.setdefault(usable, {"name": usable})

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            sides = [node.left, *node.comparators]
            for op, left, right in zip(node.ops, sides, sides[1:]):
                key = _read_key(left, aliases)
                if key is None:
                    continue
                if isinstance(op, ast.NotIn):
                    values = _string_constants(right)
                    if values is None and isinstance(right, ast.Name):
                        # A vocabulary bound to a name: its own literal.
                        values = rosters.get(right.id)
                    if values:
                        target = slot(key)
                        if target is not None:
                            _assign_allowed_values(target, values)
                elif isinstance(right, ast.Constant) and isinstance(right.value, (int, float)) \
                        and not isinstance(right.value, bool) \
                        and isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                    target = slot(key)
                    if target is None:
                        continue
                    target.setdefault("type", "float" if isinstance(right.value, float) else "int")
                    if isinstance(op, ast.Lt):
                        target["min"] = right.value
                    elif isinstance(op, ast.LtE):
                        target["min"] = right.value + 1
                    elif isinstance(op, ast.Gt):
                        target["max"] = right.value
                    elif isinstance(op, ast.GtE):
                        target["max"] = right.value - 1
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
        ):
            key = _read_key(node.args[0], aliases)
            kind = _builtin_type_name(node.args[1])
            if kind:
                # Only a type the spec vocabulary can sample: isinstance(x, dict)
                # is a structural guard, not a record column type.
                from app.factory.build.roles_handlers import _resolve_known_field_type

                kind = _resolve_known_field_type(kind)
            target = slot(key) if key is not None and kind else None
            if target is not None:
                target["type"] = kind
        elif (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "constraints" for t in node.targets)
        ):
            # The Factory's own route guard bakes this literal (see
            # roles_handlers._constraint_guard): a declaration, read as one.
            try:
                parsed = ast.literal_eval(node.value)
            except (ValueError, SyntaxError, TypeError):
                continue
            if not isinstance(parsed, dict):
                continue
            for name, rules in parsed.items():
                if not isinstance(rules, dict):
                    continue
                target = slot(name)
                if target is None:
                    continue
                allowed = rules.get("allowed_values")
                if isinstance(allowed, (list, tuple)) and allowed:
                    _assign_allowed_values(target, [str(v) for v in allowed])
                if rules.get("min") is not None:
                    target.setdefault("min", rules["min"])
                if rules.get("max") is not None:
                    target.setdefault("max", rules["max"])
                # F4: only the explicit marker arms an approval field.
                if rules.get("approval") is True:
                    target["approval"] = True

    for setting in settings_names(text):
        contracts.pop(setting, None)
    return contracts


def handler_field_contracts(handler_source: str) -> Dict[str, Dict[str, Any]]:
    """The type / vocabulary / bounds the handler enforces, per field name.

    Live VetConnect (sess_73409fa): LLM handlers enforced ``role`` in
    {veterinarian, ...}, ``is_active`` bool, ``login_count`` int >= 0 on fields
    the spec declared as bare ``str``. Copying the enforced shape onto the
    DECLARED field keeps ``_sample_payload`` in lockstep with the guard. A
    refusal MESSAGE contributes nothing, and nothing here makes a field
    required or adds one the schema does not declare.
    """
    return _mined_contracts(handler_source)


def align_spec_to_handler_fields(
    spec: Optional[Dict[str, Any]],
    contracts: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Enrich the spec's DECLARED fields with what the handler enforces.

    A VetConnect miss: fields existed as bare ``str`` while the handler
    enforced a vocabulary, a bool, or ``int >= 0``. This merge copies those
    contracts onto the declared field so the sample payload satisfies the
    guard. A name the schema does not declare is never added, and nothing
    here sets ``required``: the declared schema is the only source of both.
    """
    base = dict(spec or {})
    fields = [dict(f) for f in (base.get("fields") or []) if isinstance(f, dict)]
    by_name = {str(f.get("name")): f for f in fields if f.get("name")}
    changed: List[str] = []
    for name, contract in (contracts or {}).items():
        field = by_name.get(name)
        if field is not None and _merge_field_contract(field, contract):
            changed.append(name)

    if not changed:
        return base if spec is not None else {"fields": fields}, []
    out = dict(base)
    out["fields"] = fields
    notes = list(out.get("handler_aligned_fields") or [])
    notes.extend(changed)
    out["handler_aligned_fields"] = notes
    return out, changed


def align_spec_to_handler_source(
    spec: Optional[Dict[str, Any]],
    handler_source: str,
) -> Tuple[Dict[str, Any], List[str]]:
    """Mine a handler (or route) body and enrich the declared spec in one step."""
    return align_spec_to_handler_fields(spec, handler_field_contracts(handler_source))


def render_block_inputs_module(
    default_actions: Optional[Mapping[str, str]] = None,
    capability_classes: Optional[Mapping[str, str]] = None,
) -> str:
    """Source for the generated platform's ``app/block_inputs.py``.

    ``default_actions`` and ``capability_classes`` are THIS build's maps,
    harvested from the manifests of the blocks it vendored. A product never
    carries another product's answers.

    ``capability_classes=None`` means the caller holds no vendored manifests:
    the classes are then what the pinned Store's manifests declare. ``{}``
    means "no block declares one" and shapes nothing.
    """
    if capability_classes is None:
        from app.factory.store_kits import block_manifests, capability_class

        capability_classes = {
            bid: cls
            for bid, manifest in block_manifests().items()
            if (cls := capability_class(manifest))
        }
    return (
        '''"""Block input construction for this platform.

Generated by the factory WRITER. Handlers call prepare_block_input (via the
fail-closed execute wrapper) so domain records become block-acceptable
payloads without requiring the caller to know about channel/steps/paths.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple

STORE_BLOCK_DEFAULT_ACTIONS = __STORE_BLOCK_DEFAULT_ACTIONS__
#: block id -> the capability_class its signed block.json declares, read
#: from the blocks this platform vendored. Input shaping is chosen by the
#: class, never by the id.
STORE_BLOCK_CAPABILITY_CLASSES = __STORE_BLOCK_CAPABILITY_CLASSES__


def default_block_action(block_id, default_actions=None):
    """Keyword action for execute(..., action=). Never invent from payload."""
    bid = str(block_id or "").strip()
    if isinstance(default_actions, dict):
        cand = default_actions.get(bid)
        if isinstance(cand, str) and cand.strip():
            return cand.strip()
    mapped = STORE_BLOCK_DEFAULT_ACTIONS.get(bid)
    if isinstance(mapped, str) and mapped.strip():
        return mapped.strip()
    return None


_QUEUE_INT_KEYS = (
    "id",
    "priority",
    "delay",
    "delay_seconds",
    "timeout",
    "visibility_timeout",
    "attempts",
    "max_attempts",
    "retry_count",
    "item_id",
)
_DOC_PATH_KEYS = (
    "file_path",
    "pdf_path",
    "docx_path",
    "xlsx_path",
    "attachment_path",
    "document_path",
    "path",
)
_TEAM_STRING_KEYS = ("user_id", "name", "slug", "role", "email", "plan", "permission")
_TOPIC_KEYS = ("topic", "event", "event_type", "event_name", "reminder_type")
_MINIMAL_PDF = (
    b"%PDF-1.1\\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 3 3]>>endobj\\n"
    b"trailer<</Size 4/Root 1 0 R>>\\n"
)
_SYNTH_DOC_PATH = None
_SKIP = frozenset(
    {
        "ok",
        "error",
        "capability",
        "id",
        "status",
        "result",
        "results",
        "payload",
        "action",
        "block",
        "blocks",
        "channel",
        "message",
        "steps",
        "team_id",
    }
)


def split_execute_action(
    payload: Any,
    *,
    action: Optional[str] = None,
    default_action: Optional[str] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    data = dict(payload) if isinstance(payload, dict) else (
        {} if payload is None else {"value": payload}
    )
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}

    def _usable(value):
        if isinstance(value, str) and value.strip():
            return value
        return None

    resolved = (
        _usable(action)
        or _usable(data.get("action"))
        or _usable(inner.get("action"))
        or _usable(default_action)
    )
    data.pop("action", None)
    if isinstance(data.get("input"), dict):
        cleaned = dict(data["input"])
        cleaned.pop("action", None)
        data["input"] = cleaned
    return resolved, data


def prepare_block_input(
    block_id: str,
    domain: Any,
    *,
    action: Optional[str] = None,
    roster: Sequence[str] = (),
    product_name: str = "platform",
    entity: Optional[str] = None,
    default_actions: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    _resolved, data = split_execute_action(domain, action=action)
    bid = str(block_id or "")
    merged_actions = dict(STORE_BLOCK_DEFAULT_ACTIONS)
    if default_actions:
        merged_actions.update(default_actions)
    cls = STORE_BLOCK_CAPABILITY_CLASSES.get(bid)
    if cls == "messaging":
        return _for_notification(data, roster, self_id=bid)
    if cls == "orchestration":
        return _for_workflow(
            data,
            roster,
            product_name=product_name,
            entity=entity,
            default_actions=merged_actions,
            self_id=bid,
        )
    if cls == "membership":
        return _for_team(data, product_name=product_name)
    if cls == "document_extraction":
        return _for_document_engine(data)
    if cls == "metrics":
        return _for_analytics(data)
    if cls == "events":
        return _for_event_bus(data, roster, self_id=bid)
    if cls == "records":
        return _for_database(data, entity=entity)
    if cls == "jobs":
        return _for_queue(data)
    if cls == "layout":
        return _for_dashboard(data)
    return data


def _summary_message(data: Dict[str, Any]) -> str:
    parts = [
        f"{key}={value}"
        for key, value in data.items()
        if isinstance(value, (str, int, float, bool)) and key not in _SKIP
    ]
    return (("; ".join(parts)).strip() or "platform notification")[:500]


def _for_dashboard(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    status = out.get("status") if out.get("status") is not None else inner.get("status")
    if not (isinstance(status, str) and status.strip()):
        out["status"] = "open"
    elif not out.get("status"):
        out["status"] = status
    return out


def _notification_channel(value, data=None):
    raw = str(value or "").strip().lower()
    payload = data if isinstance(data, dict) else {}
    if raw == "mcp":
        return "mcp"
    if raw == "email":
        to = payload.get("to") or payload.get("email")
        if isinstance(to, str) and to.strip() and "@" in to:
            return "email"
        return "mcp"
    if raw == "webhook":
        url = payload.get("url") or payload.get("webhook_url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return "webhook"
        return "mcp"
    if raw == "slack":
        if payload.get("webhook_url") or payload.get("slack_webhook_url"):
            return "slack"
        return "mcp"
    return "mcp"


def _for_notification(data: Dict[str, Any], roster: Sequence[str], *, self_id: str) -> Dict[str, Any]:
    out = dict(data)
    out["channel"] = _notification_channel(out.get("channel"), out)
    if not out.get("message"):
        body = out.get("body")
        out["message"] = (
            body if isinstance(body, str) and body.strip() else _summary_message(data)
        )
    if str(out.get("channel")).lower() == "mcp" and not out.get("block") and not out.get("tool"):
        peers = [b for b in roster if b and b != self_id]
        out["block"] = peers[0] if peers else self_id
    return out


def _workflow_step_block(step):
    for key in ("block", "block_id", "name"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _step_action(block_id, default_actions):
    return default_block_action(block_id, default_actions)


def _shape_workflow_steps(
    steps,
    roster,
    *,
    product_name,
    entity,
    default_actions,
    fallback_domain,
):
    shaped = []
    for step in steps:
        if not isinstance(step, dict):
            shaped.append(step)
            continue
        item = dict(step)
        bid = _workflow_step_block(item)
        if not bid:
            shaped.append(item)
            continue
        raw = item.get("input")
        payload = raw if isinstance(raw, dict) else fallback_domain
        item["block"] = bid
        item["input"] = prepare_block_input(
            bid,
            payload,
            roster=roster,
            product_name=product_name,
            entity=entity,
            default_actions=default_actions,
        )
        if not item.get("action"):
            action = _step_action(bid, default_actions)
            if action:
                item["action"] = action
        shaped.append(item)
    return shaped


def _workflow_result_payload(data, steps=None):
    if isinstance(data, dict):
        existing = data.get("result")
        if existing not in (None, ""):
            return existing
    chain = steps
    if chain is None and isinstance(data, dict):
        chain = data.get("steps")
    for step in chain or ():
        if not isinstance(step, dict):
            continue
        inner = step.get("input")
        if isinstance(inner, dict) and inner:
            return dict(inner)
        if inner not in (None, ""):
            return inner
    if not isinstance(data, dict):
        return {"reference": "record"}
    scalars = {
        key: value
        for key, value in data.items()
        if key not in {
            "steps",
            "action",
            "result",
            "results",
            "input",
            "ok",
            "error",
            "block",
            "blocks",
        }
        and value not in (None, "")
    }
    return scalars or {"reference": data.get("reference") or "record"}


def _ensure_workflow_result(data, steps=None):
    out = dict(data) if isinstance(data, dict) else {}
    if steps is not None:
        out["steps"] = list(steps)
    if out.get("result") not in (None, ""):
        return out
    out["result"] = _workflow_result_payload(out, out.get("steps"))
    return out


def _for_workflow(
    data: Dict[str, Any],
    roster: Sequence[str],
    *,
    product_name: str = "platform",
    entity: Optional[str] = None,
    default_actions: Optional[Dict[str, str]] = None,
    self_id: str = "",
) -> Dict[str, Any]:
    out = dict(data)
    steps = out.get("steps")
    if isinstance(steps, list) and steps:
        out["steps"] = _shape_workflow_steps(
            steps,
            roster,
            product_name=product_name,
            entity=entity,
            default_actions=default_actions,
            fallback_domain=data,
        )
        return _ensure_workflow_result(out)
    peers = [b for b in roster if b and b != self_id]
    built: List[Dict[str, Any]] = []
    for block in peers[:3]:
        step = {
            "block": block,
            "input": prepare_block_input(
                block,
                data,
                roster=roster,
                product_name=product_name,
                entity=entity,
                default_actions=default_actions,
            ),
        }
        action = _step_action(block, default_actions)
        if action:
            step["action"] = action
        built.append(step)
    if not built:
        built.append({"block": self_id, "input": dict(data)})
    out["steps"] = built
    return _ensure_workflow_result(out)


def _looks_minted_team_id(value):
    if not isinstance(value, str):
        return False
    body = value[5:] if value.startswith("team_") else ""
    return len(body) >= 8 and all(c in "0123456789abcdefABCDEF" for c in body)


def _platform_team_id():
    try:
        from app.preconditions import resource_id
    except Exception:
        return None
    tid = resource_id("team")
    if tid:
        return str(tid)
    try:
        from app.preconditions import ensure_all

        ensure_all()
    except Exception:
        return None
    tid = resource_id("team")
    return str(tid) if tid else None


def _for_team(data: Dict[str, Any], *, product_name: str = "platform") -> Dict[str, Any]:
    out = dict(data)
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
    slug_base = re.sub(r"[^a-z0-9-]+", "-", (product_name or "platform").lower()).strip("-")
    slug_base = slug_base or "platform"
    out.setdefault("user_id", "system")
    out.setdefault("name", f"{product_name or 'platform'} team")
    out.setdefault("slug", f"{slug_base}-team")
    minted = _platform_team_id()
    current = out.get("team_id")
    if minted and (not current or not _looks_minted_team_id(current)):
        out["team_id"] = minted
    for key in _TEAM_STRING_KEYS:
        if key in out and out[key] is None:
            out.pop(key)
        elif key in out and not isinstance(out[key], str):
            out[key] = str(out[key])
    return out


def _existing_file_path(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return value if os.path.isfile(value) else None
    except OSError:
        return None


def _synthesized_document_path():
    global _SYNTH_DOC_PATH
    if _SYNTH_DOC_PATH and os.path.isfile(_SYNTH_DOC_PATH):
        return _SYNTH_DOC_PATH
    fd, path = tempfile.mkstemp(prefix="platform-doc-", suffix=".pdf")
    with os.fdopen(fd, "wb") as handle:
        handle.write(_MINIMAL_PDF)
    _SYNTH_DOC_PATH = path
    return path


def _for_document_engine(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in _DOC_PATH_KEYS:
        if isinstance(out.get(key), dict):
            out.pop(key)
        elif key not in out and _existing_file_path(inner.get(key)):
            out[key] = inner[key]
    existing = None
    for key in _DOC_PATH_KEYS:
        existing = _existing_file_path(out.get(key))
        if existing:
            out.setdefault("file_path", existing)
            out.setdefault("pdf_path", existing)
            break
    text = out.get("text")
    if not (isinstance(text, str) and text.strip()):
        lines = [
            f"{key}: {value}"
            for key, value in data.items()
            if isinstance(value, (str, int, float, bool))
        ]
        out["text"] = "\\n".join(lines) if lines else json.dumps(data, default=str)
    if existing:
        return out
    path = _synthesized_document_path()
    out["file_path"] = path
    out["pdf_path"] = path
    return out


def _for_analytics(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("metric", "value"):
        if key not in out and key in inner:
            out[key] = inner[key]
    if not out.get("metric"):
        for key, value in data.items():
            if key != "input" and isinstance(value, str) and value:
                out["metric"] = key
                break
        out.setdefault("metric", "event")
    if out.get("value") is None:
        for key, value in data.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out["value"] = value
                break
        out.setdefault("value", 1)
    return out


def _topic_from_domain(data):
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", ".", _summary_message(data)).strip(".")
    return (slug or "platform.event")[:80]


def _for_event_bus(data, roster=(), self_id=""):
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}
    topic = data.get("topic") or inner.get("topic")
    if not (isinstance(topic, str) and topic.strip()):
        for key in _TOPIC_KEYS:
            if key == "topic":
                continue
            cand = data.get(key) if data.get(key) is not None else inner.get(key)
            if isinstance(cand, str) and cand.strip():
                topic = cand.strip()
                break
        else:
            topic = _topic_from_domain(data)
    topic = str(topic).strip()
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else None
    if payload is None and isinstance(inner.get("payload"), dict):
        payload = inner["payload"]
    if not isinstance(payload, dict):
        payload = {
            key: value
            for key, value in data.items()
            if key not in _SKIP and isinstance(value, (str, int, float, bool))
        } or {"topic": topic}
    message = data.get("message") if isinstance(data.get("message"), str) else ""
    if not str(message).strip():
        inner_msg = inner.get("message")
        message = inner_msg if isinstance(inner_msg, str) and inner_msg.strip() else _summary_message(data)
    channel = _notification_channel(
        data.get("channel") if data.get("channel") is not None else inner.get("channel"),
        data,
    )
    return {
        "topic": topic,
        "payload": dict(payload),
        "data": dict(payload),
        "event": topic,
        "message": str(message).strip() or _summary_message(data),
        "channel": channel,
        "block": self_id,
        "tool": self_id,
    }


def _coerce_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _for_queue(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in _QUEUE_INT_KEYS:
        raw = out[key] if key in out else inner.get(key)
        if raw is None:
            continue
        coerced = _coerce_int(raw)
        if coerced is not None:
            out[key] = coerced
    if out.get("item_id") is None:
        aliased = _coerce_int(out.get("id"))
        if aliased is not None:
            out["item_id"] = aliased
    elif out.get("id") is None:
        aliased = _coerce_int(out.get("item_id"))
        if aliased is not None:
            out["id"] = aliased
    for key in ("priority", "delay", "delay_seconds", "timeout", "visibility_timeout", "attempts", "max_attempts", "retry_count"):
        if key in out and _coerce_int(out[key]) is None:
            out[key] = 0
    for key in ("id", "item_id"):
        if key in out and _coerce_int(out[key]) is None:
            out.pop(key)
    if "payload" not in out and "item" not in out:
        out["payload"] = {
            key: value
            for key, value in data.items()
            if key not in _SKIP
            and key not in {"payload", "item", "input"}
            and isinstance(value, (str, int, float, bool))
        }
    out.setdefault("priority", 0)
    return out


def _usable_table_name(value):
    if isinstance(value, str) and value.strip().isidentifier():
        return value.strip()
    return None


def _for_database(data: Dict[str, Any], *, entity: Optional[str] = None) -> Dict[str, Any]:
    # The table is the declared schema's (the capability entity), else the
    # one the handler named; SQL a handler passes is never rewritten.
    out = dict(data)
    inner = out.get("input") if isinstance(out.get("input"), dict) else {}
    for key in ("sql", "table", "table_name", "values"):
        if key not in out and key in inner:
            out[key] = inner[key]
    sql = out.get("sql")
    if isinstance(sql, str) and sql.strip():
        return out
    table = _usable_table_name(entity) or _usable_table_name(
        out.get("table") or out.get("table_name") or inner.get("table") or inner.get("table_name")
    )
    if not table:
        return out
    out["table"] = table
    if not isinstance(out.get("values"), dict):
        values = {
            key: value
            for key, value in data.items()
            if key not in _SKIP
            and key not in {"sql", "table", "table_name", "values", "input", "entity"}
            and isinstance(value, (str, int, float, bool))
        }
        if values:
            out["values"] = values
    return out
'''
    ).replace(
        "__STORE_BLOCK_DEFAULT_ACTIONS__",
        repr(dict(sorted((default_actions or {}).items()))),
    ).replace(
        "__STORE_BLOCK_CAPABILITY_CLASSES__",
        repr(dict(sorted((capability_classes or {}).items()))),
    )
