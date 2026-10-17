# Live test: build a platform with the agent

Set one key, run one command. This is the role runner — the agent-manufactured
path. It is **opt-in and does not affect production**; `ProductGenerator` is
still what the live service calls.

---

## 1. Set the key

Cursor is the Factory coding provider (`LLM_PROVIDER=cursor`). HTTP
Floor chat / architect draft use `CEREBRUM_CHAT_LLM_*`. Put this in
`backend/.env`:

```bash
LLM_PROVIDER=cursor
CURSOR_API_KEY=<your Cursor key>   # or CURSOR_AGENT_API_KEY / FACTORY_CURSOR_API_KEY
CEREBRUM_CHAT_LLM_API_KEY=<Floor chat key>
CEREBRUM_CHAT_LLM_BASE_URL=<OpenAI-compatible host>
CEREBRUM_CHAT_LLM_MODEL=<chat model>
FACTORY_CODER_ENABLED=1
```

To use Claude instead:

```bash
LLM_PROVIDER=claude
ANTHROPIC_API_KEY=<your Anthropic key>
FACTORY_CODER_ENABLED=1
```

`FACTORY_RUNNER_ENABLED` is **not** needed for the CLI — that flag gates the
runner inside the service. The CLI command below always uses the runner.

### The model matters more than anything else here

Measured on the same blueprint, same code:

| Model | Result |
|---|---|
| **`kimi-k2.7-code`** | **SUCCESS, rework 0, 7/10 artifacts agent-written** — use this |
| `moonshot-v1-8k` | FAILED_BUDGET_SPENT — the agent's route returned `None`; three rework rounds could not fix it |
| `kimi-k2.5-code` | does not exist on the API (`404`); it was the old fallback default, now corrected |
| no key | SUCCESS, fully templated, `agent_written: 0` |

`kimi-k2.7-code` writes a route that validates every field against the schema
it designed itself, type-checks each one, rejects empty strings and wraps the
handler in try/except — and it passes the strict route test on the first
attempt. `moonshot-v1-8k` is the weakest model on the endpoint and declares
one type in the model while checking another in the route.

> **Historical note, because it reversed this advice.** Until `coder.py` was
> fixed, every K2/K3 model failed with
> `400 invalid temperature: only 1 is allowed for this model` — the coder
> hardcoded `temperature: 0.2`, which reasoning models reject. Builds still
> reported SUCCESS with everything templated, which read as *"the capable
> models don't work, use the weak one."* The opposite was true. If you ever
> see `coder_failures` full of `HTTPStatusError`, suspect the request before
> the model.

If your key cannot reach the model you set, the build still succeeds — but
every artifact is templated and `coder_failures` says so. Read that field
before concluding the agent worked.

---

## 2. Run one build

```bash
cd backend
python -m app.factory.cli build \
  --blueprint ../blueprints/examples/runner_smoke.yaml \
  --out /tmp/my-platform
```

Useful flags: `--max-rework 3` (writer/tester rounds before the run fails),
`--wall-clock 7200` (seconds; `0` disables).

Point `--blueprint` at any blueprint under `blueprints/`. Capabilities must
reference dual-registered blocks or the planner fails closed.

---

## 3. Read the result

The command prints JSON and **exits non-zero on any failure** — a spent budget
is a failure, not a delivery.

```json
{
  "outcome": "SUCCESS",
  "ok": true,
  "artifacts": 10,
  "agent_written": 7,
  "agent_artifacts": ["analytics_surface", "model:analytics_surface", "..."],
  "coder_failures": {},
  "ledger": "/tmp/my-platform/build_ledger.jsonl"
}
```

**`outcome: SUCCESS` does not by itself mean the agent worked.** A fully
templated build also succeeds. The two fields that tell you what actually
happened are `agent_written` and `coder_failures`.

### The ledger

`build_ledger.jsonl` is append-only, one JSON object per line, and is the
whole record of the run:

```bash
python -c "import json,sys; [print(json.loads(l)['kind'], json.loads(l).get('role') or '-', json.loads(l)['detail']) for l in open(sys.argv[1])]" \
  /tmp/my-platform/build_ledger.jsonl
```

A healthy run ends `RUN_SUCCEEDED` with a `GATE_PASSED` for all five phases:
COLLECTOR → CLONER → WRITER → TESTER → STORE_MANAGER.

---

## 4. What "good" looks like

```
GATE_PASSED  COLLECTOR      0 gap(s) declared for the writer
GATE_PASSED  CLONER         2 block(s) import with no store configured
GATE_PASSED  WRITER         app/ compiles
GATE_PASSED  TESTER         7 passed in 0.38s
GATE_PASSED  STORE_MANAGER  no store ops applied
RUN_SUCCEEDED               all phase gates passed
```

Then check the platform itself:

```bash
cd /tmp/my-platform
python -m pytest tests -q        # its own suite, written by the TESTER
pip install -r requirements.txt
uvicorn app.main:app             # GET /health -> 200
```

The delivered tree (~23 files):

| Path | What |
|---|---|
| `app/models.py` | domain dataclasses (agent-designed schema) |
| `app/store.py` | sqlite persistence, derived from the same schema |
| `app/routes.py` | FastAPI POST + GET per capability |
| `app/actions/` | capability handlers |
| `app/dispatch.py` | local block dispatch |
| `app/main.py` | entrypoint |
| `vendor/blocks/` | vendored block source, pinned by `blocks.lock.json` |
| `tests/` | dispatch, persistence round-trip, route shape |
| `README.md`, `requirements.txt` | run scaffold |

**It runs offline.** No `httpx`, no `/v1/execute`, no store URL anywhere in the
artifact. That is the point of this path: the platform keeps working with the
block store switched off. If you find a network call in a delivered platform,
that is a bug worth reporting.

---

## 5. What failure looks like

Each of these is the system working — it refuses to deliver a platform that
does not pass its own gates.

**Budget spent** — the agent could not converge:

```
GATE_FAILED  TESTER   suite is red
REWORK       WRITER   round 1: suite is red
...
RUN_FAILED   TESTER   rework budget of 3 exhausted; TESTER gate still failing
```
`outcome: FAILED_BUDGET_SPENT`, exit 1. Try a stronger model, or raise
`--max-rework`. **This is the expected result with `moonshot-v1-8k`.**

**Gate failed outside the rework loop** — `FAILED_GATE`, exit 1. Only the
TESTER can send work back; a failed COLLECTOR or CLONER gate is terminal.
`findings` says which check and why.

**Lane violation** — `FAILED_AUTHORITY`, exit 1. A role tried to write outside
its mandate (e.g. the WRITER touching `tests/`). This should never happen; it
means a bug in a role, not in your setup.

**Silent template fallback** — `outcome: SUCCESS` but `agent_written: 0` and
`coder_failures` populated. The build is real and runnable, but no agent
touched it. Almost always a key or model problem.

---

## 6. Resuming

A killed build resumes from its ledger — rerun the same command with the same
`--out` and it picks up at the first phase that has not passed. Resume is keyed
on a hash of the **blueprint**, so editing the blueprint mid-run is refused
rather than silently mixing two builds.

---

## Known limits

- **Cutover has not happened.** This path is not what the deployed service
  runs. Testing it here tells you whether it is ready to become that.
- **STORE_MANAGER records only.** It logs which blocks were cloned and executes
  no store operation. Harvesting improvements back into the store, and adding
  client-requested blocks to inventory, are not built.
- **`CEREBRUM_LLM_API_KEY` is unset on the Render backend**, so a production
  factory run cannot use the agent at all until that dashboard secret is set.
- Persistence is stdlib sqlite3 on a local file. Fine for a test, not a
  multi-tenant production store.
