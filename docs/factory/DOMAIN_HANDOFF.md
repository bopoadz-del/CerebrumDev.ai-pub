# Post-CLONER C-BRIEF staging

> **This document described a flow that no longer exists.** The MR.FINANCE
> reach channel (GitHub issue on `CEREBRUM_BUILDS_REPO`, the
> `docs/domain_handoff.json` marker, the Cursor automation webhook and its
> `DOMAIN_HANDOFF_WEBHOOK_*` env keys) was excised, and the domain-spec
> table that resolved a "business vertical" (`DomainSpec`, `FINANCE_SPEC`,
> `AUTOMOTIVE_SPEC`, `detect_domain`, `is_finance_domain`, …) was cut
> afterwards: it had **zero** consumers in `backend/app`, `backend/scripts`
> or `frontend/src` — only the tests that existed to reference it. The file
> is kept, rewritten, so the deleted flow is not re-justified from a stale
> page.

## What the post-CLONER slot does now

One thing: freeze the compiled **C-BRIEF** at `docs/coder_brief.md` so the
WRITER reads the block contracts, REUSE inventory and gap list instead of
authoring the platform blind.

The WRITER runs on **CodeWhale (DeepSeek)** via `codewhale exec`. Without a
staged brief the agent authors from a one-line blueprint summary — the
`sess_9f67681a79324fcc` class of failure.

## Mechanism

`run_cloner` → `ensure_coder_brief(dest, blueprint=…, plan=…, blocks_root=…)`

1. An existing **non-empty** `docs/coder_brief.md` is left exactly as it is.
   A **zero-byte** one is not treated as staged — it is recompiled.
2. With no blueprint, a visible placeholder is written (`brief unavailable`).
   Never silence: a brief that did not land must be readable on disk.
3. Otherwise `compose_cbrief` compiles it deterministically. **No LLM writes
   this text.**

The staging result is recorded in the CLONER role notes under `coder_brief`
(`written`, `path`, `bytes`) so a non-delivery is visible in the build
record rather than swallowed.

## No network

This module makes no network calls and publishes to no external agent.

## Modules

| Module | Role |
|--------|------|
| `backend/app/factory/build/domain_handoff.py` | `ensure_coder_brief` — staging only |
| `backend/app/factory/build/cbrief.py` | `compose_cbrief` — the compiler's front door |
| `backend/app/factory/build/brief_compiler.py` | `compile_brief` — deterministic fill |

`compose_cbrief` lives in `cbrief.py`, **not** in `cli_pivot.py`, so retiring
the Cursor seam cannot take the C-BRIEF compiler with it.

Tests: `backend/tests/factory/test_domain_handoff.py`.
