# Regrade against the 21-check floor

The floor moved from 13 checks to 21. Old builds are re-scored against the
new file and **keep the score they actually achieve**. Nothing is back-dated:
an artifact that passed 13/13 when 13 was the floor is not 21/21 now, and
saying so is the point of the exercise.

Scores are recorded as `k/21`. Checks that need a running container are
marked `live` and are not counted as failures against an artifact that was
scored from its export on a host — they are simply not decidable that way,
and claiming either verdict would be the kind of unearned badge this table
exists to prevent.

## How to reproduce a row

```bash
# from the exported product's root, with the current harness written into it
python scripts/acceptance.py
```

The harness is generated from `backend/app/factory/acceptance_floor.v2.json`
by `store_acceptance.render_acceptance_script()`. Exported scripts are never
hand-edited; re-run the generator instead.

## Artifacts

### facility-management (`cerebrumdev-product (2).zip`, exported 2026-09-20)

Scored host-side from the export. The build predates the floor's expansion,
so it was never asked for the nine new lines.

| result | checks |
|---|---|
| **PASS (6)** | `single_persistence_root`, `ci_present_and_full_suite`, `handler_bodies_distinct`, `openapi_committed`, `migration_no_create_all`, `authorship_floor` |
| **FAIL (6)** | `health_fail_closed`, `negative_floor`, `one_live_connector`, `backup_restore_roundtrip`, `bench_p95`, `audit_clean` |
| **live (9)** | `no_token_401`, `missing_field_422`, `enum_422`, `ui_served_200`, `rag_roundtrip_hit`, `docker_health_200`, `cross_tenant_404`, `postgres_boot_200`, `metrics_served` |

**Score: 6/12 decidable host-side.** Not 21/21, and not claimed as such.

Notes worth keeping with the number:

- `migration_no_create_all` **passes** — two revisions, real DDL, no
  `create_all`. The "G5 residue" was already gone in this build; the check
  codifies it rather than fixing it.
- `negative_floor` fails with seven of eight capabilities at **zero**
  counter-cases. The whole suite carries nine refusal assertions, and they
  sit in formula and platform-auth tests rather than against capabilities.
- `health_fail_closed` fails on `evaluate_health` raising
  `ModuleNotFoundError`, which is a real defect in the artifact, not a
  scoring artefact.
- The export ships `factory_commit: "unknown"` and `blocks_commit:
  "unknown"`, so `provenance_complete` would fail too — it is listed under
  the container-only set because the gate reads it during a gate run.

## Standing rule

An artifact's row is written once, from a real run, and is not revised when
the floor changes again. A new floor gets a new column, not a rewritten
history.
