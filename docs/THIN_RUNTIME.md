# THIN RUNTIME — The Crane Cabin for the Factory's Coding Agent
**Status: implemented (v0.1)** — `runtime/` package, acceptance criteria tested in `backend/tests/runtime/` · Pairs with `docs/CODINGPROTOCOLS.md` (the law)

The thin runtime is the ~400-line program that carries an LLM through the
protocol loop. It is the crane **cabin**, never the engine: the model is a
rented brain behind one function signature; the cabin — this runtime — owns
the controls, the envelope, and the brakes.

## Design laws

1. **The engine is config.** `ENGINE_PROFILE` selects the brain
   (`cloud_api` / `ollama_cloud` / `local_sovereign`). Swapping brains changes
   one env var. The loop, the envelope, and the gates do not change.
2. **The protocols are loaded, not baked.** The runtime reads
   `CODINGPROTOCOLS.md` at startup, stamps its version into every report, and
   halts if the file is missing or unparsable. Law is data.
3. **The loop is enforced in code.** Article 2's six steps are the control
   flow — not a prompt suggestion. The model cannot skip verification because
   the runtime, not the model, sequences the steps.
4. **Small enough to audit in one sitting.** ~400 lines total. No framework,
   no plugin system, no cleverness. If a feature doesn't serve the loop, it
   doesn't exist.

## Modules

| Module | Lines (est.) | Job |
|---|---|---|
| `runtime.py` | ~80 | Main loop. Sequences the six protocol steps. Owns the run state. |
| `change_request.py` | ~40 | Load + validate an approved change-request (schema check, signature present, scope parsed). |
| `dna.py` | ~50 | Load the product DNA bundle. **Verify checksums FIRST** — mismatch = loud halt before anything else runs. |
| `protocols.py` | ~30 | Load `CODINGPROTOCOLS.md`, extract version, expose STOP conditions and anti-patterns to the system prompt. |
| `envelope.py` | ~60 | The guard. Agent declares intended files → every write tool call is checked against the declaration. Write outside the set = immediate halt + escalation report. |
| `engine.py` | ~60 | Profile resolver → one function: `complete(messages) -> str`. Retries once on transient failure; second failure = STOP (gate-failed-twice law). |
| `gates.py` | ~50 | Run tests / golden sets / evals as subprocesses. Parse exit codes and summaries. Never interprets — reports. |
| `report.py` | ~40 | Emit the Article 6 output contract (JSON + human markdown). A run without a valid report is an incomplete run. |
| `stop.py` | ~40 | Structured halt: condition, evidence, exact input needed to resume. Every halt path in every module funnels here. |

## The loop (as code)

```python
def run(cr_path: str) -> Report:
    cr = change_request.load(cr_path)            # 1. READ — validated, signed
    dna_bundle = dna.load(cr.product_id)         # 2. LOAD — checksums first
    law = protocols.load()                       #    law version stamped
    brain = engine.resolve()                     #    ENGINE_PROFILE -> complete()

    declaration = agent_declare(brain, cr, dna_bundle, law)   # 3. DECLARE
    envelope.guard(declaration)

    implement(brain, cr, dna_bundle, envelope)   # 4. IMPLEMENT (writes guarded)

    gate_results = gates.run_all(cr.product_id)  # 5. GATES
    if gate_results.failed_twice():
        stop.halt("gate_failed_twice", evidence=gate_results)

    return report.emit(                          # 6. REPORT (contract or bust)
        files_changed=envelope.actual_writes(),
        gate_results=gate_results,
        protocol_version=law.version,
    )
```

`agent_declare` and `implement` are the only two places the model speaks.
Everything else is plumbing with opinions.

## Tool surface (the only hands the model gets)

| Tool | Guard |
|---|---|
| `read_file(path)` | none |
| `write_file(path, content)` | **envelope** — path must be in the declared set |
| `run_command(cmd)` | allowlist (`pytest`, `npm test`, `git status/diff/add/commit`) |
| `git_branch(name)` / `git_pr(title, body)` | one branch, one PR per run |

No network tool. No store-write tool — cloning from the Store is a
`run_command` git operation into the product workspace, and there is
physically no tool that writes back.

## Config surface

| Var | Default | Meaning |
|---|---|---|
| `ENGINE_PROFILE` | `cloud_api` | Which brain drives the cabin |
| `PROTOCOLS_PATH` | `docs/CODINGPROTOCOLS.md` | Where the law lives |
| `GATES_COMMANDS` | repo's standard CI set | What step 5 runs |
| provider keys (`OPENAI_API_KEY`, `OLLAMA_URL`, …) | — | per profile, env only, never in repo |

## Non-goals (write them down or they creep in)

- No planning creativity — the change-request is the plan.
- No Store writes — not gated, **absent**.
- No self-modification — the runtime cannot edit the protocols or itself.
- No UI — it runs headless; the factory UI reads its reports.
- No memory between runs — DNA bundle + change-request is the whole world.

## Acceptance criteria

1. Completes an M-sized change-request end-to-end with a valid Article 6 report.
2. Envelope violation (write outside declared set) halts the run with a
   structured escalation — tested.
3. DNA checksum failure halts before any model call — tested.
4. Same change-request run under two different `ENGINE_PROFILE`s produces
   structurally identical reports (steps, gates, contract) — brains differ,
   law doesn't.
5. Total size auditable: `wc -l runtime/` ≤ 500.
