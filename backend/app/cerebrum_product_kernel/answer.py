"""Kernel-first answering with labelled out-of-kernel improvisation (F-1c).

The product's answer path is governed by one rule (0725Z): a query whose answer
a KERNEL owns is answered by the kernel — the number comes from the certified
definition / formula, never invented by the model. A query no kernel covers is
still answerable, but ONLY as a LABELLED improvisation: a visible "no kernel
covers X; improvised" banner plus cited sources, and one row appended to a
tenant-scoped new-domain-candidate log so the gap is a record, not a silent
guess. This mirrors the-shovel runtime.py's S-7 pattern (kernel lookup → found:
kernel answers; not found: labelled improvisation through a grounding gate + a
candidate-log row), ported into the product kernel so every generated platform
inherits it rather than hand-rolling a second one.

SINGLE EGRESS
-------------
There is no LLM call here and no new provider/SDK. The model phrases the prose
elsewhere (the host's single egress) and hands it in as ``answer_text``; the
cited spans and the kernel number come from the kernel / retrieved chunks, not
from the model. The grounding gate is :meth:`RetrievalEngine.labeled_answer`,
which refuses an improvisation that no retrieved excerpt supports.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from app.cerebrum_product_kernel.claim_labels import Citation, Claim, LabeledAnswer
from app.cerebrum_product_kernel.formulas import (
    TIER_BASE,
    definition_index,
    load_base_definitions,
    resolve_definitions,
)
from app.cerebrum_product_kernel.precedence import LayerObject, resolve_formula_by_id
from app.cerebrum_product_kernel.formulas.evaluator import (
    MissingInput,
    UnsafeExpression,
    extract_grounded_inputs,
    free_names,
    safe_eval,
)
from app.cerebrum_product_kernel.ingestion import retrieval_index
from app.cerebrum_product_kernel.ingestion.object_store import tenant_uploads_dir
from app.cerebrum_product_kernel.retrieval_engine import RetrievalEngine

logger = logging.getLogger(__name__)

#: The visible banner an out-of-kernel answer MUST carry. A caller renders it
#: verbatim; its presence is how the client can tell a kernel answer from an
#: improvised one at a glance.
IMPROVISED_LABEL = "no kernel covers {subject}; improvised"
#: The reason stamped on every new-domain-candidate log row.
CANDIDATE_REASON = "no_kernel_coverage"
#: Where the tenant-scoped candidate log lives (same digest dir as the uploads).
CANDIDATE_LOG_NAME = "new_domain_candidates.jsonl"

KIND_KERNEL = "kernel"
KIND_IMPROVISED = "improvised"

#: The refusal an UNKNOWN kernel answer carries when the grounded chunks do not
#: supply every input the definition needs (S-1 UNKNOWN pattern). It NAMES the
#: missing inputs and carries no number — the kernel will not invent one.
UNKNOWN_LABEL = (
    "unknown: {subject} needs grounded input(s) {inputs}, "
    "not in the retrieved excerpts"
)


@dataclass
class ProductAnswer:
    """One answered query, tagged with the path that answered it.

    ``kind`` is ``"kernel"`` (a certified definition/formula answered; the
    number is the kernel's, not the model's) or ``"improvised"`` (no kernel
    covered it; answered as a labelled, cited improvisation). ``improvised``
    is the boolean the UI gates its banner on, and ``label`` is the banner
    text — empty for a plain kernel answer, the ``IMPROVISED_LABEL`` for the
    improvised path, the ``UNKNOWN_LABEL`` when a kernel-covered query lacks a
    grounded input.

    ``kernel_value`` is the number the KERNEL computed from grounded inputs —
    the only number a kernel answer may state. ``missing_inputs`` names the
    grounded inputs that were absent (non-empty only on an UNKNOWN refusal).
    """

    query: str
    kind: str
    text: str
    improvised: bool
    label: str = ""
    kernel: Optional[Dict[str, Any]] = None
    labeled_answer: Optional[LabeledAnswer] = None
    citations: List[Citation] = field(default_factory=list)
    kernel_value: Optional[float] = None
    missing_inputs: List[str] = field(default_factory=list)
    unknown: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "kind": self.kind,
            "text": self.text,
            "improvised": self.improvised,
            "label": self.label,
            "kernel": self.kernel,
            "labeled_answer": (
                self.labeled_answer.to_dict() if self.labeled_answer else None
            ),
            "citations": [c.to_dict() for c in self.citations],
            "kernel_value": self.kernel_value,
            "missing_inputs": list(self.missing_inputs),
            "unknown": self.unknown,
        }


def _normalize(text: str) -> str:
    """Lower-case, keep word characters, collapse the rest to single spaces."""
    out = []
    for ch in str(text or "").lower():
        out.append(ch if (ch.isalnum() or ch == " ") else " ")
    return " ".join("".join(out).split())


def kernel_coverage(
    query: str,
    *,
    overlays: Iterable[Dict[str, Any]] = (),
    resolved: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Return the kernel definition row a query names, or ``None``.

    A query is kernel-covered when it names a resolved definition — by its id
    (``gross_margin_ratio`` / "gross margin ratio") or its human name. The row
    carries the definition's ``expression`` and tier, so the answer's NUMBER is
    the kernel's: mutate the definition and this row (and the answer) moves.
    Coverage is decided over the resolved set (base + any domain overlays), so
    a domain extension/override is covered too.
    """
    normalized = _normalize(query)
    if not normalized:
        return None
    index = definition_index(
        resolved if resolved is not None else resolve_definitions(overlays=overlays)
    )
    for row in index:
        ident = str(row.get("id") or "")
        candidates = {
            _normalize(ident),
            _normalize(ident.replace("_", " ")),
        }
        for cand in candidates:
            if cand and (cand == normalized or f" {cand} " in f" {normalized} "):
                return row
    return None


def _candidate_log_path(identity: str) -> Path:
    return tenant_uploads_dir(identity) / CANDIDATE_LOG_NAME


def log_new_domain_candidate(
    identity: str,
    query: str,
    *,
    sources: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Append ONE row to the tenant-scoped new-domain-candidate log.

    The log lives under the tenant's own uploads digest dir, so a candidate
    recorded for one tenant is unreachable from another's identity — the same
    isolation boundary the blobs and parse jobs use. Returns the row written.
    """
    row = {
        "query": str(query or ""),
        "reason": CANDIDATE_REASON,
        "logged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": list(sources or []),
    }
    path = _candidate_log_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    return row


def read_new_domain_candidates(identity: str) -> List[Dict[str, Any]]:
    """Every new-domain-candidate row for ``identity`` (``[]`` when none)."""
    path = _candidate_log_path(identity)
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def answer_query(
    identity: str,
    query: str,
    answer_text: str,
    *,
    engine: Optional[RetrievalEngine] = None,
    overlays: Iterable[Dict[str, Any]] = (),
    resolved: Optional[Dict[str, Any]] = None,
) -> ProductAnswer:
    """Answer ``query`` kernel-first, else as a labelled improvisation.

    Kernel-first: when :func:`kernel_coverage` finds a definition, the answer's
    NUMBER is the kernel's own — the definition's ``expression`` COMPUTED over
    inputs grounded from the tenant's retrieved chunks (:func:`_kernel_answer`),
    never a number the model's ``answer_text`` carried. When a grounded input is
    missing, the kernel REFUSES naming the missing inputs (the S-1 UNKNOWN
    pattern) rather than invent one. Otherwise the retrieval path grounds the
    answer against the attached chunks (:meth:`RetrievalEngine.labeled_answer`,
    which refuses an ungrounded claim), attaches the visible ``IMPROVISED_LABEL``
    + the chunks' citations, and appends one new-domain-candidate row.

    ``answer_text`` is the model's prose (single egress, supplied by the
    caller). On the kernel path it is DISCARDED: the kernel authors its own
    sentence around the computed number, so a wrong number in the prose can
    never reach the client.
    """
    engine = engine if engine is not None else retrieval_index.tenant_engine(identity)
    if engine is None:
        # No retrieval backend: a kernel number must be GROUNDED in retrieved
        # inputs, and an improvisation needs retrieval — either way we refuse
        # with the surfaced message rather than pass the model's prose through.
        raise RuntimeError(
            f"{retrieval_index.RETRIEVAL_BACKEND_NOT_CONFIGURED}: an answer "
            "needs retrieval (a kernel number is computed from grounded inputs; "
            "an out-of-kernel answer is a grounded improvisation)"
        )
    collection = retrieval_index.tenant_collection(identity)

    if resolved is None:
        resolved = resolve_definitions(overlays=overlays)
    covered = kernel_coverage(query, resolved=resolved)
    if covered is not None:
        return _kernel_answer(
            engine, collection, query, covered, resolved=resolved, identity=identity
        )

    labeled = engine.labeled_answer(collection, query, answer_text)
    label = IMPROVISED_LABEL.format(subject=query)
    log_new_domain_candidate(
        identity, query, sources=[c.to_dict() for c in labeled.citations]
    )
    return ProductAnswer(
        query=query,
        kind=KIND_IMPROVISED,
        text=answer_text,
        improvised=True,
        label=label,
        labeled_answer=labeled,
        citations=list(labeled.citations),
    )


def _format_kernel_number(value: float) -> str:
    """A stable string for the computed number (trims float noise, keeps it)."""
    rounded = round(float(value), 6)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.6f}".rstrip("0").rstrip(".")


def _kernel_answer(
    engine: RetrievalEngine,
    collection: str,
    query: str,
    covered: Dict[str, Any],
    resolved: Optional[Dict[str, Any]] = None,
    *,
    identity: str = "",
) -> ProductAnswer:
    """Compute the kernel's number from grounded inputs, or refuse (UNKNOWN).

    The definition's ``expression`` names its inputs; each input's value is read
    from the chunks retrieved for this tenant (grounded, with the input names
    folded into the retrieval query so the figures are found). The number in the
    answer IS :func:`safe_eval` of the expression — the model's prose never
    carries a number the kernel did not compute. A missing input → an UNKNOWN
    refusal naming it; a zero divisor / unsafe expression → the same honest
    refusal, never a fabricated figure.
    """
    expression = str(covered.get("expression") or "").strip()
    subject = str(covered.get("id") or query).replace("_", " ")
    citations: List[Citation] = []
    if not expression:
        # A covered definition with no expression (e.g. a convention) — we can
        # name it but will not invent arithmetic for it.
        return ProductAnswer(
            query=query,
            kind=KIND_KERNEL,
            text=f"{subject}: {covered.get('reason') or 'defined by the kernel'}.",
            improvised=False,
            kernel=covered,
            citations=citations,
        )

    required = free_names(expression)
    base_expression = _superseded_expression(covered)
    base_required = free_names(base_expression) if base_expression else []
    # Ground the inputs: retrieve with the query AND the input names so the
    # figures that feed the formula are in the retrieved excerpts. A retrieval
    # error is NOT swallowed — an egress failure here (EmbeddingEgressError)
    # must propagate so the route surfaces a 503, never a 200 "missing inputs"
    # that hides a broken backend behind a fabricated refusal.
    grounding_query = " ".join(
        [query, *required, *[n for n in base_required if n not in required]]
    ).strip()
    hits = engine.retrieve(collection, grounding_query)
    corpus = "\n\n".join(h.text for h in hits)
    citations = [
        Citation(file=str(h.file), location=dict(h.location or {}))
        for h in hits
        if h.file
    ]

    values, missing = extract_grounded_inputs(required, corpus)
    if missing:
        return ProductAnswer(
            query=query,
            kind=KIND_KERNEL,
            text=UNKNOWN_LABEL.format(subject=subject, inputs=", ".join(missing)),
            improvised=False,
            label=UNKNOWN_LABEL.format(subject=subject, inputs=", ".join(missing)),
            kernel=covered,
            citations=citations,
            missing_inputs=list(missing),
            unknown=True,
        )

    # K-F1: every declared guard is EVALUATED (safe_eval over the same grounded
    # inputs) BEFORE the expression. A guard that is false — or that cannot be
    # evaluated — is an UNKNOWN refusal that names the guard, so a zero divisor
    # is refused by the definition's own rule, not caught after the fact.
    # Guards are read from the RESOLVED definition (the set coverage was decided
    # over), not from the index row, so the index surface stays unwidened.
    resolved_set = resolved if resolved is not None else resolve_definitions()
    definition = resolved_set.get(str(covered.get("id") or "")) or {}
    for guard in [str(g).strip() for g in (definition.get("guards") or []) if str(g).strip()]:
        try:
            holds = bool(safe_eval(guard, values))
        except (MissingInput, UnsafeExpression, ZeroDivisionError, ArithmeticError):
            holds = False
        if not holds:
            refusal = f"unknown: {subject} not computed, guard failed: {guard}"
            return ProductAnswer(
                query=query,
                kind=KIND_KERNEL,
                text=refusal,
                improvised=False,
                label=refusal,
                kernel=covered,
                citations=citations,
                unknown=True,
            )

    try:
        computed = float(safe_eval(expression, values))
    except (MissingInput, UnsafeExpression, ZeroDivisionError, ArithmeticError) as exc:
        # Still a refusal, never a number the kernel could not compute.
        return ProductAnswer(
            query=query,
            kind=KIND_KERNEL,
            text=(
                f"unknown: {subject} could not be computed from the grounded "
                f"inputs ({type(exc).__name__})"
            ),
            improvised=False,
            label=f"unknown: {subject} could not be computed",
            kernel=covered,
            citations=citations,
            unknown=True,
        )

    number = _format_kernel_number(computed)
    grounded = ", ".join(f"{k}={_format_kernel_number(v)}" for k, v in values.items())
    text = f"{subject} = {number} (kernel-computed from {grounded})."
    labeled = _kernel_claims(
        covered, text, computed, identity, base_expression, base_required, corpus,
        citations,
    )
    return ProductAnswer(
        query=query,
        kind=KIND_KERNEL,
        text=text,
        improvised=False,
        kernel=covered,
        citations=citations,
        kernel_value=computed,
        labeled_answer=labeled,
    )


def _superseded_expression(covered: Dict[str, Any]) -> str:
    """The base expression an overlay override replaced ('' when none).

    Read from the base definition set by the superseded ADDRESS the resolver
    recorded, so the losing (certified) value can be computed for the
    divergence record."""
    supersedes = str(covered.get("supersedes") or "")
    if not supersedes:
        return ""
    base = load_base_definitions()
    for definition in base.get("definitions", []):
        address = "%s:%s_v%s" % (
            base.get("set_id"), definition.get("id"), definition.get("definition_version")
        )
        if address == supersedes:
            return str(definition.get("expression") or "")
    return ""


def _kernel_claims(
    covered: Dict[str, Any],
    text: str,
    computed: float,
    identity: str,
    base_expression: str,
    base_required: List[str],
    corpus: str,
    citations: List[Citation],
) -> LabeledAnswer:
    """Label the kernel's claim with its authority layer (K-F3).

    A base-tier definition is layer 1 ("Certified"). A tenant overlay (an
    extension or an override) is layer 3 ("Your formula"); when it overrides
    a base id, :func:`resolve_formula_by_id` decides in code and the claim
    carries the formula_shadow DivergenceRecord with BOTH values — the
    certified value is computed from the same grounded corpus, or ``None``
    when its inputs are not grounded (never invented)."""
    ident = str(covered.get("id") or "")
    address = str(covered.get("address") or ident)
    if covered.get("tier") == TIER_BASE:
        claim = Claim(text=text, layer=1, object_id=address)
        return LabeledAnswer(claims=[claim], citations=list(citations))
    tenant = identity
    if not covered.get("supersedes"):
        claim = Claim(text=text, layer=3, object_id=address)
        return LabeledAnswer(claims=[claim], citations=list(citations))
    base_value: Optional[float] = None
    if base_expression:
        # An ungrounded certified input raises MissingInput -> None (never invented).
        base_values, _ = extract_grounded_inputs(base_required, corpus)
        try:
            base_value = float(safe_eval(base_expression, base_values))
        except (MissingInput, UnsafeExpression, ZeroDivisionError, ArithmeticError):
            base_value = None
    verdict = resolve_formula_by_id(
        ident,
        [
            LayerObject(object_id=ident, layer=1, result=base_value),
            LayerObject(object_id=ident, layer=3, tenant_id=tenant, result=computed),
        ],
    )
    record = verdict.records[0] if verdict.records else None
    claim = Claim(text=text, layer=verdict.winner.layer, object_id=address, precedence=record)
    return LabeledAnswer(claims=[claim], citations=list(citations))
