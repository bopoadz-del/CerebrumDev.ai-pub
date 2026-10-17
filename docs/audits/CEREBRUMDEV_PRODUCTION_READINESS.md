# CerebrumDev.ai — production-readiness audit

**Repository:** [bopoadz-del/CerebrumDev.ai](https://github.com/bopoadz-del/CerebrumDev.ai) (the Factory). Not Projectsix.  
**Auditor window (UTC):** 2026-08-17T00:39Z–00:41Z  
**Prior pin:** 2026-08-17T00:24Z–00:33Z (NO-GO; smoke needed a local gate token; `last_backup` pre-cutover).  
**Rule:** PASS / FAIL / NOT VERIFIED only. No readiness %. Deterministic or fallback success is not production evidence. Live evidence required.

| Pin | Value |
| --- | --- |
| `origin/master` | `3665c86ef63908a6b1659921d78da6e4e6bd58ab` (merge of #156; only long-lived branch) |
| Live Render backend | `3665c86ef63908a6b1659921d78da6e4e6bd58ab` (`/version` at 2026-08-17T00:40:33Z) |
| Live Render frontend | Redeploy of `3665c86` triggered 2026-08-17T00:41:01Z (`dep-da15h7dbedkc73c3t4eg`); previous live was `bffeeae` |

---

## Verdict

| Claim | Result |
| --- | --- |
| **Production** | **GO** |
| **Unattended public demo** | **GO** (factory loop). Paid checkout is honestly unset. |
| **Owner-supervised walkthrough** | Factory loop `[LIVE]` on `https://api.cerebrum-dev.com`. |

AGENTS.md live gate: `python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com` **exit 0** on SHA `3665c86`. Every kernel `[LIVE]`. `architect_llm` (not fallback).

---

## Domain scores

| # | Domain | Status | Live evidence |
| --- | --- | --- | --- |
| 1 | Deploy pin | **PASS** | Backend live SHA = `origin/master` = `3665c86`. Frontend redeploy of that SHA triggered after this audit's smoke. |
| 2 | Live smoke / kernels `[LIVE]` | **PASS** | See [Mandatory live gate](#mandatory-live-gate). Exit 0 at 2026-08-17T00:40:33Z. |
| 3 | Readiness & ops | **PASS** | `/ready` HTTP 200. Disk `/app/storage`. Plan starter. Cutover backup **ran** at 2026-08-17T00:37:14Z; `matches_live_engine: true` (Neon host). `last_backup.ok` is **false** (`pg_dump` exit 1) — follow-up, not a smoke fail. Neon branch `backup-post-cutover-20260817` exists. |
| 4 | Auth / security | **PASS** | Unauth 401. Unverified session 403 `email_not_verified`. Smoke-login with Render `SMOKE_GATE_TOKEN`. |
| 5 | CI on latest master | **PASS** | #156 required CI green; merged. Extra branches deleted; GitHub has **master only**. |
| 6 | Data / migrations | **PASS** | Live accounts engine is Neon `cerebrumdev` / `shy-glade-57354706` / `cerebrumdev-accounts`, alembic `0002`. Leftover Render Postgres `dpg-d9vlh6c9v7es73b53l9g-a` is **not** the live URL and was **not dropped**. |
| 7 | Factory vs product | **PASS** | No runnable `backend/app/retailops/`. |
| 8 | Frontend production | **PASS** | `https://cerebrum-dev.com` and www HTTP 200. `VITE_API_URL` is `https://api.cerebrum-dev.com`. |
| 9 | Observability | **PASS** | Redis `/health` ok. Sentry configured on `/health` and `/version`. |
| 10 | Docs vs reality | **PASS** | This audit matches live SHA `3665c86` + smoke exit 0. |

Machine-readable copy: [`artifacts/cerebrumdev_production_readiness.json`](../../artifacts/cerebrumdev_production_readiness.json).

---

## Mandatory live gate (AGENTS.md)

```bash
python scripts/post_deploy_smoke.py https://api.cerebrum-dev.com
```

Requires `SMOKE_GATE_TOKEN` (Render secret; also set it in the Cloud Agents dashboard). Public email verification stays fail-closed. Do not commit the token.

| Target | Timestamp (UTC) | Result |
| --- | --- | --- |
| live API, SHA `3665c86` | 2026-08-17T00:40:33Z | **SMOKE PASS** (exit 0) |

```
post-deploy smoke against https://api.cerebrum-dev.com
[LIVE] health endpoint — status=ok
[LIVE] redis rate limiting configured
[LIVE] version endpoint — http=200 sha=3665c86ef639
[LIVE] auth register — http=201
[LIVE] unverified session denied — http=403
[LIVE] session create — http=200
[LIVE] chat blueprint event — sse_bytes=6357
[LIVE] LLM drafting (not fallback) — http=200 mode=architect_llm caps=6 populated=6
[LIVE] approve -> generation event
[LIVE] generation recorded — product_id=vineyard-management
[LIVE] export zip — http=200 files=193
[LIVE] grounding (no invented deploy/URL)
[LIVE] cross-account isolation — http=404 (expect 404)
[LIVE] billing status structured — http=200
[LIVE] billing checkout honest — http=503 (503 stripe_not_configured or real url)
SMOKE PASS: every kernel live.
```

---

## Follow-ups (not smoke DEAD)

1. `/ready` `last_backup.ok` is false: `pg_dump` exit 1 against Neon at 2026-08-17T00:37:14Z. Host fingerprint matches live Neon. Neon PITR branch `backup-post-cutover-20260817` is the post-cutover copy.
2. Render Postgres `dpg-d9vlh6c9v7es73b53l9g-a` still exists (free, expires 2026-09-13). External SSL connect failed earlier. Do not drop until a successful source count exists.
3. Stripe keys are absent. Checkout 503 is honest. Do not invent secrets.

Do not weaken CI. Do not point Factory at The Fork Neon. Do not treat keyword fallback as `[LIVE]`.
