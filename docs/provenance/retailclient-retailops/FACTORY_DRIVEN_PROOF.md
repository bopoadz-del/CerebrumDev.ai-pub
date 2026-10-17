# Factory-driven proof: RetailClient RetailOps

This directory preserves evidence that **RetailOpsReference (RetailOps)**
was driven / generated through **CerebrumDev.ai** (the Factory).

## Product relationship

| Role | Repository |
|------|------------|
| Factory | `bopoadz-del/CerebrumDev.ai` |
| Block store | `bopoadz-del/Cerebrum-Blocks` |
| Generated / operating RetailOps product | `bopoadz-del/RetailOpsReference` |

There is **one** RetailOps product repo (`RetailOpsReference`). CerebrumDev.ai
must not keep a parallel runnable RetailOps application tree.

## Provenance artifacts retained here

| File | Meaning |
|------|---------|
| `build_metadata.json` | Product id, Blocks commit pin, kit, runtime entrypoint |
| `GENERATED_PLATFORM_README.md` | Architecture of the Factory-emitted RetailOps runtime |
| `retailops_init_snapshot.py` | Snapshot of `BLOCKS_COMMIT` / version constants at cleanup |
| `vendored_kit_file_list.txt` | Files that were vendored from Cerebrum-Blocks into the package |
| `deploy_dir_listing.txt` | Deploy assets that were part of the generated package |

## Blocks pin at Factory emission

From `build_metadata.json` / init snapshot:

- **Product:** `cerebrum-retailops` `1.0.0`
- **Cerebrum-Blocks commit:** `4a5ad84246f885764ba05d1ab1398dc0d2b56be3`
- **Blocks kit:** `retail@1.1.0`
- **Runtime entrypoint (historical):** `app.retailops.app_factory:app`

## Cleanup note (Milestone 0A)

On 2026-07-18 the runnable package `backend/app/retailops/` and
`backend/tests/retailops/` were removed from CerebrumDev.ai so the Factory remains
factory-only. **These provenance documents were deliberately retained** to prove
RetailClient was Factory-driven. The live RetailOps kernel continues in
`RetailOpsReference`.

## Live product location

- GitHub: https://github.com/bopoadz-del/RetailOpsReference
- Default branch: `main`
