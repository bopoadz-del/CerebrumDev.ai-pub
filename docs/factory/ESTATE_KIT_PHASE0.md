# Private Estate Operations kit (Factory Phase 0)

**Branch:** `feat/steward-estate-kit`  
**Purpose:** Expand the Steward golden blueprint into the full estate kit required
for the Factory certification run — still Factory-side only; no product hand-patches.

## What shipped

| Surface | Change |
|---------|--------|
| `blueprints/steward/steward.v1.yaml` | Full capability set: registry (+db/storage/validation), House Manual SOP, vendor/budget, preventive maintenance, staff scheduling, principal dashboard, onboarding, dual RAG L1/L2 |
| `factory_blocks.json` | Dual-register platform blocks already present in Cerebrum-Blocks (`document_engine`, `knowledge`, `vector_search`, `formula_executor`, `notification`, `queue`, `team`, `dashboard`, `analytics`, `spec_analyzer`, `recommendation_template`, `database`, `storage`, `validation`) |
| Kit manifest | `private_estate_operations` → **1.1.0** with dual_rag + placeholder connectors |
| Demo fixtures | `backend/app/factory/kits/private_estate_operations/fixtures/demo_estate.json` |
| Generator | Estate vertical emits fixtures, dual RAG API, SPA deep links + cold-start hints |

## Doctrine check

- Blocks flow one direction: shelf entries point at Store blocks; Factory never writes to Cerebrum-Blocks.
- IoT / smart-home connectors remain **honest placeholders** (`not_implemented`).
- Product code for demo/RAG is **Factory-generated** via `ProductGenerator._write_estate_kit_surfaces`.

## Tests

`backend/tests/factory/test_estate_kit_phase0.py` — dual registration, blueprint coverage, planner, generate + RAG citations.

## Next

Phase 1 certification run: generate → package → deploy `bopoadz-del/Projectsix` →
`docs/factory/STEWARD_CERTIFICATION.md`.
