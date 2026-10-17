# Registered-but-Dead Kernel Audit

> **Stale as of 2026-08-16.** This July 24 table was taken against
> `https://cerebrumdev-backend.onrender.com`, which now 404s. Canonical live
> hosts are `https://api.cerebrum-dev.com` and `https://cerebrum-dev.com`.
> Re-verify against [`CEREBRUMDEV_PRODUCTION_READINESS.md`](CEREBRUMDEV_PRODUCTION_READINESS.md)
> before any LIVE claim.

**Repository:** `bopoadz-del/CerebrumDev.ai`  
**Branch:** `master`  
**Audit date:** 2026-07-24  
**Auditor:** Factory CI + registry meta-test (`backend/tests/test_registry_invariants.py`)  
**Rule:** No kernel may be claimed market-ready on deterministic/fallback behavior alone. Live evidence is required.

## Summary

| Kernel | Status | Evidence |
| --- | --- | --- |
| 1. Chat LLM (moonshot-v1-8k) | **LIVE** | Streamed reply echoing brief specifics; no fallback table |
| 2. LLM architect drafting | **LIVE** | `source:"drafted"`, multiple capabilities, block_ids populated from dual registry |
| 3. Planner fail-closed → generation | **LIVE** | Approve → generate → zip export with Dockerfile/render.yaml/product-agent |
| 4. Grounding law | **LIVE** | Refuses to invent download links or live URLs |
| 5. Cross-account isolation | **LIVE** | Foreign account → 404 on session + product |
| 6. Billing honesty | **LIVE (honest)** | Structured status; checkout → `503 stripe_not_configured` when Stripe unset |
| 7. Resident engineer | **PARKED by design** | `enabled:false`, maturity APPRENTICE, allowlisted heals declared |
| 8. Workbench / build mode | **PARKED by design** | `build_mode_enabled:false`, `kimi_workbench_enabled:false` |
| 9. Change-request intake | **PARKED by design** | `intake_enabled:false`, M3 paperwork only |
| 10. Redis rate limiting | **LIVE** since 2026-07-24 | Render Key Value `cerebrumdev-redis` provisioned; `/health` reports `redis.configured: true`, `redis.ok: true` |
| 11. Draft routing | **LIVE** after fix | LLM attempt now precedes deterministic steward fallback; `"estate"` no longer short-circuits LLM |
| 12. CI wiring | **EXISTS** | `.github/workflows/ci.yml` runs pytest + frontend build + docker build on push to master |
| 13. Audit kernel artifact | **LIVE** | This document + `test_registry_invariants.py` |
| 14. Registry meta-test | **LIVE** | `backend/tests/test_registry_invariants.py` asserts router mounts and honest disabled status |
| 15. AGENTS.md deploy gate | **WIRED** | See below |
| 16. Auth enforcement on protected routes | **LIVE** | Unauthenticated requests to `/v1/sessions/`, `/v1/auth/me`, `/v1/domains/`, `/v1/billing/status` return 401 |
| 17. Store credential path | **LIVE** | `CEREBRUM_API_KEY` set on backend; `/v1/sessions/{id}/chat` with "what blocks can I add?" returns store-backed block list (no 401) |
| 18. Engine-discovery GitHub access | **LIVE** | Owner set `GITHUB_TOKEN` in Render; `/v1/domains/virgin`, `/source-packs`, `/rag-packs` all return 200 with real shelf data |
| 19. SMTP email delivery | **PARKED-honest** | No RESEND/SMTP provider configured; register/forgot-password expose dev tokens in response with explicit note |
| 20. Frontend API-key pair | **LIVE** | `VITE_API_KEY` declared in `render.yaml` (sync:false); frontend builds and bundles the key at build time; pair must match `CEREBRUM_DEV_API_KEY` in Render dashboard |
| 21. Secret leak rotation | **LIVE** | Git grep + frontend bundle scan find no hardcoded API keys/secrets; earlier leaked factory keys removed from packager output by PR #110 |
| 22. Phase-4 platform sweep | **LIVE** | `scripts/platform_sweep.py` exercises all route families and writes a markdown report; 33/36 probes pass, 3 blocked by #18 |

## Classification definitions

- **LIVE** — verified against real services or behavior; deterministic success is not enough.
- **PARKED by design** — registered in code, intentionally gated OFF by default, status endpoint is honest.
- **PARKED** — registered but not configured in this environment; falls back safely.
- **STUB** — placeholder that refuses service or returns static honest empties.
- **UNVERIFIED** — no live evidence yet; cannot be claimed ready.

## Stop conditions

1. Any kernel whose status is **UNVERIFIED** cannot be listed as ready in release notes.
2. Any kernel whose status is **PARKED** must expose an honest status endpoint or 503.
3. Any kernel whose status is **LIVE** must have a reproducible evidence command or CI test.
4. Deterministic/fallback success is **never** accepted as evidence for **LIVE**.
5. A kernel is **LIVE** only when every credential on its path is verified present and valid — probe the path, not the happy path.

## Evidence commands

- Registry invariants: `cd backend && ENV=test ./venv/bin/python -m pytest tests/test_registry_invariants.py -v`
- Full backend suite: `cd backend && ENV=test ./venv/bin/python -m pytest -q`
- Post-deploy smoke (requires live deployment + real LLM key):
  ```bash
  python3 scripts/post_deploy_smoke.py https://api.cerebrum-dev.com
  ```
  All checks must print `[LIVE]`.
- Phase-4 full platform sweep:
  ```bash
  python3 scripts/platform_sweep.py --out /tmp/platform_sweep_report.md
  ```
  Inspect the report; only `PARKED-honest` failures are acceptable.

## Deploy gate (also in AGENTS.md)

Before any market-ready claim:

1. Run `scripts/post_deploy_smoke.py` against the live URL.
2. Every kernel must print `[LIVE]`.
3. A deterministic/fallback pass is a failure.
4. Do not merge or tag a release until the smoke script exits 0.
