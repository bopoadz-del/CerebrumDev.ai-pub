# Milestone 1 — Product DNA bundle

**Status:** shipped on branch `feat/product-dna` (awaiting review)  
**Gate:** `chore/factory-hygiene` merged; `factory_outputs/` gone from `master`.

## What shipped

Every Factory-generated product now gets a read-only **`/product-dna/`** bundle:

| File | Source |
|------|--------|
| `product_blueprint.yaml` | Blueprint model dump |
| `generation_manifest.json` | product_id, scenario, ui_modules, connectors, commits |
| `capability_resolution.json` | CapabilityPlanner plan |
| `source_provenance.json` | factory/blocks commits (+ empty `sources` until owned) |
| `block_lockfile.json` | Dual-registered pins |
| `architecture.json` | Derived vertical / modules / capabilities; `layers: []` |
| `entity_model.json` | Honest empty (`entities` / `relationships`) |
| `action_catalog.json` | Generated ACTION_CATALOG |
| `agent_catalog.json` | Hat manifests |
| `workflow_catalog.json` | Factory workflows |
| `security_policy.json` | human_authority + empty allowlists (M2 fills heal ops) |
| `deployment_topology.json` | Honest empty |
| `test_catalog.json` | Honest empty |
| `known_limitations.json` | Explicit limitation strings |
| `change_history.json` | Seeded from RE build event when present |

Plus `checksum_manifest.json` (`sha256`, `read_only: true`).

- Versioned JSON Schemas: `backend/app/product_dna/schemas/`
- Emitter: `backend/app/product_dna/emit.py` (wired from `ProductGenerator.generate` **after** actions/hats/workflows)
- Legacy thin DNA under `product-agent/product_dna/` remains for the RE scaffold (M2 will prefer `/product-dna/`)

## What remains

| Milestone | Scope |
|-----------|--------|
| **M2** | Resident Mode core (L1 observe, L2 allowlisted heal, injection guard); feature-flag `RESIDENT_ENGINEER_ENABLED=false` |
| **M3** | Signed change-request intake + dry-run queue |
| **M4** | Build Mode workbench (sandboxed Kimi Code) |

## Tests

See PR evidence: `backend/tests/product_dna/test_product_dna_bundle.py` — schema validation, Steward generate round-trip, checksum verify + tamper detection.
