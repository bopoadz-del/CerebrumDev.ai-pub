# The Fork — Market-Readiness Audit

**Date (UTC):** 2026-07-23
**Target:** [`bopoadz-del/The_Fork`](https://github.com/bopoadz-del/The_Fork) (audited read-only at tip `df2c398`) · live at [the-fork.onrender.com](https://the-fork.onrender.com)
**Scope:** Concept/idea, functionality, usability, security, deployment, observability, data governance. **No billing dimension** — The Fork is not a paid service; "market ready" here means *safe and credible to put in front of enterprise pilot users and to expand beyond one supervised operator.*
**Method:** Three independent deep scans (concept & architecture; functional completeness & wiring; security, deployment & UX). All critical claims re-verified directly in source before publication.
**Companion report:** [`MARKET_READINESS_AUDIT.md`](MARKET_READINESS_AUDIT.md) (CerebrumDev.ai, 2026-07-23)
**Baselines compared:** The Fork's own `PILOT_READINESS.md` (2026-07-12, "substantially ready, verification-incomplete"), `CONSTRUCTION_LEDGER.md` (2026-07-13 action census), `DECISIONS.md`, `TODO.md`

---

## Verdict

### **NOT MARKET READY — blocked by governance and orchestration, not by capability.**

This is the opposite failure mode from vaporware. The engine is genuinely built: the repo's own action census counts **86 real / 14 honest-stub / 2 parked / 0 fake / 0 broken** out of 102 actions, and spot-checks confirm it — BOQ extraction is real pandas/openpyxl/pdfplumber, drawing QTO is real ezdxf/PyMuPDF, WBS generation runs a real CPM engine, exports produce real xlsx/docx/pdf. 2,431 backend tests run in coverage-gated CI. The internal documentation culture (pilot gate, decisions log, honest-stub ledger) is better than most funded startups.

What blocks market readiness is: **live API keys committed to the repo by explicit decision, client-confidential pilot data committed to the repo, an intent router that fails to dispatch the flagship tools on roughly half of feature prompts, an incomplete verification battery, and zero production error tracking.**

| Field | Value |
|-------|-------|
| **Verdict** | **NOT MARKET READY** — conditional; est. **3–5 focused weeks** to ready |
| **Confidence** | High — all critical claims re-verified in source |
| **Ready today for** | Exactly what it's doing: a single-operator, owner-supervised enterprise pilot |
| **Not ready for** | Unsupervised pilot users, a second client, public positioning, or any security/procurement review by the client's IT organisation |

The repo's own pilot gate says the same thing in its own words: *"substantially ready, verification-incomplete — not a green light"* (`PILOT_READINESS.md:6`). This audit confirms that self-assessment and adds two governance findings the internal docs don't treat as blockers.

---

## Direct answer: what does it need to be market ready?

Five things, in strict priority order:

1. **Rotate the two live DeepSeek API keys and purge them from git history.** They appear **in full** in `docs/SECURITY_TRIAGE.md` (§"PR #14 — known-leaked keys in git history"), with a documented decision that they are *"NOT to be rotated"* and that future scanners *"will be re-dismissed against this paragraph."* They also remain retrievable via `git log -p -- .env render.yaml`. Verified firsthand. Whatever the internal risk-acceptance rationale, **no enterprise client's security review will accept live credentials in a repo**, and the doc itself is a roadmap telling an attacker exactly where to look. Rotation is free; the current stance converts a one-time mistake into a standing policy. This is the single most serious finding in the audit.

2. **Remove committed client-confidential data and scrub history.** Verified in source:
   - `rag_backfill_batch_1..5.json` + `rag_backfill_indexable_candidates.json` (repo root) contain Diriyah Gate II Infrastructure Package 1 metadata: contract-numbered document names (DD-2023-118), priced-BOQ filenames, **live Google Drive file IDs**, and the operator's local `G:\My Drive\...` paths. These files are **not** in `.dockerignore`, so they also ship inside the published Docker image.
   - `golden_set_results.jsonl` / `feature_matrix_results*.jsonl` contain real client Q&A — document names, extracted engineering values, and full LLM answers about the client's project.
   - `review_pack/` (512 KB, text only) contains real DG2 acceptance outputs (e.g. total manhours figures).
   - To be precise about the blast radius: **no raw client documents are committed** — the exposure is metadata and derived content, not source files. But filenames, contract references, and live Drive IDs are themselves confidential, and `DATA_GOVERNANCE.md` — otherwise honest — is silent about all of it.
   - Fix: move eval fixtures and backfill manifests out of the repo (private bucket or gitignored `data/`), add the patterns to `.gitignore` + `.dockerignore`, rewrite history for the worst items, and add a "what we commit about client projects" section to `DATA_GOVERNANCE.md`.

3. **Fix intent routing — the product's front door.** The live feature matrix (`feature_matrix_results_main.jsonl`, 68 runs, verified counts) scores **28 PASS / 33 FAIL / 7 PARTIAL**, with 19 failures being `ROUTE_MISS` at `confidence: 0.0` (`below_routing_gate`): the orchestrator never dispatches the right tool and the request falls through to the generic assistant. The tools work when invoked directly — users just can't reliably reach them by asking. Related: the known tool-call-discipline decay under conversation history (models reproduce hallucinated WBS tables instead of calling `generate_wbs`; the documented workaround is "create a new project per deliverable," `TODO.md:49-63`). A construction platform whose BOQ/QTO/WBS tools fire on ~half of natural asks is, from the user's chair, a platform that works half the time.

4. **Complete the verification battery the pilot gate itself demands.** `PILOT_READINESS.md` parks T5/T6 explicitly: full feature matrix, golden set, and 100-question recall eval not executed end-to-end; retrieval precision issue where general-knowledge FIDIC notes crowd out project-specific clauses (partially addressed — golden set went 10/28 → 22/28 with the frozen knob config, `DECISIONS.md` 2026-07-13); RFP under-extraction (2 chunks per RFP); 25 MB+ uploads 502 (chunked upload unbuilt, `CONSTRUCTION_LEDGER.md`).

5. **Turn on production error tracking.** Sentry is fully wired (init + 5xx/unhandled capture in `app/infra/monitoring.py:95-120`, `app/main.py:392-403`) but no-ops because `SENTRY_DSN` is unset — a parked item awaiting the operator (`PILOT_READINESS.md`, T2). The live pilot currently has no error aggregation beyond timing logs. This is a five-minute fix blocked only on supplying the DSN.

Everything else below is real but secondary to those five.

---

## Executive scorecard

| Dimension | Grade | One-line summary |
|-----------|:-----:|------------------|
| **Concept / idea** | **A−** | Sharp, validated vertical (construction intelligence with cited, honest answers); real pilot client; genuine differentiation in the honest-error doctrine |
| **Functionality (engine)** | **B+** | Tools are real and tested; zero fake actions by census and by spot-check |
| **Functionality (orchestration)** | **D+** | ~41% feature-matrix pass rate; route-misses and hallucination-under-history undermine the real engine |
| **Usability** | **C+** | Clean chat workspace with citations, a11y attention, dark mode; but flagship features are chat-only, the one built panel (ScheduleBuilder) is unmounted, errors surface via `alert()`, and there is no Arabic/RTL for a Saudi client |
| **Security (engineering)** | **B+** | Real multi-user JWT auth (PBKDF2-240k), fail-fast boot, rate limiting, upload hardening, opt-in at-rest encryption |
| **Security (governance)** | **F** | Live keys committed by policy; client data committed; SECURITY.md is an unfilled template |
| **Deployment** | **A−** | Multi-stage non-root Docker, alembic-on-boot with refuse-on-failure, complete render.yaml, persistent disk, CI with coverage floor + diff-cover |
| **Observability** | **C−** | Structured JSON logs, request IDs, Prometheus — but error tracking disabled in prod |
| **Data governance** | **C** | Honest, code-matching policy doc — undermined by the committed pilot data it doesn't mention |

---

## 1. Concept & idea — a strong vertical with a real moat candidate

The Fork ingests a construction project's documents (RFP, BOD, drawings, BOQ, specs, P6/XER schedules, reports) and gives operators a chat surface with ~100 predefined construction actions: document-grounded Q&A with citations and confidence, BOQ extraction, drawing QTO (PDF vector + DXF + DWG via ODA), CPM-validated WBS generation, EVM/cost analysis, and a curated construction knowledge base (FIDIC, CESMM4, OSHA 1926, Saudi Building Code, EVM formulas) where every answer is a cited rule with provenance and a credibility tier.

**Why the concept holds up:**
- **A real enterprise pilot exists** (Diriyah Gate II infrastructure package) with real documents, real evals against the live deployment, and a real operator using it. This is market validation most pre-launch products don't have.
- **The honest-error doctrine is a genuine differentiator.** The "fabrication kill" (F1–F4) made as-built/claims/tender-bid/daily-report actions honest-error instead of inventing data (`CONSTRUCTION_LEDGER.md`); the param-resolution rule is "2+ matches → ask which, never silently pick one" (`DECISIONS.md` 2026-07-13). In a domain where a fabricated quantity becomes a contractual claim, "never fabricates" is a sellable property — if the routing layer lets users experience it.
- **The knowledge story is layered correctly:** shipped region-agnostic curated KB with credibility tiers (`app/knowledge/construction_kb.json`) + boot-seeded reference corpus (`docs/knowledge/`, FIDIC/OSHA/WBDG/SBC) + per-project client corpus in Postgres/pgvector with hybrid BM25+vector RRF retrieval (`app/core/rag/`).

**Concept risks:**
- **Credibility papers contradict each other.** README claims ~142k indexed chunks and BGE-384 embeddings with Ollama `glm-5.2:cloud`; the pilot gate says the canonical `chunks_v2` store is 53 docs / 10,502 chunks; the schema SQL still says `vector(256)` model2vec; the runtime docstring says DeepSeek; TODO.md chronicles Groq→Ollama churn including a **production LLM path through an ephemeral cloudflared tunnel on the operator's PC** ("the tunnel will die," `TODO.md:34-45`). Any technical evaluator reading the repo will notice. One page of truth, kept current, would fix this.
- The `chunks_v2` canonical store was created outside Alembic — the migration chain doesn't fully describe production.

---

## 2. Functionality — a real engine behind an unreliable front door

### 2.1 The engine is real (verified by spot-check)

| Tool | Verdict | Evidence |
|---|---|---|
| WBS generation | Real, deterministic | `app/containers/construction/schedule.py:1952-2037` — template scaffold + real CPM (ES/EF/LS/LF/float) via `app/lib/pm_computations.compute_cpm`; durations honestly labeled rule-of-thumb |
| BOQ extraction | Real | `app/blocks/boq_processor.py:158,205-267` — pandas/openpyxl + pdfplumber tables, unit reconciliation, column aliasing |
| Drawing QTO | Real | `app/blocks/drawing_qto.py` (1,778 lines) — ezdxf for DXF, PyMuPDF vector paths for PDF, ODA converter hook for DWG; raster/scanned PDFs unsupported (known) |
| Cost analysis | Real, low-confidence data | `app/blocks/historical_benchmark.py:168-178` — location/factor-adjusted rates, self-labeled `confidence: "low"` day-one fallback |
| Exports | Real | xlsx/docx/pdf schedule + EVM + cost exports verified live (`TODO.md:76`) |
| RAG | Real | Hybrid 50 semantic + 50 BM25, RRF fusion, project-scope hard filter (`app/core/rag/retriever.py`) |

Stubs are honestly labeled, never disguised: `jetson_dispatch` (`app/containers/construction/__init__.py:567-569`), Aconex connector flag-only (`app/routers/projects.py:639`), variation-clause extraction (`__init__.py:1100`), `historical_benchmark` record persistence (`historical_benchmark.py:186-190`), advanced PDF table/annotation extraction (`__init__.py:677-684`).

### 2.2 The front door fails ~half the time

- **Feature matrix (live, 68 runs): 28 PASS / 33 FAIL / 7 PARTIAL** — verified counts from `feature_matrix_results_main.jsonl`. Nineteen failures are `ROUTE_MISS` with `confidence: 0.0` / `below_routing_gate`: the orchestrator never routes the prompt to the tool that would have answered it.
- **Tool-call discipline decays with conversation history:** in a workspace with prior turns, models drift into prose and reproduce earlier hallucinated WBS tables (telltale: every fabricated activity has Float=0/Critical=Y) instead of calling `generate_wbs` (`TODO.md:49-63`). Candidate fixes are already listed in the repo (forced `tool_choice`, history sanitization, hallucination detector) — they need to be built and measured.
- **Attachment reasoning is weak:** the `[attached: X]` marker isn't parsed server-side, and RFPs under-extract to 2 chunks (`PILOT_READINESS.md` T4b/T6b).

This is the highest-leverage functional work in the repo: the difference between "the platform has 86 real actions" and "the user experiences 86 real actions" is the router.

### 2.3 Tests & CI — strong backend, absent frontend

- **2,431 test functions across 252 files**, real coverage of blocks, RAG, auth, routers. CI (`.github/workflows/test.yml`) runs a SQLite matrix **and** a Postgres/pgvector job that executes `alembic upgrade head` before the suite — so schema drift breaks CI. Coverage floor 25% (actual ~57%) plus a **blocking 50% diff-cover** on PRs. Four more workflows (lint, PR quality, dependency audit, docker publish).
- **Zero frontend tests.** No test runner configured for the React SPA — auth, streaming chat, and Drive flows are all unverified by CI.

---

## 3. Usability — good bones, unfinished body

**Works well:** login → projects → workspace journey behind `ProtectedRoute` with an `ErrorBoundary`; SSE streaming chat with sources/citations panel; empty state with suggestion chips and doc-count hint; genuine accessibility attention (`aria-live` conversation log, roles, labels); responsive CSS and dark/light theming.

**Gaps:**
- **Flagship features have no dedicated UI.** BOQ/QTO/WBS/cost are reachable only by typing into chat and hoping the router dispatches (§2.2). The one component that exposed them directly — `frontend/src/components/ScheduleBuilder.tsx`, 396 lines, complete, calling three working endpoints (`app/routers/exports.py:564,597,636`) — is **never mounted anywhere** (verified: zero external imports). Mounting it is likely days of work for a step-change in perceived capability.
- **Errors surface via browser `alert()`** in the main workspace (`ProjectWorkspace.tsx:1196-1313`) — jarring for an enterprise product.
- **A legacy 2,748-line vanilla-JS UI still ships** at `/static` (`app/main.py:455`) — dead weight and a confusion risk.
- **No i18n and no RTL** — all strings hardcoded English, for a product whose pilot client is a Saudi megaproject and whose own Docker image installs Arabic OCR (`Dockerfile:76`).
- **Upload response bug:** returns `url: /static/{filename}` but files are written to `DATA_DIR`, so the returned URL 404s (`app/routers/upload.py:84-85`).
- Native file-picker / camera / voice paths in the composer remain human-untested (`TODO.md:86-91`).

---

## 4. Security — excellent engineering, failing governance

**Engineering (strong, verified):**
- Multi-user JWT auth (HS256) + PBKDF2-HMAC-SHA256 at **240k iterations** with constant-time compare (`app/core/users.py:26,125-140`); bootstrap admin from env; no open self-registration on the public deploy.
- Fail-fast production boot: refuses to start without `SECRET_KEY`/`DATABASE_URL` (`app/main.py:97-121`); dev key hard-disabled outside dev/test (`app/core/auth.py:71-75`).
- Real per-caller rate limiting (JWT users, hashed API keys, IPs; in-memory or Redis) (`app/core/rate_limit.py`).
- Upload hardening: size caps, extension allowlist, path-traversal defense, UUID prefixing, opt-in Fernet encryption at rest (`app/routers/upload.py:16-82`).
- Enumerated CORS (not `*`) with env-supplied prod origins; unified error envelope that never leaks stack traces; SQLAlchemy parameterized queries throughout.

**Governance (the F grade):**
- **Committed live keys, by policy** — finding #1 above (`docs/SECURITY_TRIAGE.md`). The doc's stated purpose is to pre-dismiss future scanner findings. This must be reversed: rotate, purge history, and let the triage doc record *that* it was done.
- **Committed client pilot data** — finding #2 above.
- **`SECURITY.md` is the unfilled GitHub template** — placeholder version table ("5.1.x") and literal "Tell them where to go..." boilerplate. For an enterprise-facing repo this is a credibility wound out of proportion to the five minutes it takes to fix.
- **Tenancy is not enterprise-grade:** a legacy API key resolves to the singleton `SYSTEM_USER_ID` admin and sees everything (`app/dependencies.py:247-255`); `DATA_GOVERNANCE.md` concedes no row-level ownership beyond user-scoped projects; a second, memory-backed RBAC system (`app/infra/auth.py`) exists in parallel and is ephemeral. Fine for one pilot; a blocker for client #2.

---

## 5. Deployment — production-shaped

- Multi-stage Dockerfile (python slim builder → node 20 frontend build → slim runtime), **non-root `appuser`**, `HEALTHCHECK`, volume for `/app/data`, safety model copied out of the volume-shadowed path (`Dockerfile:104-121`).
- `entrypoint.sh:27-33` runs `alembic upgrade head` on Postgres and **refuses to start on migration failure** — exactly right.
- `render.yaml`: docker runtime, persistent disk, health check path, all secrets `sync: false`.
- Minor items: Docker `HEALTHCHECK --start-period=20s` is shorter than the worst-case cold boot (embedder warm-load "~8–40s" + ONNX model, `app/main.py:194-207`) — widen it; stale legacy build path references a `.pt` model that no longer exists (`render-build.sh:22`); tracked runtime artifacts in git (`data/cerebrum.db`, `data/rate_limits.db`, test captures) should be untracked; no database/disk backup verification beyond Render's daily snapshot claim.

---

## 6. Observability — wired but dark

Structured JSON logging in prod, `X-Request-ID` tracing, Prometheus `/metrics`, three health endpoints, per-block metrics (`app/infra/monitoring.py`, `app/routers/health.py`). And then: **Sentry wired but disabled** (finding #5). Secondary: ~463 `except Exception` blocks of which ~98 swallow silently — mostly deliberate fail-open fallbacks, but at that volume real failures can vanish; worth a targeted pass on the ingestion and QTO paths once Sentry is on.

---

## Prioritized gap list

| # | Sev | Gap | Evidence | Fix |
|---|:---:|-----|----------|-----|
| 1 | **Critical** | Two live DeepSeek keys committed in full, with a documented no-rotation policy | `docs/SECURITY_TRIAGE.md` §PR#14; `git log -p -- .env render.yaml` | Rotate both keys today; purge from history (filter-repo); update triage doc to record the rotation |
| 2 | **Critical** | Client-confidential pilot metadata committed (contract-numbered filenames, live Drive IDs, operator paths, real Q&A, acceptance outputs) — backfill JSONs also ship in the Docker image | `rag_backfill_batch_*.json`, `*_results.jsonl`, `review_pack/`; absent from `.dockerignore` | Move to private storage; gitignore + dockerignore the patterns; rewrite history for the worst items; extend `DATA_GOVERNANCE.md` |
| 3 | **High** | Intent router misses ~half of feature prompts (19× confidence-0.0 route-miss) | `feature_matrix_results_main.jsonl` (28P/33F/7PARTIAL, verified) | Lower/repair the routing gate, add forced `tool_choice` on detected deliverable intent, add history sanitization + hallucination detector (candidate fixes already listed in `TODO.md:56-63`); re-run the matrix as the acceptance test |
| 4 | **High** | Verification battery incomplete; known retrieval-precision and RFP-extraction holes | `PILOT_READINESS.md` T4b/T5/T6 | Run the full matrix + golden set + 100-q recall against the frozen config; fix RFP extractor; ship chunked upload for >25 MB |
| 5 | **High** | No production error tracking | `app/infra/monitoring.py:95-120` wired, `SENTRY_DSN` unset | Supply the DSN; verify a test event; then sweep the worst silent `except` blocks |
| 6 | **Med** | Flagship features chat-only; ScheduleBuilder built but never mounted; legacy `/static` UI ships | `ScheduleBuilder.tsx` (0 external imports); `app/main.py:455` | Mount ScheduleBuilder in the workspace; retire `/static`; replace `alert()` with in-UI toasts |
| 7 | **Med** | Legacy API key = global admin; parallel ephemeral RBAC system | `app/dependencies.py:247-255`; `app/infra/auth.py` | Scope or retire legacy keys; make one auth system canonical before client #2 |
| 8 | **Med** | SECURITY.md is unfilled boilerplate; README/pilot-doc/schema claims contradict each other | `SECURITY.md`; README vs `PILOT_READINESS.md` vs `the_fork_schema.sql` | Write a real disclosure policy; maintain one current "state of the system" page |
| 9 | **Med** | No Arabic/RTL for a Saudi client | `frontend/src` (no i18n lib) | Introduce i18n scaffolding; Arabic strings + RTL layout for the chat surface first |
| 10 | **Low** | Zero frontend tests | `frontend/` | Smoke tests for auth, chat streaming, upload |
| 11 | **Low** | Health-check start period < cold boot; stale render-build path; tracked runtime artifacts | `Dockerfile:121`; `render-build.sh:22`; `data/*.db` in git | Widen start-period; delete stale script; untrack artifacts |
| 12 | **Low** | Upload response returns a `/static/` URL that 404s | `app/routers/upload.py:84-85` | Return the real document route |

---

## Strengths inventory (keep these)

- Honest-error / no-fabrication doctrine, enforced in code and censused in a public ledger (0 fake, 0 broken of 102 actions)
- A real engine: deterministic CPM, real BOQ/QTO parsers, real exports, hybrid RAG with project-scope isolation
- 2,431 backend tests; CI that migrates a real Postgres and enforces coverage floor + blocking diff-cover
- Real multi-user auth done right (PBKDF2-240k, constant-time compare, fail-fast boot, no dev keys in prod)
- Production-shaped deployment: non-root multi-stage Docker, alembic-on-boot with refuse-on-failure, persistent disk, honest health probes
- Upload hardening incl. opt-in encryption at rest, with a governance doc that matches the code
- Exceptional internal engineering record: pilot gate, decisions log with evidence, session journals — an auditor's dream (which is also why findings #1 and #2 stand out so sharply)

---

## Path to market (no billing — "market ready" = safe for unsupervised pilot users and client #2)

1. **Days 1–2 — Governance stop-the-bleed:** rotate both DeepSeek keys; purge keys and the worst client-data files from history; extend `.gitignore`/`.dockerignore`; republish the Docker image; set `SENTRY_DSN`. *(Findings 1, 2, 5.)*
2. **Week 1 — Truth and trust:** real `SECURITY.md`; reconcile README ↔ pilot docs ↔ schema into one current state page; extend `DATA_GOVERNANCE.md` to cover repo/image contents. *(Finding 8.)*
3. **Weeks 2–3 — The front door:** routing-gate fix + forced tool_choice + history sanitization; re-run the feature matrix until it clears an agreed bar (e.g. ≥85% pass); complete the golden set + recall battery; fix RFP extraction; chunked upload. *(Findings 3, 4.)*
4. **Weeks 3–4 — Experience:** mount ScheduleBuilder; retire `/static`; toasts instead of alerts; fix the upload URL; frontend smoke tests. *(Findings 6, 10, 12.)*
5. **Before client #2:** tenancy hardening (retire the global-admin legacy key, one canonical auth system), Arabic/RTL, backup verification. *(Findings 7, 9, 11.)*

The distance is short precisely because the hard part — a real, honest construction-intelligence engine with a live pilot — already exists. What remains is making the repo worthy of the engine.

---

*Audited 2026-07-23 from a read-only clone at The_Fork tip `df2c398`. All file:line citations verified at audit time. This report deliberately does not reproduce secret values or Google Drive file identifiers.*
