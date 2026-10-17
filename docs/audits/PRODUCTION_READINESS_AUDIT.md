# CerebrumDev.ai — production-readiness audit (index)

**Canonical report:** [`CEREBRUMDEV_PRODUCTION_READINESS.md`](./CEREBRUMDEV_PRODUCTION_READINESS.md)  
**JSON:** [`artifacts/cerebrumdev_production_readiness.json`](../../artifacts/cerebrumdev_production_readiness.json)

**Audit window (UTC):** 2026-08-16T21:37Z–21:41Z  
**SHA:** `442b62f5d9a14b9247722320aadbd21ff6be0638` (local = `origin/master` = live Render)

| Claim | Result |
| --- | --- |
| Production | **NO-GO** |
| Unattended public demo | **NO-GO** |

This file is a pointer. Do not treat the short table below as a substitute for the live probe log in the canonical report.

| Domain | Status |
| --- | --- |
| 1. Deploy pin | **PASS** |
| 2. Live smoke / kernels | **FAIL** |
| 3. Readiness & ops | **FAIL** |
| 4. Auth / security | **PASS** |
| 5. CI on latest master | **PASS** |
| 6. Data / migrations | **FAIL** |
| 7. Factory vs product | **PASS** |
| 8. Frontend production | **PASS** |
| 9. Observability / safe errors | **FAIL** |
| 10. Docs vs reality | **FAIL** |
