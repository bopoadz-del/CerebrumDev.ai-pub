# Milestone 4 — Build Mode workbench

**Branch:** `feat/build-mode-workbench`  
**Status:** execution arm for human-approved change-requests (flags default OFF)

## What shipped

Sandboxed Kimi Code workbench that consumes **approved** M3 queue items and produces
**candidate change sets**, re-runs DNA acceptance gates, then promotes **only** through
the Factory packager to **staging/community**. Never deploys. Never writes to the Store.
Never bypasses the packager.

```
queue (approved)
    → workbench sandbox (DNA + request + mutable envelope)
    → candidate_ready (diff + summary + transcript)
    → gates (gate_passed | gate_failed → re-approval)
    → ProductGenerator packager
    → staging/community package (bumped DNA + lockfile + checksums)
```

Certified / production-tier promotion is **out of scope** here — it still requires the
existing **chain-approve** path on the Factory configurator. The workbench has **no
deploy credentials**.

## Flags (default OFF)

| Flag | Default | Purpose |
|------|---------|---------|
| `BUILD_MODE_ENABLED` | `false` | Workbench APIs + session / gates / staging promotion |
| `KIMI_WORKBENCH_ENABLED` | `false` | When false: honest labeled **Factory regenerator stub**. When true: attempt Kimi Code CLI (`KIMI_CODE_CLI` / Ollama model) still confined by the sandbox |
| `CHANGE_REQUEST_INTAKE_ENABLED` | `false` | M3 intake/queue (required upstream) |
| `WORKBENCH_SESSION_TIMEOUT_SECONDS` | `300` | Hard session timeout (dies loudly) |
| `WORKBENCH_MAX_COMMANDS` | `200` | Command cap |

> **Network honesty:** the workbench sandbox does **not** restrict network
> egress. Subprocesses inherit the host's network access. Real enforcement
> would need a network namespace, egress proxy, or firewall rules — none of
> which exist today.

Documented in the root README feature-flag table.

## Sandbox contract

| Rule | Enforcement |
|------|-------------|
| Filesystem | Confined to `{STORAGE_PATH}/workbench/sessions/{id}/` |
| Mutable paths | `blueprint/`, `candidate/`, `transcript/`, `task_envelope.json` |
| Read-only seed | `product-dna/` (checksum-verified), `baseline/`, `request.json` |
| Network | Allowlist only; Store write hosts/credentials absent |
| Shell | `subprocess` with `shell=False`; argv paths must stay in workspace |
| Secrets | `RENDER_API_KEY`, `GITHUB_TOKEN`, `DEPLOY_REPO_URL`, Store write tokens stripped |
| Timeout / caps | Exceed → `SandboxTimeout` (loud failure, not silent hang) |
| Logging | Every command / boundary refusal appended to `transcript/session.jsonl` |

The **task envelope** is the agent boundary: DNA + approved request + M2-style mutable
surfaces (from dry-run `would_change` + allowlisted heal catalog). Actions outside the
envelope are refused and logged.

## Queue states (M3 + M4)

```
received → validated → dry_run_evaluated → awaiting_approval
  → approved | rejected
  → workbench_running → candidate_ready
  → gate_passed | gate_failed
  → staged
```

- `approved` still means **decision only** (`executes: false`) until workbench runs.
- `gate_failed` attaches evidence; **re-approval required** (no silent retry).
- `staged` = packager emitted a new product version at staging/community.

## Candidate artifact

Attached on the queue item (`candidate`):

- unified `diff`
- `summary`
- session `transcript`
- `mutation` (blueprint + pin_versions + target `product_dna_version`)
- `checksum` (tamper detection — packager refuses mismatches)

## Promotion path

1. Gate-passed candidate checksum verified again at packager entry.
2. `ProductGenerator` regenerates product with bumped `product_dna_version` and
   updated `block_lockfile` pins.
3. `checksum_manifest.json` regenerated.
4. Package lands under `{STORAGE_PATH}/workbench/staging/{product_id}/{version}/`
   with `STAGING_PROMOTION.json` receipt (`trust_tier: staging/community`,
   `certified: false`, `deployed: false`).

## What a PRODUCTION promotion path would require

Already exists outside the workbench:

1. Human **chain-approve** on the Factory session (`POST .../chain/approve`).
2. Deploy pipeline credentials (`RENDER_API_KEY`, `DEPLOY_REPO_URL`, etc.) — **never**
   injected into the workbench sandbox.
3. Certified / reviewed trust-tier gates beyond community/staging.

M4 deliberately stops at staging/community so the human keeps the production key.

## APIs

| Method | Path | Notes |
|--------|------|-------|
| GET | `/v1/workbench/status` | Always; reports flags |
| POST | `/v1/workbench/run` | Approved CR → session → candidate → gates |
| POST | `/v1/workbench/gates/{id}` | Re-run gates |
| POST | `/v1/workbench/promote/{id}` | Packager → staging |
| GET | `/v1/workbench/queue` | List (flag) |
| GET | `/v1/workbench/queue/{id}` | Get (flag) |

## UI

Factory frontend **Build mode** tab (`WorkbenchPanel`): queue states, Run Workbench,
candidate diff, gate results, Promote via Packager (staging). Operator tool — functional,
not beautiful.

## Tests

`backend/tests/workbench/test_build_mode_workbench.py`:

- Flag off → 503
- Sandbox escape refused + logged
- Store write impossible (no credentials)
- Tampered candidate → checksum failure at packager
- Steward golden EXPANSION round-trip → staged package with bumped DNA + lockfile + audit trail

## Out of scope (unchanged)

- Production-tier promotion (chain-approve)
- Multi-product fleet operations
- Autonomous Resident L3–L5 execution — M4 runs **human-approved** requests only
