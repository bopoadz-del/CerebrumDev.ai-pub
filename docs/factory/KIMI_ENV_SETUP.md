# Kimi credentials — which key is which

Do **not** confuse these two:

| Credential | Who uses it | Where it goes |
|------------|-------------|----------------|
| **Kimi Code CLI** | Factory Engineer / Floor C-BRIEF (`FACTORY_CODE_CLI`) | `~/.kimi-code/config.toml` under `[providers.kimi]` **and** `default_model` — required for headless `kimi --prompt` |
| **In-app chat LLM** | CerebrumDev UI / kit chat | `LLM_PROVIDER` + `OLLAMA_*` (default) or optional `CEREBRUM_LLM_*` / `KIMI_API_KEY` for Factory architect |

Kimi Code CLI does **not** read shell `export KIMI_API_KEY=...` or `backend/.env` for authentication.
See [Kimi Code config files](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files).

## Production Floor (C-BRIEF)

DeepSeek V4 Pro is the default Factory coding path when `DEEPSEEK_API_KEY` is
set (Kimi Code CLI + OpenAI-compat DeepSeek backend). See
[DEEPSEEK_ENV_SETUP.md](DEEPSEEK_ENV_SETUP.md).
This page stays the historical Moonshot path (`FACTORY_CODE_PROVIDER=kimi` /
`KIMI_CODE_API_KEY`).

A keyed Factory Floor dispatches **one** compiled brief through `FACTORY_CODE_CLI`
(default name `kimi`; `KIMI_CODE_CLI` still honoured). The production image
(`./Dockerfile`) installs the official Kimi Code CLI so `kimi` is on `PATH`.
Official Claude Code may still be planted as leftover unused binary —
it is not the DeepSeek vehicle.
When Cursor executor keys are present (`CURSOR_API_KEY` /
`CURSOR_AGENT_API_KEY` / `FACTORY_CURSOR_API_KEY` — same predicate as
`writer_uses_cli_pivot` / #418), Generate/Continue uses Cursor BA.
Kimi Code CLI credentials are unused: `/health` `factory_code_cli` must
not emit `FACTORY_CODE_CLI_CREDENTIALS_MISSING` as a Floor blocker, and
Floor/Platforms must not map a missing `~/.kimi-code/config.toml` into
that banner. Do **not** re-add `KIMI_CODE_API_KEY` to Render for this
path. If those Cursor keys are absent, the fail-closed Kimi/DeepSeek
rules below still apply.

If the coder is on and the executable is still missing, dispatch fail-closes
as `FACTORY_CODE_CLI_UNAVAILABLE`. If `kimi` is on `PATH` but
`~/.kimi-code/config.toml` is absent (`credentials_file_present=false`),
it fail-closes as `FACTORY_CODE_CLI_CREDENTIALS_MISSING`. If the file is
present but has no usable `default_model` and/or `[models]` entry
(`credentials_file_present=true`, `default_model_configured=false`),
generate-start and brief dispatch fail-close as
`FACTORY_CODE_CLI_NO_MODEL` *before* WRITER takeover. If the CLI still
exits with `No model configured` mid-run, that is the same named class
(still a `FACTORY_CODE_CLI_FAILED` honesty class) and WRITER stops —
no perpetual "still working". A 404 / Permission
denied on the configured model fail-closes as
`FACTORY_CODE_CLI_MODEL_DENIED` (distinct from NO_MODEL). None of those paths
open a WRITER session or claim "coding agent has taken over". Headless
`--prompt` also passes documented `kimi --model` (0.41 `-m` / `--model`)
so Floor does not depend solely on config.toml mutation. A templated
pilot zip after a CLI skip is **not** a ≥2h CLI session. HTTP oneshot is
not a substitute. `GET /health` → `factory_code_cli` reports the probe
(`available` is the binary; credentials are a separate field). Headless
Floor cannot run `kimi` `/login` (no TTY).

Exact env (owner-gated on Render; this doc does not claim the dashboard is set):

| Variable | Role |
|----------|------|
| `FACTORY_CODE_CLI` | Binary name or absolute path (`kimi` / `/abs/path`). Leftover `claude` remaps to `kimi` when DeepSeek is selected |
| `KIMI_CODE_CLI` | Legacy alias; `FACTORY_CODE_CLI` wins |
| `KIMI_CODE_API_KEY` | Writes `~/.kimi-code/config.toml` at boot when set (`[providers.kimi]` + `default_model`). Missing file + keyed Floor → `FACTORY_CODE_CLI_CREDENTIALS_MISSING` |
| `KIMI_CODE_MODEL` | Default-model alias written into that file. Default `kimi-k3` ([Moonshot API](https://platform.moonshot.ai/docs/guide/start-using-kimi-api)). Kimi Code CLI 0.41 [config-files](https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/config-files) still show managed `kimi-code/k3` (`k3` on `api.kimi.com/coding/v1` via `/login`) — that pair 404s on a platform key. Boot maps it to `kimi-k3`. Owner-gated — this doc does not claim Render is set |
| `KIMI_CODE_MODEL_ID` | Optional API model id for the `[models]` table (default `kimi-k3`). Other Moonshot ids: `kimi-k2.7-code`, `kimi-k2.7-code-highspeed`, `kimi-k2.6` |
| `KIMI_CODE_HOME` | Optional config home (Render: `/app/.kimi-code` if you pin it) |
| `FACTORY_BRIEF_HTTP_ONESHOT=1` | CI/dev escape only — **not** a ≥2h CLI session |
| `FACTORY_BRIEF_REQUIRE_CLI=1` | Force generate-start refuse (CI mutation). `ENV=production` already requires the CLI when the coder is on |

`CEREBRUM_LLM_API_KEY` / `KIMI_API_KEY` arm the architect / HTTP coder. They do
**not** install or authenticate `FACTORY_CODE_CLI`.

## Production image install (`./Dockerfile`)

Render `cerebrumdev-backend` is built from the repo-root `Dockerfile`. That
image installs the **official** Kimi Code CLI (standalone glibc binary; no
Node.js) via the documented installer, pinned with `KIMI_CODE_VERSION`:

| Item | Value |
|------|--------|
| Docs | https://www.kimi.com/code/docs/en/kimi-code-cli/guides/getting-started |
| Installer | https://code.kimi.com/kimi-code/install.sh |
| Pin | `ARG KIMI_CODE_VERSION=0.41.0` (`KIMI_VERSION` on the installer) |
| Install dir | `KIMI_INSTALL_DIR=/usr/local` → **`/usr/local/bin/kimi`** |
| Verify | `which kimi` and `kimi --version` inside the image |

Bump the pin by changing `KIMI_CODE_VERSION` (and rebuild). Do **not** commit
API keys; `KIMI_CODE_API_KEY` stays owner-gated on the Render dashboard and
writes `$HOME/.kimi-code/config.toml` (or `$KIMI_CODE_HOME/config.toml`) at
boot, including `default_model` so `kimi --prompt` does not require TTY
`/login`. `HOME=/app` in this image, so the default config home is
`/app/.kimi-code`. A credentials-only file, or a #324 `kimi-code/k3` /
`k3` file, is mutated on the next start.

A missing or wrong `FACTORY_CODE_CLI` still fail-closes. Baking the binary
does not disable that gate.

## Kimi Code (CLI)

```bash
./scripts/setup_kimi_code_env.sh 'sk-your-kimi-code-key'
# writes ~/.kimi-code/config.toml:
#   default_model = "kimi-k3"   # KIMI_CODE_MODEL override (Moonshot API)
#   [providers.kimi]
#   type = "kimi"
#   api_key = "sk-..."
#   base_url = "https://api.moonshot.ai/v1"
#   [models."kimi-k3"]
#   provider = "kimi"
#   model = "kimi-k3"
#   max_context_size = 1048576
```

Override home with `KIMI_CODE_HOME` if needed. The helper also notes the key in
`backend/.env` as `KIMI_CODE_*` for humans — that does **not** authenticate the CLI
and does **not** change `LLM_PROVIDER` or strip in-app Kimi/Moonshot vars.

## In-app chat LLM (unchanged default)

```bash
LLM_PROVIDER=ollama
OLLAMA_URL=https://ollama.com
OLLAMA_MODEL=kimi-k2.7-code:cloud
OLLAMA_API_KEY=...
```

## Role reminder

| Actor | Job |
|-------|-----|
| **Kimi API** (architect chat) | Blueprint talk in Factory UI when wired |
| **Kimi Code** | Factory Engineer + Block Store Manager via CLI |
| **CerebrumDev.ai** | Governance, dual registry, certify, regenerate |
