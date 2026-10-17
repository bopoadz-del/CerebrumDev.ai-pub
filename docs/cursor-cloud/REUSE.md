# Reuse this Cursor Cloud environment across repos

Cursor resolves environments **per repository** (repo `.cursor/environment.json` → personal saved env → team saved env). There is no single account-wide environment toggle. Use this pattern to configure once and copy lightly.

## What's in this repo

| Path | Role |
| ---- | ---- |
| [`.cursor/Dockerfile`](../../.cursor/Dockerfile) | Base image (Python 3 + Node 20 + build tools). Used by cloud agents for this repo. |
| [`.cursor/environment.json`](../../.cursor/environment.json) | This repo's install + multitasking terminals (backend `:8000`, frontend `:5173`). |
| [`reusable-base/`](./reusable-base/) | Portable copies to drop into other repositories. |

## Apply to another repo (≈2 minutes)

1. Copy the base image:
   ```bash
   mkdir -p .cursor
   cp docs/cursor-cloud/reusable-base/Dockerfile OTHER_REPO/.cursor/Dockerfile
   ```
2. Copy and edit the environment template:
   ```bash
   cp docs/cursor-cloud/reusable-base/environment.json.template OTHER_REPO/.cursor/environment.json
   ```
3. Set `install` to that repo's idempotent dependency command (e.g. `npm ci`, `pip install -r requirements.txt`, or both).
4. Set `terminals` to the processes that should stay up (API, web, worker).
5. Commit and push; start a cloud agent from that branch so it picks up the new config.

## Account-wide pieces (dashboard — do once)

These are the only settings that apply across every repo without committing files:

1. **Personal / team secrets** — [Cloud Agents → Secrets](https://cursor.com/dashboard/cloud-agents). API keys, tokens. Personal overrides team of the same name.
2. **Personal or team saved environment** — default when a repo has no `.cursor/environment.json`. Prefer keeping the shared Dockerfile + a tiny per-repo `environment.json` so each repo's `install` stays correct.
3. **Network policies / default model** — team settings on the same dashboard.

## Multitasking services

`terminals` in `environment.json` start long-lived processes in a shared tmux session when the VM boots. This repo starts backend + frontend together. For other stacks, add one terminal entry per service.
