# Factory Governed-Layers Build Prompt — v1.2

**Target repo:** `bopoadz-del/CerebrumDev.ai` (the Factory platform that generates
governed, multi-tenant client platforms such as hotelops).
**Branch:** `feat/governed-layers` off `master`. **One PR per phase. NEVER merge.**

> **This prompt is a product artifact. It is versioned.** Any change to it is a
> product change: bump the version, log it in the changelog at the bottom, and
> re-run the determinism test (T5.6). Never edit in place without a bump.

## HEADER — read before anything else

- **Which writer builds what:** Phases 0.5–4 are built with the **current writer
  path** (the existing WRITER stage). **Phase 5 replaces the writer** with the
  CodeWhale worker. Do not read this doc as "build the engine with the broken
  writer, then fix the writer" — Phase 0.5 makes the current writer trustworthy
  enough to build 1–4, which is exactly why it comes first.
- **The artifact gate (Phase 0.5) must be green on the remote before any Phase 1
  work begins.** Phase 0.5's PR is **human-reviewed and verified by CI plus the
  standalone probe P0** — it is never self-certified by the writer it gates.
- **hotelops v1 and v2 disposition:** both have **no RLS** and **lexical-over-JSON
  retrieval**. They are **SINGLE-TENANT ONLY** until the kit engine (Phase 4)
  exists. Two tenants on either is a cross-tenant breach.

## ROLE

You are the coding agent for the Factory. You will build the 4-layer governed
product: **L1 certified kit / L2 client documents / L3 client formulas /
L4 client procedures.** Never mark your own work green — CI, TESTER and the probe
suite decide.

## NON-NEGOTIABLE RULES (every phase; violating any one = the phase is REJECTED)

- **R1.** Investigate before you build. Every phase starts by mapping the relevant
  code and reporting `file:line`. Do not assume paths.
- **R2.** No stubs, placeholders, or TODOs on critical paths (auth, tenancy,
  retrieval, formulas, LLM, export). If you cannot implement something, STOP and
  report a named blocker — never ship a stub that passes.
- **R3.** Every verdict/refusal is a **named reason string** (e.g.
  `tenant_isolation_violation`, `writer_no_output`), never a bare boolean or a
  silent pass.
- **R4.** Every change ships with a test that can FAIL independently of the code
  under test. For each gate you add, also add a **mutation probe**: deliberately
  disable the gate and assert the suite goes RED. A gate that cannot go red is
  not a gate.
- **R5.** Never weaken an existing gate to make a test pass.
- **R6.** Do NOT delete or disable cli-pivot, `run_cli_pivot`, `cursor_ba.py`, or
  `FACTORY_WRITER_REQUIRES_HANDOFF`. Do NOT change PR #458's post-Cloner hold or
  the TESTER-before-N3 ordering (`writer_control.py`; the `runner.py`
  GATE_PASSED / `next: tester` hunk).
- **R7.** Cloner and Collector are unchanged. Only the WRITER handoff target
  changes (Phase 5).
- **R8.** One tenant per request, always. No code path may assemble two tenants'
  data.
- **R9.** Report per phase: a table `change → file:line → test that proves it →
  mutation probe`, plus any hole you could NOT close and why. Then STOP and wait
  for review.

---

## PHASE 0 — DISCOVER (read-only; report before touching anything)

Map and report `file:line` for:

- **a) Tenancy** — how a tenant/project is identified per request; where the DB
  session is opened; every table holding corpus / documents / chunks / embeddings /
  formulas / procedures.
- **b) Retrieval** — the retrieval entrypoint, embedder, vector store, chunking,
  and how retrieved chunks are injected into the LLM prompt.
- **c) The answer envelope** — where the final answer + sources are assembled and
  emitted (API / SSE / export).
- **d) Formulas** — any existing deterministic formula/calculation executor.
- **e) The zip export** — what it packages and the manifest (if any).
- **f) The WRITER path** — `gates.py gate_writer_contract` (~:486);
  `roles_handlers.py run_writer` (:2779) and its templated write (~:3303, :3329);
  `cli_pivot.py run_writer_via_cli_pivot` (:178); `cli_receipt.py enforce_receipt`
  (~:306–335); `authorship.py below_floor` (~:584); `level_grade.py` (~:240);
  `n3_store_gate.py` (~:565).

Output: the map. **No code changes in Phase 0.**

---

## PHASE 0.5 — CLOSE THE ARTIFACT GATE (before any Phase 1 work)

Rationale: Phases 1–4 are code written by the writer. If the writer can produce
zero agent-authored artifacts and still pass, Phase 1's tests can pass on a
templated skeleton and the isolation layer is built on a foundation already
proven hollow. This gate closes first.

Build:

- **0.5.1** `gates.py gate_writer_contract`: agent-authored artifact count `== 0`
  → FAIL with reason `writer_no_output`. **Count agent-authored artifacts only** —
  templated handler writes (`roles_handlers.py` ~:3303 / :3329) are NOT agent
  artifacts. First investigate how the agent-authored count reaches the gate
  (grep `agent_written`, `authorship`, `cli_authored`, `GateContext`); thread it
  in minimally if absent. Do not weaken other gates.
- **0.5.2** Both WRITER entry points refuse on empty agent output:
  `roles_handlers.py run_writer` (:2779) **and** `cli_pivot.py
  run_writer_via_cli_pivot` (:178). (There is no `run_full` — these two are the
  real entry points.)
- **0.5.3** `cli_receipt.py enforce_receipt`: authored set empty AND required set
  empty → `RECEIPT_INVALID` / `writer_no_output`, **never** `HANDOFF_TO_N3`.
  Keep normal non-empty id-set-equality behaviour intact.
- **0.5.4** `authorship.py`: `below_floor = not (measured and meets_floor)` —
  unmeasured or zero authorship is **BELOW floor**. Remove the "unmeasured is not
  below floor" path. **Safety:** builds that are measured AND meet the floor must
  stay green — do not red real passing builds. If a green fixture relied on
  "unmeasured = pass" but represents real recorded agent work, FLAG it rather
  than silently flipping it.
- **0.5.5** `level_grade.py`: unmeasured/zero → `ready=False`.
- **0.5.6** Restore `mutation_probes.py` as a **standalone, network-free** script
  that drives the gate/verdict logic directly (not a mocked happy path). Probe
  **P0**: force zero agent artifacts and assert the ENTIRE gate chain (CODE,
  STORE, level grade) goes RED. It must run without the orchestrator's
  cooperation.

Acceptance tests:

- **T0.5.1** templated path, artifacts==0 → RED (no CODE PASS, no STORE PASS,
  reason `writer_no_output`).
- **T0.5.2** cli-pivot path, artifacts==0 → RED.
- **T0.5.3** empty blueprint set → RED, not `HANDOFF_TO_N3`.
- **T0.5.4** unmeasured authorship → `below_floor True` / `ready False`;
  measured-and-meets-floor → still green.
- **T0.5.5** the "0 artifacts" and "Store-green" UI states cannot co-render (a
  zero-artifact build can never grade STORE_GREEN).

Mutation probe: **P0** (above). Definition of done for 0.5: CI green on the
remote + P0 RED-when-forced, confirmed by a human reviewer.

---

## PHASE 1 — STRUCTURAL TENANT ISOLATION (before any second paying tenant)

The factory's real persistence stack is NOT Postgres. The generated-platform
runtime is a per-deployment SQLite database (the steward kit's SQLAlchemy
engine and the data_lifecycle-rendered Alembic env); the factory's own corpus
is JSON snapshots plus per-session Chroma collections; and the steward kit
already derives tenant/estate scope from authenticated principals, never from
caller-controlled defaults. Postgres/RLS is reserved for the day a shared
control-plane table genuinely needs it. It is not this phase.

Isolation model (option B): physical partition. One SQLite database file per
tenant (generated platforms) and one Chroma collection per tenant, with the
store handle resolved from the authenticated tenant identity at connection
time — never overridable by config, env, or an admin path. A tenant's
connection literally cannot address another tenant's file or collection;
that is the structural property the acceptance tests assert (impossible, not
merely empty).

Build:

- **1.1** One store-resolution seam: every corpus / document / chunk /
  embedding / formula / procedure access goes through a single
  ``resolve_tenant_store(principal)`` (or equivalent) that maps identity to
  the tenant's file and collection. The resolver's input is the
  authenticated principal (or an auth-layer-issued token) — it refuses a
  raw ``tenant_id`` string, because any reachable call site that forwards
  a string is a bypass. No call site may construct a path or name a
  collection from caller input.
- **1.2** SQLite-per-tenant (generated platforms): the database file is
  keyed by tenant; the connection is opened per request scope and closed;
  migrations run per-tenant database (the Alembic renderer ships WITH the
  code, never hand-applied). **Migrate-on-open is explicit:** a new
  tenant's file runs ``alembic upgrade head`` idempotently at first open;
  re-opening an already-migrated file is a no-op. Any existing single
  ``platform.db`` layout migrates once to the keyed layout — never silently
  shared.
- **1.3** Chroma-per-tenant: collection names are derived server-side from
  the identity (e.g. ``tenant_{digest}``); the client can never name a
  collection. Replace ``collection_name(session_id)`` and audit ALL of its
  call sites (chroma_store internals, ``data_rights.py:187``,
  ``upload_processor.py:444``) — none may be left silently on the old
  naming. State the vector store explicitly in the phase report. The
  isolation is structural because the handle is identity-derived, not
  caller-supplied — the collection name is never an input.
- **1.4** Keep existing app-level project/tenant filtering as
  defense-in-depth. It is never the only guard.
- **1.5** Caches and embedding lookups keyed by tenant. The prompt-assembly
  path takes exactly one tenant.
- **1.6** Admin/ops tooling — reads AND the write-side lifecycle — go
  through the same identity-derived handle or a separate audited service:
  backup, restore, and delete included (the ``data_lifecycle.py``
  backup/restore paths and the ``data_rights.py`` delete path). A restore
  that is not tenant-scoped can cross tenants; that is the breach shape.
  Never a god-path that opens "all tenants" from the app.
- **1.7** The store handle is bound FOR THE REQUEST from the authenticated
  identity; no code path may accept a client-supplied store or collection
  name. (Replaces the SET LOCAL / PgBouncer wording of v1.1: there is no
  shared connection pool to leak a session variable through — the handle
  itself is the boundary.)
- **1.8** Boot probe: refuse to start with a named reason if any code path
  can open a store by a client-supplied name — enforced by a probe over the
  resolution seam plus T1.4. Silent sharing is the zero-artifact bug in
  database form.

Acceptance tests:

- **T1.1** ``test_cross_tenant_open_is_impossible``: from tenant A's
  request context, attempt to open tenant B's store file and collection
  through every public path → refused with a named reason (e.g.
  ``tenant_store_not_addressable``), never an empty-but-green result.
  Cross-tenant read is impossible, not merely zero rows.
- **T1.2** ``test_cross_tenant_retrieval_never_leaks``: run the full
  retrieval entrypoint as tenant A against a query only tenant B's corpus
  can answer → no B chunk in the result, and the answer refuses with
  ``tenant_isolation_no_match`` (never B's content).
- **T1.3** ``test_unauthenticated_context_has_no_store``: with no
  authenticated tenant, every store access path refuses (no default store,
  no fallback file, no shared collection).
- **T1.4** ``test_boot_probe_rejects_client_named_stores``: boot with a
  patched seam that resolves a client-supplied name → refuses to start with
  a named reason.
- **T1.5** ``test_first_open_migrates_and_reopen_is_idempotent``: a new
  tenant's first open runs ``alembic upgrade head``; reopening the same
  file does not re-run or corrupt migrations.
- **T1.6** ``test_backup_restore_delete_are_tenant_scoped``: tenant A's
  backup/restore/delete operations never touch tenant B's file or
  collection, through every lifecycle entry point.

Mutation probe: **P1** ``probe_tenant_isolation`` — patch the resolution
seam to accept a raw tenant string (and a caller-supplied store name) and
assert T1.1 / T1.2 FAIL. If breaking the seam does not turn the suite red,
the phase is REJECTED.

## PHASE 2 — AUTHORITY PRECEDENCE AS DATA, LOGGED, NEVER MODEL-DECIDED

Build:

- **2.1** A precedence table/config: rank **4 procedures > 3 formulas >
  2 documents > 1 certified**. Versioned; loaded, not hardcoded in prompts.
- **2.2** Layers as first-class typed objects with `layer ∈ {1,2,3,4}` and
  `tenant_id` (certified = layer 1, tenant-agnostic; 2–4 always tenant-scoped).
- **2.3** Enforce precedence **in code** at retrieval/rerank: when objects from
  different layers answer the same ask, the higher-ranked layer's object is what
  gets injected or executed. The model is **told** the winner; it is never asked
  to choose.
- **2.4** Formulas (L3): client-taught formulas are versioned objects executed by
  the deterministic formula engine, shadowing the certified formula **by ID**.
  Resolution happens at lookup, in code.
- **2.5** Every answer carries a **divergence record**, not just a winner. When
  L3 (taught formula) shadows L1 (certified), **compute BOTH results** and log
  both:
  `precedence: {winner_layer, loser_layer, rule_id, winner_object_id,
  winner_result, loser_object_id, loser_result}`
  Rationale: the client must be able to answer "what would the certified formula
  have said?" — that is what makes the record useful rather than decorative.
  For **text-layer** conflicts (L2 vs L1) there is no computed value:
  `loser_result` is the losing excerpt/object, not a number. Emitted in the
  answer envelope and persisted with the answer record.

Acceptance tests:

- **T2.1** `test_taught_formula_outranks_certified`: same formula ID at L1 and
  L3 → the L3 value is used; log records `winner_layer=3, loser_layer=1`.
- **T2.2** `test_client_document_outranks_certified_text`: L2 and L1 chunks both
  match → L2 is injected first / used; log `winner_layer=2`.
- **T2.3** `test_precedence_is_not_model_decided`: with the LLM mocked to
  "prefer" the L1 value, the emitted answer still uses L3 and the log says so.
- **T2.4** `test_every_answer_has_precedence_log`: any answer emitted without the
  `precedence` field fails.
- **T2.5** `test_divergence_recorded` (formulas): L3 and L1 produce different
  values → the log carries BOTH results and names L3 as winner.

Mutation probe: **P2** `probe_precedence` — invert the rank table (1 > 4) and
assert T2.1 / T2.2 FAIL.

---

## PHASE 3 — PER-CLAIM LAYER LABELS (not per-source)

One answer synthesizes one claim from multiple sources. A source-level label
cannot tell the client which *sentence* came from layer 1 and which from
layer 3 — and that sentence-level audit is the one they are paying for.

Build:

- **3.1** Every **CLAIM** in the answer carries `layer: 1|2|3|4`, and every claim
  carries its own precedence entry (`winner_layer, loser_layer, rule_id,
  winner_object_id, loser_object_id`, plus results per 2.5).
- **3.2** **Port** The_Fork's grounding-gate claim→chunk mapping
  (`app/agents/citation_provenance.py`: `EvidenceRecord`, `Evidence`,
  `build_evidence`) **into the kit engine**. It lives in a different repo —
  port it, do not import across repos, and do not build a second mechanism.
- **3.3** The sources panel and the export record carry the **same per-claim
  labels** and precedence entries. Human labels: 1 "Certified" / 2 "Your
  documents" / 3 "Your formula" / 4 "Your procedure". No fifth class.
- **3.4** An unlabeled claim **fails closed** with reason `unlabeled_claim`.

Acceptance tests:

- **T3.1** `test_every_claim_carries_layer`: an answer with any claim missing
  `layer` fails closed with `unlabeled_claim`.
- **T3.2** `test_export_record_preserves_claim_labels_and_precedence`: the
  exported/persisted record round-trips per-claim labels + precedence intact.
- **T3.3** `test_blended_answer_has_distinct_claim_labels`: an answer that blends
  an L1 claim and an L3 claim carries **two distinct claim labels** and **two
  distinct precedence entries**.

Mutation probe: **P3** `probe_claim_labels` — strip `layer` from one claim
before emit and assert T3.1 goes RED.

---

## PHASE 4 — THE KIT ENGINE: REAL RAG SHIPS IN THE CERTIFIED KIT

Build:

- **4.1** The certified kit **contains the retrieval engine**: a real embedding
  model, a vector store (per-tenant Chroma collections per Phase 1),
  chunking, hybrid retrieve + inject, and the Phase 2/3 precedence + per-claim
  label machinery. Generated platforms **inherit** it; they never hand-roll
  retrieval.
- **4.2** Remove/replace any lexical-over-JSON "retrieval" in generated-platform
  templates (the hotelops v1/v2 pattern) with the engine. If a generated
  platform's docstring claims document-upload retrieval, it must be wired or the
  claim removed.
- **4.3** Grounding is honest: no answer states a fact absent from retrieved
  layers; absence is reported as "not in the retrieved excerpts", never as "the
  corpus doesn't contain it".

Acceptance tests:

- **T4.1** `test_engine_uses_embeddings_not_keywords`: a paraphrased query with
  zero lexical overlap still retrieves the semantically-matching chunk (fails on
  keyword-only retrieval).
- **T4.2** `test_generated_platform_inherits_engine`: a freshly generated
  platform's retrieval entrypoint is the kit engine, not a local implementation.
- **T4.3** `test_no_fabrication_on_absent_fact`: a fact absent from all layers is
  refused with a named reason, never invented.

Mutation probe: **P4** `probe_engine_is_real` — swap the embedder for a keyword
matcher and assert T4.1 FAILS.

---

## PHASE 5 — THE WRITER: CODEWHALE WORKER + VERSIONED PROMPT + CONCURRENCY

(The artifact gate that used to live here is now Phase 0.5 and must already be
green.)

Build:

- **5.1** A Render background worker runs the CodeWhale (DeepSeek) coding agent
  headless via `codewhale exec` from a job queue. This **replaces the cli-pivot
  as the dispatch target**; cli-pivot code stays in the repo, undispatched (R6).
  **Verify first:** `codewhale exec` runs non-interactively on the Render base
  image (no approval-prompt hang; glibc compatible). If it hangs or fails to
  install, STOP and report — do not fake a pass.
- **5.2** Pipeline: Cloner → queue → CodeWhale worker → branch → TESTER → CI.
  Cloner/Collector untouched (R7). Keep #458's post-Cloner hold and
  TESTER-before-N3 (R6).
- **5.3** The prompt template is the product: store it versioned (this file;
  changelog below), filled per platform from the brief.
- **5.4** Multi-tenant concurrency: each job gets its own checkout + branch; the
  worker runs under the **tenant-scoped store handle** (Phase 1 isolation applies to
  the builder too); an explicit concurrency cap; per-run cost recorded
  (tokens → COGS).

Acceptance tests:

- **T5.1** `test_codewhale_exec_headless`: the worker completes a trivial job on
  the Render image without an interactive prompt.
- **T5.2** `test_worker_runs_under_tenant_role`: a worker job cannot read another
  tenant's corpus (reuses T1.1 against the worker's session).
- **T5.3** `test_concurrency_cap_enforced`: job N+1 beyond the cap is queued or
  refused with a named reason, never run.
- **T5.6** `test_prompt_template_is_deterministic`: the same filled template on
  the same brief yields the same class of output.

Mutation probe: **P5** `probe_worker_isolation` — run the worker with the
tenant session var unset and assert T5.2 goes RED.

---

## PHASE 6 — THE HONEST ZIP

Build:

- **6.1** A `MANIFEST.json` in every zip declaring: engine version; retrieval
  mode (`vector_rag` | `keyword_lexical`); embedder + vector store; which layers
  are populated (1–4) with object counts; `tenant_id`; prompt template version;
  the CI run id that greened it; whether the kit engine is included; and the
  **tenancy mode** (`multi_tenant_rls` | `single_tenant_only`).
- **6.2** The UI/export label is **derived from the manifest**. It is impossible
  to display "RAG included" unless the manifest says `vector_rag` AND the engine
  is present. It is impossible to display multi-tenant unless tenancy mode is
  `multi_tenant_rls`.
- **6.3** A zip is produced ONLY after CI is green AND the artifact gate
  (Phase 0.5) passed.
- **6.4** **hotelops v1 and v2** ship with tenancy mode `single_tenant_only`
  and retrieval mode `keyword_lexical` until Phase 4 lands. Two tenants on either
  is a cross-tenant breach; the manifest must say so.

Acceptance tests:

- **T6.1** `test_manifest_matches_contents`: manifest claims are verified against
  the zip's actual files (an engine claim with no engine files → fail
  `manifest_mismatch`).
- **T6.2** `test_ui_cannot_overclaim`: with `retrieval_mode=keyword_lexical` the
  UI never renders "RAG included"; with `single_tenant_only` it never renders
  multi-tenant.
- **T6.3** `test_no_zip_without_green_ci_and_artifact_gate`.

Mutation probe: **P6** `probe_manifest_honesty` — forge `vector_rag` in the
manifest with no engine present and assert T6.1 goes RED.

---

## DEFINITION OF DONE (per phase, then overall)

- All that phase's acceptance tests pass in **CI on the remote**; all its mutation
  probes are RED-when-disabled (proven, not asserted).
- The repo's existing test suite is not weakened; any test you change, you
  explain.
- One PR per phase, not merged, with the R9 report table.
- Overall: the standalone `mutation_probes.py` suite runs every probe P0–P6 and
  can fail without the orchestrator's cooperation. If any probe cannot be made to
  go red, the product is not governed — say so plainly.
- A "shipped"/"green" claim is only valid against the **remote's CI**. Local
  subsets do not count (a local-green / CI-red self-report is itself a defect).

---

## CHANGELOG

### v1.2 — 2026-09-15
- **Review delta (2026-09-15):** 1.1 resolver input is the authenticated
  principal (refuses a raw tenant string); 1.2 migrate-on-open explicit
  (T1.5 first-open-migrates / re-open-idempotent); 1.3 pins the census
  (replace ``collection_name(session_id)``, audit all 9 call sites); 1.6
  extended to the write-side lifecycle (backup/restore/delete through the
  resolver, T1.6 tenant-scoped lifecycle); P1 now patches a raw-string
  seam. Per-request open/close confirmed (no engine cache to bound).
- **Phase 0.5 merged** (PR #463, master `5dc2600`): the artifact gate is live;
  this cut is the Phase 1 rescope it unblocks.

- **PHASE 1 rewritten to the real stack (option B — physical partition):**
  per-tenant SQLite file + per-tenant Chroma collection, the store handle
  resolved from the authenticated tenant identity at connection time, never
  overridable by config or admin. Postgres/RLS explicitly reserved for a
  future shared control-plane table. 1.7/1.8 rewritten (handle bound for the
  request; boot probe against client-named stores). T1.1/T1.4 rewritten
  (cross-tenant open impossible, not merely empty; boot probe). Phases 4/5
  cross-references updated. hotelops v1/v2 disposition unchanged.
- **Note:** T5.6 (prompt determinism) is still pending Phase 5; this cut
  carries the Phase 1 rescope only.

### v1.1 — 2026-09-14
- **RESEQUENCE:** artifact gate moved Phase 5 → new **Phase 0.5**; must be green
  on the remote before any Phase 1 work. Rationale: Phases 1–4 are
  writer-produced code; a zero-artifact-passing writer makes Phase 1 tests
  passable on a templated skeleton.
- **HEADER added:** Phases 0.5–4 built with the current writer path; Phase 5
  replaces the writer. Phase 0.5's PR is human-reviewed + CI/P0-verified, never
  writer-self-certified. hotelops v1/v2 are SINGLE-TENANT ONLY until Phase 4.
- **0.5:** names the two real WRITER entry points — `run_writer`
  (`roles_handlers.py:2779`) and `run_writer_via_cli_pivot` (`cli_pivot.py:178`).
  (`run_full` does not exist in the pipeline.)
- **1.3:** state the vector store; external store ⇒ vector-surface isolation is
  app-level and must be reported as such; pgvector ⇒ embeddings under RLS,
  covered by T1.1.
- **1.7:** RLS session var `SET LOCAL` per transaction; verify PgBouncer pooling
  mode on Render and report it.
- **1.8 + T1.4:** app role no-owner, no-`BYPASSRLS`; boot refuses with a named
  reason.
- **2.5 + T2.5:** divergence record carries `winner_result` AND `loser_result`
  (computed for formulas; losing excerpt for text layers).
- **3.1–3.4:** labels are **per-claim**, not per-source; per-claim precedence
  entries; **port** The_Fork `citation_provenance` (`EvidenceRecord` / `Evidence`
  / `build_evidence`) into the kit engine — do not import cross-repo, do not
  build a second mechanism; `unlabeled_claim` replaces `unlabeled_source`;
  T3.1/T3.2 updated; T3.3 added (blended L1+L3 answer).
- **5:** artifact-gate items removed (now 0.5); adds T5.2/T5.3/P5 for
  tenant-scoped worker + concurrency cap.
- **6.1/6.2/6.4:** manifest carries tenancy mode; UI cannot render multi-tenant
  without `multi_tenant_rls`; v1/v2 disposition recorded in the manifest.
- **DoD:** a green claim is only valid against the remote's CI.

### v1 — 2026-09-14
- Initial six-phase prompt (Discover, Isolation, Precedence, Labels, Engine,
  Writer+artifact gate, Zip).
