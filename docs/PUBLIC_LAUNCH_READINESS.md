# Public Launch Readiness Register

Findings from a four-dimension audit of **production code only**
(`CerebrumDev.ai@origin/master b005fab`, `Cerebrum-Blocks@origin/main`), run
2026-08-01 ahead of opening the platform to public registration.

Nothing here credits the unmerged `claude/cerebrum-free-trial-readiness-4vwzmn`
branch. Where a fix exists only on that branch, it is marked **UNMERGED** —
meaning it is *not* protecting production today.

**Verdict: not ready to open registration.** The three items fixed below were
the reachable ones. The blockers that remain are mostly operational, and the
single largest is that there are no backups of anything.

---

## Fixed in this branch

### Security

| # | Finding | Where |
|---|---|---|
| C1 | **Arbitrary recursive delete.** `output_dir` from the request body reached `shutil.rmtree`. Any registered user could delete `/app/storage` — accounts DB, all sessions, all uploads. Found independently by three reviewers. | `factory/paths.py`, `factory/generator.py`, both generate routes |
| C2 | **Account takeover.** `/v1/auth/forgot-password` returned the live reset token to an unauthenticated caller whenever mail delivery failed — and it failed open both when unconfigured and on any transient provider error. | `routers/accounts.py` |
| C3 | **Fail-open auth.** The anonymous `dev` principal was the default whenever no master key was set, guarded only by `ENV == "production"` exactly. Not live (render.yaml pins it), but one typo away. | `core/auth.py` |

### Durability

- **Nightly backups with a verified restore path.** `core/backup.py` +
  `scripts/backup_cli.py`, scheduled at 03:00 UTC with 14-day retention. Uses
  SQLite's online backup API rather than a file copy (a copy of a live database
  can capture a torn write and produce an archive that restores cleanly and is
  quietly corrupt), integrity-checks every snapshot, and restores to an
  explicit target so a drill can never overwrite production. `backup_cli drill`
  proves the round trip.

  *Correction (2026-08-02, post-merge):* the `type: cron` job this page
  originally scheduled the backup with **can never run on Render** — cron jobs
  cannot mount persistent disks, and a disk is readable by exactly one
  service, so no separate job can ever see `/app/storage`. The vehicle is now
  an in-process asyncio task inside the web service
  (`core/backup_scheduler.py`): bootstrap snapshot at first boot when no
  archive exists, nightly at `BACKUP_UTC_HOUR` (03 UTC), retention via
  `BACKUP_KEEP`, every run's report written to `backups/last_backup.json` and
  surfaced (non-gating) at `/ready` under `details.last_backup`. Failures log
  at ERROR, which Sentry's logging integration exports. The invariant test now
  asserts the inverse shape: no cron in the blueprint may declare a disk, and
  the web service must arm the scheduler at startup.
- **Managed Postgres declared**, with `scripts/migrate_accounts_to_postgres.py`
  (`--verify` re-reads both sides). `ACCOUNTS_DATABASE_URL` is deliberately
  left unwired: setting it against an empty database loses every account while
  the app boots normally reporting no users.
- **Orphan sessions eliminated.** Ownership is now recorded before the session
  exists and its failure is fatal. Previously it was written afterwards and
  swallowed, producing sessions tied to no account — unlistable, unexportable
  and unerasable, while still holding chat history and uploads.
- **Learning engine moved off `/tmp`** (Blocks), where every deploy was wiping
  accumulated user corrections.

### Cost and abuse

- **The unmetered LLM path is metered.** `/product/draft` and `/plan` now spend
  a `draft` counter (daily, like chat — not the lifetime generation counter).
  The gate sits outside the handlers' `try/except`, which catches bare
  `Exception` and would otherwise downgrade every 429 into a 400.
- **Prompt and response bounded** — `max_tokens` plus an explicit brief
  truncation marker, so a 200k-character brief cannot become a 200k-token bill.
- **Body-size ceiling** (`core/request_limits.py`), registered before CORS so a
  413 carries CORS headers and reads as "file too large" rather than an opaque
  browser CORS failure.
- **Upload caps** — per-file size, file count, extension allowlist, chunked
  reads that abort mid-write, and cleanup of partial files on rejection.
- **Rate limiter bounded and proxy-aware** — the bucket store can no longer
  grow without limit, and `X-Forwarded-For` is honoured only when
  `TRUSTED_PROXY` is explicitly set, read from the right by hop count. Default
  remains the socket peer, because trusting the header by default makes the
  limit key caller-controlled.

### Privacy

- **Data export and erasure** (`core/data_rights.py`, three routes). Deletion
  requires the current password, so a stolen bearer token is not enough, and
  purges sessions, uploads, Drive tokens, workbench state and vector
  collections before the account row. Categories it cannot reach are named in
  the response rather than silently claimed — a partial purge reporting success
  is worse than an honest one.
- **Retention pass** for expired tokens, callable rather than scheduled.

### Failure visibility

- `/ready` returns **503** when dependencies are broken, and is now the
  health-check path. It previously returned 200 while reporting "degraded", and
  Render only reads the status code.
- **A failed migration stops the boot** instead of leaving a service running on
  the wrong schema answering health checks normally.
- **Scheduled jobs are packaged.** `backend/scripts/` was missing from the
  image while render.yaml scheduled `python -m scripts.backup_cli` — the backup
  would have failed at import nightly and, since `notifyOnFail` covers only
  deploy failures, said nothing. An invariant test now ties every cron
  entry point to a `COPY`.

---

## Open — must close before launch

### Blockers

- **No backups. Of anything.** Every account, password hash, API key, upload
  and generated product lives on two 1 GB Render disks with no verified copy.
  The only backup cron in the workspace belongs to an unrelated repo and is
  suspended. **Nothing else on this page matters if this stands.**
- **All CDEV identity is SQLite on one unbacked disk.** `ACCOUNTS_DATABASE_URL`
  is unset, so `accounts_store` resolves to `/app/storage/accounts.db` —
  confirmed by `SQLiteImpl` in every production boot log. SQLite is also
  single-writer: public signup load produces `database is locked` as 500s.
- **Cross-tenant data access in Blocks.** `_namespace` is caller-supplied and
  unenforced on `origin/main`; `project_id` likewise, which reaches another
  project's pgvector corpus through the `knowledge` block. `/v1/memory/{action}`
  bypasses trust scope entirely. **Fix is UNMERGED** in PR #58.
- **Trust scope resolves every caller to the same tenant.** The live auth dict
  carries no `id`/`email`, so `enforce_trust_scope` substitutes
  `tenant_id="apikey:anonymous"` for everyone — the partition is real but the
  identity it partitions on is degenerate.
- **No Terms of Service, no Privacy Policy.** Drafts now in `docs/legal/`,
  requiring legal review. Open registration + email collection + Stripe without
  these is a compliance exposure.
- **No data export or deletion path.** No `delete_account` exists anywhere.
  Nothing expires. This is the GDPR/CCPA gap.

### Cost and abuse

- **`/product/draft` is an unmetered paid LLM call.** No quota check, no
  `max_tokens`, untruncated user brief, retries once against a fallback model.
  Unbounded spend per free account, and accounts are unlimited.
- **Registration is unlimited and instantly usable.** Email verification is off
  by default; every trial counter keys on `account_id` only, so quotas reduce to
  "how fast can you POST /register".
- **The auth rate limiter is keyed on `request.client.host`** with no proxy
  headers configured — behind Render that is not the end user. It is either
  globally shared (10 requests locks out login for everyone) or unevenly
  enforced. Do not naively trust `X-Forwarded-For` when fixing; bound the
  bucket dict too.
- **No request size limit anywhere on CDEV.** `upload.py` does
  `await file.read()` over an unbounded file list. Blocks gets this right
  (10 MB + allowlist + libmagic); CDEV has nothing.
- **Shared key into Blocks.** CDEV drives Blocks with one server-side key; if it
  holds the master value the tier is `unlimited` and block access control is
  bypassed for all CDEV traffic. **Verify which value is set.**

### Operational visibility

- **Migration failure is silent.** The Dockerfile CMD is
  `alembic upgrade head || echo ...` — a failed migration yields a *running*
  service on the wrong schema, `/health` still 200, no restart, no alert.
- **Health checks do not fail.** CDEV `/health` returns 200 even when
  `degraded`; Blocks `/health` returns `healthy` unconditionally with no probe
  at all. Render only reads the status code, so a broken disk reads as healthy.
  The useful endpoint, `/ready`, is not wired to `healthCheckPath`.
- **Nobody gets paged.** Both services are `notifyOnFail: default` — deploy
  failures only. No uptime check, no metric alert, no log alert. For an
  OOM, a hang, or a full disk, the first signal is a user complaining.
- **Capacity: 512 MB / 0.5 vCPU, single instance, disk attached.** Blocks idles
  at 32% with 7 of 64 blocks loaded; the first request routing to a
  `sentence_transformers` block imports torch and OOM-kills the only instance.
  Horizontal scaling is impossible while a disk is attached.

### Disclosure

Four of the five items below are fixed in Cerebrum-Blocks PR #59
(`chore/public-launch-readiness`, 2026-08-02) — **UNMERGED**, so production is
unprotected until it lands:

> **Follow-up finding (2026-08-02), also fixed in PR #59:** `app.core.file_crypto`
> was imported by the OCR and image blocks on main but the module only existed on
> the unmerged `feat/automotive-pilot-pr2` branch — Phase 1 shipped the imports
> without the module. Image-block metadata raised `ModuleNotFoundError` and OCR
> degraded to an error dict on every execution; OCR is in the FREE tier's
> allowlist, so entry-tier functionality was broken in production. The module
> (passthrough when `DATA_ENCRYPTION_KEY` is unset) is restored with its tests,
> which also unblocks ever enabling encryption at rest.

- **Sentry on Blocks has no `before_send` and no body-size cap**, so
  `/v1/execute` bodies — which carry plaintext secrets for the `secrets`,
  `config` and `auth` blocks — go to a third-party SaaS on any exception.
  *Fix in PR #59: bodies never attached, two-pass scrubber over frame locals
  and breadcrumbs, failed scrub drops the event.*
- **Encryption at rest is off.** `DATA_ENCRYPTION_KEY` appears nowhere in
  `render.yaml`, so Google OAuth refresh tokens are written plaintext despite
  docstrings claiming otherwise; uploads never go through `file_crypto` at all.
  **Still open — no fix in any PR.** Setting the key requires a read-path
  migration for existing plaintext tokens; do not set it blind.
- **Unauthenticated `/health` on Blocks** runs a subprocess and returns its
  stderr plus the raw value of `KIMI_CLI_PATH`. `/stats` re-exposes the block
  inventory that `/blocks` is deliberately auth-gated to protect.
  *Fix in PR #59: liveness/readiness/diagnostics split; `/stats` gated; CLI
  probe authenticated and TTL-cached.*
- **Raw API keys stored plaintext** as a SQLite primary key in Blocks
  (`rate_limits.db`), including the master key.
  *Fix in PR #59: salted-digest keys, legacy plaintext rows purged with
  VACUUM + WAL truncation.*
- **Per-tier block access is a silent no-op** in Blocks: live tiers are
  `standard`/`unlimited`, the `Tier` enum only accepts `free|pro|enterprise`, so
  the check `ValueError`s into a bare `return`. The primary gate still holds —
  `code`/`sandbox`/`secrets` are not open — but `workbench` (returns raw
  subprocess output) is reachable by any issued key.
  *Fix in PR #59: `TIER_ALIASES` maps issued strings; unknown tiers fail
  closed to FREE.*

---

## Confirmed sound

Worth recording so nobody "fixes" these:

- **Stripe is correct** — webhook signature verified, 503 when unconfigured,
  trial/paid state not client-settable, account derived from the credential.
- **Session ownership holds** — cross-account access returns 404, not 403, so
  existence does not leak.
- **Password hashing** — PBKDF2-HMAC-SHA256, 200k iterations, per-user salt,
  constant-time verify. Reset revokes all login tokens.
- **Drive OAuth CSRF binding is real** — server-generated single-use `state`
  bound to the session.
- **No secret is committed** in either history, and no generated artifact
  embeds a factory credential — the packagers emit per-package random values.
- **No global exception handler**, so tracebacks do not reach clients.

---

## Owner actions — nobody else can do these

1. Merge, in order: CDEV PR #126, CDEV PR #127, Blocks PR #58, Blocks PR #59.
   The tenant-isolation fix is in #58; the disclosure fixes (Sentry scrubbing,
   health/stats gating, fail-closed tiers, hashed rate-limit keys) are in #59.
   All four are green and mergeable; the session permission classifier blocks
   `gh pr merge`, so these are operator commands.
2. Set up a backup of `/app/storage` and `/app/data`, or move accounts to
   managed Postgres with PITR. Confirm whether Render retains disk snapshots.
   (CDEV backups now run in-process inside the web service — the #127 cron
   design was impossible on Render, see the Durability correction above.
   Blocks `/app/data` still has no equivalent.)
3. Confirm what **Moonshot AI** does with submitted content — whether it is
   retained or trained on, and whether that can be disabled. The privacy policy
   cannot truthfully claim "we do not train on your content" until this is
   answered upstream.
4. Set `STORAGE_PATH=/app/data` on the live `cerebrum-blocks` service
   (`srv-d8rrorvavr4c73evhvi0`); the blueprint does not manage that service.
   (Attempted 2026-08-02 via the Render MCP env tool; blocked by the session
   permission classifier — still pending.) After #59 deploys, also flip the
   service's Health Check Path from `/health` to `/ready` in the dashboard —
   one click, Settings → Health & Alerts.
5. Rotate the Render API key that was pasted into a working session, and move
   the block-signing private key out of the ephemeral scratchpad.
6. Have counsel review `docs/legal/` and resolve every `[[NEEDS INPUT]]`.
