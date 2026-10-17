# CerebrumDev.ai — the Factory

**One account. Tell the factory. Receive your platform.**

CerebrumDev.ai is a conversational platform factory. A user registers, lands on
the **Factory Floor**, describes the platform they need in plain language, and
the factory drafts a blueprint. On the user's word — *approve* — the generator
builds a brand-new, exportable platform from certified Cerebrum-Blocks kernels.
The subscription plan decides how deep the factory builds.

Live deployment (Render):

- Frontend (Factory Floor): `https://cerebrum-dev.com` (`https://www.cerebrum-dev.com`)
- Backend API: `https://api.cerebrum-dev.com`
- Live Render slugs (fallbacks, not the canonical hosts):
  `https://cerebrumdev-frontend-kkz2.onrender.com`,
  `https://cerebrumdev-backend-goia.onrender.com`
- Block store: `https://cerebrum-blocks-10ug.onrender.com`

## The flow (what a user experiences)

1. **Register / sign in** — email + password. Email verification is built in
   (SMTP-configured deployments send mail; unconfigured ones surface a dev
   token honestly in the UI). Password reset is self-serve.
2. **Factory Floor chat** — "Build me a multi-user platform for …". The
   product architect drafts a blueprint (golden steward for estate briefs,
   LLM drafting when enabled, deterministic keyword drafting always available
   offline).
3. **Approve & build** — the capability planner resolves every capability
   against the dual registry (fail-closed: unknown blocks → UNSUPPORTED), then
   the generator writes the platform.
4. **Your Platforms** — download the export zip: a real, runnable platform
   tree (FastAPI app, vendored certified blocks, resident-engineer charter,
   Dockerfile, render.yaml, README, factory plan).
5. **Subscription** — every account starts on a free trial (default 3 days,
   full access). Upgrade is Stripe checkout; the portal manages the
   subscription. On deployments where the owner has not linked Stripe, the UI
   says so plainly — that is the single remaining owner-side configuration.

## Multi-user by construction

Every session is owned by an account. A shared `require_owned_session` guard
backs **all** session-scoped routers (chat, config, upload, train, deploy,
drive, session_product): user credentials (`cdt_` login tokens, `cdk_` API
keys) only ever reach their own sessions — cross-account access gets a
non-leaking 404. Admin/master and local-dev principals pass through.
Regression-covered in `backend/tests/test_session_ownership.py`.

## Observability (Sentry)

Error tracking and performance are wired on both tiers and activate by DSN:

- Backend: set `SENTRY_DSN` (Sentry project platform *python/fastapi*).
  Initialization lives in `app/core/observability.py`, runs at package import,
  and is inert without a DSN. Release is tagged from `RENDER_GIT_COMMIT`.
- Frontend: set `VITE_SENTRY_DSN` (platform *javascript-react*) on the
  frontend web service (build-time). The bundle loads Sentry lazily — zero
  cost when unset. Production serves `frontend/dist` via `node serve.mjs`,
  which stamps the five smoke security headers.

## Platform status

- **Auth**: register, login, email verification, password reset, per-account
  API keys — live. Rate-limited (Redis-backed when `REDIS_URL` is set,
  in-memory otherwise).
- **Accounts DB**: sqlite by default, Postgres via `ACCOUNTS_DATABASE_URL`.
  Migrations run through **Alembic** (`backend/alembic/`); the container
  applies `alembic upgrade head` at boot.
- **Chat → LLM → blueprint → plan → generate → export**: live E2E.
- **Billing**: trial lifecycle, entitlement checks, Stripe checkout/portal/
  webhook are implemented. Stripe keys are the owner's dashboard
  configuration (`STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`,
  `STRIPE_WEBHOOK_SECRET`) — without them the API answers
  `503 stripe_not_configured` honestly. Enforcement flips via
  `BILLING_ENFORCEMENT=true`.
- **Drive connector**: OAuth pending states are Redis-backed when `REDIS_URL`
  is set.

## Under the floor (how platforms are built)

- **Dual registry + capability planner** — capabilities resolve to REUSE /
  ADAPT / COMPOSE / GENERATE / STUB; anything unknown fails closed.
- **Certified kernels** come from the Cerebrum-Blocks store (the 24-kit
  universal kernel); the factory never ships unregistered blocks.
- **Resident Engineer** — each generated platform carries its resident
  engineer charter (observe/diagnose/repair under human authority).
- **Reasoning layer** — chain generation, rule injection.
- **The acceptance floor** — every platform must pass
  [21 measured checks](backend/app/factory/acceptance_floor.v2.json) before
  export. The file is the single source: the coder's brief renders each
  `requirement_text` verbatim, and the Store gate executes the same ids, so
  the agent builds toward the checklist instead of discovering it one
  rework round at a time. Neither side may keep its own copy — a test fails
  the build if they drift. Scores per artifact live in
  [STORE_REGRADE_P.md](STORE_REGRADE_P.md); old builds keep the score they
  achieved and are never back-dated.

  The nine checks added in v2 cost roughly 2–4 minutes of extra build time
  and a few cents of coder time per build: the negative-case harness, the
  Postgres boot, one live connector delivery, a backup restored, a
  200-request bench and a dependency audit.

## Development

Local happy path (no master key in the frontend; the SPA uses `cdt_` login tokens):

```bash
# backend — port 8000 (Vite proxies /v1 here). Do not use 8001 unless you
# are on docker-compose, which maps host 8001 → container 8000.
cd backend
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt -r requirements-dev.txt
ALLOW_ANONYMOUS_DEV=1 ENV=dev ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
# GET /health and GET /ready

# frontend — same-origin /v1 when VITE_API_URL is unset
cd frontend && npm install && npm run dev -- --host
# open http://127.0.0.1:5173 — register, then Floor chat / Design product
```

```bash
# tests
cd backend && ENV=test ./venv/bin/python -m pytest
cd frontend && npm test && npm run lint && npm run build
# mocked Playwright (no live API): npm run e2e:mocked

# accounts migrations
cd backend && ./venv/bin/alembic upgrade head
```

Do **not** set `VITE_API_KEY`. Do **not** flip `BILLING_ENFORCEMENT` or
`ACCOUNTS_REQUIRE_VERIFIED_EMAIL` until mail + Stripe checkout work.

### LLM providers: Cursor for Factory coding, CEREBRUM_CHAT_* for HTTP chat

Factory coding uses Cursor Background Agents when any of
`CURSOR_API_KEY` / `CURSOR_AGENT_API_KEY` / `FACTORY_CURSOR_API_KEY` is
set (`LLM_PROVIDER=cursor` is valid). HTTP Floor chat / architect draft
use `CEREBRUM_CHAT_LLM_*` (then leftover `CEREBRUM_LLM_*` / Moonshot).
Cursor has no public `/v1/chat/completions` — those keys are BA only.

Leftover Kimi/Moonshot and Claude remain as opt-in HTTP providers.

```bash
# Cursor BA (Factory coding) — any one key arms cli-pivot
CURSOR_API_KEY=...              # or CURSOR_AGENT_API_KEY / FACTORY_CURSOR_API_KEY
LLM_PROVIDER=cursor

# HTTP Floor chat / architect — OpenAI-compatible host already on Render
CEREBRUM_CHAT_LLM_API_KEY=...
CEREBRUM_CHAT_LLM_BASE_URL=...
CEREBRUM_CHAT_LLM_MODEL=...

# Claude — opt in explicitly
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=...
```

Two rules make provider choice deliberate rather than accidental:

- **A second key never moves your traffic.** `_detect_provider()` resolves to
  Kimi whenever Kimi credentials exist, *even if Claude credentials also
  exist*. Claude is auto-selected only when it is the sole provider
  configured; otherwise you ask for it by name. Adding `ANTHROPIC_API_KEY` to
  a running deployment changes nothing about what it calls or what it costs.
- **A missing key is an error, not a fallback.** Setting `LLM_PROVIDER=claude`
  without `ANTHROPIC_API_KEY` fails loudly naming that variable. It will not
  quietly borrow the Kimi key. A silent provider switch is a cost surprise,
  which is a product bug.

`LLM_PROVIDER` accepts `cursor`, `kimi`/`moonshot` (aliased to `kimi`) and
`claude`/`anthropic` (aliased to `claude`).

Claude is called through the **native Messages API**, not an OpenAI-compatible
shim: `x-api-key` rather than a bearer token, a mandatory `anthropic-version`
header, the system prompt as a top-level `system` parameter rather than a
message role, and a reply of typed content blocks. Those are the three things
an OpenAI-shaped port silently gets wrong, so they are asserted by a
request-shape test.

**Agentic coding CLI.** Production Floor C-BRIEF hands the one compiled brief
to this CLI. The seam is shallow — run a command, read its result:

```bash
FACTORY_CODE_CLI=kimi      # Kimi Code → DeepSeek V4 Pro when DEEPSEEK_API_KEY is set
FACTORY_CODE_PROVIDER=kimi # historical Moonshot when KIMI_CODE_API_KEY is set
```

`KIMI_CODE_CLI` is still honoured; `FACTORY_CODE_CLI` wins. When
`DEEPSEEK_API_KEY` is set, DeepSeek is the default Factory coding path
(`kimi` + official OpenAI-compat `https://api.deepseek.com` on the
**subprocess only**). Leftover `FACTORY_CODE_CLI=claude` remaps to `kimi`.
Claude Code is not the DeepSeek vehicle. Floor / architect chat stays on
OpenRouter — do not burn DeepSeek for chat. See
`docs/factory/DEEPSEEK_ENV_SETUP.md` and `docs/factory/KIMI_ENV_SETUP.md`.

The production `Dockerfile` installs official Kimi Code at `/usr/local/bin/kimi`
(pin `KIMI_CODE_VERSION`). Official Claude Code may still be planted at
`/usr/local/bin/claude` as leftover unused binary. A keyed Floor without the
selected binary fail-closes as `FACTORY_CODE_CLI_UNAVAILABLE`. A Moonshot
Kimi binary without `~/.kimi-code/config.toml` fail-closes as
`FACTORY_CODE_CLI_CREDENTIALS_MISSING` when Cursor executor keys are
absent. When `CURSOR_API_KEY` (or sibling Cursor BA keys) is present,
Generate/Continue uses Cursor BA — Kimi CLI credentials are unused and
`/health` must not surface that class as a Floor blocker. DeepSeek selected without
`DEEPSEEK_API_KEY` is the same named class. A credentials file without a usable `default_model` / `[models]` entry
fail-closes as `FACTORY_CODE_CLI_NO_MODEL` before WRITER takeover (still
`FACTORY_CODE_CLI_FAILED` honesty — no HTTP oneshot, no fake WRITER
takeover). A CLI exit of `No model configured` is the same named class
and stops the coding-agent takeover (no perpetual "still working"). A 404 / Permission
denied on the configured model fail-closes as
`FACTORY_CODE_CLI_MODEL_DENIED`. Boot writes DeepSeek
`[providers.deepseek]` from `DEEPSEEK_API_KEY` (model `deepseek-v4-pro`)
or Moonshot `default_model` from `KIMI_CODE_MODEL` / `KIMI_CODE_MODEL_ID`
(default `kimi-k3`) so headless `kimi --prompt` does not need TTY
`/login`. DeepSeek uses `kimi --prompt "<coder_brief.md>"` (prompt + stdin,
not a bare `@docs/coder_brief.md` mention) with `deepseek-v4-pro`. A
templated pilot zip after that skip is not a ≥2h CLI session. CLI
credentials are `DEEPSEEK_API_KEY` or `KIMI_CODE_API_KEY` →
`~/.kimi-code/config.toml`, not the in-app `CEREBRUM_LLM_API_KEY`. Render
dashboard values stay owner-gated.

### Running the tests: the factory coder changes the results

The single most confusing thing about this suite is that a test can pass in CI
and fail on your laptop, or the reverse, **because of a key you forgot you
had**. Read this before debugging a local-only failure.

`backend/app/main.py` calls `load_dotenv()`, so a `backend/.env` carrying
`CEREBRUM_LLM_API_KEY` is picked up by the whole pytest session. When that key
is present the factory coder really calls the LLM for every `GENERATE`
capability and writes a live module into the generated product. When it is
absent — as on CI — the coder raises `CoderError` and an honest stub ships
instead. Same command, two different products.

**The one-command diagnostic.** If a test fails locally and you suspect the
coder rather than your machine:

```bash
cd backend
FACTORY_CODER_ENABLED=0 python -m pytest <the failing test>
```

If that turns it green, the coder is the variable. `FACTORY_CODER_ENABLED`
defaults to `1` (`app/factory/coder.py`), so the coder is **on** by default
whenever a key is reachable.

**No test requires an LLM key.** Tests that care about coder behaviour stub
`app.factory.coder.generate_handler_body` instead of calling it — it is
imported inside `ProductGenerator._write_actions`, so it is patchable at call
time. That is deliberate: CI must exercise the coder's *wiring* without an API
key and without paying for non-deterministic output.

**What is and is not reproducible.** An LLM does not emit the same bytes
twice, so whole-tree byte equality across a regeneration only holds while the
coder is idle. The guarantee the suite actually enforces
(`tests/factory/test_generate_regenerate.py`) is narrower and true:

- the scaffold is byte-reproducible with the coder off;
- every file that differs between two builds is a coder-written
  `app/actions/` module carrying `strategy=GENERATE`;
- turning the coder on perturbs nothing outside `app/actions/`.

**Blocks resolution.** Tests never hardcode a Store path. `real_blocks_root()`
resolves `CEREBRUM_BLOCKS_ROOT` / `CEREBRUM_BLOCKS_PATH`, then a sibling
`../Cerebrum-Blocks` checkout, and only counts a candidate that actually
contains `block_registry/`. With no checkout the factory's
`vendor_blocks_mirror` supplies blocks — note that
`dual_registry.load_blocks_registry` merges that mirror in *unconditionally*,
so some blocks resolve from the mirror even when a real Store is present (see
`KNOWN_INCOMPLETE.md`).

**Store pin (`blocks.lock.json`).** Factory builds resolve consumed store
blocks through the repo-root lockfile. An unlocked or hash-mismatched block
is a hard failure naming the block and both hashes — never a warning, never
fall-through to latest. Refresh the pin only on purpose:

```bash
cd backend
PYTHONPATH=. python3 -m app.factory.cli update-lock \
  --blocks-root "$CEREBRUM_BLOCKS_ROOT"
```

See [`docs/factory/BLOCKS_LOCK.md`](docs/factory/BLOCKS_LOCK.md).

**Dev dependencies are not optional.** `requirements-dev.txt` pins
`pgvector`, which the Steward kit's SQLAlchemy models import at module scope.
A venv built from `requirements.txt` alone fails ~7 tests with
`ModuleNotFoundError: No module named 'pgvector'`.

Admin-gated routes (e.g. the automotive-core foundation pack) require
`CEREBRUM_ADMIN_KEY` set on the server and passed as the `X-Admin-Key` header;
without it those routes fail closed (503 unconfigured / 403 unauthorized).

## Repository layout

- `backend/app/routers/` — API: accounts, sessions, chat (SSE), product,
  billing, deploy, drive, domains/RAG, resident engineer, workbench
- `backend/app/factory/` — product architect, planner, dual registry,
  generator, platform chat flow
- `backend/app/core/` — auth, accounts store, billing, rate limit,
  observability, session guard
- `backend/alembic/` — accounts DB migrations
- `frontend/src/` — the Factory Floor console (React + Vite)
- `docs/reports/` — readiness audits and generation proofs
