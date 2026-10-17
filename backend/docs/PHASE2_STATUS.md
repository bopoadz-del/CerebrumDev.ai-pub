# Phase 2 — status and parked work (overnight handoff)

Written 2026-09-17. Owner: codewhale session.

## Merged
- **§0.1** — RAG gate fix: PR #503, squash-merged to master (2026-09-17 02:21Z), CI green,
  zero builds running at merge time. `rag_roundtrip_hit` can no longer be satisfied by an echo.
- **§0.2** — tenant isolation + persistence-contract migration: PR #504, squash-merged to
  master as `1dcdf001` (2026-09-17). Handlers are pure dispatch; the ROUTE persists via the
  tenant-scoped save(payload). CI green on the final push. Includes the platform-deterministic
  block hash (git-tracked files only, posix ordering, LF normalization) and the store pin
  a372e76 → a198eba6.

## Parked — two factory bugs, ruled by the .md (do NOT fix under Phase 2)

Reported 2026-09-17, both verified still standing in the current code:

1. **converge silent skip** — `converge_writer_emitters` returns `{"ok": False, "skipped": ...}`
   when the blueprint/plan is not the expected type; the WRITER swallows it, so
   `docs/build_provenance.json` is written intermittently (session-resumed builds carry a dict
   blueprint). Without it, `release_gate.py` fails at Dockerfile:20 — unbuildable image,
   Store gate 0/12 on an otherwise healthy platform. Fix direction (parked): coerce
   dict-shaped blueprint/plan in converge + refuse the skip loudly in the WRITER caller.
2. **stale N3 failure blocks reopen** — `handoff_awaiting_n3` walks the ledger from the start
   and returns False on any historical `N3_STORE_GATE_FAILED`, so a build reopened after one
   gate failure can never be ingested even with a fresh HANDOFF appended. Fix direction
   (parked): judge from the LAST handoff only.

**Ruling: PHASE2_PROMPT.md overrides.** Its RULES forbid touching
`n3_store_gate.py`'s HANDOFF_TO_N3 machinery (bug 2 lives there), and neither bug is in the
§1–§4 plan. Both stay unfixed; do not silently implement either under this phase.

## Parked — the whole Factory (owner ruling, 2026-09-17)

Factory work stops here. §1 PR #505 stays open (CI runs) but is NOT merged by the agent;
§2/§3/§4 and the estate mirror-drop follow-up are parked. Work continues on the Store
(Cerebrum-Blocks) per its `docs/survey/EXECUTION_PLAN.md` — "finish the store revamp".
Next factory session resumes from this file.

### Noted for later — COLLECTOR rung ladder + STORE_MANAGER trust tiers (owner design, unimplemented)

```
COLLECTOR: "<vertical> — no certified kit"
  ├─ rung 1: certified kit?        → hotel_management, private_estate_operations only
  ├─ rung 2: unregistered Store material? → e.g. agriculture_v2.py, 615 lines, FREE
  └─ rung 3: assemble from trustable sources → candidate kit
             ↓ hands WRITER: sources + kit standard (kernel_manifest.json)
STORE_MANAGER gate → registered as technically_verified
             ↓
                                  → domain_approved
```

Verified absent from the system (2026-09-17): the rung ladder is not implemented or documented;
the tier vocabulary `technically_verified` / `domain_approved` does not exist — the only tiers in
code are `platform` / `contributor_reviewed` (accepted, `compliance_gate.py`) and
`staging/community` (workbench ceiling). `app/blocks/agriculture_v2.py` exists unregistered
(free-floating material). Scope decision (document vs implement; store-side vs factory-side) is
open.

## Parked issue inventory — every issue parked across this session

### Ruled by the owner (do not fix under Phase 2)
1. **converge silent skip** — `converge_writer_emitters` returns `{"ok": False, "skipped": ...}`
   on a non-ProductBlueprint/ProductPlan ctx; the WRITER swallows it → `docs/build_provenance.json`
   written intermittently → `release_gate.py` fails at Dockerfile:20, Store gate 0/12 on a healthy
   platform. Fix direction: coerce dict-shaped blueprint/plan in converge + refuse the skip loudly.
2. **stale N3 failure blocks reopen** — `handoff_awaiting_n3` walks the ledger from the start and
   any historical `N3_STORE_GATE_FAILED` short-circuits a fresh HANDOFF. Fix direction: judge from
   the LAST handoff only. PHASE2_PROMPT.md RULES forbid touching n3_store_gate.py's HANDOFF_TO_N3
   machinery → parked by ruling, not fixed.

### Parked from the audit todo (owner's "Things I'd add" list, 2026-09-16)
3. **2b — per-capability readiness declaration** — a vertical on the ready list can silently
   substitute a generic block for a missing domain-relevant one (the vet REUSE case). The
   exclusion must catch "resolved to a block, but not a domain-relevant one", not just
   "resolved to something". Unbuilt.
4. **3d — automated acceptance suite** — store_acceptance.json regressions sit unmeasured; the
   acceptance run must become an automated gate, not a manual follow-up. Unbuilt.
5. **known_limitations.json emitter** — the generator leaks estate vertical / work_order / budget /
   staff boilerplate into non-estate products; the disclosure document must not lie. Fix whoever
   generates it (not listed in build_provenance.json → possibly not writer-attributed). Unfixed.
6. **5 — rebuild a ready vertical end-to-end** — full rebuild of a ready vertical (retail,
   construction) exercising every gate, to prove the closed loop. Not attempted.
7. **frontend mount evidence** — `ui_served_200` passes via the fallback vanilla-JS page even when
   the React app is dead; the acceptance gate needs real mount evidence, not 200-on-index. Unfixed.
   (The paired vacuous-RAG half is FIXED: §0.1 merged.)
8. **domain-pack extraction (REASONING_KERNEL.md)** — the mechanism aimed at fixing thin-domain
   products is still unbuilt; new verticals stay thin until the Store actually stocks
   domain content. Unbuilt.

### Parked with the factory pause (2026-09-17)
9. **§1 PR #505** — rebased, CI green expected; left open, not merged by the agent.
10. **§2 Drive connector / §3 Formula intake / §4 acceptance checks** — not started, per
    PHASE2_PROMPT.md (full specs in that file).
11. **Estate mirror-drop follow-up** — after the Store's real estate blocks (main `9b8d781f`),
    drop the five `vendor_blocks_mirror` copies + advance the store pin past `9b8d781f`;
    `test_generate_regenerate.py` flips to the store source by design.
12. **COLLECTOR rung ladder + STORE_MANAGER trust tiers** — unimplemented and undocumented
    (see the owner-design note above; scope decision open).

### Store-side remaining (active work — Cerebrum-Blocks `docs/survey/EXECUTION_PLAN.md`)
- Wave 1.7 parked batch: `safety_world_detector` wrap+register; `review.py` billing dep +
  purchase/usage verification; `discovery.py` vector routing; `sandbox.py` mandatory
  `SANDBOX_RUNNER_URL`; connector-infra (`mcp_adapter` cleanup + inbound webhook); finance CoA
  lifecycle + `finance_import` CSV/XLSX idempotency; hotel `opera_connector` real block +
  `hotel_v2.process()` product endpoint; core-infra/reasoning registrations
  (`project_reasoner` block.json + remaining unregistered).
- Deferred: Projectten enhancements (API-key scopes/IP/expiry, webhook persistence, shared
  rate-limit store). Waves 2–6 (clone candidates, governance pair, factory CI tooling,
  connectors, close-out) still queued.

## Store (Cerebrum-Blocks)
- Real estate blocks committed to Store main `9b8d781f` (estate_registry, estate_maintenance,
  evidence_verifier, portfolio_rollup, readiness_engine + registry manifests + 12-test suite;
  estate_maintenance manifest `due` corrected to optional).
- Factory follow-up (parked with the factory): drop the five estate copies from
  `vendor_blocks_mirror` and advance the store pin past `9b8d781f` —
  `test_generate_regenerate.py` flags the mirror-vs-store provenance flip by design.

## In flight (paused): §1 (client ingestion) — PR #505
- Chunker implemented (naive splitter, contract-exact chunk dicts, tenant refused by name),
  ingestion router mounted inventory-gated (`deploy.render_main(product_name, vertical)`),
  `_render_main` threads the vertical (was dropping it), 7 route tests green locally.
- Rebased onto master after #504; CI was running at park time. Do not merge unilaterally.

## Not started (parked)
- **§2** Drive connector, **§3** Formula intake, **§4** acceptance checks — per PHASE2_PROMPT.md.

## Housekeeping
- Local secrets: `CerebrumDev.ai/backend/.env` (gitignored) carries RENDER_API_KEY,
  CEREBRUM_DEV_API_KEY, CEREBRUM_API_URL/KEY, CEREBRUM_BUILDS_GITHUB_TOKEN.
- The deploy warning is real: auto-deploy on master; a merge kills in-flight builds.
