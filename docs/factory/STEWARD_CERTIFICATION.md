# Steward Factory Certification Report

**Product:** Cerebrum Steward — Remote Asset & Estate Operations Platform  
**Factory repo:** `bopoadz-del/CerebrumDev.ai`  
**Product repo:** `bopoadz-del/Projectsix` (private; FACTORY-GENERATED)  
**Live URL:** https://projectsix.onrender.com  
**Run log:** `docs/factory/certification-runs/steward-20260721T080317Z.log.jsonl`  
**Factory tip (pilot RAG):** merges of [#82](https://github.com/bopoadz-del/CerebrumDev.ai/pull/82) + [#84](https://github.com/bopoadz-del/CerebrumDev.ai/pull/84)  
**Sibling tip pushed:** `64618d7ea88429c2a7fe33c8f4ed610b6f27656f` (`main`, 2026-07-21)  
**Render service:** `srv-d9fl136rnols73c72nf0` (`projectsix`)  
**Render deploy:** `dep-d9fn427aqgkc739lecq0` — **live** on sibling `64618d7`  
**Postgres:** `dpg-d9fmrsnjqk9s73eik2b0-a` (`projectsix-db`, basic_256mb, oregon)

---

## Verdict

**FACTORY CERTIFIED — generation, sibling publish, live Render verify, pilot dual RAG**

The Factory built Steward end-to-end from the estate kit blueprint. Output was
pushed to the pre-existing private sibling `bopoadz-del/Projectsix` (no
`gh repo create`). Render web service is live.

**Pilot dual RAG (live):** `/v1/rag/*` serves **`fastembed:BAAI/bge-small-en-v1.5`**
with durable **Postgres JSONB** persistence (`postgres_jsonb_v1`), fail-closed
production flags, and semantic score floor `0.62` for honest insufficiency.
Live oracle **PASS 10/10** — see
`docs/factory/certification-runs/steward-live-oracle-20260721-pilot.md`.

No hand-patch of product code was performed; all product files came from Factory
regeneration.

---

## Timeline (UTC)

| Stage | Timestamp | Notes |
|-------|-----------|-------|
| Certification run start | `2026-07-21T08:03:17Z` | Phase 1 local generate |
| Dual RAG Factory upgrade | `#74` | Ingest + vector query (feature-hash demo) |
| Production packs templates | `#77` | `app/steward` Postgres/pgvector templates |
| Pilot persistent RAG + FastEmbed | `#82` merged ~`13:11Z` | Dual RAG Postgres + FastEmbed |
| Semantic insufficiency floor | `#84` merged ~`13:19Z` | `STEWARD_RAG_SEMANTIC_FLOOR=0.62` |
| Sibling push (pilot) | `64618d7` | `bopoadz-del/Projectsix` `main` |
| Render deploy live | `dep-d9fn427aqgkc739lecq0` | commit `64618d7` |
| Pilot live oracle | `2026-07-21T13:22:32Z` | **PASS 10/10** FastEmbed |

### Factory PRs

| PR | Result |
|----|--------|
| [#69](https://github.com/bopoadz-del/CerebrumDev.ai/pull/69)–[#74](https://github.com/bopoadz-del/CerebrumDev.ai/pull/74) | Estate kit → dual RAG demo |
| [#77](https://github.com/bopoadz-del/CerebrumDev.ai/pull/77) | Production RAG templates + feature-hash oracle |
| [#82](https://github.com/bopoadz-del/CerebrumDev.ai/pull/82) | Persistent dual RAG + FastEmbed |
| [#84](https://github.com/bopoadz-del/CerebrumDev.ai/pull/84) | FastEmbed insufficiency floor |

---

## Live oracle suite — PASS 10/10 (pilot path)

**URL:** https://projectsix.onrender.com  
**Ran (UTC):** 2026-07-21T13:22:32Z  
**Embedding used on live service:** `fastembed:BAAI/bge-small-en-v1.5`  
**Persistence:** `postgres_jsonb_v1` (Render Postgres)  
**Suite:** `steward_live_oracle_v1`  
**Results:** `docs/factory/certification-runs/steward-live-oracle-20260721-pilot.md`

Coverage: cross-layer isolation, property scoping, two insufficiency unknowns, citation lineage — all PASS on the FastEmbed + Postgres path.

---

## Live remote verification (pilot)

| Probe | Result |
|-------|--------|
| `GET /health` | **200** |
| `GET /v1/rag/dual` | **200** — FastEmbed + `postgres_jsonb_v1`; indices populated |
| Live oracle | **PASS 10/10** |

Notes:

- Free-tier cold starts still expected (web plan upgrade via API returned internal error).
- Full hybrid RRF + governed packs remain available under `/v1/steward/rag/*` (generated); pilot certifies `/v1/rag/*`.

---

## Gaps (not hidden)

| Gap | Blocks pilot? | Close path |
|-----|---------------|------------|
| Free-tier cold start | Ops friction | Paid starter instance |
| UI stubs | Yes for UI pilot | Thin ops shell or API-only scope |
| IoT / CMMS | Yes for ops-loop | Blocks connectors |
| Full `/v1/steward/rag` hybrid RRF live | No for fixture pilot | Wire when needed |

---

## Acceptance checklist

| Criterion | Status |
|-----------|--------|
| Estate kit in Factory | Done |
| Factory-generated Steward with DNA + Resident | Done |
| `bopoadz-del/Projectsix` on GitHub | **Done** (push-only; never `gh repo create`) |
| Deployed on Render | **Done** |
| Dual RAG with citations (live FastEmbed + Postgres) | **Done** |
| Live oracle 10/10 naming real embedding provider | **Done** |
| No hand-patches in product | Confirmed |

---

## Operator notes

```bash
# Regenerate (Factory machine)
cd backend && PYTHONPATH=. python -m app.factory.cli generate \
  --blueprint ../blueprints/steward/steward.v1.yaml \
  --out ../factory_outputs/Projectsix \
  --blocks-root "$CEREBRUM_BLOCKS_ROOT"

# Push only — never gh repo create (sibling already exists)
# Target: https://github.com/bopoadz-del/Projectsix
```

Repo description law: **FACTORY-GENERATED — do not hand-edit; changes go through the Factory.**
