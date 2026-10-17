# Milestone 3 — Change-request loop

**Branch:** `feat/change-request-loop`  
**Status:** paperwork / dry-run only — **no agent execution** (that is M4)

## Flags (default OFF)

| Flag | Default | Purpose |
|------|---------|---------|
| `CHANGE_REQUEST_INTAKE_ENABLED` | `false` | Factory intake + dry-run evaluator + queue mutations |
| `RESIDENT_EMIT_CHANGE_REQUESTS` | `false` | Resident L2 escalations may emit signed REPAIR requests |
| `RESIDENT_ENGINEER_ENABLED` | `false` | Resident Mode routes (M2); required for `/v1/resident/change-request` |
| `REDIS_URL` | unset | Optional queue index; disk JSON under `STORAGE_PATH/change_requests/` always works |
| `PRODUCT_CHANGE_REQUEST_SIGNING_KEY` | unset | Optional product private key (base64 Ed25519); tests generate ephemeral keys |

## Schemas (v1)

Under `backend/app/change_requests/schemas/`:

| Kind | File | Intent |
|------|------|--------|
| **REPAIR** | `repair_request.schema.json` | Restore declared state (drift / failed component / config) |
| **EXPANSION** | `expansion_request.schema.json` | Add capability (block / endpoint / hat) |
| **UPGRADE** | `upgrade_request.schema.json` | Version bump of block/kit/DNA surface |

Common fields: `product_id`, `dna_version`, `requester` (`resident`|`human`), `evidence`, `requested_autonomy_level`, `signature`.

**Unknown fields are rejected** (`additionalProperties: false`). Schema version is `1.0.0`.

## Signature scheme (Ed25519)

1. Canonical payload = JSON of the request **without** `signature`, sorted keys, compact separators.
2. Sign with Ed25519 private key → base64 raw signature in `signature.value`.
3. `signature.algorithm` must be `ed25519`; `public_key_id` binds the product key.
4. Factory registers public keys under `{STORAGE_PATH}/change_requests/keys/{product_id}.json`.
5. Unsigned / malformed / tampered → **rejected at the door** and appended to `rejections.jsonl`.

This is the Factory signing pattern (no Store Ed25519 existed in-repo; Clone-from-Blocks doctrine unchanged — we never write to Cerebrum-Blocks).

## Queue states

```
received → validated → dry_run_evaluated → awaiting_approval → approved | rejected
```

- Persistence: `{STORAGE_PATH}/change_requests/queue/{request_id}.json` (atomic write).
- Redis (if `REDIS_URL`): index only; disk remains source of truth.
- **Idempotent re-delivery:** same `request_id` returns the existing item.
- `approved` / `rejected` are **recorded decisions only** — `decision.executes = false`. M4 consumes awaiting-approval items.

## Dry-run report format

Attached as `dry_run_report` on the queue item:

```json
{
  "schema_version": "1.0.0",
  "kind": "REPAIR|EXPANSION|UPGRADE",
  "would_change": [ { "surface": "...", "action": "...", "against_dna": "..." } ],
  "required_autonomy_level": "L3",
  "risk_flags": [ { "code": "...", "severity": "...", "message": "..." } ],
  "acceptance_gates_to_rerun": ["dna_checksum_verify", "..."],
  "mutates_product": false,
  "honesty": "dry-run only — no files/blocks/config were modified"
}
```

Also creates a **workspace record** (`workspaces/{request_id}/workspace.json`) with `worktree_created: false` / `agent_started: false`.

## Store upgrade compare (advisory)

`POST /v1/change-requests/store-compare` — read-only pins vs shelf/Store versions → advisories with `recommend: ignore | later | now`. **Never auto-emits UPGRADE requests.**

## APIs

| Method | Path | Notes |
|--------|------|-------|
| GET | `/v1/change-requests/status` | Always; reports intake flag |
| POST | `/v1/change-requests/intake` | Signed document → queue (flag) |
| GET | `/v1/change-requests/queue` | List (flag) |
| GET | `/v1/change-requests/queue/{id}` | Get (flag) |
| POST | `/v1/change-requests/queue/{id}/decision` | Record approve/reject only |
| POST | `/v1/change-requests/store-compare` | Advisory |
| POST | `/v1/resident/change-request` | Resident emit REPAIR (both RE + emit flags) |

## What M4 will consume

- Queue items in `awaiting_approval` (and later `approved` decisions)
- `dry_run_report.would_change` + `acceptance_gates_to_rerun`
- `workspace.path` as the seed for an isolated worktree
- Signed original `request` for audit

M4 owns: Kimi Code workbench, any mutation, Store publish, UI beyond this paperwork.

## Tests

`backend/tests/change_requests/test_change_request_loop.py` — schema reject, unsigned/tamper reject, Steward signed REPAIR round-trip, idempotent re-delivery, Store compare one-behind, Resident escalate hook, decision non-execution.
