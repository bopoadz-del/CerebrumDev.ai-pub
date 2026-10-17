# AUDIT_BASELINE — Factory CLI-pivot (N0) — 2026-09-12

The map before any cut, per N0. Classifies every module the internal-author path owns vs
the CLI/gate path, and flags SHARED modules with both call sites. Ready to commit to
`CerebrumDev.ai` as `AUDIT_BASELINE.md` (owner/agent pushes it — N0's "commit the map first").

Baseline tip: `master` post-#409/#411 (`71c3116`+). 55 modules under `backend/app/factory/build/`.

## HEADLINE FINDING — the cut is not clean

The author path is **not** an isolable set of files you can `rm`. `writer_phase`, `TESTER`,
`BLOCK_DEFAULT_ACTIONS` and `_templated_body` thread through a dozen-plus modules, and the
decisive ones are **SHARED**: the same file holds the surviving vendoring / brief-compile /
receipt-parse / budget code *and* the author-path code. N2 is therefore an **extract-then-delete
refactor across ~7 shared modules**, and every regression risk lands on the **surviving** path
(COLLECTOR/CLONER vendoring, budgets, brief compile). This is the strongest argument for
prove-before-delete: touching these files risks the survivors, so the replacement must be green
first.

## A. PURE INTERNAL-AUTHOR — delete candidates (no CLI/gate caller)

| module | what it is | note |
|---|---|---|
| `writer_phases.py` | three-phase WRITER machinery (`writer_phase`, `compile_phase_brief`, `accept_writer_phase`, `checkpoint_landed_phase`) | the phase-stamp system behind every run7–9 wall |
| `writer_behaviour.py` | self-grading TESTER probes (`_recording_execute`, `_forced_failure`, round-trip/refusal classification) | the self-grade N3 replaces |
| `writer_brief.py` | `writer_system_brief` — the internal-author system prompt | |
| `writer_fanout.py` | `fan_out`, `coder_fanout`, `loop_budget_too_low` | one-shot CLI has no fanout; whole module goes |
| `product_gate.py` | `gate_round_trip`, `gate_product` — **Factory grading its own build** | N3 makes the artifact's own `acceptance.py`-in-Docker the only grade; this is the self-grade to retire |

## B. CLI / GATE PATH — keep and promote

| module | role in the new path |
|---|---|
| `code_cli.py` | the `FACTORY_CODE_CLI` seam → becomes the **`cli` driver** (on-prem) under N1a |
| `coder_session.py` | dispatch + **receipt parsing** (`cli_authored_ids`) → keep the receipt half; see SHARED |
| `brief_compiler.py` | `compile_brief` / `fill_template` / deterministic fill → the **compose-C-BRIEF** step, heart of N1; see SHARED (`render_slot_bodies`) |
| `store_acceptance.py` | `parse_acceptance_output`, `acceptance_is_kk`, 12-line render/parse → **the N3 gate**, becomes central |
| `harvest.py` | `evaluate_harvest`, store-write authorization → vendoring/registry, survives |
| `registrar.py`, `reuse_lookup.py`, `reuse_accept.py`, `vendored_integrity.py` | registry resolution + REUSE vendoring at pinned commits → survive (COLLECTOR/CLONER support) |

## C. SHARED — the risk zone, split not delete (two call sites each)

| module | KEEP leg | DELETE leg | action |
|---|---|---|---|
| `roles.py` (2400+ ln, method-heavy) | COLLECTOR/CLONER role dispatch | coder micro-loops **@2373/2418/2468**, `_templated_body` | extract the two surviving roles; delete the WRITER/TESTER loops + template body |
| `roles_handlers.py` | `run_collector`, `_pin_source`, `_vendor_mirror_dir`, block cloning | `_templated_body`, `accept_writer_phase` | split: keep vendoring, delete author emitters |
| `runner.py` | build orchestration, COLLECTOR/CLONER phases, S07 ceiling wiring | WRITER/TESTER phase drivers | remove only the author phases from the phase list |
| `coder_session.py` | receipt parse, `classify_cli_exit`, driver dispatch | plant-vs-author harvest reconciliation (`factory_planted_ids`, `merge_writer_phase_dispatch`) | keep receipt+dispatch; the plant logic goes with the author path |
| `budget_inspect.py` | S07 hard ceiling (`CEILING_S`, `_extend_wall` clamp) — **FORBIDDEN to touch** | WRITER-loop budget inspection | keep the ceiling; drop the loop budget |
| `brief_compiler.py` | brief text fill, slot rendering for the brief | `render_slot_bodies` **if it emits handler bodies** — verify | confirm at N0-close: brief-text = keep, handler-body = delete |
| `roles_constants.py` | shared role identifiers | `BLOCK_DEFAULT_ACTIONS` emitter constants | keep ids, drop the default-action emitters |

## D. Grep gate for CI (N2) — must fail on regrowth

```
_templated_body | BLOCK_DEFAULT_ACTIONS.*emit | writer_phase | gate_product\(
```

Any hit after N2 = author path regrowing = CI fail. Add `product_gate.gate_product` to the
banned set — it is the self-grade, not just the templates.

## E. Open question to resolve before N0 closes

`brief_compiler.render_slot_bodies` (line 422) — does it render **brief text** (keep) or
**handler bodies** (delete)? It is the one module that could be either. Read it; if it emits
executable handler bodies, that leg moves to bucket A and the brief-compile keep-leg narrows.

---

*N0 map, 2026-09-12. Non-destructive. Nothing deleted. Commit this before N1.*

## E resolution

**KEEP.** `render_slot_bodies` renders **brief text** (C-BRIEF template slots), not executable handler bodies. The brief-compile keep-leg stays: `compile_brief` / `fill_template` / `render_slot_bodies`. Handler-body emission is a different DELETE-leg (`roles_handlers._templated_body`).

Evidence (tip `2fc962d`, 2026-09-12):

- `backend/app/factory/build/brief_compiler.py:422` defines `render_slot_bodies`. Docstring at `:436` is `Deterministic slot fill. No LLM. Returns template slot → body.`
- Return at `brief_compiler.py:702-709` is `{"TARGET", "INVENTORY", "VALIDATE", "BUILD", "ACCEPTANCE", "FORBIDDEN"}` — markdown/prose sections for `BRIEF_TEMPLATE.md`, not `def handle(` Python.
- Callers `render_gated_brief` (`:778` / `:783`) and `compile_brief` (`:850`) pass those slots to `fill_template(load_brief_template(), slots)` (`:796`, `:863`). The BUILD slot *instructs* the coder to author `app/actions/{capability_id}.py`; it does not write those files.
- Executable handler-body emission lives at `backend/app/factory/build/roles_handlers.py:1377` (`_templated_body`) — that is the DELETE-leg already named in table C for `roles_handlers.py`.

## Owner 2026-09-12 — Option C Hybrid (two jails)

**C Hybrid — not a blanket A expand.** Two path jails stay distinct:

| jail | `tests/**` | until |
|---|---|---|
| cerebrum-builds / cli-pivot BA (`cli_receipt.ba_allowed_globs`) | **allowed** | now |
| in-process Factory WRITER (`authority.py` WRITER lanes) | **sealed** | N2 |

A Background Agent on the cli-pivot path may land the product test suite
alongside handlers. The in-process Factory WRITER that grades itself must
**not** write `tests/**` until N2 — TESTER still owns that lane. Do not
blanket-expand in-process WRITER to `tests/**`.

**12/12 remains cheat-resistance**: a clean receipt+diff is still
`HANDOFF_TO_N3`, never green. The N3 store gate (`scripts/acceptance.py`
in Docker, k/12 of the named floor) is the only green.

Still never expand either jail to:

- `vendor/**` / `vendor_blocks/**` / `vendor_blocks_mirror/**`
- `blocks.lock.json`
- `build_ledger.jsonl` (ledger)
- `.git/**`

N3 / G floor names stay: `ci_present_full_suite`, `authorship==receipt`. Factory
`store_acceptance.ACCEPTANCE_CHECK_NAMES` still uses the pre-G aliases
(`ci_present_and_full_suite`, `authorship_floor`); that is the old self-grade
and is not rewritten here.
