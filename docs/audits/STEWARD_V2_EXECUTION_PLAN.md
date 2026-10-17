# Steward V2 Execution Plan

**Status:** Post-audit implementation roadmap (not started)  
**Prerequisite:** [`STEWARD_V2_AGENT_AUDIT.md`](STEWARD_V2_AGENT_AUDIT.md) consolidated — guarded pilot **NO-GO**  
**Branch:** `feat/steward-pilot-certification-v2` (Factory)  
**Target generated branch:** `factory/steward-pilot-certification-v2` (Projectsix)  
**Blocks branch (if needed):** `feat/steward-store-contracts-v2`

---

## Delivery rules

1. One substantial Factory PR on `feat/steward-pilot-certification-v2`.
2. At most one Blocks PR if a genuine reusable Store capability is missing.
3. One generated Steward PR or deterministic commit from Factory regen.
4. Local validation + `scripts/steward_gate_v2.py` before any GitHub Actions push.
5. No hand-patches to durable product behaviour in `Projectsix`.

---

## Ordered work phases

### Phase 0 — Gate scaffolding (Factory)

| Step | Work | Owner | Output |
|------|------|-------|--------|
| 0.1 | Add `scripts/steward_gate_v2.py` emitting PASS/FAIL/NOT VERIFIED per mandatory gate | Factory | Gate script |
| 0.2 | Add `scripts/steward_oracle_v2.py` + `docs/STEWARD_ORACLE_V2.md` skeleton | Factory | Oracle V2 runner |
| 0.3 | Add `artifacts/steward_factory_determinism.json` generator to CLI or script | Factory | Determinism artifact |

**Exit:** Gate script runs locally (mostly NOT VERIFIED/FAIL baseline).

---

### Phase 1 — Security foundation (Factory → regen)

| Step | Work | Owner | Blocks? |
|------|------|-------|---------|
| 1.1 | Pilot auth: `PilotPrincipal`, `PilotAccessToken`, `EstateAccess`, bearer middleware | Factory | No |
| 1.2 | Remove `_scope()` defaults; implement `authorize_estate()` | Factory | No |
| 1.3 | Safe errors: replace `detail=str(exc)`; request ID header | Factory | No |
| 1.4 | `/health`, `/ready`, `/version` with active checks | Factory | No |
| 1.5 | Fail-closed startup (no silent Resident import swallow) | Factory | No |

**Tests:** auth unit tests; cross-estate denial; ready false when bypass on.

**Exit:** Gates G2, G3, G4 partial; G12 partial.

---

### Phase 2 — Canonical RAG (Factory → regen)

| Step | Work | Owner | Blocks? |
|------|------|-------|---------|
| 2.1 | Canonical runtime: `app.steward` only in production | Factory | No |
| 2.2 | Disable or auth-gate `/v1/rag/*` demo routes in production profile | Factory | No |
| 2.3 | Unified insufficiency policy (semantic + lexical + RRF floors) | Factory | No |
| 2.4 | Calibrate → `artifacts/steward_retrieval_calibration.json` | Factory | No |
| 2.5 | Authenticated ingest; Layer 1 curator role | Factory | No |

**Tests:** extend `backend/tests/factory/test_dual_rag_persistence.py`; steward hybrid retrieval tests.

**Exit:** Gate G6; retrieval domain moves toward PASS.

---

### Phase 3 — Database and audit (Factory → regen)

| Step | Work | Owner |
|------|------|-------|
| 3.1 | Replace `create_all` migration with explicit Alembic DDL | Factory |
| 3.2 | Remove/protect `POST /v1/steward/admin/migrate` and `reset_embedder` | Factory |
| 3.3 | PostgreSQL `AuditEvent` append-only ledger | Factory |
| 3.4 | Migration head exposed on `/ready` | Factory |

**Exit:** Gate G5; audit domain FAIL → PASS.

---

### Phase 4 — Domain model and money (Factory → regen)

| Step | Work | Owner | Blocks? |
|------|------|-------|---------|
| 4.1 | Populate `product-dna/entity_model.json` | Factory | No |
| 4.2 | Float → NUMERIC(20,4) + Decimal API serialization | Factory | Maybe `formula_executor` if shared |
| 4.3 | Human approval model for budget/maintenance/staff/evidence | Factory | No |

**Exit:** Gates G11; domain 13–14 PASS.

---

### Phase 5 — Store, agents, workflows (Factory + Blocks if needed)

| Step | Work | Owner |
|------|------|-------|
| 5.1 | Audit block pins vs real execution; fix COMPOSE/REUSE templates | Factory |
| 5.2 | If block runtime missing: implement in Cerebrum-Blocks, pin, regen | Blocks + Factory |
| 5.3 | Agent runtime certification harness → `artifacts/steward_agent_runtime_certification.json` | Factory |
| 5.4 | Workflow business oracles (6 workflows) | Factory |
| 5.5 | Enforce `human_authority_gate` with persisted approvals | Factory |

**Exit:** Gates G7, G8, G9.

---

### Phase 6 — Resident Engineer (Factory → regen)

| Step | Work | Owner |
|------|------|-------|
| 6.1 | Classify heals REAL/SIMULATED/UNAVAILABLE | Factory |
| 6.2 | `HealApproval` model; auth on observe/diagnose/heal | Factory |
| 6.3 | Simulated heals return `executed=false, simulation=true` | Factory |
| 6.4 | Maturity evidence → BUILD_OBSERVER before any heal enablement | Factory |

**Exit:** Gate G10; maturity may reach BUILD_OBSERVER (not RESIDENT_READY in first PR).

---

### Phase 7 — Oracle V2 and determinism (Factory)

| Step | Work | Owner |
|------|------|-------|
| 7.1 | Implement suites A–R per v2 charter | Factory |
| 7.2 | Double-generation proof | Factory |
| 7.3 | Tier CI: PR = focused gates; main/manual = full oracle + docker readiness | Factory |

**Exit:** Gates G1, G13; CI domain PASS.

---

### Phase 8 — Documentation honesty (Factory)

| Step | Work |
|------|------|
| 8.1 | Update certification docs — remove readiness percentages |
| 8.2 | Document pilot guardrails, connector placeholders, Resident APPRENTICE |
| 8.3 | Supersede v1 PILOT READY language with v2 gate table |

**Exit:** Gate G14 PASS.

---

### Phase 9 — Regenerate, deploy, verify

```bash
cd backend && PYTHONPATH=. python -m app.factory.cli generate \
  --blueprint ../blueprints/steward/steward.v1.yaml \
  --out ../factory_outputs/Projectsix \
  --blocks-root "$CEREBRUM_BLOCKS_ROOT"
```

| Step | Action |
|------|--------|
| 9.1 | Push to `factory/steward-pilot-certification-v2` on Projectsix |
| 9.2 | Deploy Render from merged SHA |
| 9.3 | Run Oracle V2 against live URL |
| 9.4 | Re-run consolidated audit; seek guarded pilot GO only if all mandatory gates PASS |

---

## PR sequencing

| PR | Repo | Scope |
|----|------|-------|
| PR-1 | CerebrumDev.ai | Phases 0–8 (single coherent Factory PR) |
| PR-2 (optional) | Cerebrum-Blocks | Only if Phase 5.2 triggered |
| PR-3 | Projectsix | Factory-generated output only |

---

## Explicit non-goals (this program)

- Production certification
- Live CMMS / IoT / smart-home / payment connectors
- RESIDENT_READY maturity in first implementation tranche
- Hand-editing generated Steward source for durable fixes

---

## Success criteria for guarded pilot GO

All 14 mandatory gates in [`STEWARD_V2_AGENT_AUDIT.md`](STEWARD_V2_AGENT_AUDIT.md) report **PASS**; Oracle V2 reports no mandatory **FAIL**; determinism artifact clean; `/ready` true on deployed SHA matching provenance pin.
