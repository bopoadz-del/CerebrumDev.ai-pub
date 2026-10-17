# Honest stubs & capability matrix (Factory UI)

Last audited against live `cerebrumdev-backend` / `cerebrumdev-frontend` (API matrix + Playwright).

## Working end-to-end

| Flow | Notes |
|------|--------|
| Session create / load | Real |
| Domain list + config save | Real |
| Kit chat (SSE) | Real when LLM configured (currently Ollama cloud until `KIMI_API_KEY` is set) |
| Chain preview / approve | Real after chat proposes a chain |
| Upload + index | Real |
| Deploy package (`platform` / `edge` / `cloud` packager) | Real; download uses authenticated API fetch |
| Design product draft → plan → approve → generate | Real Floor path (`/v1/sessions/{id}/product/*`); Steward uses golden blueprint |

## Optional / gated

| Flow | Honest state |
|------|----------------|
| **Fine-tune** | Removed. Platforms deploy without a fine-tuning step. |
| **Google Drive connector** | API real when `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` set; otherwise status shows **not configured** (UI panel). Connect returns 503 until env is set. |
| **Cloud Render deploy** | Packager real; live Render ship needs Render credentials on the service. |

## Generated product honest stubs

Emitted by Factory generator into product repos (e.g. Steward):

| Artifact | State |
|----------|--------|
| `app/connectors/*_stub.py` | `STATUS = "not_implemented"` — intentional |
| Resident Engineer | APPRENTICE scaffold; dual cert **pending** |
| Generated UI modules | Template shells bound to capabilities/health — not full product UX |
| Estate Blocks (registry/maintenance/…) | Platform block adapters; some are thin until Blocks deepens them |
| `capture` / `audit` | Real Cerebrum-Blocks adapters when Blocks root or vendor mirror is current |

## Intentionally not in Factory UI

| Capability | Where it lives |
|------------|----------------|
| Domains RAG dry-run / ingestion APIs | `/v1/domains/...` API only |
| Standalone `/v1/factory/product/*` | Removed — Floor session product is the only HTTP surface |
| Orphan `ChatChainGenerator.tsx` | Unused duplicate — do not mount |

## Role reminder

- **Kimi API** — Product Architect (needs `KIMI_API_KEY` for Factory architect path)
- **Kimi Code** — Factory Engineer + Block Store Manager
- **CerebrumDev.ai** — governance, dual registry, certify, regenerate
