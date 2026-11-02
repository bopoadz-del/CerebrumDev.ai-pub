"""F-10c-b: seed a generated product's OWN retrieval corpus from the session's
uploaded documents, so the built platform can retrieve the owner's docs.

The session's "+" uploads are already parsed into chunks (core.upload_processor
-> state.chunks / state.source_files). This re-uses those chunks through the
kernel ingestion path (``retrieval_index.ingest_parsed_chunks`` -> the tenant's
RetrievalEngine collection) -- NO second parser. It is append-only: a later
upload adds to the corpus, it never rebuilds it (resume-never-restart).

This module only builds the ingest rows and hands them over; the handoff hook
that calls ``seed_product_corpus_from_session`` at generate/handoff is wired
separately so this stays unit-testable without the build pipeline.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def session_chunk_rows(state: Any) -> List[Dict[str, Any]]:
    """Ingest-shaped chunk dicts from the session's uploaded documents.

    Each row keeps ``file`` + ``location`` so the product can cite it as
    ``document:<filename>``; ``source_class`` marks it project corpus. Returns
    [] when nothing was uploaded, so a build with no uploads seeds nothing.
    """
    chunks = list(getattr(state, "chunks", None) or [])
    names = list(getattr(state, "source_files", None) or [])
    rows: List[Dict[str, Any]] = []
    for i, text in enumerate(chunks):
        fname = names[i] if i < len(names) else "uploaded"
        rows.append(
            {
                "id": f"upload:{i:04d}",
                "text": text,
                "file": fname,
                "location": f"chunk {i + 1}",
                "layer": 2,
                "source_class": "project_corpus",
            }
        )
    return rows


def seed_product_corpus_from_session(
    state: Any,
    identity: str,
    *,
    ingest: Optional[Callable[[str, List[Dict[str, Any]]], Any]] = None,
) -> Any:
    """Seed tenant ``identity``'s product corpus from the session's uploaded
    chunks via the kernel ingestion path. No-op (returns None) when nothing was
    uploaded. Append-only; never rebuilds. ``ingest`` is injectable for tests;
    it defaults to ``retrieval_index.ingest_parsed_chunks``.
    """
    rows = session_chunk_rows(state)
    if not rows:
        return None
    if ingest is None:
        from app.cerebrum_product_kernel.ingestion import retrieval_index

        ingest = retrieval_index.ingest_parsed_chunks
    return ingest(identity, rows)


def record_corpus_finding(state: Any, reason: str) -> str:
    """Record a VISIBLE finding that the uploaded documents did NOT reach the
    product corpus, and return its text. The user is always told: it is set on
    ``state.upload`` (the surface the Floor renders) and appended as a NOTE to
    the handoff build ledger. Never a silent log line.
    """
    finding = f"documents were not indexed into the product corpus: {reason}"
    up = state.upload
    up.message = finding
    up.failed_files = list(up.failed_files or []) + [finding]
    out = (getattr(getattr(state, "product_design", None), "generation", None) or {}).get("output_dir")
    if out:
        from app.factory.build.ledger import BuildLedger, EventKind

        BuildLedger(Path(out) / "build_ledger.jsonl").append(
            EventKind.NOTE, detail=finding, payload={"corpus_seed_finding": reason}
        )
    return finding


def seed_uploaded_corpus(
    state: Any,
    *,
    seed: Optional[Callable[..., Any]] = None,
) -> Any:
    """Generate/handoff hook (F-10c-b): seed the owner-tenant product corpus
    from the session's uploads (tenant = session owner).

    The build always CONTINUES, but the user is ALWAYS told. A missing owner
    tenant, a seed error, or an ingest ``not_indexed`` reason is recorded as a
    VISIBLE finding (``record_corpus_finding`` -> Floor + handoff ledger) and
    returned -- never a silent ``None``. No-op (returns ``None``) only when
    nothing was uploaded, so a no-upload build is unchanged. ``seed`` is
    injectable for tests.
    """
    rows = session_chunk_rows(state)
    if not rows:
        return None  # nothing uploaded -> nothing to seed, nothing to tell
    identity = str(getattr(state, "user_id", "") or "")
    if not identity:
        return record_corpus_finding(state, "no owner tenant was resolved for the session")
    seed = seed or seed_product_corpus_from_session
    try:
        outcome = seed(state, identity)
    except Exception as exc:  # recorded as a finding, NOT swallowed
        return record_corpus_finding(state, f"{type(exc).__name__}: {exc}")
    if getattr(outcome, "state", "") == "not_indexed":
        return record_corpus_finding(state, str(getattr(outcome, "detail", "retrieval backend unavailable")))
    return outcome
