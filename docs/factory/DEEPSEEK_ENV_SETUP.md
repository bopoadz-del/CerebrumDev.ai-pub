# DeepSeek V4 Pro — Factory coding CLI

Do **not** confuse these two:

| Credential | Who uses it | Where it goes |
|------------|-------------|----------------|
| **DeepSeek → Kimi Code CLI** | Factory Engineer / Floor C-BRIEF (`FACTORY_CODE_CLI=kimi`) | Kimi Code **subprocess** (`KIMI_MODEL_*` + `DEEPSEEK_*`) and `~/.kimi-code/config.toml` `[providers.deepseek]`. Never git. |
| **Floor / architect chat** | CerebrumDev UI / kit chat | OpenRouter free (or `CEREBRUM_LLM_*` / `KIMI_API_KEY`). **Do not** point chat at DeepSeek. |

`DEEPSEEK_API_KEY` does **not** authenticate in-app chat. Boot does **not**
export `ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY` process-wide — those names
would leak into `LLM_PROVIDER=claude` and burn DeepSeek on Floor talk.

Claude Code is **not** the DeepSeek vehicle. Live Claude Code 2.1.x rejected
`deepseek-v4-pro[1m]` (sess_be217f6d), bare `deepseek-v4-pro` (sess_401e6619),
and the #371 remap to `claude-opus-4-6`. Owner order: drop Claude entirely;
hook DeepSeek under Kimi (OpenAI-compat) or directly.

Kimi Code OpenAI-compat provider:
https://www.kimi.com/code/docs/en/kimi-code-cli/configuration/providers.html

DeepSeek OpenAI catalog:
https://api-docs.deepseek.com/quick_start/pricing

## Production Floor (C-BRIEF)

A keyed Factory Floor dispatches **one** compiled brief through `FACTORY_CODE_CLI`.
When `DEEPSEEK_API_KEY` is set (or `FACTORY_CODE_PROVIDER=deepseek`), the default
binary is `kimi` and the session uses DeepSeek V4 Pro
(`deepseek-v4-pro` — the OpenAI-compat catalog id).

When that CLI is ready (binary + DeepSeek key), C-BRIEF **must** dispatch via
the Kimi subprocess — including store-complete inventories that are 100%
REUSE/COMPOSE (no GENERATE gaps). Empty `inventory_gaps` is not a skip
(sess_9d0b43c81b2b4620 thin SUCCESS in ~13s with `stub_rate=1.0`). Leftover
`FACTORY_BRIEF_REQUIRE_CLI=0` / `FACTORY_BRIEF_DISPATCH=0`,
COLLECTOR/GENERATE factory-LLM fallthrough, and leftover walls ≤600s
(sess_b9fbae7 ~47s `FAILED_BUDGET_SPENT` on OpenRouter `minimax-m3:free`)
must not send WRITER through in-process OpenRouter. Budget inspect /
pilot_open must not SUCCESS thin templates (`written=0`, `stub_rate≈1.0`)
before a real stage-1 wall (≥1800s). HTTP oneshot stays CI-only.

The production image (`./Dockerfile`) installs official Kimi Code at
`/usr/local/bin/kimi`. Official Claude Code may still be planted at
`/usr/local/bin/claude` as leftover unused binary — it is **not** the
DeepSeek vehicle.

Fail-closed (same named classes as Moonshot Kimi; no HTTP oneshot takeover):

| Condition | Blocker |
|-----------|---------|
| `kimi` not on `PATH` while DeepSeek is the selected coder | `FACTORY_CODE_CLI_UNAVAILABLE` |
| DeepSeek selected, `DEEPSEEK_API_KEY` unset | `FACTORY_CODE_CLI_CREDENTIALS_MISSING` |
| 404 / permission denied on the model | `FACTORY_CODE_CLI_MODEL_DENIED` |
| leftover `[claude-code:unrecognized_model]` (or similar SDK reject) | `FACTORY_CODE_CLI_MODEL_DENIED` |
| 429 / insufficient balance / quota | `FACTORY_CODE_CLI_BILLING` |

None of those paths claim a ≥2h CLI session or founding-customer-ready /
`pilot_zip`. `FACTORY_BRIEF_HTTP_ONESHOT=1` is CI/dev only.

`GET /health` → `factory_code_cli` reports `provider`, `deepseek_key_present`
(boolean only), `default_model`, and the named `blocker`.

Keep historical Moonshot: set `FACTORY_CODE_PROVIDER=kimi` and
`KIMI_CODE_API_KEY` (see [KIMI_ENV_SETUP.md](KIMI_ENV_SETUP.md)). That
explicit provider wins even when `DEEPSEEK_API_KEY` is also present.

Leftover `FACTORY_CODE_CLI=claude` while DeepSeek is selected remaps to
`kimi`.

## Render dashboard (source of truth; not in git)

Set these after merge. This doc does **not** claim the dashboard is already set.

| Variable | Required | Role |
|----------|----------|------|
| `DEEPSEEK_API_KEY` | yes (DeepSeek path) | DeepSeek Platform key. Boot writes `[providers.deepseek]` and dispatch injects `KIMI_MODEL_API_KEY` / `DEEPSEEK_API_KEY` on the **kimi subprocess only** |
| `FACTORY_CODE_CLI` | recommended | `kimi`. When unset, `DEEPSEEK_API_KEY` defaults the binary to `kimi`. Leftover `claude` remaps to `kimi` |
| `FACTORY_CODE_PROVIDER` | optional | `deepseek` forces the DeepSeek coder even before the key is present (fail-closed `CREDENTIALS_MISSING`). `kimi` keeps historical Moonshot |
| `DEEPSEEK_CODE_MODEL` | optional | Override; default `deepseek-v4-pro`. Do not set `deepseek-v4-pro[1m]` or leftover `claude-opus-4-6` |
| `DEEPSEEK_BASE_URL` | optional | Default `https://api.deepseek.com` |
| `DEEPSEEK_THINKING_EFFORT` | optional | Default `high` (`KIMI_MODEL_THINKING_EFFORT`) |
| `KIMI_CODE_HOME` | optional | Render: `/app/.kimi-code` |
| `FACTORY_CODER_TIMEOUT_S` | recommended (C-BRIEF wall) | `7200` so the kimi/DeepSeek wait + Floor `deadline_s` are ~7230s (timeout + 30s grace). A leftover `1800` pin freezes dispatch at `STAGE_1-15=1785s` and kills a quiet CLI before harvest (sess_2fba31ab1a194a73). `1200` in `render.yaml` is the **HTTP hang abort** only — CLI dispatch does not freeze at 1785 when TIMEOUT or `FACTORY_CODER_BUDGET_S` is higher |
| `FACTORY_CODER_BUDGET_S` | recommended | `7200`. Honoured by the C-BRIEF CLI wait when ≥1800. Keep Floor chat on OpenRouter; DeepSeek stays on the kimi subprocess |
| `FACTORY_CODER_ATTEMPT_WALL_S` | optional | Override the Floor calling-NOTE twin. Default follows TIMEOUT (`7200` → `7230`) |

Do **not** set process-wide `ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic`
or `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` on the web service. That is
the dead Claude Code path and would leak into Floor chat.

Do **not** set `ANTHROPIC_MODEL=deepseek-v4-pro[1m]` or leftover
`ANTHROPIC_MODEL=claude-opus-4-6`. Factory ignores those for the live
Kimi→DeepSeek path and uses `deepseek-v4-pro`.

Leave Floor chat on OpenRouter. Do not put API keys in this repo.

## How Kimi talks to DeepSeek

Boot writes `~/.kimi-code/config.toml` (or `$KIMI_CODE_HOME/config.toml`):

```toml
default_model = "deepseek-v4-pro"

[providers.deepseek]
type = "openai"
api_key = "sk-..."
base_url = "https://api.deepseek.com"

[models."deepseek-v4-pro"]
provider = "deepseek"
model = "deepseek-v4-pro"
max_context_size = 1048576
```

Dispatch also injects official Kimi session-only env (`KIMI_MODEL_NAME`,
`KIMI_MODEL_API_KEY`, `KIMI_MODEL_PROVIDER_TYPE=openai`,
`KIMI_MODEL_BASE_URL`, `KIMI_MODEL_MAX_CONTEXT_SIZE`) so headless
`kimi --prompt` does not depend solely on the file.

Headless Floor uses `kimi --prompt "<coder_brief.md body>" --add-dir . --model deepseek-v4-pro`
and pipes the same brief on stdin. A trailing `@docs/coder_brief.md`
mention is **not** a prompt.

## Local / Cloud Agent

```bash
# Required for DeepSeek C-BRIEF (never commit the key)
export DEEPSEEK_API_KEY='sk-...'
export FACTORY_CODE_CLI=kimi
# optional; default when DEEPSEEK_API_KEY is set
# export FACTORY_CODE_PROVIDER=deepseek
```

Install Kimi Code on the host (`kimi` on `PATH`) or use the production
image. `backend/.env.example` lists the same names as comments.

## Role reminder

| Actor | Job |
|-------|-----|
| **OpenRouter / architect chat** | Floor conversation + blueprint talk |
| **DeepSeek V4 Pro via Kimi Code** | Factory Engineer C-BRIEF (one gated ≥2h-capable CLI session) |
| **Kimi / Moonshot** | Alternate FACTORY_CODE_CLI when `FACTORY_CODE_PROVIDER=kimi` + `KIMI_CODE_API_KEY` |
| **CerebrumDev.ai** | Governance, dual registry, certify, regenerate |
