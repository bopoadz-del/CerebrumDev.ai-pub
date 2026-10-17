# STORE_REGRADE — STEP 0 baseline (BEFORE)

- **Tip date:** 2026-09-08
- **Live tip note:** `0031a67`
- **Scope:** measurement only — nine Store-green pilots; **no** `acceptance.py` implementation; **no** Store-gate change
- **Boot method:** uvicorn in shared venv (docker not available on box); host ports 18001–18009
- **Golden zip:** downloaded authenticated GET `/v1/sessions/sess_4591d5cc45d04fe1/product/package` → 622282 bytes

## BEFORE — pre-change baseline

This section is the pre-change baseline before any acceptance stamp work.

| session_id | product | zip_bytes | first_cap | no_token_POST | missing_field_POST | GET_/ | RAG | notes |
|---|---|---:|---|---|---|---|---|---|
| sess_7ca4ceb151ae48e9 | RetailHub | 662699 | `product_inventory_management` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18001; B=empty_body_missing_required |
| sess_d10dfc2890f7487b | InsureDistribute | 560024 | `workflow` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18002; B=empty_body_missing_required |
| sess_593357a0e1e54078 | Estate Steward / estate-management | 483170 | `estate_registry` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18003; B=empty_body_missing_required |
| sess_baecd2e879ca49a9 | FinanceOps FinFlow | 300558 | `automated_account_reconciliation` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18004; B=empty_body_missing_required |
| sess_5782f2264e0e4ff4 | projectsix founding | 436098 | `estate_registry` | 200 | 200 | 404 | empty | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18005; rag_caps=['dual_rag_sop', 'dual_rag_estate_docs']; B=empty_body_missing_required |
| sess_cec9a1345b2049bb | VetCare | 596988 | `vetcare_hub_veterinary_core` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18006; B=empty_body_missing_required |
| sess_fe80bf177a8545e6 | residential-lettings (old) | 439019 | `unit_registry_and_vacancy_tracking` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18007; B=empty_body_missing_required |
| sess_4591d5cc45d04fe1 | residential-lettings golden (new unlocked) | 622282 | `unit_registry_and_vacancy_tracking` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18008; B=empty_body_missing_required |
| sess_1bb2f36be7004ff4 | residential-lettings fresh GENERATE | 555916 | `unit_registry_and_vacancy_tracking` | 200 | 200 | 404 | n/a-no-rag | A:200 ok:false missing-required (JSON); C: no UI at /; boot=uvicorn port=18009; B=empty_body_missing_required |

## Summary — expected vs observed

Owner expected on **unfixed** stamps: **`200 / 200 / 404 / empty`** (RAG column `empty` when a RAG surface exists; otherwise no RAG).

| Probe | Expected (unfixed) | Observed (9/9) |
|---|---|---|
| A no_token_POST `/v1/<first_cap>` `{}` | 200 | **200** on all nine (JSON `ok:false` Missing required field — not HTTP 401) |
| B missing_field_POST | 200 | **200** on all nine (empty body / missing required; no auth gate on capability POST) |
| C GET `/` | 404 | **404** on all nine (frontend present in zip but not mounted at `/`) |
| D RAG | empty (or n/a) | Steward (`sess_5782f226…`): **empty** via `dual_rag_sop` plant+query; other eight: **n/a-no-rag** |

**Pattern match:** observed matches the expected unfixed signature (`200 / 200 / 404 / empty|n/a-no-rag`) across all nine pilots. No pilot claimed D1–D7 pass.

## Method notes

- `first_cap` taken from `docs/coder_receipt.json` → `capabilities[0]`.
- Zips staged under `/workspace/store_regrade_step0/zips/`; work trees under `/workspace/store_regrade_step0/work/<session_id>/`.
- Per-pilot JSON evidence: `/workspace/store_regrade_step0/logs/<session_id>.result.json`.
- Processes stopped after probes; no acceptance stamp code changed.

## Pilot zip sources

| session_id | zip |
|---|---|
| sess_7ca4ceb151ae48e9 | launching_ready_1137_cycle/retail-sess_7ca4ceb1.zip (662699) |
| sess_d10dfc2890f7487b | launching_ready_1137_cycle/insure-sess_d10dfc28.zip (560024) |
| sess_593357a0e1e54078 | launching_ready_1137_cycle/estate-sess_593357a0.zip (483170) |
| sess_baecd2e879ca49a9 | launching_ready_1137_cycle/finance-sess_baecd2e879ca49a9.zip (300558) |
| sess_5782f2264e0e4ff4 | projectsix-sess_5782f226-founding-pilot.zip (436098) |
| sess_cec9a1345b2049bb | launching_ready_1137_cycle/vetcare-sess_cec9a134.zip (596988) |
| sess_fe80bf177a8545e6 | launching_ready_1206_cycle/zips/fe80bf177a85-sess_fe80bf177a8.zip (439019) |
| sess_4591d5cc45d04fe1 | API product/package download (622282) |
| sess_1bb2f36be7004ff4 | lettings_regen_997c0d8/residential-lettings-sess_1bb2f36be7004ff4.zip (555916) |

## AFTER — store gate = acceptance (this PR)

- **Store-green** is now `scripts/acceptance.py` k/12 inside the Store-built Docker image.
- Export / Download is enabled only on k/k PASS. Authorship floor is not acceptance.
- `/platforms` shows a numeric `k/12` next to the pilot (not a colour).
- Factory WRITER / ProductGenerator stamp `scripts/acceptance.py` plus the files the harness measures (`app/auth.py`, `.github/workflows/ci.yml`, `docs/openapi.json`, `app/static/index.html`).
- Capability POST is HTTP 401 without a token and HTTP 422 on missing/enum validation.

### Steward pre-fix fail table (`sess_5782f226` / projectsix founding zip)

The founding zip is not on this cloud workspace (STEP 0 staged it under `/workspace/store_regrade_step0/`). The STEP 0 probes plus the new 12-line harness contract give this pre-fix table. Re-run `python scripts/acceptance.py` (or the factory evaluator) against the extracted zip after stamping a temp copy of the harness to confirm.

| # | check | STEP 0 / pre-fix result | why |
|---|---|---|---|
| 1 | no_token_401 | **FAIL** | POST `/v1/estate_registry` `{}` → HTTP **200** `ok:false` Missing required field (not 401) |
| 2 | missing_field_422 | **FAIL** | empty body → HTTP **200** (not 422) |
| 3 | enum_422 | **FAIL** (expected) | same 200-ok:false validation path; no HTTP 422 |
| 4 | ui_served_200 | **FAIL** | GET `/` → HTTP **404** (frontend in zip, not mounted) |
| 5 | rag_roundtrip_hit | **FAIL** | Steward RAG surface present (`dual_rag_sop`, `dual_rag_estate_docs`); plant+query was **empty** |
| 6 | single_persistence_root | unmeasured here | likely PASS (sqlite `STORAGE_PATH`) |
| 7 | ci_present_and_full_suite | **FAIL** (expected) | pre-stamp zip has no `.github/workflows/ci.yml` |
| 8 | handler_bodies_distinct | unmeasured here | — |
| 9 | health_fail_closed | unmeasured here | S11 health exists on later stamps |
| 10 | openapi_committed | **FAIL** (expected) | no committed `docs/openapi.json` |
| 11 | docker_health_200 | **FAIL** | no Store-image health measurement / no HEALTHCHECK |
| 12 | authorship_floor | likely PASS | founding pilot; last line, not first |

**Pre-fix fail count ≥ 4** (measured on STEP 0 A/B/C/D: 401, 422, UI, RAG; plus CI/openapi/docker on the unstamped zip).

### Floor regen (parent)

This cloud agent cannot Floor-Approve `sess_5782f226`. After merge:

1. Open Factory Floor for `sess_5782f226`.
2. Approve the existing projectsix blueprint (do not invent a domain).
3. Let GENERATE + pilot cycle stamp the new harness.
4. Store gate must run `python scripts/acceptance.py` inside the built image.
5. Target: **12/12**. Export stays disabled until then.
6. `/platforms` must show `12/12` as text, not a green authorship pill.

AFTER live re-probe of all nine pilots was **not** re-run in this environment (docker/zips unavailable). Stub only — parent can paste a new table here after regen.
