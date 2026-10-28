# F-1b: parse-on-upload is the default (reversed-with-evidence)

**Orders:** ORDERS_1030Z / ORDERS_1330Z.

## What changed and why

Parsing now runs **by default** on upload. The `INGESTION_AUTO_PARSE` env flag
was a placeholder for an "auto" gate that never belonged in the shipped product:
an upload that stores a blob but leaves it unparsed is not the real boundary.

- Removed `_autoparse_enabled()` and the `INGESTION_AUTO_PARSE` env read from
  `backend/app/cerebrum_product_kernel/ingestion/router.py` entirely.
- In `ingest_uploads`, after the validate-all-before-store pass, each stored
  object is now parsed **inline and unconditionally** via
  `parse_object(tenant_id, stored.object_id, job_id=job.job_id)`, and the
  response status is taken from the parse result (`parsed` / `failed` per file).
  There is no `auto` gate. The validate-all-before-store two-phase structure is
  unchanged. `"queued"` survives only if the request is interrupted before the
  inline parse runs — it is never the normal post-upload response state.

## Reversed test expectations (evidence)

Because upload now parses inline, the post-upload status assertions that F-1a
carried were re-pointed from `"queued"` to the **real terminal state**
(`parsed` for a valid file; `failed` for a corrupt accepted-type file). Only the
post-upload status expectation changed; no 415/413/cross-tenant/line/location/
job-mismatch/validate-all-before-store assertion was weakened.

Tests re-pointed:

- `tests/factory/test_phase2_floor_upload.py::test_floor_upload_parses_a_real_job`
  (was `test_floor_upload_queues_a_real_job`): valid PDF upload — response and
  status GET re-pointed `"queued"` -> `"parsed"`; now uploads a real parseable
  PDF.
- `tests/factory/test_phase2_upload_untested_rules.py::test_status_reflects_real_state_both_surfaces`
  (Rule 1, "status reflects real state"): rewritten to upload a valid PDF and
  assert `"parsed"` on BOTH the upload response and the status GET, on both the
  kernel and Floor surfaces. The pre-advance `== "queued"` baseline and the
  `set_status(...PARSING/PARSED)` dance were removed (upload already parsed).
  The mutant it pins: hardwiring `"status": "queued"` in either payload
  (`router.ingest_uploads` response or `router.job_status_payload` GET) now
  fails the test, since the real job is `parsed`.
- `tests/factory/test_phase2_parsers.py`: removed the two
  `monkeypatch.setenv("INGESTION_AUTO_PARSE", "1")` lines (flag gone; parse is
  default). `test_upload_route_does_not_500_on_corrupt_autoparsed_file` still
  asserts `"failed"`; `test_autoparse_reaches_parsed_through_the_route` still
  asserts `"parsed"`.
- `tests/factory/test_phase2_client_ingestion.py::test_upload_route_is_real_pdf_and_xlsx_distinct`:
  the three post-upload status assertions (the PDF response, the XLSX response,
  and the GET) were re-pointed `"queued"` -> `"failed"`. These fixtures are
  deliberately minimal non-parseable bytes (`_PDF_BYTES`, `_XLSX_BYTES`), so the
  real terminal state is `failed`; the test's purpose (two inputs -> two distinct
  `object_id`/`job_id`) is unchanged. This completes the re-point flagged on the
  5cb6738 grade and missed by the e76924e rework.

Unchanged (still unit tests of the job record, not post-upload):
`tests/factory/test_phase2_upload_object_store.py::test_parse_job_is_real_queued_record`
still creates a job and asserts `QUEUED`, because `create_job` legitimately
starts at `queued` before any parse.
