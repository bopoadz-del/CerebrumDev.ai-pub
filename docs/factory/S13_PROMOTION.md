# S13 promotion evaluate

`PILOT_READY` is machine-emitted by `app.factory.build.promotion.evaluate_promotion`.
Do not stamp it by hand.

## Re-run at current HEAD

From the repo root, on the commit you want recorded in `provenance.git_sha`:

```bash
FACTORY_PILOT_WORKSPACE=build/pilot_workspace ./scripts/evaluate_s13_promotion.sh
```

Equivalent module invocation (what #206 used):

```bash
cd backend
export PYTHONPATH=.
export FACTORY_PILOT_WORKSPACE=../build/pilot_workspace
python -m app.factory.build.promotion
```

The emitter writes:

- `build/stages/S13_promotion.json`
- `build/stages/S13_promotion.reread.json`

`provenance.git_sha` / the reread `git_sha` is `git rev-parse HEAD` at run time.
Commit the twins after the run; do not rewrite the SHA.

## Without a pilot workspace

If `FACTORY_PILOT_WORKSPACE` is unset, U7 fail-closes:

`pilot_cycle:no pilot workspace given — set FACTORY_PILOT_WORKSPACE to the build workspace whose pilot cycle backs this promotion`

That is an honest FAIL, not a missing secret. Stage documents alone cannot promote.

## Hard stops

- Do not create `build/stages/HARVEST_AUTHORIZED.json`. Harvest stays BLOCKED.
- Do not invent a ledger or a workspace. The in-tree `build/pilot_workspace` is the performed RoleRunner smoke from #206.
- Exit `2` means `PILOT_READY` is false; the twins were still written.
