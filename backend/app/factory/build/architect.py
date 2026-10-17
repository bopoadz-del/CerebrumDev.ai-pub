"""The architect-foreman: the model writes understanding, code owns the contract.

The compiled brief (brief_compiler) is the CONTRACT: registry verdicts per
capability with block ids, the acceptance floor's requirement lines, the
FORBIDDEN list, the receipt contract, lane paths, build level, locale and the
intake-derived kit rules. It is deterministic and the model never sees a way
to change it -- the model only fills the NARRATIVE: what this platform is for,
how its resolved blocks compose into its capabilities, what the domain rules
mean for each handler, the hard parts and the order of work, and what done
looks like for this user. The brief that reaches the coder is

    NARRATIVE  +  NARRATIVE_SEPARATOR  +  CONTRACT (verbatim)

so a narrative cannot omit or edit a contract line by construction: there is
no code path that writes a narrative into the contract. What the validator
still refuses, before anything is dispatched, is narrative text whose SHAPE is
wrong (the brief lint's own rules, never a phrase list):

* a block it declares using (its ``blocks`` list) that this build did not
  resolve, or any Store block id in its text that this build did not resolve;
* a capability it declares describing (its ``capabilities`` list) that is not
  in the blueprint;
* a build session id, or another platform's machine identity -- a
  capability / product / platform id another platform declared and this
  build did not (the brief lint's data-loaded set; display names are never
  evidence);
* a template slot or a ``[check:...]`` tag -- acceptance is code's;
* more than the length cap.

A failing narrative is regenerated ONCE with the findings; a second failure
falls back to the CONTRACT-only brief, and the fallback is recorded.

This is one module with two entry points sharing one model call, one lint
and one ledger path: ``compose_narrative`` (the brief, F0) now; the foreman's
rework instructions (F2) are the second entry point and reuse
``architect_call``, ``lint_architect_text`` and ``record_architect_call``.

``FACTORY_BRIEF_NARRATIVE`` = ``off`` (default) | ``shadow`` | ``on``. Shadow
composes, lints and records the narrative but dispatches the CONTRACT-only
brief, so a build's outcome is unchanged while the comparison runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Set

from app.factory.build.brief_lint import CHECK_TAG_RE, SESSION_ID_RE, SLOT_RE

FLAG_ENV = "FACTORY_BRIEF_NARRATIVE"
MODES = ("off", "shadow", "on")
MAX_CHARS_ENV = "FACTORY_BRIEF_NARRATIVE_MAX_CHARS"
MAX_CHARS_DEFAULT = 8000

#: The fixed boundary between the model's NARRATIVE and the code's CONTRACT.
NARRATIVE_SEPARATOR = "\n\n---\n# CONTRACT (compiled by the Factory -- binding, verbatim)\n\n"

#: Identifier-shaped tokens; a token is a block reference only if it is in
#: the Store's own id set (data, never a phrase).
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")

#: Ledger NOTE stage for every architect call (both entry points).
LEDGER_STAGE = "architect"

LlmCall = Callable[[List[Dict[str, str]]], Dict[str, Any]]


def narrative_mode() -> str:
    raw = str(os.getenv(FLAG_ENV, "") or "").strip().lower()
    return raw if raw in MODES else "off"


def narrative_max_chars() -> int:
    try:
        value = int(str(os.getenv(MAX_CHARS_ENV, "") or MAX_CHARS_DEFAULT).strip())
    except ValueError:
        return MAX_CHARS_DEFAULT
    return value if value > 0 else MAX_CHARS_DEFAULT


@dataclass
class ArchitectLint:
    ok: bool
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors)}


@dataclass
class NarrativeResult:
    mode: str
    narrative: str = ""
    used: bool = False
    fallback: bool = False
    attempts: int = 0
    lint: Dict[str, Any] = field(default_factory=dict)


# -- the shared core (both entry points) --------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _default_llm() -> LlmCall:
    """The architect model already on the Floor (factory LLM config)."""
    from app.factory.product_architect import _llm_json_call

    return lambda messages: _llm_json_call(messages)


def architect_model() -> str:
    from app.core.llm_config import get_factory_llm_config

    try:
        return str((get_factory_llm_config() or {}).get("model") or "")
    except Exception:  # noqa: BLE001 -- the model name is telemetry
        return ""


def failover_note(note: Optional[Callable[..., None]]) -> Optional[Callable[[Dict[str, Any]], None]]:
    """A model-ladder failover sink that writes one ledger NOTE per failover."""
    if note is None:
        return None

    def _sink(event: Dict[str, Any]) -> None:
        note(
            f"model provider failover: {event.get('provider')} refused "
            f"({event.get('kind')}"
            + (f", HTTP {event.get('status')}" if event.get("status") else "")
            + f"); trying {event.get('next_provider')}",
            stage=LEDGER_STAGE,
            source="model_ladder",
            failover=dict(event),
        )

    return _sink


def architect_call(
    messages: List[Dict[str, str]],
    *,
    llm: Optional[LlmCall] = None,
    note: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """One architect model call. Returns the parsed JSON object.

    ``note`` (the build ledger's note) receives one NOTE per provider failover.
    """
    from app.core.model_ladder import failover_to

    call = llm or _default_llm()
    with failover_to(failover_note(note)):
        out = call(messages)
    return out if isinstance(out, dict) else {}


def _known_literals() -> FrozenSet[str]:
    from app.factory.build.brief_lint import _known_literals as known

    return known()


def lint_architect_text(
    text: str,
    *,
    resolved_blocks: Set[str],
    store_blocks: Set[str],
    capabilities: Set[str],
    own_names: Set[str],
    declared_blocks: Sequence[str] = (),
    declared_capabilities: Sequence[str] = (),
    known_literals: Optional[FrozenSet[str]] = None,
    max_chars: Optional[int] = None,
) -> ArchitectLint:
    """Shape-only lint for model-written text (narrative or instruction)."""
    errors: List[str] = []
    body = str(text or "")
    cap = max_chars if max_chars is not None else narrative_max_chars()
    if not body.strip():
        errors.append("empty text")
    if len(body) > cap:
        errors.append(f"length {len(body)} over the {cap}-character cap")
    slots = SLOT_RE.findall(body)
    if slots:
        errors.append("template slot in model text: " + ", ".join(sorted(set(slots))))
    if CHECK_TAG_RE.search(body):
        errors.append("model text declares a [check:...] tag (acceptance is the contract's)")
    sessions = sorted(set(SESSION_ID_RE.findall(body)))
    if sessions:
        errors.append("cites a build session: " + ", ".join(sessions[:4]))
    declared = {str(b) for b in declared_blocks}
    # A Store block id in the text is a block reference whether declared or not.
    bare = {t for t in _TOKEN_RE.findall(body) if t in store_blocks}
    unresolved = sorted((declared | bare) - resolved_blocks)
    if unresolved:
        errors.append("block id that does not resolve for this build: " + ", ".join(unresolved[:8]))
    outside = sorted({str(c) for c in declared_capabilities} - capabilities)
    if outside:
        errors.append("capability outside the blueprint: " + ", ".join(outside[:8]))
    from app.factory.build.product_literals import foreign_identities_in

    known = known_literals if known_literals is not None else _known_literals()
    foreign = foreign_identities_in(body, known, own_names | resolved_blocks | capabilities)
    if foreign:
        errors.append("carries another product's identity (capability / product / platform id): " + ", ".join(foreign[:6]))
    return ArchitectLint(ok=not errors, errors=errors)


def record_architect_call(
    note: Optional[Callable[..., None]],
    *,
    entry: str,
    inputs: Mapping[str, Any],
    output: str,
    lint: ArchitectLint,
    fallback: bool,
    mode: str,
    attempt: int,
    model: str,
    tokens: Optional[int] = None,
    fallback_to: str = "the CONTRACT-only brief",
) -> None:
    """One ledger NOTE per architect call: what went in, what came out.
    ``fallback_to`` names what the entry point falls back to."""
    if note is None:
        return
    inputs_blob = json.dumps(inputs, sort_keys=True, default=str)
    note(
        f"architect {entry} (attempt {attempt}, mode {mode}): "
        + ("lint ok" if lint.ok else "lint refused: " + "; ".join(lint.errors)[:300])
        + (f" -- fell back to {fallback_to}" if fallback else ""),
        stage=LEDGER_STAGE,
        source="architect",
        architect={
            "entry": entry,
            "mode": mode,
            "attempt": attempt,
            "inputs_hash": _sha(inputs_blob),
            "output_hash": _sha(output or ""),
            "output": output or "",
            "model": model,
            "tokens": tokens,
            "lint": lint.to_dict(),
            "fallback": fallback,
        },
    )


# -- entry point 1: the brief's NARRATIVE (F0) ---------------------------------


def _resolved_blocks(compiled: Any) -> Set[str]:
    out: Set[str] = set()
    for item in getattr(compiled, "inventory", None) or []:
        missing = set(getattr(item, "missing", None) or [])
        for bid in list(getattr(item, "block_ids", None) or []) + list(
            getattr(item, "verified_present", None) or []
        ):
            if bid and bid not in missing:
                out.add(str(bid))
    return out


def _own_names(compiled: Any) -> Set[str]:
    from app.factory.build.brief_lint import _own_names as own

    return own(compiled)


def narrative_inputs(compiled: Any, *, user_request: str = "") -> Dict[str, Any]:
    """What the model reads: Store contracts + kit manifests (signed, as the
    compiler loaded them), the intake record, the domain pack (kernel), the
    resolved inventory, and the user's own request."""
    return {
        "product_name": getattr(compiled, "product_name", ""),
        "capabilities": list(getattr(compiled, "capabilities", None) or []),
        "inventory": [
            {
                "capability_id": getattr(i, "capability_id", ""),
                "strategy": getattr(i, "strategy", ""),
                "blocks": sorted(
                    set(getattr(i, "block_ids", None) or [])
                    - set(getattr(i, "missing", None) or [])
                ),
            }
            for i in getattr(compiled, "inventory", None) or []
        ],
        "block_contracts": dict(getattr(compiled, "contracts", None) or {}),
        "kit_manifests": dict(getattr(compiled, "kit_manifests", None) or {}),
        "intake": dict(getattr(compiled, "intake", None) or {}),
        "domain_pack": dict(getattr(compiled, "domain_pack", None) or {}),
        "user_request": str(user_request or ""),
    }


def _narrative_messages(inputs: Mapping[str, Any], findings: Sequence[str]) -> List[Dict[str, str]]:
    system = (
        "You are the lead engineer writing the NARRATIVE part of a coding brief. "
        "A separate CONTRACT section (compiled by code: block verdicts, acceptance "
        "lines, FORBIDDEN, receipts, lanes, level, locale) is appended after your "
        "text verbatim; you cannot change it and must not restate or contradict it. "
        "Write: what this platform is for; how the resolved blocks compose into each "
        "capability; what the domain rules mean for each handler; the hard parts and "
        "the order of work; what done looks like for this user. Name only blocks from "
        "the inventory given and only capabilities from the capabilities given. Do not "
        "write acceptance check tags or template markers. Reply as JSON: "
        "{\"narrative\": \"...\", \"blocks\": [block ids you named], "
        "\"capabilities\": [capability ids you described]}."
    )
    user = json.dumps(inputs, sort_keys=True, default=str)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if findings:
        msgs.append(
            {
                "role": "user",
                "content": "Your previous narrative was refused by the lint: "
                + "; ".join(findings)
                + ". Write it again without those defects.",
            }
        )
    return msgs


def compose_narrative(
    compiled: Any,
    *,
    user_request: str = "",
    llm: Optional[LlmCall] = None,
    note: Optional[Callable[..., None]] = None,
    mode: Optional[str] = None,
    known_literals: Optional[FrozenSet[str]] = None,
) -> NarrativeResult:
    """Compose, lint (twice at most) and record the brief's NARRATIVE."""
    active = mode if mode in MODES else narrative_mode()
    result = NarrativeResult(mode=active)
    if active == "off":
        return result
    inputs = narrative_inputs(compiled, user_request=user_request)
    resolved = _resolved_blocks(compiled)
    store = {str(b) for b in getattr(compiled, "store_ids", None) or []}
    caps = {str(c) for c in getattr(compiled, "capabilities", None) or []}
    own = _own_names(compiled)
    model = architect_model()
    findings: List[str] = []
    for attempt in (1, 2):
        result.attempts = attempt
        try:
            raw = architect_call(_narrative_messages(inputs, findings), llm=llm, note=note)
            text = str(raw.get("narrative") or "").strip()
            used_blocks = [str(b) for b in raw.get("blocks") or [] if b]
            used_caps = [str(c) for c in raw.get("capabilities") or [] if c]
            tokens = raw.get("_tokens") if isinstance(raw.get("_tokens"), int) else None
        except Exception as exc:  # noqa: BLE001 -- the model failing is a fallback, not a crash
            text, tokens = "", None
            lint = ArchitectLint(ok=False, errors=[f"architect call failed: {type(exc).__name__}"])
        else:
            lint = lint_architect_text(
                text,
                resolved_blocks=resolved,
                store_blocks=store,
                capabilities=caps,
                own_names=own,
                declared_blocks=used_blocks,
                declared_capabilities=used_caps,
                known_literals=known_literals,
            )
        last = attempt == 2 or lint.ok
        record_architect_call(
            note,
            entry="narrative",
            inputs=inputs,
            output=text,
            lint=lint,
            fallback=bool(last and not lint.ok),
            mode=active,
            attempt=attempt,
            model=model,
            tokens=tokens,
        )
        result.lint = lint.to_dict()
        if lint.ok:
            result.narrative = text
            result.used = active == "on"
            return result
        findings = list(lint.errors)
    result.fallback = True
    return result


def attach_narrative(
    ctx: Any,
    compiled: Any,
    *,
    llm: Optional[LlmCall] = None,
    mode: Optional[str] = None,
) -> Any:
    """The WRITER's one call site. ``on``: return the compiled brief with its
    NARRATIVE attached (CONTRACT untouched). ``shadow``: compose, lint and
    record, but return the brief unchanged. ``off``: no call at all."""
    from dataclasses import replace

    active = mode if mode in MODES else narrative_mode()
    if active == "off" or not hasattr(compiled, "narrative"):
        return compiled
    state = getattr(ctx, "state", None) or {}
    result = compose_narrative(
        compiled,
        user_request=str(state.get("brief") or ""),
        llm=llm,
        note=getattr(ctx, "note", None),
        mode=active,
    )
    if isinstance(state, dict):
        state["brief_narrative"] = {
            "mode": result.mode,
            "used": result.used,
            "fallback": result.fallback,
            "attempts": result.attempts,
        }
    if result.used and result.narrative:
        return replace(compiled, narrative=result.narrative)
    return compiled


def dispatch_text(compiled: Any) -> str:
    """The text the coder receives: NARRATIVE + separator + CONTRACT verbatim,
    or the CONTRACT alone when no narrative is attached."""
    contract = str(getattr(compiled, "text", "") or "")
    narrative = str(getattr(compiled, "narrative", "") or "").strip()
    if not narrative:
        return contract
    return narrative + NARRATIVE_SEPARATOR + contract
