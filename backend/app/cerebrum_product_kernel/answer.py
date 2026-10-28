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

from app.cerebrum_product_kernel.claim_labels import Citation, LabeledAnswer
from app.cerebrum_product_kernel.formulas import definition_index, resolve_definitions
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


@dataclass
class ProductAnswer:
    """One answered query, tagged with the path that answered it.

    ``kind`` is ``"kernel"`` (a certified definition/formula answered; the
    number is the kernel's, not the model's) or ``"improvised"`` (no kernel
    covered it; answered as a labelled, cited improvisation). ``improvised``
    is the boolean the UI gates its banner on, and ``label`` is the banner
    text — empty for a kernel answer, the ``IMPROVISED_LABEL`` for the other.
    """

    query: str
    kind: str
    text: str
    improvised: bool
    label: str = ""
    kernel: Optional[Dict[str, Any]] = None
    labeled_answer: Optional[LabeledAnswer] = None
    citations: List[Citation] = field(default_factory=list)

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

    Kernel-first: when :func:`kernel_coverage` finds a definition, the answer is
    the kernel's — its expression/number, tier-labelled, with NO improvisation
    banner. Otherwise the retrieval path grounds the answer against the tenant's
    attached chunks (:meth:`RetrievalEngine.labeled_answer`, which refuses an
    ungrounded claim), attaches the visible ``IMPROVISED_LABEL`` + the chunks'
    citations, and appends one new-domain-candidate row. ``answer_text`` is the
    model's prose (single egress, supplied by the caller); the kernel number and
    the cited spans are not model-invented.
    """
    covered = kernel_coverage(query, overlays=overlays, resolved=resolved)
    if covered is not None:
        return ProductAnswer(
            query=query,
            kind=KIND_KERNEL,
            text=answer_text,
            improvised=False,
            label="",
            kernel=covered,
            citations=[],
        )

    engine = engine if engine is not None else retrieval_index.tenant_engine(identity)
    if engine is None:
        raise RuntimeError(
            "no retrieval engine configured: an out-of-kernel query can only be "
            "answered as a grounded improvisation, which needs retrieval"
        )
    collection = retrieval_index.tenant_collection(identity)
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
