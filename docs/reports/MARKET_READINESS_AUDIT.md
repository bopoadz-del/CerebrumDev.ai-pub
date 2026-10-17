# CerebrumDev.ai — Market-Readiness Audit

**Date (UTC):** 2026-07-23
**Scope:** Full repository — concept/idea, functionality, usability, security, deployment, billing, observability
**Method:** Three independent deep scans (concept & architecture; functional completeness & wiring; security, deployment & UX), with all headline claims independently re-verified against the source before publication. Every finding cites file:line evidence.
**Baseline compared:** [`PILOT_READINESS_AUDIT.md`](PILOT_READINESS_AUDIT.md) (2026-07-21, "PILOT READY 86%" for the generated Steward product)

---

## Verdict

### **NOT MARKET READY — but much closer than a typical pre-launch repo, and for different reasons than believed.**

The stated expectation was: *"the only thing that should be off is the billing."*

**That expectation is inverted.** Billing is the *most* finished subsystem in the repository — a production-grade, tested Stripe integration that is merely **unconfigured**. The actual launch blockers are elsewhere: a largely **orphaned frontend** (users cannot reach password reset, upload, training, deploy, or the plans page), **factory output that is a scaffold rather than a turnkey product**, and **zero production observability**.

| Field | Value |
|-------|-------|
| **Verdict** | **NOT MARKET READY** (est. 2–4 focused weeks from ready, given the strong backend) |
| **Confidence** | High — all headline claims re-verified in source |
| **Ready today for** | Owner-supervised pilots, API-first demos, design-partner engagements |
| **Not ready for** | Self-serve signups, charging money, unattended production traffic |

---

## Direct answer: "Is billing the only thing off?"

**No. Billing is essentially done; five other things are actually off.**

| Area | Expected state | Actual state |
|------|----------------|--------------|
| **Billing** | ❌ Not ready | ✅ **Built and tested.** Full Stripe checkout / portal / signature-verified webhook / subscription-state mapping / trial + entitlement gates. Only missing: `STRIPE_*` env keys, `BILLING_ENFORCEMENT=on`, and frontend wiring (see §Billing) |
| **Frontend completeness** | ✅ Ready | ❌ ~20 built components are **dead code** — never mounted. No reachable password reset, email verification, upload, training, deploy, or plans UI |
| **Factory output quality** | ✅ Ready | ⚠️ Generated products ship a **stub UI** and `not_implemented` connectors — a scaffold, not a turnkey platform |
| **Observability** | ✅ Ready | ❌ No error tracking, no metrics, no alerting. Production exceptions would be invisible |
| **Scale-out safety** | ✅ Ready | ⚠️ Rate limiting and OAuth state are in-memory (single instance only); accounts DB has no migration system; sessions are JSON files on disk |
| **Security & deployment** | ✅ Ready | ✅ Genuinely strong (see §Security) |
| **Concept & tests** | ✅ Ready | ✅ Coherent, documented, 611 real tests in CI |

---

## Executive scorecard

| Dimension | Grade | One-line summary |
|-----------|:-----:|------------------|
| **Concept / idea** | **A−** | Clear, differentiated, provable ("factory that generates AI platforms"), with a live generated product (Steward) as evidence; heavy reliance on one external service and default-off flags dilute the demo |
| **Functionality (backend)** | **B+** | Coherent, extensively tested, honest about stubs; core paths real |
| **Functionality (frontend)** | **D** | Shipped app is 4 views; the rest of the built UI is orphaned dead code |
| **Usability** | **C** | The live path (chat → blueprint → approve → download) is clean, but users can't recover passwords or reach most features; billing UI contradicts working backend |
| **Security** | **A−** | PBKDF2-200k, hashed tokens, per-account isolation with non-leaking 404s, zero committed secrets, verified webhooks, no shell injection |
| **Deployment** | **B+** | Complete `render.yaml` + Docker + health/ready probes + CI with prod image build; ephemeral-FS and single-instance caveats documented but real |
| **Billing** | **A− (built) / F (activated)** | Production-grade code, inert until configured and contradicted by frontend copy |
| **Observability** | **F** | No Sentry/equivalent, no metrics, 25 files still using `print()` |

---

## 1. Concept & idea — Sound, differentiated, and evidenced

**What it is:** "The Factory" — a domain expert describes a platform in chat, the architect drafts a `product_blueprint.v1`, the generator ships a full platform (backend + UI + DB + Docker + CI), optionally fine-tuned (Tinker LoRA) and deployed (Render). Positioning: collapse a 6–12-month custom-AI build into days (`README.md:1-26`).

**Why the concept holds up:**
- **It has shipped proof.** A real generated product (Projectsix) is live, certified 10/10 on a live oracle, with provenance docs (`docs/reports/PILOT_READINESS_AUDIT.md`, `docs/provenance/`). Very few pre-launch repos can point at a working instance of their own output.
- **The commercial model is ratified and coherent** — Free/Pro/Team, "Free understands the domain, paid understands your organisation," with an honest launch trial design (3 docs / 7-day index / visible countdown) (`docs/COMMERCIAL_TIERS.md`).
- **The moat logic is written down:** Universal Kernel (~80% shared) + Domain Pack (~20%), One-Hour Standard, dual-registry governance (`docs/UNIVERSAL_KERNEL.md`, `docs/PRODUCT_DELIVERY_STANDARD.md`).
- **Honest-stub culture** — placeholders self-identify (`docs/factory/HONEST_STUBS.md`); this is rare and valuable engineering hygiene.

**Concept risks:**
- **External single point of failure:** the 18 domain kits live in the separate Cerebrum-Blocks service; without it `/v1/domains/*` returns 503 (`AGENTS.md:16`). Only one kit is vendored locally. The headline "18 domain kits" claim depends on an external free-tier service.
- **Demo quality is gated on config:** without an LLM key, a free-text brief yields a near-empty blueprint (single `health_surface` capability) (`backend/app/factory/product_architect.py:50-57`). A cold evaluator cloning the repo sees a much weaker product than the docs promise.
- **Two positioning narratives coexist** (old 5-phase configurator vs. new Factory Floor), and the README still leads with the old one. Not fatal, but the story a visitor reads is not the product the app ships.

---

## 2. Functionality — Strong backend, hollowed-out frontend

### 2.1 The biggest finding: the orphaned frontend

`frontend/src/main.tsx:3-8` renders **only** `App.tsx` — a self-contained 475-line console with 4 views (Factory Floor, Platforms, Subscription, Account) plus an inline `AuthGate`.

Meanwhile, an entire second UI exists under `frontend/src/components/` and is **never imported by anything reachable** (verified by grep — `ConfigCanvas`, `AppShell`, `PlansPage`, `LoginPage` etc. are referenced only within the orphaned tree itself):

- `auth/` — `LoginPage`, `RegisterPage`, **`ForgotPasswordPage`, `ResetPasswordPage`, `VerifyEmailPage`**
- `billing/` — `PlansPage`, `BillingPage`, `TrialBanner`
- `DataUploader`, `TrainingPanel`, `DeployPanel`, `ChatChainGenerator`, `DesignProductPanel`, `AIConfigPanel`, `DomainSelector`, `WorkbenchPanel`, `canvas/ConfigCanvas`, `layout/AppShell`, `chat/ChatSidebar`

**User-facing consequences:**
- **A user who forgets their password is stuck.** `/v1/auth/forgot-password`, `/reset-password`, `/verify-email` all exist server-side (`backend/app/routers/accounts.py:125-165`) but no reachable screen calls them.
- Upload, training, deploy, chain configuration, and the polished plans/billing pages are all unreachable. The live app's only workflow is: chat a brief → approve → download a zip.
- Two parallel API clients with different token stores (`api/factory.ts` — `cerebrum.factory.token` — vs. `api/client.ts` + `api/auth.ts` — `cerebrumdev:login-token`), so even mounting the old pages today would not share the login session.
- Dead control in the live UI: the boot-error "Retry" button only clears the error state and does not re-run the boot effect (`frontend/src/App.tsx:65-73`).

CI cannot catch this: orphaned components still compile and lint, and there are **no frontend unit/component tests** (`.github/workflows/ci.yml` builds and lints only).

### 2.2 Backend functionality — genuinely solid

- 14 routers covering auth, billing, sessions, config, upload (+ RAG ingestion sub-API), chat (SSE), training (Tinker), deploy (Render/GitHub), Google Drive, domains, product factory, delivery standard, plus flag-gated Resident Engineer / change requests / workbench (`backend/app/routers/`, `backend/app/main.py`).
- **611 test functions across 107 files**, and they are real: domain smoke contracts for ~18 verticals, RAG pipeline, auth, session isolation, packager, deployer, Stripe (`backend/tests/`). CI runs the full suite plus a production Docker build.
- Error handling is deliberate, not swallowed: chat errors surface as SSE `error` events (`backend/app/routers/chat.py:174-185`), billing degrades with honest 503s, health probes never raise.
- Session state persistence uses atomic writes with `.bak` fallback (`backend/app/core/session_persistence.py`).

### 2.3 Factory output — a scaffold, not a turnkey product

The thing the customer ultimately receives is weaker than the factory that makes it:

- Generated products get a **stub UI**: `_write_ui_stub()` (`backend/app/factory/generator.py:927`).
- Connectors are written as honest stubs: `STATUS = "not_implemented"` (`backend/app/factory/generator.py:1008-1012`).
- The workbench "coder agent" is a deterministic stub unless `KIMI_WORKBENCH_ENABLED=true` (`backend/app/workbench/agent.py:134-229`).
- Generated deployment packages ship `CORS_ORIGINS: *` (`deployments/deploy-sess_deploy_demo2_1782061080/render.yaml:18`) — permissive CORS in customer-facing artifacts.
- Deferred wiring, self-declared: virgin shelf / source packs / RAG packs into chain generation (`backend/app/core/virgin_shelf_loader.py:8` et al.), rule injection placeholder (`backend/app/core/rule_injector.py:13,47`), estate entity tables deferred (`backend/app/product_dna/emit.py:203`).

This is consistent with the internal pilot audit's own honesty ("UI stubs; connectors `not_implemented`") — fine for a supervised pilot, not for a paying customer expecting the README's promise.

---

## 3. Usability — one clean path, many missing paths

**What works:** AuthGate → auto-created session → Factory Floor chat with streaming and typing indicator → blueprint approval → Platforms list with zip download → Account. Friendly copy, real loading/empty/error states ("No platform built yet", `frontend/src/App.tsx:348-360`), 401 → auto-logout. Radix UI primitives give an accessibility baseline.

**What hurts:**
- No password recovery or email-verification journey (§2.1) — a hard blocker for self-serve.
- The **Subscription page undermines trust**: it renders raw snake_case key/value pairs and, on Upgrade, tells the user *"Billing is being connected — the only piece still pending at the factory"* (`frontend/src/App.tsx:407-411`) — even though the backend checkout works the moment Stripe keys are set. The polished `PlansPage`/`TrialBanner` that should replace this are in the orphaned tree.
- If `PLATFORM_CHAT_FLOW_ENABLED` is off, the Floor becomes a dead end — chain/rules events are intercepted with "describe the platform instead" (`frontend/src/App.tsx:238-247`).
- No i18n; all strings hardcoded English. Acceptable for launch, worth noting for the enterprise positioning.

---

## 4. Security — the strongest dimension

Verified strengths:
- **Passwords:** PBKDF2-HMAC-SHA256, 200,000 iterations, per-user 16-byte salt, constant-time compare (`backend/app/core/accounts_store.py:165-182`).
- **Tokens:** login/API/verify/reset tokens all stored as SHA-256 hashes with sensible TTLs; password reset invalidates all sessions (`accounts_store.py:309-373, 490-493`).
- **Authorization:** `require_owned_session` enforces per-account ownership on every session-scoped route with a non-leaking 404 (`backend/app/core/session_guard.py:20-31`) — completed in the most recent commits (`e88f114`, `9b3d6fc`).
- **No committed secrets** — full scan for key patterns (sk-, sk_live_, AKIA, ghp_, AIza, private keys) found zero; `.gitignore` covers `.env`, keys, storage.
- **Stripe webhook signature verification** with 400 on bad signature (`backend/app/routers/billing.py:78-89`).
- **No shell injection:** all subprocess use is list-form, no `shell=True`; deployer verifies the target repo is private before pushing (`backend/app/core/deployer.py:38-39, 148-152`).
- **Boot safety:** production refuses to start without `CEREBRUM_DEV_API_KEY` (`backend/app/main.py:30`, `backend/app/core/auth.py:35-41`).
- CORS is origin-specific with credentials, not wildcard (`backend/app/main.py:44-50`) — in the *platform*; the *generated packages* are the exception (§2.3).

Residual items (medium/low):
- Rate limiting is in-memory per-process, applied only to auth endpoints (`backend/app/core/rate_limit.py`) — ineffective under multi-worker/multi-instance deployment.
- Google Drive OAuth `state` tokens live in an in-memory dict (`backend/app/routers/factory_drive.py:49`) — breaks on multi-instance.
- Login token in `localStorage` — standard SPA trade-off, XSS-exposable.

---

## 5. Deployment — would deploy today, with known sharp edges

- `render.yaml` is complete: Docker web service + static frontend, `healthCheckPath: /health`, 1 GB persistent disk at `/app/storage`, SPA rewrites, secrets `sync: false` (`render.yaml:1-91`).
- Real `/health` (storage + redis probes, `degraded` states) and `/ready` (storage, blocks connectivity, LLM, API key) (`backend/app/main.py:145-184`).
- CI: pytest + frontend build/lint + production Docker build, with concurrency cancellation (`.github/workflows/ci.yml`).
- Sharp edges: SQLite accounts DB is safe only because the disk mount covers it — the `.env.example` itself warns about ephemeral filesystems (`backend/.env.example:40-43`); no Alembic for the core accounts schema (`accounts_store.py:148-150` uses `create_all`); session state is JSON-on-disk, a durability/concurrency ceiling for multi-user SaaS; `docker-compose.yml` runs the Vite dev server for the frontend (local-only path, fine); CI triggers on `master` only.

---

## 6. Billing — the expected gap that isn't

**Implemented and tested:**
- Checkout with returning-customer reuse (`backend/app/core/stripe_billing.py:54-76`), customer portal (`:79-91`), webhook event construction with signature verification (`:94-96`), and a complete event handler mapping `checkout.session.completed`, `customer.subscription.*`, `invoice.payment_failed` to internal subscription states via a single idempotent seam (`:99-178`).
- Routes mounted at `/v1/billing`: status, checkout, portal, webhook (`backend/app/routers/billing.py:27-89`).
- Trial + entitlement gating with 402 `trial_expired` applied to paid factory routes (`backend/app/core/billing.py:82-97`, `backend/app/main.py:68-80`).
- Test coverage: `test_stripe_billing.py`, `test_billing_api.py`, `test_commercial_tiers.py`, `test_platform_tiers.py`.

**What "off" actually means:**
1. No `STRIPE_SECRET_KEY` / `STRIPE_PRICE_ID` / `STRIPE_WEBHOOK_SECRET` configured → endpoints return honest 503s.
2. `BILLING_ENFORCEMENT` defaults to **off** (`backend/app/core/billing.py:28-29`) → paid gates are open until flipped.
3. The reachable Subscription UI shows "being connected" copy and raw status keys instead of the built `PlansPage` (§3).

**Activation is a configuration-plus-wiring task measured in days, not a build task measured in weeks.**

---

## 7. Observability — the invisible failure mode

- **No error tracking anywhere** (no Sentry/Rollbar/equivalent, repo-wide). In production, exceptions die in Render logs unseen. This is the single highest-leverage pre-launch fix.
- No metrics, no APM, no request tracing. `/health` + `/ready` support uptime checks only.
- 25 files still use `print()` instead of `logging`; logging is unstructured.
- Broad `except Exception` hot spots worth a targeted pass: `rag_ingestion_store.py` (17), `packager.py` (16), `upload_processor.py` (11), `chroma_store.py` (9).

---

## Prioritized gap list

| # | Sev | Gap | Evidence | Suggested fix |
|---|:---:|-----|----------|---------------|
| 1 | **High** | Orphaned frontend: no reachable password reset, email verify, upload, training, deploy, plans pages; two token stores | `frontend/src/main.tsx:3-8`; `components/**` unimported | Decide: mount the component tree behind a router (wouter is already a dependency) on the `factory.ts` client, or delete the dead tree and rebuild the 3–4 screens that matter (reset password, verify email, plans). Unify on one API client/token key |
| 2 | **High** | No error tracking in production | repo-wide | Add Sentry (FastAPI + React SDKs); ~1 day including release tagging |
| 3 | **High** | Subscription UI says billing is "pending" while backend works; enforcement off | `App.tsx:407-411`; `core/billing.py:28-29` | Configure Stripe keys, set `BILLING_ENFORCEMENT=on`, wire `PlansPage`/real checkout flow, remove apologetic copy |
| 4 | **Med** | Generated products: stub UI, `not_implemented` connectors, `CORS_ORIGINS: *` | `factory/generator.py:927,1008-1012`; `deployments/.../render.yaml:18` | Set explicit buyer expectations in product docs; harden generated CORS to the product's own frontend origin; roadmap the UI generator |
| 5 | **Med** | In-memory rate limiting + OAuth state break under >1 worker/instance | `core/rate_limit.py:15-16`; `routers/factory_drive.py:49` | Move both to Redis (`REDIS_URL` plumbing already exists); extend rate limiting beyond auth endpoints |
| 6 | **Med** | No migration system for accounts DB; JSON-file session store | `accounts_store.py:148-150`; `core/session_store.py` | Introduce Alembic for the accounts schema now (cheap while schema is small); plan Postgres for session state post-launch |
| 7 | **Med** | Demo quality depends on LLM key + external Cerebrum-Blocks service | `product_architect.py:50-57`; `AGENTS.md:16` | Ship a canned offline demo path (golden blueprints already exist) surfaced by default when unconfigured |
| 8 | **Low** | Dead "Retry" button on boot error | `App.tsx:65-73` | Re-trigger the boot effect, not just clear the error |
| 9 | **Low** | No frontend tests; CI on `master` only | `ci.yml` | Add smoke-level component tests (auth, floor, subscription); consider branch coverage |
| 10 | **Low** | `print()` in 25 files; unstructured logs | repo-wide | Migrate to `logging`; adopt JSON logs when adding Sentry |
| 11 | **Low** | README leads with the old 5-phase configurator narrative | `README.md:51-84` | Rewrite around the Factory Floor flow the app actually ships |

---

## Strengths inventory (keep these)

- Per-account session isolation with non-leaking 404s, completed across all session-scoped routers
- PBKDF2-200k password hashing, hashed tokens, session invalidation on reset
- Zero committed secrets; disciplined `.gitignore` and `.env.example` hygiene
- Signature-verified Stripe webhooks; single idempotent mutation seam for subscription state
- 611 real backend tests wired into CI, plus production Docker build in CI
- Honest-stub doctrine — placeholders self-identify instead of faking capability
- Complete Render blueprint with health checks and persistent disk
- Ratified, coherent commercial tier model with an honest trial design
- A live, certified, factory-generated product (Steward) as concept proof

---

## Path to launch (before charging money)

1. **Week 1 — Reach & recover:** unify the frontend (gap #1) — at minimum: password reset, email verification, and a real plans/checkout page on the live client. Fix the Retry button.
2. **Week 1 — See failures:** add Sentry to backend + frontend (gap #2).
3. **Week 2 — Turn billing on:** Stripe keys in Render, `BILLING_ENFORCEMENT=on`, webhook endpoint registered, end-to-end test with a test-mode card (gap #3).
4. **Week 2 — Multi-instance safety:** Redis-backed rate limiting and OAuth state; Alembic baseline migration (gaps #5, #6).
5. **Week 3 — Buyer expectations:** align README/marketing with the shipped Floor flow; document exactly what a generated product contains (scaffold + API + RAG, stub UI, no live connectors) so the first paying customers get what they expect (gaps #4, #11).
6. **Week 3–4 — Hardening pass:** generated-package CORS, frontend smoke tests, `print()` → logging sweep (gaps #4, #9, #10).

With the backend and security posture already at this level, the distance to market is short — but it runs through the frontend and observability, not through billing.

---

*Audited 2026-07-23 against branch `claude/market-readiness-audit-2enick` (tip `e88f114`). All file:line citations verified at audit time.*
