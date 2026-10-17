# What a generated platform contains (buyer expectations)

Every platform the factory generates is a **runnable backend platform** with
real wiring — not a mock — plus honest placeholders where bespoke product work
continues. This document is the contract.

## Always included

- `app/` — FastAPI application with the platform's real endpoints and the
  resident-engineer surface.
- `vendor/blocks/<id>/` — the certified Cerebrum-Blocks kernels the blueprint
  selected (REUSE capabilities), vendored byte-exact from the store.
- `product-agent/` — resident engineer charter, authority policy, product DNA,
  repair catalog.
- `tests/`, `scripts/`, `docs/` — release-gate script, smoke probes, operator
  docs.
- `Dockerfile`, `render.yaml`, `docker-compose.yml`, `Procfile`,
  `requirements.txt`, `.env.example` — deploy anywhere; Render blueprint
  included.
- `README.md`, `factory_plan.json` — what was planned (REUSE/GENERATE per
  capability) and the inputs hash for provenance.

## Honest placeholders (by design)

- **GENERATE capabilities** are scaffolded with a working kernel/template
  surface and marked in `factory_plan.json` — they are the parts the factory
  builds bespoke *next* (with the buyer, in follow-on sessions), not shipped
  as finished bespoke features in the first export.
- **Frontend** is a functional stub UI for operating the API, not a finished
  product design.
- **Connectors** (e.g. Google Drive) activate when the buyer provides their
  own OAuth credentials via the environment — the code paths are real,
  credentials are never baked in.
- **CORS** ships locked to the service's own origin plus localhost
  (`https://<service>.onrender.com,http://localhost:5173`) — never `*`.
  Buyers set `CORS_ORIGINS` for custom domains.

## What "market ready" means here

A generated platform is a production-shaped starting point: it boots, passes
its own release gate, enforces auth, and isolates tenants. Turning GENERATE
capabilities into bespoke features is the factory's continuing service — not
a gap hidden from the buyer.
