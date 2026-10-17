# AGENTS.md

## Platform build doctrine — read GATES.md first

Before building a platform product (a repo assembled from Cerebrum-Blocks)
— or reviewing such a PR — read [GATES.md](GATES.md) in this repo. This is
binding on every writer, human or AI, including work in external product
repos.

- A platform build must pass gates G0–G7 before it is pushed.
- A platform PR must map its acceptance criteria to GATES.md gates.
- A gate failure the brief never defined means the GATE is wrong, not the
  product: the gate goes advisory immediately, with the reason in the
  ledger, and no patch is written. A gate exists only if a brief can turn
  it on. (The build runner enforces this: each round's failures are
  classified brief-defined vs factory-invented before any writer dispatch,
  and only brief-defined failures reach the writer.)
- Verify any product repo with
  `python scripts/check_platform_gates.py --repo <path>`.

## Cursor Cloud specific instructions

### Committed cloud environment (versioned)

- Cloud agents use [`.cursor/environment.json`](.cursor/environment.json) (Dockerfile build + idempotent `install` + multitasking `terminals` for backend/frontend).
- Base image: [`.cursor/Dockerfile`](.cursor/Dockerfile) (Ubuntu 24.04, Python 3 + Node 20 + build tools). Do not `COPY` the project into that image.
- To reuse across other repos: copy `docs/cursor-cloud/reusable-base/` into that repo's `.cursor/` and edit only `install` / `terminals`. See [`docs/cursor-cloud/REUSE.md`](docs/cursor-cloud/REUSE.md).

### Local dev environment (non-obvious)

- Backend deps live in a venv at `backend/venv` (Python 3.12 on the VM works; CI/Docker pin 3.11). Run the API in dev with `cd backend && ALLOW_ANONYMOUS_DEV=1 ENV=dev ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`. Health/readiness: `GET /health`, `GET /ready`.
- Architect LLM drafting and the coding agent turn on automatically when `OPENROUTER_API_KEY` (HTTP chat / architect) or a Cursor BA key (`CURSOR_API_KEY` / `CURSOR_AGENT_API_KEY` / `FACTORY_CURSOR_API_KEY`) is set (explicit `ARCHITECT_LLM_DRAFTING_ENABLED=0` still wins). Put those keys in the Cloud Agents dashboard secrets — they are account-wide; do not commit them. Without a key the Floor labels **template fallback** and Your Platforms shows **0 agent-written** artifacts. Floor actions are typed (`backend/app/factory/floor_actions.py`): the Approve, Continue, Draft and feature-list controls send an explicit `action` (+ `value`) with the chat request, and only the typed `approve` / `continue` / `run_pilot` action starts or resumes the coding agent (the Floor then shows the coder takeover panel). Free-typed text goes to the Floor chat LLM, whose answer is a typed decision that never starts a build by itself; with no chat LLM the Floor points the user at its controls. Nothing reads the user's words to choose an action, and the hardwiring gate scans the routers and chat-handling modules for it.
- Use port **8000** for the backend in dev (not 8001). Vite proxies `/v1` to `http://localhost:8000` (`frontend/vite.config.ts`); the `8001` in README/`docker-compose.yml` is only the Docker port mapping.
- Frontend: `cd frontend && npm run dev -- --host` (Vite on 5173). The app auto-creates a session on load. Lint/build/test commands are in `frontend/package.json` and `.github/workflows/ci.yml`.
- The Cerebrum-Blocks domain store is an **optional external service** (`CEREBRUM_API_URL`). Without it, `/v1/domains/*` returns 503 and the UI shows a "Domain store unreachable" toast — this is expected in dev. The Factory Floor (`/v1/sessions/{id}/product/draft` plus session chat) works fully offline from golden blueprints in `blueprints/`.
- Backend tests: `cd backend && ENV=test ./venv/bin/python -m pytest` (see `pytest.ini`). Factory subset: `./venv/bin/python -m pytest tests/factory`.

### PR babysit / auto-merge (owner rule)

For Resident Engineer and related Factory milestone PRs on this repo:

1. Babysit CI and triage review comments until the PR is merge-ready.
2. When **all required checks are green**, the PR is **MERGEABLE/CLEAN**, and
   blocking comments are addressed (or validly declined), **merge immediately** —
   do **not** wait for an extra human review round.
3. Never force-push. Never weaken CI workflows just to pass.

See also `docs/resident-engineer/STANDING_ORDERS.md`.

### CerebrumDev.ai is the Factory

This repository is the **product-generation factory**. It is not a RetailOps or
Steward runtime host.

- Configurator / legacy stack: `backend/app/main.py` (ChromaDB sessions, packager).
- Product-kernel + blueprint generation live under `backend/app/factory/` (see
  Factory-first Steward plan).
- Generated products are separate repositories (e.g.
  `bopoadz-del/RetailOpsReference`, future `Projectsix`).

### Provenance: RetailClient was Factory-driven

Do **not** reintroduce a runnable `backend/app/retailops/` tree here.

Proof that RetailClient / RetailOps was driven through this Factory is retained in:

- [`docs/provenance/retailclient-retailops/FACTORY_DRIVEN_PROOF.md`](docs/provenance/retailclient-retailops/FACTORY_DRIVEN_PROOF.md)

Live RetailOps kernel: https://github.com/bopoadz-del/RetailOpsReference

### Tests

Legacy configurator: `python -m pytest` from `backend/` (no `tests/retailops`).

Factory / product-generation tests: `python -m pytest tests/factory` from
`backend/` when present.

### Blocks dual-registration

Every block used to generate a product must be registered in **both**:

1. Cerebrum-Blocks (`block_registry/` + kit shelf)
2. CerebrumDev.ai Factory shelf/registry (`backend/app/factory/block_registry/` or
   equivalent shelf consumer)

### Before creating a module

Grep this repo's `master` **and** sibling repos (Cerebrum-Blocks, The_Fork)
before adding a new Python module. Consume the existing module, or dual-register
it with a drift test. Never re-implement a module that already exists under
another name. Factory `build/root_cause.py` (lane-authority map) is not
Cerebrum `healing/root_cause.py`; do not copy one over the other.

### Stage evidence never ships inside feature PRs

Do not commit `build/stages/*.json` (or reread twins) in a feature PR. Stage
evidence is regenerable; emit it in a later single run at final HEAD after the
code merge. Closed #203 was rejected for ~5k hand-committed regenerable
evidence plus a second S4 filename. Canonical S4 evidence is
`S4_ship_kernel.json` (`S4_kernel.json` is not a reader input).


## Product factory (Milestone 1+)

- Kernel: `backend/app/cerebrum_product_kernel/`
- Factory: `backend/app/factory/` (`blueprint`, `planner`, `dual_registry`, `generator`)
- Blueprints: `blueprints/`
- Factory block shelf (dual-register mirror): `backend/app/factory/shelves/factory_blocks.json`
- Generate: `cd backend && PYTHONPATH=. python3 -m app.factory.cli generate --blueprint ../blueprints/steward/steward.v1.yaml --out ../factory_outputs/Projectsix --blocks-root $CEREBRUM_BLOCKS_ROOT`
- Factory tests: `python3 -m pytest tests/factory --asyncio-mode=auto`

## Deploy gate (market-ready claims)

Before any market-ready claim, release tag, or public demo:

1. Run the live post-deploy smoke against the deployed backend:
   ```bash
   python3 scripts/post_deploy_smoke.py https://api.cerebrum-dev.com
   ```
   Production requires `SMOKE_GATE_TOKEN` (Render secret) or a verified
   `SMOKE_EMAIL`/`SMOKE_PASSWORD`. Public email verification stays on.
2. Every kernel must print `[LIVE]`.
3. Deterministic or fallback success is **not** accepted as evidence.
4. Do not merge to `main` or tag a release until the smoke script exits 0.

See also `docs/audits/REGISTERED_BUT_DEAD_AUDIT.md`.



### Product architect API (Floor — canonical)

- `POST /v1/sessions/{id}/product/draft` — brief → `product_blueprint.v1` (Steward brief uses golden YAML)
- `POST /v1/sessions/{id}/product/plan` — plan capabilities (fail-closed dual registry)
- `POST /v1/sessions/{id}/product/generate` — regenerate product into `factory_outputs/`

`/v1/factory/product/*` is not a second product surface (removed; Floor only).
