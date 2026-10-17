# CerebrumDev.ai — A-to-Z repository audit

**Repository:** [bopoadz-del/CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai)
**Pin:** `7ea3c48` (`origin/master`, live Render backend `/version`)
**Date (UTC):** 2026-08-28
**Kind:** investigation and report only. No fixes in this pass.
**Method:** code review of the shipping tree, live HTTP probes against `https://api.cerebrum-dev.com` and `https://www.cerebrum-dev.com`, `scripts/scan_secrets.py`, and `pytest --collect-only` (1469 tests).

---

## Executive summary

CerebrumDev.ai is a **hosted product-generation factory**, not a RetailOps or Steward runtime. A user registers, describes a platform on the Factory Floor, and the factory drafts a blueprint, plans capabilities against a dual block registry, and generates an exportable product tree. Live production at this pin is **up**, Redis and Neon accounts backups are healthy, Sentry is configured, OpenAPI is closed, unauthenticated API calls 401, and dangerous flag-gated surfaces (workbench, resident engineer) are **off**.

The system has been hardened in the right places: fail-closed boot auth, hashed credentials, session ownership 404s, Stripe webhook signatures, upload/output path confinement, RAG SSRF controls, coder AST sandbox, and a large automated suite with stub/secret/gate audits in CI.

**No Critical vulnerability was verified in the current production posture** (master key set, `ALLOW_ANONYMOUS_DEV` not granting anonymous access when a master key exists, `BUILD_MODE_ENABLED=false`, `/docs` 404, no committed secrets).

The worst remaining risks are **launch and operations**, not an open RCE:

1. **Legal vacuum** while the site is already a public multi-user SaaS collecting emails.
2. **Billing gate is on the wrong HTTP surface** — the Floor uses session chat/product routes that do not use `require_entitled`.
3. **Generate is not single-flight**; Steward briefs start two runner builds.
4. **Deploy config is not the live system** (`render.yaml` self-disclaims; runbooks still describe baking a master key into the frontend).
5. **Market-ready smoke is a manual script**, not a pipeline gate, despite AGENTS.md.

Fix those before claiming paid launch or “market-ready.” Do not treat a green CI run as live factory evidence.

---

## Scope

In scope: layout, architecture, runtime flow, security, correctness, code quality, tests, CI/CD, deploy/config/observability, docs vs reality, licensing.

Out of scope: rewriting the factory, enabling billing, or changing production. Sibling repos (Cerebrum-Blocks, generated products) were not opened.

---

## What is working well

- **Honest product identity.** This repo is the factory. There is no runnable `backend/app/retailops/` tree. Provenance for RetailClient lives under `docs/provenance/`.
- **Fail-closed auth boot.** `verify_production_auth()` in `backend/app/core/auth.py` refuses to start without `CEREBRUM_DEV_API_KEY` or an explicit `ALLOW_ANONYMOUS_DEV=1`. The old “ENV must equal the string production” fail-open is gone.
- **Length-safe master-key compare** (`_tokens_match` / SHA-256 then `hmac.compare_digest`) so a `cdt_` token cannot 500 against the master key (#212).
- **Session isolation.** User principals get a non-leaking 404 on foreign sessions. Factory, Drive, and `GET /sessions/{id}` share `owned_session_or_404` (#212). Live unauthenticated `/v1/sessions/` and `/v1/auth/me` return 401.
- **Password and token hygiene.** PBKDF2-HMAC-SHA256 (200k iterations); login tokens `cdt_` and API keys `cdk_` stored as SHA-256 hashes; account deletion requires the current password (`backend/app/routers/accounts.py`).
- **Production HTTP surface.** `/docs`, `/redoc`, `/openapi.json` 404 on the live API (#211). Responses get `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, HSTS in production.
- **`/ready` is a real probe.** Render health-checks `/ready` (status code, not a 200-while-degraded `/health`). Storage + API key gate readiness. LLM configured now requires a real key, not `LLM_PROVIDER` alone (#211).
- **Billing honesty.** Unconfigured Stripe returns `503 stripe_not_configured` instead of a fake checkout. Webhook signature is the gate (`backend/app/routers/billing.py`).
- **Factory fail-closed planning.** Unknown blocks raise `DualRegistryError`. `generate_product()` is the single door and runs `assert_compliant()` (`backend/app/factory/product_architect.py`). Output dirs are confined by `safe_output_dir()` before `rmtree`.
- **Coder sandbox.** AST gate in `backend/app/factory/coder.py` blocks `eval`/`exec`/`os`/`subprocess` and classic dunder escapes. Failures become honest stubs, not silent fake code.
- **Upload and DoS controls.** ASGI body ceilings (2 MiB JSON / 100 MiB multipart), extension allowlists, per-account LLM burst limits, bounded in-memory rate-limit store, `TRUSTED_PROXY` opt-in for `X-Forwarded-For`.
- **RAG SSRF controls.** HTTPS-only, loopback/private IP block, redirect re-validation, size caps (`backend/app/core/rag_source_acquisition.py`).
- **Ops that match Render constraints.** Nightly backup runs **in-process** (cron cannot mount the disk). Alembic runs at container boot and a failed migration crashes the process (root `Dockerfile`). Live backup at 2026-08-28 03:00 UTC: `ok: true`, Neon Postgres, `pg_dump` 18.6, host matches live engine.
- **CI meta-gates.** ruff (pyflakes), full pytest, keyed-path coder wiring without paid calls, LotDesk gate, stub audit, gate audit, secret scan, Windows cp1252 job, frontend test/build/lint, production Docker build.
- **No committed secrets** on non-test paths (`scripts/scan_secrets.py` exit 0 this run).
- **Live flags parked.** `/health` reports `build_mode_enabled: false`, `resident_engineer_enabled: false`, `kimi_workbench_enabled: false`. Redis `ok: true`. Sentry configured on backend.

---

## Ranked findings

### Critical

None verified against the live production posture at SHA `7ea3c48`.

A Critical would require a default-on auth bypass, committed credentials, or unauthenticated RCE. Those were not found. Several **High** items become Critical if operators follow stale runbooks or copy Cloud-dev `ALLOW_ANONYMOUS_DEV=1` into a shared environment.

---

### High

#### H1. Public SaaS collects PII with no LICENSE and legal docs marked not in force

**Where:** repo root (no `LICENSE`); `docs/legal/TERMS_OF_SERVICE.md`; `docs/legal/PRIVACY_POLICY.md`; live `https://www.cerebrum-dev.com` (registration).

**Why it matters:** The live site registers accounts (email + password). Terms and Privacy both open with **DRAFT — NOT YET IN FORCE** and `[[NEEDS INPUT]]` placeholders for legal entity, age, refunds, lawful bases. There is no SPDX license for the factory codebase or for generated product trees that re-vendor blocks. Contributors, customers, and generated-product owners have no grant. GDPR/CCPA-style export and erasure **code** exists (`/v1/auth/export`, `DELETE /v1/auth/account`) but is not backed by published terms.

**Fix:** Add an explicit LICENSE (and NOTICE for vendored Blocks). Complete counsel review of Terms/Privacy, publish them from the product, and stop marketing “live multi-user” until that ships. Do not treat the draft Markdown as a substitute.

#### H2. The Floor’s generate path is not behind `require_entitled`

**Where:** `backend/app/main.py` (session_product mounted with `require_api_key` only; `/v1/factory/product/*` uses `require_entitled`); `backend/app/routers/session_product.py`; `backend/app/routers/chat.py`; `frontend/src/api/factory.ts` (UI never calls `/v1/factory/product/*`).

**Why it matters:** `BILLING_ENFORCEMENT` 402s expired trials on **session create** and the **stateless** factory API. The product users actually use is Floor chat + `/v1/sessions/{id}/product/*`. Those routes meter trial **quotas** (`trial_limits`) but do not call `require_entitled`. Flipping enforcement at launch therefore blocks new sessions while leaving generate/chat on an existing session open until quotas run out — or forever for `active` vs expired logic gaps. The gate the README describes is not the gate the UI hits.

**Fix:** Mount `session_product` (and chat generate branches) with `require_entitled`, or call it inside those handlers. Add an HTTP test: expired trial + existing session → 402 on generate and on approve-and-generate chat. Keep `/v1/factory/product/*` as-is.

#### H3. Generate is not single-flight; Steward starts two runner builds

**Where:** `backend/app/factory/build_jobs.py` `start_runner_build`; `backend/app/routers/session_product.py` (Steward canonical mirror ~401–406); `backend/app/factory/platform_chat_flow.py` (`_live_build_thread` only on some chat resume paths); `backend/app/factory/build/ledger.py` `append` (no file lock).

**Why it matters:** `start_runner_build` always spawns a new daemon thread. Chat resume has a live-thread check; HTTP generate and first approve do not. Double-click generate, overlapping chat approve + REST generate, or two Render workers writing one ledger produce torn workspaces, interleaved JSONL, and doubled LLM spend. Steward (`product_id == "projectsix"`) unconditionally calls `generate_product` a second time toward `factory_outputs/Projectsix`. Trial quota is consumed **before** success (`trial_limits.consume` via `_enforce_generation_quota`).

**Fix:** Refuse a second start when `build_status` is `building`. File-lock ledger appends. Make the Steward canonical copy a post-success clone, not a second runner. Consume `generation` quota on `RUN_SUCCEEDED` only (or refund on `RUN_FAILED`).

#### H4. Deploy source of truth is the dashboard; docs still teach a master-key frontend leak

**Where:** `render.yaml` lines 1–10 (“NOT A LIVE BLUEPRINT”); `docs/runbook.md` (set `VITE_API_KEY` to match `CEREBRUM_DEV_API_KEY`, rebuild frontend); `frontend/.env.example`; `render.yaml` frontend `VITE_API_KEY`; `docs/audits/REGISTERED_BUT_DEAD_AUDIT.md` item 20.

**Why it matters:** The frontend **does not read** `VITE_API_KEY` (`frontend/src/api/factory.ts` uses only `VITE_API_URL` + Bearer `cdt_`). Vite would still bake any `VITE_*` referenced at build time into public JS. The runbook’s rotation steps would put the **master admin key** in a static bundle — historically the cross-tenant blast radius (`principal.kind != "user"` skips ownership). `render.yaml` also pins `ACCOUNTS_REQUIRE_VERIFIED_EMAIL: "0"` and leaves `ACCOUNTS_DATABASE_URL` “unwired” while live `/ready` shows Neon Postgres and the Aug 17 smoke showed unverified sessions 403. Syncing this file as a Blueprint would **change** production security.

**Fix:** Delete `VITE_API_KEY` from `render.yaml`, `.env.example`, and the runbook. Rewrite the runbook around dashboard-as-source-of-truth. Export live env and replace the blueprint, or stop presenting `render.yaml` as intended state. Add a CI check that `VITE_API_KEY` never appears in `frontend/src`.

#### H5. Market-ready live smoke is not in CI or deploy

**Where:** `scripts/post_deploy_smoke.py`; `.github/workflows/ci.yml` (PR-only, no smoke, no `${{ secrets.* }}`); `AGENTS.md` deploy gate.

**Why it matters:** CI collected **1469** tests this run and never calls production. AGENTS.md forbids market-ready claims without `python3 scripts/post_deploy_smoke.py https://api.cerebrum-dev.com` exiting 0 with every kernel `[LIVE]`. A broken LLM key, coder wiring, or CORS on the custom domain can merge green. `docs/audits/CEREBRUMDEV_PRODUCTION_READINESS.md` is a GO from 2026-08-17 SHA `3665c86`, not this pin.

**Fix:** Add a post-merge (or Render deploy-hook) workflow with `SMOKE_GATE_TOKEN`. Fail the release, not the PR unit job, on non-zero smoke. Re-run and refresh the canonical readiness doc at this SHA before any launch claim.

---

### Medium

#### M1. Public `/ready` discloses the Neon accounts hostname

**Where:** `backend/app/core/backup.py` `accounts_host_fingerprint` (returns `urlparse(url).hostname`); `backend/app/main.py` `_backup_details`; live `/ready`.

**Evidence (live):** `accounts_host` / `live_accounts_host` = `ep-sweet-hill-ay5e13we.c-5.us-east-2.aws.neon.tech`.

**Why it matters:** Credentials are not leaked, but the unauthenticated health endpoint is a map of the accounts database. Combined with other recon it is unnecessary exposure.

**Fix:** Hash or truncate the hostname for public `/ready`; keep the full value on the master-key backup endpoint.

#### M2. `ALLOW_ANONYMOUS_DEV` is not refused when `ENV=production`

**Where:** `backend/app/core/auth.py` `_anonymous_dev_allowed`, `verify_production_auth`; `.cursor/environment.json`.

**Why it matters:** With a master key set, anonymous callers still 401 (good). If production is ever started **without** `CEREBRUM_DEV_API_KEY` and **with** `ALLOW_ANONYMOUS_DEV=1`, every caller is `Principal(kind="dev")` and **bypasses all ownership checks**. Cloud-dev correctly sets the flag locally.

**Fix:** `verify_production_auth()` should raise if `ENV` is `production`/`prod` and `ALLOW_ANONYMOUS_DEV` is set, even when a master key exists (belt and suspenders).

#### M3. Workbench `product_root` is caller-controlled when the flag is on

**Where:** `backend/app/workbench/router.py` `RunBody.product_root`; `backend/app/workbench/session.py` `_resolve_product_root`; `backend/app/workbench/sandbox.py` (network not restricted).

**Why it matters:** Default is off (live confirms). If `BUILD_MODE_ENABLED` is flipped, any authenticated user can point the sandbox at an arbitrary directory and run subprocesses with host egress.

**Fix:** Keep the flag off in production. If enabled, resolve `product_root` only under `factory_outputs/` (same pattern as `safe_output_dir`).

#### M4. Email verification and billing enforcement default off in the blueprint

**Where:** `render.yaml` `ACCOUNTS_REQUIRE_VERIFIED_EMAIL: "0"`, `BILLING_ENFORCEMENT: "false"`; `backend/app/core/billing.py`.

**Why it matters:** Unverified accounts can consume LLM/generation if the dashboard matches the file. Enforcement off is documented and currently matches “Stripe not configured,” but expired trials then rely only on quotas. Launch requires both mail and Stripe **and** these flags, plus H2.

**Fix:** After mail/Stripe are verified live, set both to on in the **dashboard**, then update `render.yaml` to match. Do not flip enforcement while checkout is 503 (users get 402 with no self-serve remedy — already warned in `render.yaml`).

#### M5. Encryption at rest is opt-in

**Where:** `backend/app/core/file_crypto.py`; Drive OAuth token storage in `backend/app/routers/factory_drive.py`.

**Why it matters:** Unset `DATA_ENCRYPTION_KEY` stores OAuth tokens and uploads plaintext on the Render disk. `render.yaml` declares `generateValue: true`; live dashboard is unknown. Same-disk backups do not protect those tokens if the disk is copied.

**Fix:** Fail `/ready` (or boot) in production when Drive is configured and encryption is off. Document rotation (`docs/runbook.md` already cautions).

#### M6. Rate limits fail open on Redis outage; Drive OAuth state is per-process then

**Where:** `backend/app/core/rate_limit.py`; `backend/app/routers/factory_drive.py`; `backend/tests/test_redis_outage_injection.py`.

**Why it matters:** Documented and tested. Multi-instance + Redis down ≈ N× the auth brute-force budget. OAuth callback can land on a worker that never saw the pending state.

**Fix:** Alert on Redis `ok: false`. Consider fail-closed for **auth** buckets only. Keep OAuth pending state on Redis with no in-memory fallback, or sticky sessions.

#### M7. Session state is last-write-wins JSON; Chroma rehydrate sets `user_id="anonymous"`

**Where:** `backend/app/core/session_store.py`, `session_persistence.py`.

**Why it matters:** Concurrent chat + generate, or two instances without a shared disk, drop `product_design`. If `state.json` is lost but Chroma remains, rehydration stamps `user_id="anonymous"` and the real owner then gets 404.

**Fix:** Persist `user_id` in Chroma metadata; rehydrate from `session_owners`. Version session JSON or move product_design to Postgres. Document single-instance + persistent disk as a hard requirement until then.

#### M8. `increment_usage` is not an UPSERT

**Where:** `backend/app/core/accounts_store.py` `increment_usage` (update then insert). In-process `_LOCK` helps one worker; two instances can `IntegrityError` → 500 on first concurrent increment.

**Fix:** `INSERT … ON CONFLICT DO UPDATE`. Catch `IntegrityError` and retry the update.

#### M9. Broad `except Exception` → HTTP 400 on product routes

**Where:** `backend/app/routers/session_product.py` draft/plan/pilot/generate.

**Why it matters:** Disk full, DB errors, and bugs are indistinguishable from bad briefs. Operators lose signal; clients retry forever.

**Fix:** Catch `BlueprintError` / `DualRegistryError` as 400; let the rest 500 and hit Sentry.

#### M10. Docker installs `requirements.txt`, not the lockfile; image runs as root

**Where:** root `Dockerfile` (`pip install -r requirements.txt`, no `USER`); `backend/requirements.lock` exists; `backend/Dockerfile` skips Alembic and blueprints.

**Why it matters:** Transitives float at image build. Root in the container raises blast radius if the coder/workbench ever executes. Compose uses `backend/Dockerfile` on port **8001**, which is not the production image.

**Fix:** `pip install -r requirements.lock` (or `pip-tools` in CI). Add a non-root `USER`. Make `backend/Dockerfile` an alias of the root image or delete it.

#### M11. Playwright E2E exists but is unwired; `backend/app/tests/` is still orphaned

**Where:** `frontend/e2e/*.spec.ts`, `frontend/playwright.config.ts`, `frontend/package.json` (no `@playwright/test`); `backend/pytest.ini` `testpaths = tests`; 8 files still under `backend/app/tests/` (including a duplicate `test_session_ownership.py`).

**Why it matters:** #212 copied ownership tests into `backend/tests/` (CI now sees them). The old tree is still not collected. UI auth-gate / Floor / production registration specs never run. XSS rendering is untested.

**Fix:** Delete or merge `backend/app/tests/`. Add Playwright as a devDependency and a CI job (mocked spec always).

#### M12. Fifteen tests are permanently xfailed

**Where:** `backend/tests/test_generated_deployed_router.py` (module-level xfail; `_write_deployed_router` removed).

**Why it matters:** Green counts include known-dead packager coverage.

**Fix:** Delete the module or rewrite against the current packager. Do not xfail entire files indefinitely.

#### M13. No metrics, no Dependabot, factory `print()` logging

**Where:** `backend/app/main.py` (health/ready/version only); `KNOWN_LIMITATIONS.md`; `backend/app/factory/build/roles.py` and others.

**Why it matters:** Sentry catches errors; there are no SLIs for build duration, LLM errors, or quota 429s. No automated dependency CVE feed. `print()` in the build path is noisy and easy to leak briefs.

**Fix:** One Prometheus `/metrics` or Render metric plugin. Enable Dependabot/pip-audit/npm audit in CI. Replace factory `print` with `logging`.

#### M14. Frontend static site has no CSP / frame-ancestors; tokens live in `localStorage`

**Where:** `frontend/src/api/factory.ts`; live `https://www.cerebrum-dev.com` response headers (this run: `x-content-type-options: nosniff` only; no CSP, no `X-Frame-Options`, no HSTS on the HTML response).

**Why it matters:** Chat text is React-escaped (no `dangerouslySetInnerHTML` found). Any future XSS steals `cdt_` tokens. Clickjacking the Floor is possible without `frame-ancestors`.

**Fix:** Render static headers: CSP `default-src 'self'`, `frame-ancestors 'none'`, HSTS. Longer term: HttpOnly cookie + CSRF for login tokens.

#### M15. Authenticated RAG source fetch is still an SSRF/egress surface

**Where:** `backend/app/routers/domains.py`; `backend/app/core/rag_source_acquisition.py`.

**Why it matters:** Controls are strong. Residual: DNS rebinding TOCTOU and using factory egress to scan the public internet.

**Fix:** Pin resolved IPs for the fetch; admin-only ingestion; egress allowlist.

#### M16. 1 GiB Render disk holds sessions, Chroma, backups, and factory outputs

**Where:** `render.yaml` `disk.sizeGB: 1`; `backend/app/factory/paths.py`.

**Why it matters:** Role-runner products are not tiny. Disk-full during generate looks like a random 400 (see M9). Same-disk backups do not survive disk loss (already documented).

**Fix:** Raise disk size; put `BACKUP_DIR` off-disk; expire old `factory_outputs/sessions/*`.

#### M17. `runtime/` `subprocess.run(..., shell=True)` with a prefix allowlist

**Where:** `runtime/runtime.py`, `runtime/gates.py`. Not imported by `main.py`.

**Why it matters:** Prefix allowlists plus `shell=True` still parse oddly (`allowed_cmd; evil`). Low until this runtime is wired to HTTP.

**Fix:** Argv lists, no shell.

---

### Low

#### L1. God files

`backend/app/factory/build/roles.py` (3487 lines), `generator.py` (1474), `coder.py` (1185), `platform_chat_flow.py` (980), `accounts_store.py` (830). Split when next touched.

#### L2. Dual generators and dual HTTP APIs

Floor uses session product + chat. `/v1/factory/product/*` and `backend/app/platform_generator/` (automotive overlays, scripts) are parallel. Easy to “fix” the unused path.

#### L3. Dual schema mutation

Alembic (`backend/alembic/`) plus runtime `_ensure_column` in `accounts_store.py`. Fresh boots and old SQLite files can drift.

#### L4. FastAPI `@app.on_event` deprecated

`backend/app/main.py` startup/shutdown. Pytest already warns. Migrate to lifespan.

#### L5. Tracked generated artifacts

`deployments/deploy-sess_deploy_demo2_1782061080/` (9 files, gitignored pattern but still tracked). `build/stages/*.json` (33 files) and `build/pilot_workspace/` (103 files). AGENTS.md says stage evidence must not ship in feature PRs; it is already on master. No live secrets found in the demo deploy tree.

#### L6. Hardcoded `/home/ubuntu/repos/Cerebrum-Blocks` in `dual_registry.py` and some factory tests

CI relies on `vendor_blocks_mirror` when that path is absent. Use `real_blocks_root()` everywhere.

#### L7. CORS default list includes production origins even in local `.env`-less boots

`backend/app/main.py` default `CORS_ALLOW_ORIGINS` concatenates localhost and live hosts. Fine for prod; surprising for a misconfigured laptop talking to production.

#### L8. No mypy/pyright in CI

Large untyped dict payloads in factory/session state.

#### L9. `KNOWN_LIMITATIONS.md` test section is stale

Still says “647 passed” plus two failures and six errors. This pin collects **1469** tests. Misleading for anyone triaging CI.

---

### Info

| ID | Note |
| --- | --- |
| I1 | Live backend SHA `7ea3c48` matches `origin/master`. Frontend HTML `Last-Modified: Wed, 26 Aug 2026` — static site may lag the API by ~2 days. |
| I2 | CI runs only on **pull_request to master**, not push-to-master (`ci.yml` comments: Render is the main-branch build). `REGISTERED_BUT_DEAD_AUDIT.md` item 12 still says CI on push. |
| I3 | `GET /export` does not require password (bearer is enough). `DELETE /account` does. Intentional; document it. |
| I4 | `STORE_MANAGER` write half is deliberately unimplemented (`KNOWN_INCOMPLETE.md` §1c). Harvest is a policy no-op. |
| I5 | Domain store 503 without `CEREBRUM_API_URL` is expected; Floor toasts it; product draft still works from `blueprints/`. |
| I6 | `vendor_blocks_mirror` can satisfy dual-registry in CI without a real Blocks checkout (`KNOWN_INCOMPLETE.md`). README’s “certified kernels only” is aspirational when the mirror is in play. |
| I7 | Secret scanner skips `build/` and test paths by design. |
| I8 | `docs/audits/PRODUCTION_READINESS_AUDIT.md` table still says **NO-GO** while the linked canonical file from 2026-08-17 says **GO**. First-click trap. |

---

## Architecture notes

### What the product is

A conversational factory: register → Factory Floor chat → blueprint card → approve → background role-runner build → download zip. Subscription/trial is supposed to bound depth. Generated platforms are **separate repos**; this host never runs RetailOps.

### Layout (source vs generated)

| Path | Role |
| --- | --- |
| `backend/app/main.py` | FastAPI factory API |
| `backend/app/core/` | Auth, accounts, billing, sessions, rate limit, RAG, backup, observability |
| `backend/app/factory/` | Architect, planner, dual registry, generator, role runner, coder |
| `backend/app/routers/` | HTTP surface |
| `backend/alembic/` | Accounts migrations (boot `upgrade head`) |
| `frontend/src/` | SPA (view-state, no React Router): Floor, Platforms, Subscription, Account |
| `blueprints/` | Golden Steward + examples, copied into the production image |
| `runtime/` | Standalone coding-agent loop; **not** mounted on the Floor |
| `build/` | Committed stage evidence + pilot workspace (regenerable) |
| `deployments/` | Legacy kit package (gitignored pattern; demo tree still tracked) |
| `factory_outputs/` | Generate output (gitignored; `$STORAGE_PATH/factory_outputs` in prod) |

### Request flow (what the UI actually calls)

```
Browser  --Bearer cdt_-->  FastAPI
  POST /v1/auth/register|login
  POST /v1/sessions/
  POST /v1/sessions/{id}/chat          SSE (blueprint / generation / delta)
  GET  /v1/sessions/{id}/product/build-status   poll ~4s
  GET  /v1/sessions/{id}/product/package        zip
```

Chat `platform_chat_flow` drafts via `draft_blueprint_from_brief` (LLM → golden Steward YAML → keyword fallback), plans via `CapabilityPlanner` + dual registry, generates via `product_architect.generate_product`.

Default engine is **`FACTORY_BUILD_ENGINE=runner`**: background thread, JSONL ledger, phases COLLECTOR → CLONER → WRITER → TESTER → STORE_MANAGER. `template` reverts to synchronous `ProductGenerator` (more files, no agent-written handlers). Download 409s unless the ledger says `succeeded`.

### Auth model

| Principal | How | Ownership |
| --- | --- | --- |
| `admin` | `CEREBRUM_DEV_API_KEY` | All sessions |
| `user` | `cdt_` / `cdk_` | Own sessions only |
| `dev` | `ALLOW_ANONYMOUS_DEV=1` and **no** master key | All sessions (sandbox) |

### Data stores (live at this pin)

| Store | Live | Used for |
| --- | --- | --- |
| Neon Postgres | yes (`engine: postgres` on `/ready`) | Accounts, tokens, usage, session_owners |
| Render disk `/app/storage` | yes | Sessions JSON, Chroma, uploads, factory_outputs, backups |
| Redis | yes | Rate limits, Drive OAuth pending |
| Cerebrum-Blocks HTTP | yes (`cerebrum_blocks.status: 200`) | Domain kits; generation still wants a git clone / `CEREBRUM_BLOCKS_ROOT` |
| Stripe | unconfigured (historical smoke: 503) | Checkout/portal/webhook |

SQLite remains the code default when `ACCOUNTS_DATABASE_URL` is unset.

### Dual paths (do not confuse them)

- **Floor path:** session chat + session product. **Stateless API:** `/v1/factory/product/*` (entitlement-gated, unused by UI).
- **Draft:** LLM / golden Steward / keywords.
- **Build:** runner (prod default) / template (revert).
- **Blocks:** env/clone / `vendor_blocks_mirror`.
- **Legacy kit configurator:** upload → chain → `deploy.py` still mounted; Floor does not call `/deploy`.

### Tests and CI (this pin)

- **1469** tests collected under `backend/tests/` (`pytest.ini` `testpaths = tests`).
- Factory subset is large; keyed-path CI proves coder **wiring** with stub keys.
- Frontend: Vitest in CI (`frontend/src/__tests__/`, 8 files). Playwright specs present, not in `package.json` or CI.
- Single workflow `.github/workflows/ci.yml`: backend, Windows encoding, frontend, Docker. No smoke, no Playwright, no license scan, no lockfile install.
- Local `backend/.env` + `load_dotenv()` can make factory tests call a real LLM; `FACTORY_CODER_ENABLED=0` is the diagnostic (README is accurate here).

### Docs vs reality (short list)

| Doc | Reality |
| --- | --- |
| `render.yaml` as blueprint | Header: not applied; live Neon + email-verify behavior already diverged |
| `docs/runbook.md` `VITE_API_KEY` | Frontend does not use it; dangerous if revived |
| README session routers include “train” | No `train` router |
| README/AGENTS `:8000` vs `docker-compose.yml` `8001:8000` | Compose is the odd one out |
| `KNOWN_LIMITATIONS.md` “647 passed” | 1469 collected |
| `PRODUCTION_READINESS_AUDIT.md` NO-GO | Canonical 2026-08-17 GO; neither is this SHA |
| `REGISTERED_BUT_DEAD_AUDIT.md` CI on push; VITE_API_KEY bundled | CI is PR-only; key not referenced in `frontend/src` |
| README “certified Cerebrum-Blocks kernels” | Mirror can satisfy dual registry in CI |
| Generation “synchronous” in `KNOWN_LIMITATIONS.md` | Runner is background + poll (partially outdated) |

---

## Recommended fix order

1. **Legal:** LICENSE + counsel-ready Terms/Privacy, linked from the product (H1).
2. **Entitlement:** `require_entitled` on the Floor generate/chat path + tests (H2).
3. **Generate races:** single-flight, ledger lock, Steward copy-after-success, quota on success (H3).
4. **Docs/config:** kill `VITE_API_KEY`; make `render.yaml` match the dashboard or mark it archival (H4).
5. **Smoke:** post-deploy workflow at this SHA; refresh readiness docs (H5).
6. **Then M1–M8** (hostname leak, anonymous-dev production refuse, workbench path bind, encryption/ready, usage UPSERT, session rehydrate owner).

Do not flip `BILLING_ENFORCEMENT` until H2 and Stripe checkout are both true.

---

## Evidence from this run

- Secret scan: `NO SECRETS DETECTED ON NON-TEST PATHS.`
- Pytest collect: `1469 tests collected`.
- Live `/version`: `git_sha=7ea3c48a23967d488d043955a8a2d96721e285e2`, `env=production`, `sentry_configured=true`.
- Live `/health`: storage ok, redis ok, workbench/RE flags false.
- Live `/ready`: 200, `llm_configured=true`, backup ok 2026-08-28T03:00Z, Neon host as above.
- Live `/docs` and `/openapi.json`: 404.
- Live unauthenticated sessions/me: 401.
- Frontend: HTTP 200 at `https://www.cerebrum-dev.com/` (301 from apex).
