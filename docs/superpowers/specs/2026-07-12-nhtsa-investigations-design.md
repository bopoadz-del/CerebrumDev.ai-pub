# NHTSA ODI Investigations — PR 2 Product Slice Design

> **Status:** Design approved — ready for implementation planning  
> **Parent spec:** `docs/superpowers/specs/2026-07-11-automotive-safety-intelligence-pilot-design.md`  
> **Parent plan:** `docs/superpowers/plans/2026-07-11-automotive-pilot-pr2-automotive-intelligence.md`  
> **Target repo:** `bopoadz-del/CerebrumDev.ai`  
> **Target branches:** `feat/automotive-pilot-pr2` (CerebrumDev.ai), `feat/automotive-pilot-pr2` (Cerebrum-Blocks)  

---

## 1. Objective

Add the **NHTSA Office of Defects Investigation (ODI) investigations** source family to the Automotive Core RAG foundation pack already built for recalls in PR 2. The slice must:

- Harvest the official ODI bulk flat file.
- Normalize rows into typed canonical investigation records.
- Compile deterministic retrieval chunks.
- Index into the existing `automotive_core_v1` foundation corpus / `chunks_v2` namespace using BAAI/bge-small-en-v1.5 384-dimensional embeddings.
- Support exact investigation-number lookup, vehicle lookup, and component lookup.
- Reuse every PR 2 seam (harvester, normalizer, pack builder, retrieval, migration, tests) rather than creating a parallel pipeline.
- Keep the client-private overlay and Google Drive integration untouched — those belong to PR 3.

This slice is **design-only before coding**. No implementation code is produced until the plan is written and approved.

---

## 2. Official source confirmation

### 2.1 Bulk file

```text
URL:      https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip
Member:   FLAT_INV.txt
Format:   tab-delimited, no header row
Rows:     ~154,183 (as of 2026-07-12)
Size:     ~389 MB uncompressed
Updated:  daily per NHTSA/ODI metadata
```

### 2.2 Data dictionary

```text
URL: https://static.nhtsa.gov/odi/ffdd/inv/INV.txt
Fields ( verified 2026-07-12 ):

1  NHTSA_ACTION_NUMBER   CHAR(10)   NHTSA Identification Number, e.g. AQ08001, DP20-001, PE16-007
2  MAKE                  CHAR(25)   Vehicle/Equipment Make
3  MODEL                 CHAR(256)  Vehicle/Equipment Model
4  YEAR                  CHAR(4)    Model Year; 9999 if Unknown or N/A
5  COMPNAME              CHAR(256)  Component Description
6  MFR_NAME              CHAR(40)   Manufacturer's Name
7  ODATE                 CHAR(8)    Date Opened (YYYYMMDD)
8  CDATE                 CHAR(8)    Date Closed (YYYYMMDD); empty if open
9  CAMPNO                CHAR(9)    Recall Campaign Number, if applicable
10 SUBJECT               CHAR(200)  Summary Description
11 SUMMARY               CHAR(6000) Summary Detail
```

Key observations:

- The file is **sorted by NHTSA_ACTION_NUMBER** (alphabetically since 2023-09-05).
- One investigation can span **multiple rows** for different model years of the same vehicle line (e.g. `AQ08001` covers 2003, 2004, 2005 `PACE AMERICAN TRAILER`).
- `CAMPNO` links an investigation to a recall campaign when an investigation resulted in a recall.
- Dates use `YYYYMMDD`; missing/unknown dates are empty or `99999999`.

### 2.3 Source-manifest entry

The existing `Cerebrum-Blocks/block_store/kits/automotive/source_manifest.json` already declares:

```json
{
  "source_id": "nhtsa_investigations",
  "source_family": "investigation",
  "official_publisher": "National Highway Traffic Safety Administration",
  "source_uri": "https://www-odi.nhtsa.dot.gov/downloads/",
  "retrieval_method": "download_csv_zip",
  "source_class": "approved_official_public_data",
  "licence_evidence": "US federal public data",
  "authority_rating": "primary",
  "jurisdiction": "US",
  "coverage_start": "2015-01-01",
  "coverage_end": "",
  "harvest_timestamp": "",
  "content_hash": "",
  "record_count": 0,
  "normalization_version": "automotive_core_v1",
  "chunking_version": "v2",
  "embedding_identity": "bge-small-en-v1.5-384",
  "refresh_policy": "monthly_full_rebuild"
}
```

The implementation must **update** this entry's `source_uri` to the canonical bulk URL:

```text
https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip
```

and fill `harvest_timestamp`, `content_hash`, `coverage_end`, and `record_count` after each harvest.

---

## 3. Design approach

### 3.1 Chosen approach: extend the recall pipeline (Approach A)

Extend the PR 2 recall implementation to support a second source family in the same foundation pack.

Reasons:

- The recall harvester already has `--family` plumbing; investigations use the same `download_csv_zip` retrieval method and the same tab-delimited flat-file shape.
- The source manifest already contemplates multiple `source_families` in one `PackManifest`.
- One foundation pack (`automotive_core_rag_v1`) and one vector namespace (`chunks_v2`) keeps PR 2 scope manageable and matches the existing evaluation/retrieval architecture.
- Complaints, ratings, and vPIC can follow the same pattern later without architectural churn.

Rejected alternatives:

- **Separate investigation pack/namespace** — would split the foundation corpus and force retrieval to merge two indexes; unnecessary at this stage.
- **Fully generic family-agnostic pipeline** — would require redesigning code that already passed CI for recalls; premature abstraction before we have three concrete families.

### 3.2 Cross-cutting execution contract

Any investigation-related action exposed to users (e.g. `summarize_investigation`, `lookup_investigation`, `compare_vehicle_safety`) must be registered through the generic Cerebrum-Blocks domain-kit interface. The runtime must use one domain-neutral path for every installed kit:

```python
container = resolve_allowed_domain_container(selected_domain)
actions = container.get_actions()
if selected_action not in actions:
    raise UnsupportedActionError(selected_action)

RESERVED_CONTEXT_KEYS = {
    "action",
    "project_id",
    "user_id",
    "tenant_id",
    "organisation_id",
    "permissions",
    "allowed_blocks",
}

safe_input = {
    key: value
    for key, value in action_args.items()
    if key not in RESERVED_CONTEXT_KEYS
}

trusted_params = {
    "action": selected_action,
    "project_id": runtime_project_id,
    "user_id": runtime_user_id,
    "tenant_id": runtime_tenant_id,
}

result = await container.execute(safe_input, trusted_params)
```

Rules:

- The action is placed in `params["action"]`, not only inside `input_data`. `UniversalContainer.process()` reads from `params["action"]`, so passing it only in the payload defaults to `"status"`.
- The core orchestrator must not contain `if action == "summarize_investigation": ...` branches or construction-only generic branches.
- Reserved context keys are stripped from model-generated arguments and injected from authenticated runtime state.
- Action names should be namespaced (`automotive.lookup_investigation`, `construction.estimate_costs`) to avoid collisions with generic blocks.
- Result success is confirmed from the structured execution envelope; missing status is not treated as automatic success.
- The LLM must not claim an action ran unless a real successful execution result exists.

For this slice, the immediate impact is:

- The retrieval functions in `app/core/automotive_retrieval.py` remain kit-internal utilities.
- Automotive container actions are added to `AutomotiveContainer.get_actions()` and invoked through `container.route(action, input_data, params)` with `params["action"]` set correctly.
- No central CerebrumDev.ai executor is edited for investigation-specific behavior.
- The broader runtime-orchestrator dispatch fix is a prerequisite for chat synthesis but is not part of this data-source slice.

---

## 4. Canonical investigation record

### 4.1 Model

Add `AutomotiveInvestigation` to `app/models/automotive_records.py`:

```python
class AutomotiveInvestigation(BaseModel):
    record_id: str                           # deterministic identity
    source_id: Literal["nhtsa_investigations"]
    source_family: Literal["investigation"]
    investigation_number: str                # NHTSA_ACTION_NUMBER, e.g. "PE16-007"
    status: Optional[str] = None             # inferred from CDATE: "Open" or "Closed"
    investigation_type: Optional[str] = None # prefix: DP, PE, EA, RQ, INCLA, INOA, etc.
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[str] = None         # single year from one row; "9999" -> None
    model_year_range: Optional[str] = None   # aggregated range per investigation (computed later)
    component: Optional[str] = None
    manufacturer: Optional[str] = None
    subject: Optional[str] = None            # SUBJECT
    summary: Optional[str] = None            # SUMMARY
    opening_date: Optional[str] = None       # ISO date from ODATE
    closing_date: Optional[str] = None       # ISO date from CDATE
    associated_campaign_number: Optional[str] = None  # CAMPNO
    source_url: Optional[str] = None
    jurisdiction: str = "US"
    authority_rating: str = "primary"
    harvest_timestamp: str
    raw_record_hash: str
    normalization_version: str = "automotive_core_v1"
```

Rules:

- Preserve `investigation_number` exactly as it appears in the official file.
- `status` is inferred: empty `CDATE` or `99999999` → `"Open"`; otherwise → `"Closed"`.
- `investigation_type` is the alphabetic prefix of `investigation_number` (`PE`, `EA`, `DP`, `RQ`, `INCLA`, `INOA`, etc.).
- Normalize dates deterministically to `YYYY-MM-DD` or `null`.
- Strip NUL bytes and control characters; bound `subject` to 200 chars and `summary` to 6000 chars without silently dropping the truncation fact.
- Use `null` for `9999`, `99999999`, empty strings, `N/A`, `NULL`.
- Generate deterministic `record_id` and `raw_record_hash` using the same SHA-256 approach as recalls.

### 4.2 Row-to-record normalization

Add `normalize_investigation_row(source_id, record_sequence, row)` to `app/core/automotive_normalizers.py`.

Defensive aliases for known column-name variations:

```text
NHTSA_ACTION_NUMBER:  NHTSA_ACTION_NUMBER, nhtsa_action_number, ACTION_NUMBER, action_number
MAKE:                 MAKE, make, MAKETXT
MODEL:                MODEL, model, MODELTXT
YEAR:                 YEAR, year, YEARTXT, YEAR_txt
COMPNAME:             COMPNAME, compname, COMPONENT
MFR_NAME:             MFR_NAME, mfr_name, MFGNAME, manufacturer
ODATE:                ODATE, odate, OPENING_DATE
CDATE:                CDATE, cdate, CLOSING_DATE
CAMPNO:               CAMPNO, campno, RECALL_NUMBER
SUBJECT:              SUBJECT, subject, TOPIC
SUMMARY:              SUMMARY, summary, DETAIL
```

The official file uses the names in section 2.2; aliases protect against future header-row fixtures or renamed upstream files.

> **Note:** `YEAR` is the official column name in the ODI dictionary. The alias `YEARTXT` is included only for defensive compatibility with possible future headers; the implementation must not require it.

### 4.3 Multi-row investigation grouping

Because one investigation can span multiple rows (different model years), the implementation must decide how to represent it for retrieval:

- **Canonical records:** one `AutomotiveInvestigation` per raw row. This preserves provenance and makes `record_id` stable per row.
- **Chunks:** each canonical row becomes one chunk. The chunk text includes `investigation_number`, `make`, `model`, `model_year`, `component`, `subject`, `summary`, and, when present, the associated `campaign_number`.
- **Retrieval:** exact investigation-number lookup returns all chunks for that investigation; vehicle/component queries return the relevant rows.

A future optimization may aggregate rows by investigation number into a single canonical record with a `model_year_range`, but that is **not required** for this slice. Per-row canonicalization keeps the implementation deterministic and byte-stable.

---

## 5. Harvester extension

### 5.1 CLI changes

Extend `backend/scripts/harvest_nhtsa.py` to support:

```bash
python -m scripts.harvest_nhtsa \
  --family investigation \
  --output-dir <path> \
  --since-year <year> \
  --dry-run \
  --fixture <path> \
  --force-download
```

### 5.2 Source-specific configuration

```python
DEFAULT_INVESTIGATION_URL = "https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip"
DEFAULT_INVESTIGATION_DICT_URL = "https://static.nhtsa.gov/odi/ffdd/inv/INV.txt"
EXPECTED_INVESTIGATION_ARCHIVE_MEMBERS = {"FLAT_INV.txt"}

NHTSA_INVESTIGATION_FIELDNAMES = [
    "NHTSA_ACTION_NUMBER",
    "MAKE",
    "MODEL",
    "YEAR",
    "COMPNAME",
    "MFR_NAME",
    "ODATE",
    "CDATE",
    "CAMPNO",
    "SUBJECT",
    "SUMMARY",
]
```

### 5.3 Required harvester behavior

Reuse the recall harvester's existing guarantees:

- Stream the ZIP download with bounded timeout (120 s default) and bounded retries with exponential backoff.
- Write to a temporary file and atomically publish on success.
- Compute SHA-256 of the downloaded archive.
- Reject unsafe ZIP paths and path traversal.
- Extract only expected members.
- Preserve the raw archive.
- Be idempotent: reuse a cached archive when its hash and metadata are valid; re-download with `--force-download`.
- Fail clearly when the official source is unavailable; never silently substitute sample data.
- Support `--fixture` for offline tests using an authored tab-delimited file.
- Apply `--since-year` filter on `YEAR`.

### 5.4 Output layout

```text
<output-dir>/
  raw/
    nhtsa_investigations/
      FLAT_INV.zip
      extracted/
        FLAT_INV.txt
      harvest_manifest.json
  canonical/
    investigations.jsonl
  harvest_manifest.json
```

The recall and investigation harvests may share the same parent `output-dir` (e.g. `storage/automotive_core_rag_v1/<harvest-id>/`). Each family writes its own `raw/<source_id>/harvest_manifest.json`. The root `harvest_manifest.json` is a merged view listing both source families, written by the pack builder or by a small orchestrator after all harvests complete. The pack builder discovers `canonical/recalls.jsonl` and `canonical/investigations.jsonl` from the shared parent directory.

---

## 6. Pack builder extension

### 6.1 Multi-family canonical input

Add a higher-level orchestrator, e.g. `build_automotive_core_pack_from_families`, that accepts a list of canonical record paths and runs the existing recall/investigation chunk compilers. Keep the existing `build_automotive_core_pack(recalls_path, ...)` signature unchanged for backwards compatibility.

```python
def build_automotive_core_pack_from_families(
    canonical_records_paths: List[Path],
    output_dir: Path,
    project_id: str = "automotive_core_v1",
    dry_run: bool = False,
) -> PackManifest:
    ...
```

The orchestrator loads each canonical JSONL, dispatches to the correct chunk compiler by inspecting `source_family`, writes family-specific chunk JSONL files, and produces one multi-family `pack_manifest.json`.

### 6.2 Family-aware chunk compiler

Add `chunk_investigation_records(records: List[AutomotiveInvestigation]) -> List[AutomotiveChunk]`.

`AutomotiveChunk` already has `source_family` and `record_reference`. For investigations:

```text
knowledge_layer = automotive_core_v1
foundation_pack_id = automotive_core_rag_v1
source_family = investigation
source_authority = primary
record_reference = investigation_number
jurisdiction = US
chunk_index = 0   # one row -> one chunk in this slice
text = stable concatenation of investigation number, make, model, model year,
       component, manufacturer, status, opening date, closing date,
       associated campaign, subject, summary
```

The chunk text must:

- Always include `investigation_number`.
- Include `make`, `model`, `model_year`, `component` when present.
- Include `knowledge_layer` and foundation pack metadata in the structured fields.
- Reject blank or evidence-free chunks.
- Produce deterministic `chunk_id`, `text_hash`, and field ordering.

### 6.3 Output artifacts

```text
<storage>/automotive_core_rag_v1/<harvest-id>/
  raw/
    nhtsa_recalls/
    nhtsa_investigations/
  canonical/
    recalls.jsonl
    investigations.jsonl
  chunks/
    recalls.jsonl
    investigations.jsonl
  harvest_manifest.json
  pack_manifest.json
```

`pack_manifest.json` must record:

```text
source_families: ["recall", "investigation"]
record_count: total across both families
chunk_count: total across both families
status: "indexed" when indexed, "validated" when dry-run
```

### 6.4 Idempotency, replacement and family identity

Re-running the same pack must:

- Produce identical canonical record IDs and chunk IDs for unchanged rows.
- Upsert chunks into `chunks_v2` without duplicates.
- Not wipe unrelated project rows.
- Update changed content in place via `chunk_id`-keyed upsert.

`_bulk_index_chunks` must encode `source_family` in the stored `doc_id`:

```python
doc_id = f"{chunk.source_family}:{chunk.record_id}"
```

The current `chunks_v2` migration does not expose `source_family` as a top-level column, so the `doc_id` prefix is the reliable discriminator at retrieval time. Recall rows keep `recall:{record_id}`; investigation rows use `investigation:{record_id}`.

---

## 7. Retrieval extension

### 7.1 Identifier patterns

Extend `app/core/automotive_retrieval.py` with investigation-aware patterns. FLAT_INV action numbers are alphanumeric with an optional hyphen, e.g.:

```text
AQ08001
DP20-001
EA22002
PE16-007
RQ24005
```

```python
_INVESTIGATION_RE = re.compile(
    r"\b(?:DP|PE|EA|RQ|AQ)\d{2,3}[\-]?\d{3,4}\b",
    re.IGNORECASE,
)
```

The pattern intentionally covers the known ODI prefixes. If future data introduces additional prefixes, the regex must be widened and the change recorded in the source manifest.

### 7.2 Exact investigation lookup

When the query contains an investigation number:

1. Extract the identifier.
2. Issue a metadata filter search on `record_reference` (investigation_number) in `automotive_core_v1`.
3. Return matching chunks as top results.
4. Preserve source citation and foundation layer.

### 7.3 Vehicle and component queries

Reuse existing semantic + BM25 hybrid search. Vehicle queries (`2014 Honda Accord`) and component queries (`braking defect investigation`) should return relevant investigation evidence within top five when the corpus contains matches.

### 7.4 Citation envelope

Each result must include:

```text
knowledge_layer: "automotive_core_v1"
foundation_pack_id: "automotive_core_rag_v1"
source_family: "investigation"
source_title: "NHTSA Investigation <investigation_number>"
source_authority: "primary"
source_url: <computed URL or null>
record_reference: <investigation_number>
retrieval_score: <float>
chunk_text: <chunk text>
metadata: { chunk_id, doc_id, chunk_index, associated_campaign_number, status, ... }
```

Source URL strategy: link to the NHTSA public search when an investigation number is present, e.g. `https://www.nhtsa.gov/nhtsa-datasets-and-apis` or a manufacturer-facing ODI summary page. If no stable per-investigation URL exists, set `source_url` to the top-level ODI downloads page and document that in the manifest.

### 7.5 Family-aware source title

`retrieve_foundation_evidence` must set `source_title` based on `source_family`:

```text
recall:       "NHTSA Recall <campaign_number>"
investigation: "NHTSA Investigation <investigation_number>"
```

This prevents investigation results from being mislabelled as recalls.

---

## 8. Migration and indexing

### 8.1 Reuse existing migration

The PR 2 recall slice already added `alembic/versions/0010_automotive_vector_384.py` creating `chunks_v2` with:

```text
embedding vector(384)
tsvector/GIN lexical search
embedding identity columns
knowledge_layer / foundation_pack_id / source_family / record_reference / metadata
```

No new migration is required for investigations. The implementation must confirm the migration applies cleanly and that investigation rows populate the same table with `source_family = "investigation"`.

### 8.2 Indexing requirements

- Use BAAI/bge-small-en-v1.5, 384 dims, L2-normalized, cosine distance.
- Batch embeddings and bulk upsert.
- Verify every vector has 384 finite values.
- Verify vectors are normalized within tolerance.
- Populate BM25/tsvector fields.
- Preserve `source_family`, `record_reference`, `foundation_pack_id`, `knowledge_layer`.

---

## 9. Tests

### 9.1 Harvester tests (extend `tests/test_harvest_nhtsa.py`)

- `test_investigation_fixture_harvest_succeeds`
- `test_investigation_dry_run_performs_no_writes`
- `test_investigation_content_hash_is_recorded`
- `test_investigation_unsafe_zip_path_rejected`
- `test_investigation_valid_cache_reused`
- `test_investigation_corrupt_cache_rejected`

### 9.2 Normalizer tests (extend overlay `tests/test_automotive_normalizers.py`)

- `test_normalize_investigation_row_maps_official_columns`
- `test_investigation_status_open_when_no_cdate`
- `test_investigation_status_closed_when_cdate_present`
- `test_investigation_missing_values_remain_null`
- `test_investigation_number_preserved`
- `test_investigation_deterministic_id_and_hash`
- `test_investigation_control_characters_removed`
- `test_investigation_reprocessing_is_byte_stable`

### 9.3 Chunk/compiler tests (extend overlay `tests/test_automotive_pack_builder.py`)

- `test_chunk_investigation_records_are_deterministic`
- `test_investigation_number_appears_in_every_chunk`
- `test_investigation_knowledge_layer_metadata_present`
- `test_multi_family_build_writes_both_chunk_files`
- `test_multi_family_manifest_lists_both_source_families`
- `test_rebuild_is_idempotent` (already exists; extend to cover investigations)

### 9.4 Retrieval tests (extend overlay `tests/test_automotive_retrieval.py`)

- `test_exact_investigation_lookup_returns_top_result`
- `test_investigation_vehicle_lookup_returns_relevant_evidence`
- `test_investigation_component_query_returns_relevant_evidence`
- `test_unsupported_investigation_does_not_fabricate_match`
- `test_investigation_citation_envelope_is_complete`
- `test_mixed_family_query_returns_recall_and_investigation`

### 9.5 Indexing tests

- Clean migration still applies.
- Idempotent upsert across recalls + investigations.
- No duplicate chunks.
- Foundation collection isolation.
- BM25 field populated for investigation text.
- Vector identity preserved.

---

## 10. Evaluation seed

Add investigation questions to `Cerebrum-Blocks/block_store/kits/automotive/evaluation/development_seed.jsonl`. Mark each with `"development_seed": true`.

Example categories:

```jsonl
{"question_id": "auto-seed-009", "category": "exact_investigation_lookup", "question": "Summarize NHTSA investigation PE16-007.", "expected_source_family": "investigation", "expected_identifiers": ["PE16-007"], "required_evidence": ["investigation_number", "summary"], "forbidden_unsupported_claim": true, "answerability": "answerable", "development_seed": true}
{"question_id": "auto-seed-010", "category": "investigation_component_lookup", "question": "Which NHTSA investigations involve unintended braking?", "expected_source_family": "investigation", "expected_identifiers": ["braking"], "required_evidence": ["component", "summary"], "forbidden_unsupported_claim": true, "answerability": "answerable", "development_seed": true}
{"question_id": "auto-seed-011", "category": "investigation_vehicle_lookup", "question": "Find NHTSA investigations for the 2014 Ford F-150.", "expected_source_family": "investigation", "expected_identifiers": ["Ford", "F-150", "2014"], "required_evidence": ["make", "model", "model_year", "investigation_number"], "forbidden_unsupported_claim": true, "answerability": "answerable", "development_seed": true}
{"question_id": "auto-seed-012", "category": "unsupported", "question": "What is NHTSA investigation ZZ99-999?", "expected_source_family": null, "expected_identifiers": [], "required_evidence": [], "forbidden_unsupported_claim": true, "answerability": "unanswerable", "development_seed": true}
```

These are **development_seed** questions, not the final 50-question golden evaluation pack.

---

## 11. Out of scope

Do not implement in this slice:

- NHTSA consumer complaints source
- NHTSA safety ratings source
- Manufacturer communications source
- Full vPIC harvesting
- Client-private RAG blending
- Google Drive end-to-end journey
- Frontend changes
- Admin activation UI
- Production deployment
- Chat system-prompt conversion
- 50-question golden evaluation pack
- Billing, agents, LoRA, fine-tuning
- Changes to The_Fork production code

Do not create empty modules for these items.

---

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Official ODI file format drift | Version source manifest; normalize defensively with aliases; validate column count on load. |
| Multi-row investigation representation | Keep per-row canonical records now; aggregate later if retrieval quality requires it. |
| Investigation-number pattern false positives | Pattern must include alphabetic prefix (`PE`, `EA`, `DP`, `RQ`, etc.) and digits. |
| Recall/investigation citation confusion | Set `source_family` and `source_title` explicitly in every result envelope. |
| Embedding/indexing cost | Batch and stream; cap first live run to a bounded subset if needed. |
| Cross-family upsert conflicts | `chunk_id` is deterministic and keyed by record content; different families cannot collide because source data differs. |

---

## 13. Definition of done for this slice

- [ ] Design spec approved and committed.
- [ ] Implementation plan written and approved.
- [ ] `harvest_nhtsa.py` supports `--family investigation` with all safety guarantees.
- [ ] `AutomotiveInvestigation` model and normalizer exist with real ODI column handling.
- [ ] Pack builder accepts recalls + investigations and produces one multi-family manifest.
- [ ] Investigation chunks index into `chunks_v2` with BGE 384 vectors.
- [ ] Retrieval supports exact investigation lookup, vehicle lookup, and component lookup.
- [ ] All new and existing tests pass.
- [ ] Development seed questions added to Cerebrum-Blocks.
- [ ] No PR 3 scope (client overlay, Drive, frontend) is touched.
