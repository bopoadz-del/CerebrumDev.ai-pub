# Phase 1 — Dead-control dispositions (CerebrumDev.ai)

Rule applied: a control that exists but is not on the live path is either
WIRED into the live path or DELETED along with every claim about it.
There is no third state.

| Control | Location | Decision | Live path now |
| --- | --- | --- | --- |
| `assert_host_allowed` | `backend/app/workbench/sandbox.py` | **DELETED** | An assert on a host string before spawning a subprocess controls nothing. Real egress control needs a netns, egress proxy, or firewall rules — none exist. The network-confinement claim was deleted from the sandbox docstring, `M4-build-mode-workbench.md`, and `STANDING_ORDERS.md`; sandbox metadata now reports `network_isolation: none`. |
| `validate_dna_document` | `backend/app/product_dna/validate.py` | **WIRED** | `load_verified_dna` (`backend/app/workbench/envelope.py`) now schema-validates every DNA document after checksum verification and raises `EnvelopeError` on violation. Test: `test_load_verified_dna_rejects_schema_invalid_doc`. |
| `assert_not_executable` | `backend/app/resident_engineer/injection_guard.py` | **DELETED** | A post-sanitize residual check that cannot fire by construction: `sanitize_untrusted` (live, on the envelope/agent path) strips every pattern the check would look for, and the returned warnings had no reader. |
| `validate_index` | `backend/app/core/rag_vector_store_adapters.py` | **WIRED** | `run_vector_index_dry_run` (`backend/app/core/rag_vector_indexing.py`) now calls `adapter.validate_index` after read-back verification and fails the run with `VECTOR_INDEX_VALIDATION_FAILED` on any error. Test: `test_index_validation_errors_fail_the_run`. |

The Cerebrum-Blocks dispositions (`validate_transition`, `validate_shelf` ×3,
`verify_token`, `verify_kit`, schema_registry validators) are recorded in the
same file name in that repository.
