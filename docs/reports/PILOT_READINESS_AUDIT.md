# CerebrumDev.ai — Pilot Readiness Audit

**Date (UTC):** 2026-07-21 (re-audit after pilot RAG ship)  
**Auditor scope:** Factory repo `bopoadz-del/CerebrumDev.ai` + live Steward product  
**Live URL:** https://projectsix.onrender.com  
**Product sibling:** https://github.com/bopoadz-del/Projectsix (`main` @ `64618d7`, FACTORY-GENERATED)  
**Factory tip:** merge of [#82](https://github.com/bopoadz-del/CerebrumDev.ai/pull/82) + [#84](https://github.com/bopoadz-del/CerebrumDev.ai/pull/84)  
**Oracle artifact:** [`docs/factory/certification-runs/steward-live-oracle-20260721-pilot.md`](../factory/certification-runs/steward-live-oracle-20260721-pilot.md)  
**Render:** `srv-d9fl136rnols73c72nf0` · deploy `dep-d9fn427aqgkc739lecq0` · Postgres `dpg-d9fmrsnjqk9s73eik2b0-a` (`projectsix-db`, basic_256mb)

---

## Verdict

### **PILOT READY** — API/demo pilot with durable semantic dual RAG

| Field | Value |
|-------|-------|
| **Verdict** | **PILOT READY** |
| **Confidence** | **86%** |
| **Ready for** | Owner-supervised estate dual-RAG pilot on fixture corpus; durable ingest; FastEmbed retrieval; cited Layer 1/2 isolation |
| **Not ready for** | Full ops SPA, real IoT/CMMS connectors, paid always-on SLA (web service still **free**), or commercial multi-tenant SSO/billing |

Factory → sibling `bopoadz-del/Projectsix` → Render is certified. Live `/v1/rag/*` now serves **`fastembed:BAAI/bge-small-en-v1.5`** with **Postgres JSONB persistence** (`postgres_jsonb_v1`). Live oracle **PASS 10/10** against that path (incl. insufficiency + property isolation).

---

## Executive scorecard

| Dimension | Score | Notes |
|-----------|-------|-------|
| 1. Factory path | **Pass** | Blueprint → plan → generate → dual registry; 0% hand-patch in product |
| 2. Steward product surfaces | **Partial** | Estate demo + durable dual RAG + Resident packaged; UI stubs; connectors `not_implemented` |
| 3. Live deploy | **Pass (pilot)** | Health 200; FastEmbed + Postgres; free-tier cold start remains |
| 4. Oracle / certification | **Pass** | 10/10 live oracle on FastEmbed + Postgres |
| 5. Tests / CI | **Pass** | Factory PRs #82 / #84 merged green |
| 6. Gaps / pilot risks | **Scoped** | UI stubs, connectors, free-tier cold start — accept in writing |

---

## Live evidence (2026-07-21T13:22Z)

| Probe | Result |
|-------|--------|
| `GET /health` | 200 `{ok:true, product_id:"projectsix", vertical:"estate"}` |
| `GET /v1/rag/dual` | `embedding_provider: fastembed:BAAI/bge-small-en-v1.5`; `persistence_adapter: postgres_jsonb_v1`; `persistent: true`; `semantic_score_floor: 0.62` |
| Live oracle | **PASS 10/10** |
| Sibling tip | `64618d7ea88429c2a7fe33c8f4ed610b6f27656f` on `bopoadz-del/Projectsix` |

### Factory PRs for this uplift

| PR | Result |
|----|--------|
| [#82](https://github.com/bopoadz-del/CerebrumDev.ai/pull/82) persistent dual RAG + FastEmbed | Merged green |
| [#84](https://github.com/bopoadz-del/CerebrumDev.ai/pull/84) FastEmbed semantic score floor (insufficiency) | Merged green |

---

## Go / no-go (updated)

### Go for **PILOT READY** when ALL hold — status

- [x] Factory regenerate path green; no hand-patch doctrine enforced
- [x] Live `/health` + estate demo + dual RAG with citations
- [x] Postgres provisioned; ingest survives restart (`STEWARD_REQUIRE_PERSISTENT_RAG=1`)
- [x] Live path uses `STEWARD_EMBED_BACKEND=fastembed` with `STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1`
- [x] Live oracle **10/10** naming actual `embedding_provider`
- [x] Connectors labeled `not_implemented`
- [x] CI green on Factory merges
- [ ] Paid web instance (starter) — **MCP/API plan upgrade blocked**; cold-start risk remains on free
- [ ] Minimum viable UI — deferred (API-only pilot acceptable if agreed)

### Remaining gaps (honest)

1. **Free-tier cold start** — web service still `plan=free`; first hit may take tens of seconds.
2. **UI stubs** — not an estate-ops SPA.
3. **IoT / CMMS** — `not_implemented` stubs only.
4. **Full hybrid RRF `/v1/steward/rag/*`** — generated; pilot dual RAG uses JSONB + in-process cosine on `/v1/rag/*` (sufficient for fixture pilot).
5. **Resident OFF** by default on live.

---

## Evidence index

| Item | Link / path |
|------|-------------|
| Pilot oracle 10/10 (FastEmbed) | `docs/factory/certification-runs/steward-live-oracle-20260721-pilot.md` |
| Steward certification | `docs/factory/STEWARD_CERTIFICATION.md` |
| Factory persistent RAG | https://github.com/bopoadz-del/CerebrumDev.ai/pull/82 |
| FastEmbed insufficiency floor | https://github.com/bopoadz-del/CerebrumDev.ai/pull/84 |
| Product sibling | https://github.com/bopoadz-del/Projectsix |
| Live service | https://projectsix.onrender.com |

---

*Re-audited 2026-07-21 after live FastEmbed + Postgres dual RAG. Verdict upgraded from CONDITIONAL (68%) to **PILOT READY** (86%) for an API/demo pilot with written scope on UI/connectors/cold-start.*
