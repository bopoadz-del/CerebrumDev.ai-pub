"""Client-facing ingestion surface (Phase 2 scaffold — PLACEHOLDER, no logic yet).

WHAT THIS PACKAGE IS
---------------------
The pitch for CerebrumDev.ai is two phases: Phase 1, the Factory assembles a
platform shell in ~20 minutes (COLLECTOR -> CLONER -> WRITER -> TESTER ->
STORE_MANAGER, all under backend/app/factory/); Phase 2, the CLIENT connects
their own Google Drive or uploads their own documents/formulas to their OWN
deployed platform, and the platform ingests that into ITS OWN retrieval store
and formula overlay — no Factory involvement, no code change.

Phase 1 exists and is being actively hardened (see the cleanup work merged
through master as of 2026-09-16, PRs #487-#494). Phase 2's REASONING side
already exists and is real, not a placeholder — read before writing anything
here:

  - ``cerebrum_product_kernel/retrieval_engine.py`` — RetrievalEngine.ingest()
    / .retrieve() / .labeled_answer(). Real. Mutation-tested (P4). Per-tenant
    Chroma collection. This is what a chunked document is handed TO.
  - ``cerebrum_product_kernel/precedence.py`` — the 4-layer authority ladder
    (1 certified < 2 documents < 3 formulas < 4 procedures), versioned JSON,
    logged divergence records. This is what a formula overlay is handed TO.
  - ``cerebrum_product_kernel/formulas/__init__.py`` — the extend/override
    overlay contract (id, definition_version, overrides, provenance, reason).
  - ``cerebrum_product_kernel/isolation.py`` — process memory isolation
    (fork() child address-space budget), NOT tenant data isolation. Do not
    confuse the two; tenant isolation here is the per-tenant Chroma
    collection in retrieval_engine.py.

WHAT IS MISSING, AND WHAT THIS PACKAGE SCAFFOLDS
--------------------------------------------------
Nothing in the shipped kernel turns "a client connects their Drive" or "a
client uploads a formula sheet" into a call against the engines above. That
missing glue is this package. Every module here is a STUB: real docstrings
and exact signatures, ``raise NotImplementedError`` bodies. No logic. Not
wired into any router or into app/main.py. Zero behaviour change while this
package exists — same discipline The_Fork uses for RAG_LAYERED (flag off =
byte-for-byte unchanged) and pilot-doc-control-plan.md ("Prepare-only now").

REFERENCE ARCHITECTURE — bopoadz-del/The_Fork, app/core/rag/
---------------------------------------------------------------
The Fork's RAG pipeline is the most mature, incident-tested version of this
shape in either codebase (layered RAG go-live, 2026-09; STEP 0 structural
isolation, retriever.py:964; source_class.py's project_corpus/knowledge_base/
template/master_corpus taxonomy, driven by two named Sev-1 incidents G1/A5).
Mirror its DISCIPLINE — named refusals, structural (not ranking-based)
isolation, an incident before every taxonomy decision, flag-gated rollout —
not necessarily its exact module boundaries, since CerebrumDev's kernel
already has a comparable (and in one respect stronger: divergence-logged
formula-shadow records) precedence engine of its own.

DOC-CURRENCY WARNING for whoever does the heavy lifting: The Fork's docs/
directory is NOT uniformly current. Trustworthy as of 2026-09-16:
``docs/layered-rag-golive.md`` (2 weeks old, describes what's live today),
``docs/rag-reranker.md`` (2 weeks old, a measured decision record),
``docs/routing_matrix.md`` (test-locked by test_routing_matrix.py, cannot
rot silently). STALE/DO NOT TREAT AS CURRENT SPEC: ``docs/rag-deployment-plan.md``
(says "do not build the layers during pilot" — they are now built, see
layered-rag-golive.md), ``docs/reasoning-consolidation-plan.md`` (header says
SUPERSEDED), ``docs/pilot-doc-control-plan.md`` (header says "AGREED,
DEFERRED... Not built" — a plan, not a report of shipped code). When in
doubt, read the code (app/core/rag/*.py), not the doc.

MODULES IN THIS PACKAGE
------------------------
- chunker.py         — document text -> the exact chunk dict shape
                        RetrievalEngine.ingest() requires.
- drive_connector.py — per-PRODUCT (not per-factory-session) Google Drive
                        OAuth connect + sync, mirroring factory_drive.py's
                        shape but scoped to a deployed platform's own tenant.
- formula_intake.py  — client submission of an L3/L4 overlay definition,
                        validated against the SAME contract
                        cerebrum_product_kernel/formulas already enforces.
- router.py           — FastAPI route stubs for the above. NOT included in
                        any app/main.py or generated-product entrypoint yet.
"""

from __future__ import annotations
