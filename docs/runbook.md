# Pilot / Factory Runbook

One-page operations guide for CerebrumDev.ai factory and Automotive Safety Intelligence pilot.

## Deploy (factory)

`render.yaml` is **not a live Blueprint**. The Render dashboard for
`cerebrumdev-backend` / `cerebrumdev-frontend` is the source of truth.
Do not sync this file as a Blueprint; it would change production security
(email-verify, accounts DB wiring, and historically a frontend master key).

Frontend security headers are stamped by `frontend/serve.mjs` (values in
`frontend/security-headers.json`). A Render `static_site` does not read
`frontend/public/_headers` and never applied the old Blueprint `headers:`
block. The live service must be a Node web service with
`startCommand: node serve.mjs` — not dashboard Custom Headers.

1. Set secrets in the **Render dashboard** (never commit, never bake into Vite):
   - `CEREBRUM_DEV_API_KEY` — master/admin key, backend only
   - `CEREBRUM_API_KEY` — must match Cerebrum-Blocks store key
   - `CURSOR_API_KEY` (or `CURSOR_AGENT_API_KEY` / `FACTORY_CURSOR_API_KEY`) — Factory coding (Cursor BA)
   - `CEREBRUM_CHAT_LLM_API_KEY` / `BASE_URL` / `MODEL` — HTTP Floor chat (`LLM_PROVIDER=cursor` is BA naming only)
   - `OPENROUTER_API_KEY` — optional factory coder fallback leg (not required for Floor chat)
   - `CEREBRUM_LLM_API_KEY` — optional leftover shared HTTP key (do not re-add `KIMI_*` / `DEEPSEEK_*`)
   - `REDIS_URL` — Internal URL from Key Value `cerebrumdev-redis`
   - `SMOKE_GATE_TOKEN` — production smoke verified-principal gate
   - `SENTRY_DSN` / frontend `VITE_SENTRY_DSN` — optional
   - `DATA_ENCRYPTION_KEY` — required in production when Google Drive is configured
2. Frontend env is only `VITE_API_URL` (and optional `VITE_SENTRY_DSN`).
   The SPA authenticates with `cdt_` login tokens. **Do not** set
   `VITE_API_KEY` to the master key — Vite would publish it in the static bundle.
3. Deploy backend first. Confirm `GET /health` and `GET /ready` return healthy.
4. After each production deploy, the post-merge smoke workflow runs
   `scripts/post_deploy_smoke.py` against `https://api.cerebrum-dev.com`.
   That job (not PR unit CI) is the market-ready gate.

## Deploy (automotive pilot package)

```bash
python -m scripts.generate_automotive_platform --output generated/automotive-safety-intelligence
python -m scripts.deploy_automotive_pilot --package generated/automotive-safety-intelligence
cd generated/automotive-safety-intelligence
docker compose up --build
```

Smoke:

- `GET /health`
- `GET /ready`
- `GET /metrics` (factory: master API key; same gate as `POST /v1/ops/backup`)

## Rollback

- **Render:** Redeploy previous successful deploy from the Render dashboard (Manual Deploy → select prior image/commit).
- **Pilot pack activation:** `POST /v1/admin/automotive-core/rollback`
- **Compose:** `git checkout <prior-sha>` in the deploy repo and recreate containers.

## Key rotation

1. Rotate `CEREBRUM_DEV_API_KEY` on the **backend** service only. Do not
   put it on the frontend. Existing `cdt_` sessions stay valid until expiry.
2. Rotate `CEREBRUM_API_KEY` to match Cerebrum-Blocks.
3. Rotate `DATA_ENCRYPTION_KEY` only with a re-encrypt plan for Drive tokens.
4. Restart backend after rotation; rebuild frontend only when `VITE_API_URL`
   or `VITE_SENTRY_DSN` changes.

## Disk / backup (`/app/storage`)

Render disk is ephemeral across service deletion but persists across deploys while attached.

- Snapshot: copy `/app/storage` via one-off job or `render disk` backup before destructive changes.
- Restore: stop service, restore files to mount path, restart.
- Pilot Postgres: use managed `pg_dump` before pack rebuilds.

### Automated backups

An in-process scheduler inside the web service (03:00 UTC nightly, plus a
bootstrap snapshot on first boot when no archive exists, plus an immediate
retry when `last_backup.json` records a failure) snapshots the accounts
database plus `uploads/`, `sessions/` and `chroma/` into `$BACKUP_DIR`
(default `$STORAGE_PATH/backups`), keeping 14 archives. It is deliberately not
a Render cron job: cron jobs cannot mount the persistent disk, so only the
service that owns the disk can back it up. Status surfaces non-gating at
`/ready` under `details.last_backup`.

If `/ready` is stuck on a stale fail after a dump-path fix, trigger one run
inside the web service (master API key, not a user token):

```bash
curl -X POST https://api.cerebrum-dev.com/v1/ops/backup \
  -H "Authorization: Bearer $CEREBRUM_DEV_API_KEY"
```

The accounts snapshot uses SQLite's online backup API rather than a file copy —
copying a live database can capture a torn write, producing an archive that
restores cleanly and is quietly corrupt. Every snapshot is opened and
integrity-checked before the job reports success.

**What this does not cover:** by default the copy is on the same disk as the
original. That protects against logical loss (accidental delete, bad migration)
but not against losing the disk. For that, point `BACKUP_DIR` at another
volume, or move accounts to managed Postgres and use its point-in-time
recovery.

**Rehearse the restore.** An untested backup is not a backup:

```bash
python -m scripts.backup_cli drill    # snapshot -> restore -> verify row counts
```

Non-zero exit means the archive cannot be put back. Run it now, and again after
any change to the storage layout.

**Restore for real:**

```bash
python -m scripts.backup_cli list
python -m scripts.backup_cli restore <archive> /tmp/restore-check
# inspect, confirm counts, THEN promote deliberately
```

Restore never writes over the live location, so a drill cannot destroy
production. Promoting is a separate manual act.

## Moving accounts to Postgres

SQLite is one file with one writer: under public signup load it returns
`database is locked` as HTTP 500s, and it has no point-in-time recovery.

**The switch copies nothing.** Setting `ACCOUNTS_DATABASE_URL` against an empty
database loses every account — and the app boots fine reporting no users, so
nobody notices until a customer cannot log in.

**`ACCOUNTS_DATABASE_URL` must stay set** once the live service uses Postgres.
Unsetting it (env thrash) makes the app fall back to disk SQLite at
`STORAGE_PATH/accounts.db` while login reads the new/empty Postgres. The real
owner account (`acct_<id>`, `owner.m@projecteleven`) has lived on that
SQLite file. `cerebrum-builds` is **not** the accounts store — never migrate
accounts there.

```bash
# 1. sync the blueprint so cerebrumdev-accounts is provisioned
# 2. create the schema in the new database
ACCOUNTS_DATABASE_URL=<url> python -m alembic upgrade head
# 3. copy the data and check it landed
ACCOUNTS_DATABASE_URL=<url> python -m scripts.migrate_accounts_to_postgres --verify
# 4. only now set ACCOUNTS_DATABASE_URL on the web service
# 5. never unset it again
```

The CLI refuses a non-empty target unless `--force` is given. `--force` is
**merge** mode: insert missing ids/emails only, never wipe smoke/existing
Postgres rows. `--verify` confirms every non-conflict source primary key is
present (counts may differ after a merge).

### Live restore (master key, one-shot)

When disk SQLite still holds real accounts and Postgres already has smoke
rows, do not shell into the box. After deploy:

```bash
# Compare ids + emails only (no password hashes)
curl -sS -H "Authorization: Bearer $CEREBRUM_DEV_API_KEY" \
  https://api.cerebrum-dev.com/v1/ops/accounts-restore

# Merge missing SQLite rows into Postgres (idempotent; safe to re-run)
curl -sS -X POST -H "Authorization: Bearer $CEREBRUM_DEV_API_KEY" \
  "https://api.cerebrum-dev.com/v1/ops/accounts-restore?force=true"
```

`POST` without `force=true` refuses if Postgres already has rows. The JSON
returns sqlite counts, postgres counts before/after, and emails migrated —
never password hashes. Re-running inserts nothing already present.

Disk SQLite is often probe-era only. Historical owner rows
(`acct_c38ae401…`) live in nightly archives under `STORAGE_PATH/backups`:

```bash
# List archives and whether accounts.dump / .sql / .db is present
curl -sS -H "Authorization: Bearer $CEREBRUM_DEV_API_KEY" \
  https://api.cerebrum-dev.com/v1/ops/accounts-restore/backups

# Merge the named dump (or omit archive= for the latest with a dump)
curl -sS -X POST -H "Authorization: Bearer $CEREBRUM_DEV_API_KEY" \
  "https://api.cerebrum-dev.com/v1/ops/accounts-restore/from-backup?archive=cerebrumdev-backup-20260912T030000Z.tar.gz"
```

`from-backup` defaults to `force=true` and `prefer_source=true`: insert
missing ids, park a conflicting live email
(`displaced+<id>@invalid.cerebrum-dev.restore`) so the backup id can reclaim
the real address, and never wipe smoke rows. Missing `session_owners` keys
are advisory (sessions may live outside the dump) and do not fail the
request.

Do **not** change Render env to perform this restore. Keep
`ACCOUNTS_DATABASE_URL` set.

## Health and alerting

- `/ready` is the platform health check. It probes real dependencies and returns
  **503** when they are broken, so Render can act on it.
- `/health` is informational and always 200 — never point a health check at it.
- A failed migration now stops the boot instead of leaving a service running on
  the wrong schema. Render shows a failed deploy and keeps the previous version.

**Gap:** `notifyOnFail: default` is deploy-failure email only. Nothing pages
anyone for an OOM, a hang, or a full disk. Add an external uptime check against
`/ready` before opening registration, or the first signal of an outage is a
customer complaining.

## Before opening registration / paid launch

Do **not** flip `BILLING_ENFORCEMENT` or `ACCOUNTS_REQUIRE_VERIFIED_EMAIL`
in the dashboard while Stripe checkout is unconfigured (users 402 with no
remedy). Order:

1. Mail provider live (`RESEND_API_KEY` / SMTP), then email-verify.
2. Stripe checkout + webhook live (not 503).
3. Confirm Floor generate + approve-and-generate chat return 402 for an
   expired trial (`require_entitled` on those paths).
4. Then `BILLING_ENFORCEMENT=true` in the dashboard. Update `render.yaml`
   comments to match; do not treat the file as applied.

Also:

- [ ] `backup_cli drill` passes.
- [ ] An external uptime check points at `/ready`.
- [ ] `ACCOUNTS_EXPOSE_DEV_TOKENS` is unset or `0` everywhere.
- [ ] `DATA_ENCRYPTION_KEY` is set whenever Drive OAuth is configured
      (`/ready` fails in production if Drive is on and encryption is off).
- [ ] `ALLOW_ANONYMOUS_DEV` is unset in production (boot refuses it).
- [ ] Post-deploy smoke workflow is green at this SHA.
- [ ] Counsel has cleared `docs/legal/` (those files are still drafts).

## Provider failure

Factory coding uses Cursor BA (`CURSOR_API_KEY` /
`CURSOR_AGENT_API_KEY` / `FACTORY_CURSOR_API_KEY`). HTTP Floor chat /
architect use OpenRouter (`OPENROUTER_API_KEY`). There is no Kimi/DeepSeek
pin in `render.yaml`.

1. Check `/ready` and backend logs; confirm the Cursor / OpenRouter keys
   are set and the hosts are reachable.
2. HTTP fallback is the OpenRouter `:free` leg
   (`backend/app/core/llm_config.py`; `FACTORY_LLM_FALLBACK_MODEL`).
3. While the provider is down: drafting falls back through Golden Steward /
   keyword mode with the mode disclosed on the blueprint; the coder ships
   honest stubs with reasons. RAG/admin read paths stay available.
