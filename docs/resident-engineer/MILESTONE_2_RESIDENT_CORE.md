# Milestone 2 — Resident Engineer core (Resident Mode)

**Branch:** `feat/resident-engineer-core`  
**Flag:** `RESIDENT_ENGINEER_ENABLED=false` (default)

## What shipped

| Surface | Detail |
|---------|--------|
| `backend/app/resident_engineer/` | Factory-side Resident Mode module |
| DNA loader | Prefers `/product-dna/`, verifies checksums, sanitizes untrusted text |
| L1 Observe | Health probe, log/error summary, block lockfile vs Store (advisory), anomaly flags |
| L2 Heal | Allowlisted only: `retry_ingestion`, `restart_worker`, `rebuild_index`, `restore_config_default` — confirmation-gated, pre/post checks, append-only audit, rollback on failed post-check. **No shell.** |
| L3–L5 | `draft_change_request` only (no execution) |
| Injection guard | Strips instruction-like patterns from DNA/log text |
| Shipped package | Generator injects `app/resident_engineer/` + repair catalog into products |
| API | `/v1/resident/status` (always), `/observe` `/diagnose` `/heal` `/draft-change-request` (flag-gated) |
| DNA | `security_policy.json.allowlisted_heal_actions` filled at emit |

## Deploy panel (Factory UI)

Right-side **Phase 5: Ship / Deploy** now shows Live Factory frontend/backend links so you can open the running UI while packaging. Google Drive optional; Redis optional via `REDIS_URL`.

## Redis

Not required for M2. When you want multi-worker session fan-out / queue later:

1. Provision Render Key Value (Redis-compatible)
2. Set `REDIS_URL` on the backend service
3. `/health` reports `redis.configured` / `redis.ok` (needs `redis` Python package installed to ping)

## What remains

- **M3** Signed change-request intake + dry-run queue + advisory Store upgrade compare
- **M4** Build Mode workbench (sandboxed Kimi Code)

## Tests

`backend/tests/resident_engineer/` — allowlist, audit immutability, injection guard, L2 rollback, flag default off, DNA inject.
