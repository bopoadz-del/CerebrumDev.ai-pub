# MR. FINANCE Writer control (Owner lock 2026-09-14)

Floor must not auto-start Cursor BA Writer after Cloner. MR. FINANCE
launches Writer. After Writer (including cli-pivot BA) the **TESTER**
acceptance inspector must run before STORE_MANAGER / N3.

Kernels (`docs/factory/AGENT_IN_THE_KERNELS.md`):

| Kernel | Job |
|---|---|
| WRITER | Platform manufacturer (agent) |
| TESTER | Acceptance inspector (agent consult) |
| STORE_MANAGER | Store registrar |

## Post-Cloner hold

After CLONER succeeds and `handoff_after_cloner` fires (GitHub issue +
optional `DOMAIN_HANDOFF_WEBHOOK_URL`):

1. The same Generate / Continue autopilot must **not** enter WRITER,
   `run_cli_pivot`, `run_background_agent`, or `create_agent`.
2. Session honesty is `awaiting_mr_finance_writer`. Floor shows
   **Awaiting MR. FINANCE to launch Writer** and a **Launch Writer**
   button (sends `continue`).
3. WRITER / BA starts only when MR. FINANCE (or an explicit Floor action
   he owns) triggers Continue / `start_coder`.

Env gate: `FACTORY_WRITER_REQUIRES_HANDOFF` (default **ON** in production
and dev). Explicit `0`/`false` disables. Unset under `ENV=test` or pytest
keeps prior full-pipeline autopilot so unit tests do not all need the hold.

## Writer → TESTER → STORE_MANAGER

cli-pivot Writer success used to `_finish(HANDOFF_TO_N3)` immediately
(`runner.py`), skipping TESTER. STORE_MANAGER / N3 could still run.

Required order:

1. WRITER (cli-pivot BA or in-process) succeeds
2. Advance to TESTER; run the acceptance inspector gate
3. TESTER fail → existing rework loop back to WRITER (max rework unchanged)
4. TESTER pass → STORE_MANAGER
5. Then N3 GitHub store-gate (`HANDOFF_TO_N3`) as the Store-green proof

N3 remains Store-green proof. TESTER must have run first.

## MR. FINANCE after Cloner handoff

1. Review Collector + Cloner
2. Fix gaps
3. Launch Writer
4. Tester
5. Audit
6. Release

Grok chat is not the delivery surface.

See also `docs/factory/DOMAIN_HANDOFF.md` and `ROLESANDTASKS.md`.
