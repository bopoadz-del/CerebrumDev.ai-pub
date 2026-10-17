"""F5: a FAILED build explains itself -- from the ledger, never invented.

Third entry point of the architect/foreman module (F0 ``compose_narrative``,
F2 ``foreman.review``): the same model call (``architect.architect_call``),
the same lint (``architect.lint_architect_text``) and the same ledger record
(``architect.record_architect_call``).

INPUT is the ledger only: one FACT per phase verdict, rework round and
terminal event, each with the event's own ``seq`` as its id. The model may
only rephrase those facts: every sentence cites fact ids, and a phase or a
check named in the text must be one the cited facts name. Anything else --
an uncited sentence, an unknown id, a phase or check the ledger never
recorded -- is refused; one regeneration with the refusals, then the
deterministic narrative below, which is rendered by code from the same facts
and is ALWAYS available (FACTORY_FOREMAN=off uses it alone).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from app.factory.build import architect
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind

ENTRY = "failure_narrative"
#: NOTE payload key carrying the attached narrative.
NARRATIVE_KEY = "failure_narrative"
SOURCE_MODEL = "model"
SOURCE_LEDGER = "ledger"
#: The facts a narrative may draw on: verdicts, rework rounds, terminals.
FACT_KINDS = (
    EventKind.GATE_PASSED,
    EventKind.GATE_FAILED,
    EventKind.PHASE_ABORTED,
    EventKind.REWORK,
    EventKind.RUN_FAILED,
)
FACTS_MAX = 40
DETAIL_CHARS = 300
NARRATIVE_CHARS_MAX = 2400

LlmCall = Callable[[List[Dict[str, str]]], Dict[str, Any]]


@dataclass
class Narrative:
    text: str
    source: str
    cites: List[str] = field(default_factory=list)
    mode: str = "off"
    attempts: int = 0
    refusals: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "source": self.source,
            "cites": list(self.cites),
            "mode": self.mode,
            "attempts": self.attempts,
            "refusals": list(self.refusals),
        }


# -- facts ---------------------------------------------------------------------


def _phase_of(event: Any) -> str:
    if getattr(event, "role", None) is not None:
        return event.role.value
    payload = event.payload or {}
    decision = payload.get("decision") or {}
    return str(decision.get("gate") or payload.get("gate") or payload.get("source") or "")


def _check_of(event: Any) -> str:
    payload = event.payload or {}
    decision = payload.get("decision") or {}
    return str(decision.get("check") or payload.get("check") or "")


def ledger_facts(events: Sequence[Any]) -> List[Dict[str, Any]]:
    """One fact per phase verdict, rework round and terminal -- id = the event seq."""
    facts: List[Dict[str, Any]] = []
    for event in events:
        if event.kind not in FACT_KINDS:
            continue
        payload = event.payload or {}
        decision = payload.get("decision") or {}
        facts.append(
            {
                "id": f"e{event.seq}",
                "kind": event.kind.value,
                "phase": _phase_of(event),
                "check": _check_of(event),
                "reason": str(decision.get("reason") or payload.get("reason") or ""),
                "finding": str(decision.get("finding") or "")[:DETAIL_CHARS],
                "detail": str(event.detail or "")[:DETAIL_CHARS],
            }
        )
    return facts[-FACTS_MAX:]


def _join(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def deterministic_narrative(facts: Sequence[Mapping[str, Any]]) -> Narrative:
    """What was tried, where it stopped, why -- rendered by code from the facts."""
    passed: List[str] = []
    passed_ids: List[str] = []
    reworks: List[Mapping[str, Any]] = []
    terminal: Optional[Mapping[str, Any]] = None
    last_failed: Optional[Mapping[str, Any]] = None
    for fact in facts:
        kind = fact["kind"]
        if kind == EventKind.GATE_PASSED.value and fact["phase"] and fact["phase"] not in passed:
            passed.append(fact["phase"])
            passed_ids.append(fact["id"])
        elif kind == EventKind.REWORK.value:
            reworks.append(fact)
        elif kind in (EventKind.GATE_FAILED.value, EventKind.PHASE_ABORTED.value):
            last_failed = fact
        elif kind == EventKind.RUN_FAILED.value:
            terminal = fact
    sentences: List[str] = []
    cites: List[str] = []
    if passed:
        sentences.append(f"What was tried: the run passed {_join(passed)}.")
        cites += passed_ids
    else:
        sentences.append("What was tried: no phase passed.")
    if reworks:
        where = sorted({f"{r['phase']}/{r['check']}".strip("/") for r in reworks if r["phase"] or r["check"]})
        sentences.append(
            f"It ran {len(reworks)} rework round{'s' if len(reworks) != 1 else ''}"
            + (f" ({', '.join(where)})." if where else ".")
        )
        cites += [r["id"] for r in reworks]
    stop = terminal or last_failed
    if stop is not None:
        phase = stop["phase"] or (last_failed or {}).get("phase") or "an unnamed phase"
        sentences.append(f"Where it stopped: {phase}" + (f" ({stop['check']})." if stop["check"] else "."))
        why = stop["finding"] or stop["reason"] or stop["detail"]
        if why:
            sentences.append(f"Why: {why}")
        cites.append(stop["id"])
    return Narrative(text=" ".join(sentences), source=SOURCE_LEDGER, cites=cites)


# -- validation ----------------------------------------------------------------


def _check_universe() -> FrozenSet[str]:
    """Every check id the Factory knows (floor + brief/gate constants)."""
    from app.factory.build import brief_gates
    from app.factory.build.acceptance_floor import check_ids

    ids = set(check_ids())
    ids.update(brief_gates.WRITER_CHECKS)
    ids.update({brief_gates.SUITE_CHECK, brief_gates.PRODUCT_GATE_CHECK})
    return frozenset(ids)


_PHASES: FrozenSet[str] = frozenset(r.value for r in BuildRole)


def validate_sentences(
    raw: Any, facts: Sequence[Mapping[str, Any]], *, checks: Optional[FrozenSet[str]] = None
) -> Tuple[Optional[List[Dict[str, Any]]], List[str]]:
    """Refuse anything the ledger does not say. Returns (sentences, refusals)."""
    errors: List[str] = []
    sentences = (raw or {}).get("sentences") if isinstance(raw, Mapping) else None
    if not isinstance(sentences, list) or not sentences:
        return None, ["no sentences"]
    by_id = {f["id"]: f for f in facts}
    universe = checks if checks is not None else _check_universe()
    out: List[Dict[str, Any]] = []
    for i, item in enumerate(sentences, 1):
        if not isinstance(item, Mapping):
            errors.append(f"sentence {i} is not an object")
            continue
        text = str(item.get("text") or "").strip()
        cites = [str(c) for c in (item.get("cites") or []) if str(c)]
        if not text:
            errors.append(f"sentence {i} is empty")
            continue
        if not cites:
            errors.append(f"sentence {i} cites no ledger fact")
            continue
        unknown = [c for c in cites if c not in by_id]
        if unknown:
            errors.append(f"sentence {i} cites facts the ledger does not have: {', '.join(unknown[:4])}")
            continue
        cited = [by_id[c] for c in cites]
        cited_phases = {f["phase"] for f in cited}
        cited_checks = {f["check"] for f in cited}
        tokens = set(architect._TOKEN_RE.findall(text))
        bad_phases = sorted((tokens & _PHASES) - cited_phases)
        if bad_phases:
            errors.append(f"sentence {i} names a phase its facts do not: {', '.join(bad_phases)}")
        bad_checks = sorted((tokens & universe) - cited_checks)
        if bad_checks:
            errors.append(f"sentence {i} names a check its facts do not: {', '.join(bad_checks)}")
        out.append({"text": text, "cites": cites})
    return (out if not errors else None), errors


# -- the entry point -----------------------------------------------------------


def _messages(facts: Sequence[Mapping[str, Any]], refusals: Sequence[str]) -> List[Dict[str, str]]:
    system = (
        "You explain to the platform's owner why their build stopped. You may use ONLY the "
        "ledger facts given -- never add a phase, check, file, cause or fix the facts do not "
        "state. Reply as JSON: {\"sentences\": [{\"text\": str, \"cites\": [fact ids]}]} -- "
        "three to five sentences covering what was tried, where it stopped and why; every "
        "sentence cites the ids of the facts it restates. No product, session or probe names "
        "beyond what the facts contain."
    )
    user = "LEDGER FACTS:\n" + json.dumps(list(facts), indent=1)
    if refusals:
        user += "\n\nYOUR LAST ANSWER WAS REFUSED:\n- " + "\n- ".join(refusals)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def compose_failure_narrative(
    events: Sequence[Any],
    *,
    mode: Optional[str] = None,
    llm: Optional[LlmCall] = None,
    note: Optional[Callable[..., None]] = None,
) -> Narrative:
    """The narrative attached to a FAILED run. Deterministic unless the
    foreman is ``on`` and the model's text survives validation."""
    from app.factory.build import foreman as fm

    active = mode if mode in fm.MODES else fm.foreman_mode()
    facts = ledger_facts(events)
    fallback = deterministic_narrative(facts)
    fallback.mode = active
    if active == "off" or not facts:
        return fallback
    own = {t for f in facts for t in architect._TOKEN_RE.findall(f"{f['detail']} {f['finding']}")}
    model = architect.architect_model()
    refusals: List[str] = []
    for attempt in (1, 2):
        output = ""
        tokens = None
        try:
            raw = architect.architect_call(_messages(facts, refusals), llm=llm, note=note)
            tokens = raw.get("_tokens") if isinstance(raw.get("_tokens"), int) else None
            body = {k: v for k, v in raw.items() if k != "_tokens"}
            output = json.dumps(body, sort_keys=True, default=str)
            sentences, errors = validate_sentences(body, facts)
            if sentences is not None:
                text = " ".join(s["text"] for s in sentences)
                lint = architect.lint_architect_text(
                    text,
                    resolved_blocks=set(),
                    store_blocks=set(),
                    capabilities=set(),
                    own_names=own,
                    max_chars=NARRATIVE_CHARS_MAX,
                )
                errors = list(lint.errors)
        except Exception as exc:  # noqa: BLE001 -- the model failing is the fallback
            sentences, errors = None, [f"narrative call failed: {type(exc).__name__}"]
        verdict = architect.ArchitectLint(ok=not errors, errors=list(errors))
        last = attempt == 2 or verdict.ok
        architect.record_architect_call(
            note,
            entry=ENTRY,
            inputs={"facts": list(facts)},
            output=output,
            lint=verdict,
            fallback=bool(last and not verdict.ok),
            mode=active,
            attempt=attempt,
            model=model,
            tokens=tokens,
            fallback_to="the ledger-rendered narrative",
        )
        if verdict.ok and sentences:
            if active != "on":
                fallback.attempts = attempt
                return fallback  # shadow: recorded, never shown
            return Narrative(
                text=" ".join(s["text"] for s in sentences),
                source=SOURCE_MODEL,
                cites=[c for s in sentences for c in s["cites"]],
                mode=active,
                attempts=attempt,
            )
        refusals = list(errors)
    fallback.attempts = 2
    fallback.refusals = refusals
    return fallback


def attach(ledger: BuildLedger, narrative: Narrative, *, role: Optional[BuildRole] = None) -> None:
    """Record the narrative on the run's ledger (read by build status + MANIFEST)."""
    ledger.append(
        EventKind.NOTE,
        role=role,
        detail="failure narrative (" + narrative.source + ")",
        payload={NARRATIVE_KEY: narrative.to_json()},
    )


def read_narrative(output_dir: Path | str) -> Optional[Dict[str, Any]]:
    """The run's narrative: the attached one, else rendered from the ledger now
    (so every FAILED run -- older ledgers included -- explains itself)."""
    ledger = BuildLedger(Path(output_dir) / "build_ledger.jsonl")
    try:
        if not ledger.exists():
            return None
        events = list(ledger.events())
    except Exception:  # noqa: BLE001 -- a torn ledger has no narrative
        return None
    for event in reversed(events):
        if event.kind is EventKind.NOTE and NARRATIVE_KEY in (event.payload or {}):
            got = (event.payload or {}).get(NARRATIVE_KEY)
            if isinstance(got, Mapping) and got.get("text"):
                return dict(got)
        if event.kind is EventKind.RUN_STARTED:
            break
    facts = ledger_facts(events)
    if not facts:
        return None
    return deterministic_narrative(facts).to_json()

