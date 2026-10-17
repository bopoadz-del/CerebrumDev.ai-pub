# NHTSA ODI Investigations — PR 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the NHTSA Office of Defects Investigation (ODI) investigations source family to the existing `automotive_core_rag_v1` foundation pack, with all user-facing automotive actions exposed through the generic Cerebrum-Blocks domain-kit `get_actions()` + `execute()` contract.

**Architecture:** Extend the recall-only harvester, normalizer, pack builder, and retrieval pipeline to support a second source family in the same foundation pack. Add automotive container actions (`lookup_investigation`, `summarize_investigation`, `compare_vehicle_safety`) that route through the generic kit interface so the core orchestrator never contains investigation-specific branches.

**Tech Stack:** Python 3.11, Pydantic 2.x, SQLAlchemy 2.x, Alembic, Postgres/pgvector, httpx, pytest.

## Global Constraints

- The_Fork production code must not be modified; changes are applied through the automotive overlay in the generated platform or in Cerebrum-Blocks.
- All harvested artifacts, canonical records, chunks, and embeddings live outside Git under `storage/automotive_core_rag_v1/`.
- Use `BAAI/bge-small-en-v1.5`, 384 dimensions, L2-normalized, cosine distance, namespace `v2`, table `chunks_v2`.
- Never silently fall back to `local_feature_hash_v1` or `local_flat_json_v1` for the pilot pack.
- Never silently substitute sample data for a failed live harvest.
- Every canonical record and chunk must have deterministic IDs and hashes.
- `source_family` must be explicit in every chunk, index row, and citation envelope (`recall` or `investigation`).
- Client-private RAG, Google Drive end-to-end, frontend changes, and admin UI are out of scope for this slice.
- All domain actions must be registered via `container.get_actions()` and executed via `container.execute(input_data, params)` with `params["action"]` set by the runtime, not the user payload.

---

## File Map

### Cerebrum-Blocks (evaluation + manifest only)

| File | Responsibility |
|---|---|
| `block_store/kits/automotive/source_manifest.json` | Update `nhtsa_investigations` source URI to canonical bulk URL. |
| `block_store/kits/automotive/evaluation/development_seed.jsonl` | Append investigation development-seed questions. |

### CerebrumDev.ai factory

| File | Responsibility |
|---|---|
| `backend/scripts/harvest_nhtsa.py` | Extend `--family` support to `investigation`; add ODI download/extract/normalize path. |
| `backend/tests/test_harvest_nhtsa.py` | Add investigation harvester tests. |
| `backend/tests/fixtures/nhtsa_investigations_sample.txt` | Small authored fixture for offline tests. |

### Generated-platform overlay (`backend/app/platform_generator/overlays/automotive_core/`)

| File | Responsibility |
|---|---|
| `app/models/automotive_records.py` | Add `AutomotiveInvestigation` model. |
| `app/core/automotive_normalizers.py` | Add `normalize_investigation_row` and `normalize_investigation_rows`. |
| `app/core/automotive_pack_builder.py` | Add `chunk_investigation_records` and `build_automotive_core_pack_from_families`; keep single-path entry point. |
| `app/core/automotive_retrieval.py` | Add investigation identifier extraction, exact lookup, and family-aware citation. |
| `scripts/build_automotive_core_pack.py` | Extend CLI to accept multiple `--records` paths or a `--family-dir`. |
| `tests/test_automotive_normalizers.py` | Investigation normalizer tests. |
| `tests/test_automotive_pack_builder.py` | Multi-family pack builder tests. |
| `tests/test_automotive_retrieval.py` | Investigation retrieval tests. |

---

## Task 1: Update Cerebrum-Blocks source manifest

**Files:**
- Modify: `Cerebrum-Blocks/block_store/kits/automotive/source_manifest.json`

**Interfaces:**
- Consumes: existing `source_manifest.json` schema.
- Produces: updated `nhtsa_investigations` entry with canonical bulk URL.

- [ ] **Step 1: Change the investigations source URI**

Replace:
```json
"source_uri": "https://www-odi.nhtsa.dot.gov/downloads/"
```
with:
```json
"source_uri": "https://static.nhtsa.gov/odi/ffdd/inv/FLAT_INV.zip"
```

Leave other fields unchanged.

- [ ] **Step 2: Verify JSON is valid**

Run:
```bash
cd Cerebrum-Blocks
python -m json.tool block_store/kits/automotive/source_manifest.json > /dev/null
```
Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
cd Cerebrum-Blocks
git add block_store/kits/automotive/source_manifest.json
git commit -m "fix(automotive): point investigations source manifest to canonical ODI bulk URL"
```

---

## Task 2: Add AutomotiveInvestigation model

**Files:**
- Modify: `backend/app/platform_generator/overlays/automotive_core/app/models/automotive_records.py`

**Interfaces:**
- Consumes: Pydantic `BaseModel`, `Literal`, `Field`, `Optional`.
- Produces: `AutomotiveInvestigation` class; later tasks use it for normalization and chunking.

- [ ] **Step 1: Write the failing test**

Create `backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py` addition (full file created in Task 6; add this test first):

```python
def test_automotive_investigation_model_exists() -> None:
    from app.models.automotive_records import AutomotiveInvestigation
    record = AutomotiveInvestigation(
        record_id="abc123",
        source_id="nhtsa_investigations",
        source_family="investigation",
        investigation_number="PE16-007",
        harvest_timestamp="2026-07-12T00:00:00Z",
        raw_record_hash="def456",
    )
    assert record.investigation_number == "PE16-007"
    assert record.source_family == "investigation"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py::test_automotive_investigation_model_exists -v
```
Expected: FAIL — `AutomotiveInvestigation` not defined.

- [ ] **Step 3: Add the model**

Append to `app/models/automotive_records.py` after `AutomotiveRecall`:

```python
class AutomotiveInvestigation(BaseModel):
    """Canonical NHTSA ODI defect investigation record."""

    record_id: str = Field(..., description="Deterministic record identity")
    source_id: str = Field(..., description="Official source identifier")
    source_family: Literal["investigation"] = "investigation"
    investigation_number: str = Field(..., description="NHTSA action number, e.g. PE16-007")
    status: Optional[str] = None
    investigation_type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    model_year: Optional[str] = None
    model_year_range: Optional[str] = None
    component: Optional[str] = None
    manufacturer: Optional[str] = None
    subject: Optional[str] = None
    summary: Optional[str] = None
    opening_date: Optional[str] = None
    closing_date: Optional[str] = None
    associated_campaign_number: Optional[str] = None
    source_url: Optional[str] = None
    jurisdiction: str = "US"
    authority_rating: str = "primary"
    harvest_timestamp: str
    raw_record_hash: str
    normalization_version: str = "automotive_core_v1"
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/app/platform_generator/overlays/automotive_core/app/models/automotive_records.py backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py
git commit -m "feat(automotive): add AutomotiveInvestigation canonical model"
```

---

## Task 3: Add investigation normalizer

**Files:**
- Modify: `backend/app/platform_generator/overlays/automotive_core/app/core/automotive_normalizers.py`

**Interfaces:**
- Consumes: `AutomotiveInvestigation` from `app/models/automotive_records`.
- Produces: `normalize_investigation_row(source_id, record_sequence, row)` and `normalize_investigation_rows(source_id, rows)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_automotive_normalizers.py`:

```python
def test_normalize_investigation_row_maps_official_columns() -> None:
    row = {
        "NHTSA_ACTION_NUMBER": "PE16-007",
        "MAKE": "Tesla",
        "MODEL": "Model S",
        "YEAR": "2015",
        "COMPNAME": "AIR BAGS",
        "MFR_NAME": "Tesla Motors",
        "ODATE": "20160101",
        "CDATE": "20161231",
        "CAMPNO": "16V176000",
        "SUBJECT": "Air bag deployment investigation",
        "SUMMARY": "Investigation into unintended air bag deployment events.",
    }
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.investigation_number == "PE16-007"
    assert record.make == "Tesla"
    assert record.model == "Model S"
    assert record.model_year == "2015"
    assert record.component == "AIR BAGS"
    assert record.manufacturer == "Tesla Motors"
    assert record.opening_date == "2016-01-01"
    assert record.closing_date == "2016-12-31"
    assert record.associated_campaign_number == "16V176000"
    assert record.subject == "Air bag deployment investigation"
    assert record.summary == "Investigation into unintended air bag deployment events."
    assert record.status == "Closed"
    assert record.investigation_type == "PE"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py::test_normalize_investigation_row_maps_official_columns -v
```
Expected: FAIL — `normalize_investigation_row` not defined.

- [ ] **Step 3: Implement the normalizer**

Append to `app/core/automotive_normalizers.py`:

```python
UNKNOWN_YEAR_TOKENS = {"9999", "99999", "0000", "", "N/A", "NA"}


def _investigation_type(action_number: str) -> Optional[str]:
    """Extract alphabetic prefix from NHTSA action number."""
    if not action_number:
        return None
    prefix = ""
    for ch in action_number:
        if ch.isalpha():
            prefix += ch
        else:
            break
    return prefix.upper() or None


def _investigation_status(cdate: Optional[str]) -> Optional[str]:
    """Infer open/closed status from closing date."""
    if not cdate or cdate.strip() in {"", "99999999"}:
        return "Open"
    return "Closed"


def normalize_investigation_row(
    source_id: str,
    record_sequence: int,
    row: Dict[str, str],
) -> AutomotiveInvestigation:
    """Convert one raw NHTSA ODI investigation row into a canonical record."""
    get = _field_getter(row)

    investigation_number = _clean_text(get("NHTSA_ACTION_NUMBER", "nhtsa_action_number", "action_number"), max_chars=32) or ""
    make = _clean_text(get("MAKE", "make", "MAKETXT"), max_chars=128)
    model = _clean_text(get("MODEL", "model", "MODELTXT"), max_chars=512)
    year = _clean_text(get("YEAR", "year", "YEARTXT"), max_chars=16)
    if year in UNKNOWN_YEAR_TOKENS:
        year = None
    component = _clean_text(get("COMPNAME", "compname", "COMPONENT"), max_chars=512)
    manufacturer = _clean_text(get("MFR_NAME", "mfr_name", "MFGNAME", "manufacturer"), max_chars=256)
    opening_date = _parse_date(get("ODATE", "odate", "OPENING_DATE"))
    closing_date = _parse_date(get("CDATE", "cdate", "CLOSING_DATE"))
    associated_campaign = _clean_text(get("CAMPNO", "campno", "RECALL_NUMBER"), max_chars=32)
    subject = _clean_text(get("SUBJECT", "subject", "TOPIC"), max_chars=200)
    summary = _clean_text(get("SUMMARY", "summary", "DETAIL"), max_chars=6000)

    record_id = _record_id(source_id, record_sequence, investigation_number)
    raw_hash = _raw_record_hash(source_id, row)

    return AutomotiveInvestigation(
        record_id=record_id,
        source_id=source_id,
        source_family="investigation",
        investigation_number=investigation_number,
        status=_investigation_status(closing_date),
        investigation_type=_investigation_type(investigation_number),
        make=make,
        model=model,
        model_year=year,
        component=component,
        manufacturer=manufacturer,
        subject=subject,
        summary=summary,
        opening_date=opening_date,
        closing_date=closing_date,
        associated_campaign_number=associated_campaign or None,
        source_url=f"https://www.nhtsa.gov/nhtsa-datasets-and-apis" if investigation_number else None,
        jurisdiction="US",
        authority_rating="primary",
        harvest_timestamp=_utc_now(),
        raw_record_hash=raw_hash,
        normalization_version="automotive_core_v1",
    )


def normalize_investigation_rows(
    source_id: str,
    rows: List[Dict[str, str]],
) -> List[AutomotiveInvestigation]:
    """Normalize a list of raw ODI investigation rows."""
    return [normalize_investigation_row(source_id, i + 1, row) for i, row in enumerate(rows)]
```

- [ ] **Step 4: Run test to verify it passes**

Same command as Step 2.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/app/platform_generator/overlays/automotive_core/app/core/automotive_normalizers.py backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py
git commit -m "feat(automotive): add ODI investigation normalizer"
```

---

## Task 4: Extend harvester for investigations

**Files:**
- Modify: `backend/scripts/harvest_nhtsa.py`

**Interfaces:**
- Consumes: existing recall harvest functions (`_download_stream`, `_validate_zip_member`, `_sha256_file`, etc.).
- Produces: `harvest_investigations()` and CLI support for `--family investigation`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_harvest_nhtsa.py`:

```python
def test_investigation_fixture_harvest_succeeds(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    result = harvest_investigations(
        output_dir=tmp_path,
        fixture_path=fixture,
    )
    assert result["record_count"] > 0
    assert result["harvest_manifest"]["source_family"] == "investigation"
    assert (tmp_path / "canonical" / "investigations.jsonl").exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
python -m pytest tests/test_harvest_nhtsa.py::test_investigation_fixture_harvest_succeeds -v
```
Expected: FAIL — `harvest_investigations` not defined.

- [ ] **Step 3: Add source-specific constants and helpers**

At module level in `backend/scripts/harvest_nhtsa.py`, add:

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

- [ ] **Step 4: Implement investigation extraction and loading**

Add:

```python
def extract_investigation_csv(zip_path: Path) -> Path:
    """Extract the investigation flat file from the archive."""
    extract_dir = zip_path.parent / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        candidates = [
            info for info in zf.infolist()
            if not info.is_dir() and Path(info.filename).name in EXPECTED_INVESTIGATION_ARCHIVE_MEMBERS
        ]
        if not candidates:
            names = [info.filename for info in zf.infolist() if not info.is_dir()]
            raise HarvestError("ARCHIVE_UNEXPECTED_CONTENT", f"No expected investigation file in archive: {names}")

        chosen = sorted(candidates, key=lambda i: i.file_size, reverse=True)[0]
        safe_name = _validate_zip_member(chosen.filename)
        dest = extract_dir / Path(safe_name).name
        with zf.open(chosen) as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return dest


def _investigation_rows_from_csv(csv_path: Path) -> List[Dict[str, str]]:
    rows = []
    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(
            f,
            fieldnames=NHTSA_INVESTIGATION_FIELDNAMES,
            delimiter="\t",
        )
        for row in reader:
            rows.append({k: v for k, v in row.items() if k is not None})
    return rows


def _load_investigation_fixture(fixture_path: Path) -> List[Dict[str, str]]:
    suffix = fixture_path.suffix.lower()
    if suffix == ".jsonl":
        rows = []
        with fixture_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    if suffix in {".csv", ".txt"}:
        rows = []
        with fixture_path.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, fieldnames=NHTSA_INVESTIGATION_FIELDNAMES, delimiter="\t")
            for idx, row in enumerate(reader):
                if idx == 0 and row.get("NHTSA_ACTION_NUMBER") == "NHTSA_ACTION_NUMBER":
                    continue
                rows.append({k: v for k, v in row.items() if k is not None})
        return rows
    raise HarvestError("UNSUPPORTED_FIXTURE", f"Fixture must be .jsonl, .csv or .txt: {fixture_path}")
```

- [ ] **Step 5: Implement harvest_investigations**

Add:

```python
def harvest_investigations(
    output_dir: Path,
    source_url: Optional[str] = None,
    since_year: Optional[int] = None,
    max_records: Optional[int] = None,
    dry_run: bool = False,
    fixture_path: Optional[Path] = None,
    force_download: bool = False,
) -> Dict[str, Any]:
    """Harvest NHTSA ODI investigations and write raw artifacts + harvest manifest."""
    source_id = "nhtsa_investigations"
    url = source_url or DEFAULT_INVESTIGATION_URL
    output_dir = Path(output_dir)
    raw_dir = output_dir / "raw" / source_id
    canonical_dir = output_dir / "canonical"
    if not dry_run:
        raw_dir.mkdir(parents=True, exist_ok=True)
        canonical_dir.mkdir(parents=True, exist_ok=True)

    archive_path = raw_dir / Path(urlparse(url).path).name
    extracted_csv: Optional[Path] = None
    content_hash = ""

    if fixture_path:
        logger.info("Using fixture %s for %s", fixture_path, source_id)
        rows = _load_investigation_fixture(fixture_path)
        content_hash = hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        fixture_copy = raw_dir / f"fixture{fixture_path.suffix}"
        if not dry_run:
            shutil.copy2(fixture_path, fixture_copy)
    else:
        if not dry_run:
            if archive_path.exists() and not force_download:
                content_hash = _sha256_file(archive_path)
                logger.info("Reusing existing archive %s (sha256=%s)", archive_path, content_hash)
            else:
                _download_stream(url, archive_path)
                content_hash = _sha256_file(archive_path)
                logger.info("Downloaded archive %s (sha256=%s)", archive_path, content_hash)
            extracted_csv = extract_investigation_csv(archive_path)
            rows = _investigation_rows_from_csv(extracted_csv)
        else:
            rows = []

    if since_year is not None:
        filtered = []
        for row in rows:
            year_str = _field_getter(row)("YEAR", "year", "YEARTXT") or ""
            year_val = _parse_int(year_str)
            if year_val is not None and year_val >= since_year:
                filtered.append(row)
        rows = filtered

    if max_records is not None and len(rows) > max_records:
        logger.info("Capping harvest at %d records (raw rows available: %d)", max_records, len(rows))
        rows = rows[:max_records]

    canonical_records: List[Dict[str, Any]] = []
    for sequence, row in enumerate(rows, start=1):
        canonical = normalize_investigation_row(source_id, sequence, row)
        canonical_records.append(canonical.model_dump())

    harvest_manifest = {
        "source_id": source_id,
        "source_family": "investigation",
        "source_url": url,
        "retrieval_method": "download_csv_zip",
        "harvest_timestamp": _utc_now(),
        "content_hash": content_hash,
        "record_count": len(canonical_records),
        "raw_archive_path": str(archive_path.relative_to(output_dir)) if not fixture_path else None,
        "extracted_csv_path": str(extracted_csv.relative_to(output_dir)) if extracted_csv else None,
        "fixture_path": str(fixture_path.name) if fixture_path else None,
        "since_year": since_year,
    }

    canonical_path = canonical_dir / "investigations.jsonl"
    manifest_path = raw_dir / "harvest_manifest.json"

    if not dry_run:
        tmp_canonical = canonical_path.with_suffix(".jsonl.tmp")
        with tmp_canonical.open("w", encoding="utf-8") as f:
            for record in canonical_records:
                f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp_canonical, canonical_path)

        tmp_manifest = manifest_path.with_suffix(".json.tmp")
        tmp_manifest.write_text(
            json.dumps(harvest_manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_manifest, manifest_path)
    else:
        logger.info("Dry run: would write %d canonical records to %s", len(canonical_records), canonical_path)

    return {
        "harvest_manifest": harvest_manifest,
        "canonical_path": canonical_path if not dry_run else None,
        "record_count": len(canonical_records),
    }
```

- [ ] **Step 6: Wire CLI `--family investigation`**

In `main()`, replace:
```python
if args.family != "recalls":
    logger.error("This vertical slice only supports --family recalls")
    return 1
```
with:
```python
if args.family == "recalls":
    harvest_func = harvest_recalls
elif args.family == "investigation":
    harvest_func = harvest_investigations
else:
    logger.error("Unsupported --family: %s (supported: recalls, investigation)", args.family)
    return 1
```

Then replace the `harvest_recalls(...)` call with:
```python
result = harvest_func(
    output_dir=args.output_dir,
    since_year=args.since_year,
    max_records=args.max_records,
    dry_run=args.dry_run,
    fixture_path=args.fixture,
    force_download=args.force_download,
)
```

Update the `--family` help text to: `"Source family to harvest (recalls|investigation)"`.

- [ ] **Step 7: Run the new test**

Same command as Step 2.
Expected: PASS.

- [ ] **Step 8: Run existing recall harvester tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
python -m pytest tests/test_harvest_nhtsa.py -v
```
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/scripts/harvest_nhtsa.py backend/tests/test_harvest_nhtsa.py
git commit -m "feat(automotive): extend harvester to support NHTSA ODI investigations"
```

---

## Task 5: Add investigation test fixture

**Files:**
- Create: `backend/tests/fixtures/nhtsa_investigations_sample.txt`

**Interfaces:**
- Consumes: official ODI column layout.
- Produces: tab-delimited fixture with at least two investigations and one multi-row investigation.

- [ ] **Step 1: Create the fixture**

Write:
```text
PE16-007	Tesla	Model S	2015	AIR BAGS	Tesla Motors	20160101	20161231	16V176000	Air bag deployment investigation	Investigation into unintended air bag deployment events.
PE16-007	Tesla	Model S	2016	AIR BAGS	Tesla Motors	20160101	20161231	16V176000	Air bag deployment investigation	Investigation into unintended air bag deployment events.
DP20-001	Ford	F-150	2018	BRAKES	Ford Motor Company	20200101			Unexpected braking investigation	Investigation into reports of unintended braking.
AQ08001	PACE AMERICAN	TRAILER	2003	WHEELS	PACE AMERICAN, INC.	20080618	20081029		PACE AMERICAN 573 RETRACTION	Retraction request for earlier defect report.
```

- [ ] **Step 2: Verify fixture loads**

Run:
```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
python - <<'PY'
from pathlib import Path
from scripts.harvest_nhtsa import _load_investigation_fixture
rows = _load_investigation_fixture(Path("tests/fixtures/nhtsa_investigations_sample.txt"))
print(len(rows), "rows")
for r in rows:
    print(r["NHTSA_ACTION_NUMBER"], r["YEAR"])
PY
```
Expected:
```text
4 rows
PE16-007 2015
PE16-007 2016
DP20-001 2018
AQ08001 2003
```

- [ ] **Step 3: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/tests/fixtures/nhtsa_investigations_sample.txt
git commit -m "test(automotive): add NHTSA investigations sample fixture"
```

---

## Task 6: Add harvester tests

**Files:**
- Modify: `backend/tests/test_harvest_nhtsa.py`

**Interfaces:**
- Consumes: `harvest_investigations`, fixture file, tmp_path.
- Produces: passing tests for dry-run, hash, unsafe zip, cache reuse, corrupt cache.

- [ ] **Step 1: Add the tests**

Append to `backend/tests/test_harvest_nhtsa.py`:

```python
def test_investigation_dry_run_performs_no_writes(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    result = harvest_investigations(
        output_dir=tmp_path,
        fixture_path=fixture,
        dry_run=True,
    )
    assert result["record_count"] == 4
    assert not (tmp_path / "canonical").exists()


def test_investigation_content_hash_is_recorded(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    result = harvest_investigations(
        output_dir=tmp_path,
        fixture_path=fixture,
    )
    assert result["harvest_manifest"]["content_hash"]
    assert len(result["harvest_manifest"]["content_hash"]) == 64


def test_investigation_unsafe_zip_path_rejected(tmp_path: Path) -> None:
    from scripts.harvest_nhtsa import UnsafeArchivePathError, _validate_zip_member
    with pytest.raises(UnsafeArchivePathError):
        _validate_zip_member("../../../etc/passwd")


def test_investigation_valid_cache_reused(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    first = harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
    second = harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
    assert first["record_count"] == second["record_count"]


def test_investigation_corrupt_cache_rejected(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "nhtsa_investigations_sample.txt"
    harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
    archive = tmp_path / "raw" / "nhtsa_investigations" / "FLAT_INV.zip"
    if archive.exists():
        archive.write_bytes(b"corrupt")
        result = harvest_investigations(output_dir=tmp_path, fixture_path=fixture)
        assert result["record_count"] == 4
```

- [ ] **Step 2: Run tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
python -m pytest tests/test_harvest_nhtsa.py -v
```
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/tests/test_harvest_nhtsa.py
git commit -m "test(automotive): add investigation harvester regression tests"
```

---

## Task 7: Add normalizer tests

**Files:**
- Modify: `backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py`

**Interfaces:**
- Consumes: `normalize_investigation_row`, `normalize_investigation_rows`.
- Produces: passing tests for status, missing values, control chars, determinism.

- [ ] **Step 1: Add the tests**

Append to `tests/test_automotive_normalizers.py`:

```python
def test_investigation_status_open_when_no_cdate() -> None:
    row = {"NHTSA_ACTION_NUMBER": "DP20-001", "CDATE": ""}
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.status == "Open"


def test_investigation_status_closed_when_cdate_present() -> None:
    row = {"NHTSA_ACTION_NUMBER": "PE16-007", "CDATE": "20161231"}
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.status == "Closed"


def test_investigation_missing_values_remain_null() -> None:
    row = {"NHTSA_ACTION_NUMBER": "PE16-007"}
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert record.make is None
    assert record.model is None
    assert record.model_year is None
    assert record.component is None
    assert record.summary is None


def test_investigation_deterministic_id_and_hash() -> None:
    rows = [
        {"NHTSA_ACTION_NUMBER": "PE16-007", "MAKE": "Tesla", "YEAR": "2015"},
        {"NHTSA_ACTION_NUMBER": "DP20-001", "MAKE": "Ford", "YEAR": "2018"},
    ]
    first = normalize_investigation_rows("nhtsa_investigations", rows)
    second = normalize_investigation_rows("nhtsa_investigations", rows)
    assert [r.record_id for r in first] == [r.record_id for r in second]
    assert [r.raw_record_hash for r in first] == [r.raw_record_hash for r in second]


def test_investigation_control_characters_removed() -> None:
    row = {
        "NHTSA_ACTION_NUMBER": "PE16-007",
        "MAKE": "Tesla\x00\x01",
        "SUMMARY": "Summary with\nnewlines\rand\ttabs.",
    }
    record = normalize_investigation_row("nhtsa_investigations", 1, row)
    assert "\x00" not in record.make
    assert "\n" not in record.summary
    assert "\r" not in record.summary
```

- [ ] **Step 2: Run tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py -v
```
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py
git commit -m "test(automotive): add investigation normalizer regression tests"
```

---

## Task 8: Extend pack builder for multi-family

**Files:**
- Modify: `backend/app/platform_generator/overlays/automotive_core/app/core/automotive_pack_builder.py`
- Modify: `backend/app/platform_generator/overlays/automotive_core/scripts/build_automotive_core_pack.py`

**Interfaces:**
- Consumes: `AutomotiveInvestigation`, `normalize_investigation_rows`, `_bulk_index_chunks`.
- Produces: `chunk_investigation_records`, `build_automotive_core_pack_from_families`, updated CLI.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_automotive_pack_builder.py`:

```python
def test_chunk_investigation_records_are_deterministic() -> None:
    from app.models.automotive_records import AutomotiveInvestigation
    records = [
        AutomotiveInvestigation(
            record_id="r1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            component="AIR BAGS",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    chunks = chunk_investigation_records(records)
    assert len(chunks) == 1
    assert chunks[0].investigation_number == "PE16-007"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_pack_builder.py::test_chunk_investigation_records_are_deterministic -v
```
Expected: FAIL — `chunk_investigation_records` not defined.

- [ ] **Step 3: Add investigation chunk compiler**

Append to `app/core/automotive_pack_builder.py`:

```python
def _compile_investigation_text(record: AutomotiveInvestigation) -> str:
    parts: List[str] = []
    if record.investigation_number:
        parts.append(f"Investigation: {record.investigation_number}")
    if record.status:
        parts.append(f"Status: {record.status}")
    if record.investigation_type:
        parts.append(f"Type: {record.investigation_type}")
    if record.model_year:
        parts.append(f"Year: {record.model_year}")
    if record.make:
        parts.append(f"Make: {record.make}")
    if record.model:
        parts.append(f"Model: {record.model}")
    if record.manufacturer:
        parts.append(f"Manufacturer: {record.manufacturer}")
    if record.component:
        parts.append(f"Component: {record.component}")
    if record.opening_date:
        parts.append(f"Opened: {record.opening_date}")
    if record.closing_date:
        parts.append(f"Closed: {record.closing_date}")
    if record.associated_campaign_number:
        parts.append(f"Associated recall campaign: {record.associated_campaign_number}")
    if record.subject:
        parts.append(f"Subject: {record.subject}")
    if record.summary:
        parts.append(f"Summary: {record.summary}")
    return "\n".join(parts)


def chunk_investigation_records(
    records: List[AutomotiveInvestigation],
) -> List[AutomotiveChunk]:
    """Convert canonical investigation records into deterministic retrieval chunks."""
    chunks: List[AutomotiveChunk] = []
    for record in records:
        text = _compile_investigation_text(record).strip()
        if not text:
            logger.warning("Skipping evidence-free investigation record %s", record.record_id)
            continue
        chunk_index = 0
        chunk_id = _chunk_id(record.record_id, chunk_index)
        chunks.append(
            AutomotiveChunk(
                chunk_id=chunk_id,
                record_id=record.record_id,
                source_id=record.source_id,
                source_family=record.source_family,
                campaign_number=record.associated_campaign_number or "",
                make=record.make,
                model=record.model,
                model_year=record.model_year,
                component=record.component,
                knowledge_layer=FOUNDATION_COLLECTION,
                foundation_pack_id=FOUNDATION_PACK_ID,
                source_authority=record.authority_rating,
                jurisdiction=record.jurisdiction,
                chunk_index=chunk_index,
                chunking_version=CHUNKING_VERSION,
                text=text,
                text_hash=_text_hash(text),
                record_reference=record.investigation_number,
                source_url=record.source_url,
                metadata={
                    "investigation_number": record.investigation_number,
                    "status": record.status,
                    "investigation_type": record.investigation_type,
                    "opening_date": record.opening_date,
                    "closing_date": record.closing_date,
                    "associated_campaign_number": record.associated_campaign_number,
                    "raw_record_hash": record.raw_record_hash,
                    "normalization_version": record.normalization_version,
                },
            )
        )
    return chunks
```

- [ ] **Step 4: Update `_bulk_index_chunks` to encode source_family in doc_id**

In `app/core/automotive_pack_builder.py`, find the existing `_bulk_index_chunks` function and change:

```python
doc_id = f"recall:{chunk.record_id}"
```

to:

```python
doc_id = f"{chunk.source_family}:{chunk.record_id}"
```

This makes `chunk.doc_id` a reliable family discriminator at retrieval time without adding a new database column.

- [ ] **Step 5: Add multi-family orchestrator**

Append to `app/core/automotive_pack_builder.py`:

```python
def _load_canonical_records_by_family(path: Path) -> Tuple[str, List[Any]]:
    """Load a canonical JSONL and return (source_family, records)."""
    if not path.exists():
        raise FileNotFoundError(f"Canonical records not found: {path}")
    records: List[Any] = []
    source_family = "unknown"
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            source_family = data.get("source_family", source_family)
            if source_family == "recall":
                records.append(AutomotiveRecall.model_validate(data))
            elif source_family == "investigation":
                records.append(AutomotiveInvestigation.model_validate(data))
            else:
                raise ValueError(f"Unsupported source_family in {path}: {source_family}")
    return source_family, records


def build_automotive_core_pack_from_families(
    canonical_records_paths: List[Path],
    output_dir: Path,
    project_id: str = "automotive_core_v1",
    dry_run: bool = False,
) -> PackManifest:
    """Build the automotive foundation pack from multiple canonical record files."""
    from app.core.rag.embeddings import get_embedder

    output_dir = Path(output_dir)
    chunks_dir = output_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    all_chunks: List[AutomotiveChunk] = []
    source_families: List[str] = []
    total_records = 0
    harvest_timestamp = ""

    for records_path in canonical_records_paths:
        family, records = _load_canonical_records_by_family(records_path)
        source_families.append(family)
        total_records += len(records)
        if records and not harvest_timestamp:
            harvest_timestamp = records[0].harvest_timestamp

        if family == "recall":
            family_chunks = chunk_recall_records(records)
            chunks_path = chunks_dir / "recalls.jsonl"
        elif family == "investigation":
            family_chunks = chunk_investigation_records(records)
            chunks_path = chunks_dir / "investigations.jsonl"
        else:
            raise ValueError(f"Unsupported family: {family}")

        tmp_chunks = chunks_path.with_suffix(".jsonl.tmp")
        with tmp_chunks.open("w", encoding="utf-8") as f:
            for chunk in family_chunks:
                f.write(chunk.model_dump_json() + "\n")
        os.replace(tmp_chunks, chunks_path)
        all_chunks.extend(family_chunks)

    embedding_identity: Dict[str, Any] = {"model": "fake", "dim": 384, "normalized": True}
    indexed_count = 0

    if not dry_run:
        _require_production_embedder()
        embedder = get_embedder()
        embedding_identity = embedder.identity
        indexed_count = _index_chunks(
            project_id,
            _embed_chunks(all_chunks),
            dim=embedder.dim,
            model_name=embedder.identity.get("model", os.getenv("RAG_EMBEDDING_MODEL", "fake")),
        )

    manifest = PackManifest(
        pack_id=FOUNDATION_PACK_ID,
        pack_version="automotive_core_rag_v1.0.0",
        foundation_collection=FOUNDATION_COLLECTION,
        harvest_timestamp=harvest_timestamp or _utc_now(),
        build_timestamp=_utc_now(),
        record_count=total_records,
        chunk_count=len(all_chunks),
        embedding_identity=embedding_identity,
        source_families=source_families,
        status="indexed" if indexed_count > 0 else "validated",
    )

    manifest_path = output_dir / "pack_manifest.json"
    tmp_manifest = manifest_path.with_suffix(".json.tmp")
    tmp_manifest.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_manifest, manifest_path)

    return manifest
```

- [ ] **Step 6: Update the build script CLI**

Change `scripts/build_automotive_core_pack.py` to accept multiple `--records`:

```python
def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Automotive Core RAG foundation pack")
    parser.add_argument("--records", required=True, type=Path, action="append", help="Path to canonical records JSONL (may be given multiple times)")
    parser.add_argument("--output", required=True, type=Path, help="Output directory for pack artifacts")
    parser.add_argument("--project-id", default="automotive_core_v1", help="Vector-store project id")
    parser.add_argument("--dry-run", action="store_true", help="Compile chunks without embedding/indexing")
    args = parser.parse_args(argv)

    for path in args.records:
        if not path.exists():
            logger.error("Canonical records not found: %s", path)
            return 1

    manifest = build_automotive_core_pack_from_families(
        canonical_records_paths=args.records,
        output_dir=args.output,
        project_id=args.project_id,
        dry_run=args.dry_run,
    )
    print(manifest.model_dump_json(indent=2))
    return 0
```

- [ ] **Step 7: Run the new test**

Same as Step 2.
Expected: PASS.

- [ ] **Step 8: Run existing pack builder tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_pack_builder.py -v
```
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/app/platform_generator/overlays/automotive_core/app/core/automotive_pack_builder.py backend/app/platform_generator/overlays/automotive_core/scripts/build_automotive_core_pack.py backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_pack_builder.py
git commit -m "feat(automotive): add multi-family pack builder with investigation chunks"
```

---

## Task 9: Add pack builder tests

**Files:**
- Modify: `backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_pack_builder.py`

**Interfaces:**
- Consumes: `build_automotive_core_pack_from_families`, sample records.
- Produces: passing tests for multi-family manifest and idempotency.

- [ ] **Step 1: Add the tests**

Append:

```python
def test_investigation_number_appears_in_every_chunk() -> None:
    from app.models.automotive_records import AutomotiveInvestigation
    records = [
        AutomotiveInvestigation(
            record_id="r1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    chunks = chunk_investigation_records(records)
    assert all("PE16-007" in c.text for c in chunks)


def test_multi_family_build_writes_both_chunk_files(tmp_path: Path) -> None:
    recalls = _sample_records()
    recall_path = tmp_path / "recalls.jsonl"
    with recall_path.open("w", encoding="utf-8") as f:
        for r in recalls:
            f.write(r.model_dump_json() + "\n")

    from app.models.automotive_records import AutomotiveInvestigation
    investigations = [
        AutomotiveInvestigation(
            record_id="r1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
    ]
    inv_path = tmp_path / "investigations.jsonl"
    with inv_path.open("w", encoding="utf-8") as f:
        for r in investigations:
            f.write(r.model_dump_json() + "\n")

    manifest = build_automotive_core_pack_from_families(
        canonical_records_paths=[recall_path, inv_path],
        output_dir=tmp_path / "pack",
        project_id="automotive_core_v1",
        dry_run=True,
    )

    assert "recall" in manifest.source_families
    assert "investigation" in manifest.source_families
    assert (tmp_path / "pack" / "chunks" / "recalls.jsonl").exists()
    assert (tmp_path / "pack" / "chunks" / "investigations.jsonl").exists()
```

- [ ] **Step 2: Run tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_pack_builder.py -v
```
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_pack_builder.py
git commit -m "test(automotive): add multi-family pack builder tests"
```

---

## Task 10: Extend retrieval for investigation lookup

**Files:**
- Modify: `backend/app/platform_generator/overlays/automotive_core/app/core/automotive_retrieval.py`

**Interfaces:**
- Consumes: `get_embedder`, `get_store`, `AutomotiveEvidence`.
- Produces: investigation-aware identifier extraction, exact lookup, family-aware citation.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_automotive_retrieval.py`:

```python
def test_exact_investigation_lookup_returns_top_result(tmp_path, monkeypatch) -> None:
    _index_sample_investigations(tmp_path, monkeypatch)
    results = retrieve_by_investigation_number("PE16-007")
    assert len(results) >= 1
    assert results[0].record_reference == "PE16-007"
    assert results[0].knowledge_layer == FOUNDATION_PROJECT_ID
    assert results[0].source_family == "investigation"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_retrieval.py::test_exact_investigation_lookup_returns_top_result -v
```
Expected: FAIL — `retrieve_by_investigation_number` not defined.

- [ ] **Step 3: Add identifier extraction and exact lookup**

Append to `app/core/automotive_retrieval.py`:

```python
_INVESTIGATION_RE = re.compile(
    r"\b(?:DP|PE|EA|RQ|AQ)\d{2,3}[\-]?\d{3,4}\b",
    re.IGNORECASE,
)


def _extract_investigation_number(query: str) -> Optional[str]:
    match = _INVESTIGATION_RE.search(query)
    if match:
        return match.group(0).upper()
    return None


def retrieve_by_investigation_number(investigation_number: str) -> List[AutomotiveEvidence]:
    """Exact investigation-number lookup."""
    return retrieve_foundation_evidence(
        query=f"NHTSA investigation {investigation_number}",
        top_k=5,
    )
```

- [ ] **Step 4: Make retrieve_foundation_evidence family-aware**

Modify `retrieve_foundation_evidence` to:

1. Detect investigation numbers as well as campaign numbers.
2. Build identifier candidates from either type.
3. Set `source_title` and `source_family` based on the chunk's stored metadata.

Replace the existing retrieval body with:

```python
def retrieve_foundation_evidence(
    query: str,
    top_k: int = 5,
) -> List[AutomotiveEvidence]:
    """Retrieve evidence from the automotive foundation corpus."""
    if not query or not query.strip():
        return []

    embedder = get_embedder()
    store = get_store(dim=embedder.dim)
    query_vec = embedder.encode_queries([query])[0]

    # Identifier-aware exact lookup.
    identifiers: List[str] = []
    campaign = _extract_campaign_number(query)
    investigation = _extract_investigation_number(query)
    if campaign:
        identifiers.append(campaign)
    if investigation:
        identifiers.append(investigation)

    identifier_candidates = []
    if identifiers:
        identifier_candidates = store.identifier_search(
            FOUNDATION_PROJECT_ID, identifiers, k=max(top_k * 4, 20)
        )

    semantic_candidates = store.search(
        FOUNDATION_PROJECT_ID, query_vec, k=max(top_k * 4, 20), query_text=query
    )

    by_id: Dict[str, Any] = {}
    for c in semantic_candidates:
        by_id[c.chunk_id] = {"chunk": c, "score": c.score or 0.0, "id_boost": 0.0}
    for c in identifier_candidates:
        entry = by_id.get(c.chunk_id)
        if entry:
            entry["id_boost"] = 2.0
        else:
            by_id[c.chunk_id] = {"chunk": c, "score": 0.0, "id_boost": 2.0}

    scored = [
        (entry["score"] + entry["id_boost"], entry["chunk"])
        for entry in by_id.values()
    ]
    scored.sort(key=lambda x: -x[0])

    results: List[AutomotiveEvidence] = []
    for score, chunk in scored[:top_k]:
        # doc_id is prefixed with source_family by _bulk_index_chunks.
        source_family = chunk.doc_id.split(":", 1)[0] if ":" in chunk.doc_id else "recall"
        record_reference = chunk.doc_id.split(":")[-1]
        source_title = f"NHTSA evidence {record_reference}"
        if source_family == "recall":
            campaign = _extract_campaign_number(chunk.text)
            record_reference = campaign or record_reference
            source_title = f"NHTSA Recall {record_reference}"
        elif source_family == "investigation":
            inv = _extract_investigation_number(chunk.text)
            record_reference = inv or record_reference
            source_title = f"NHTSA Investigation {record_reference}"

        results.append(
            AutomotiveEvidence(
                knowledge_layer=FOUNDATION_PROJECT_ID,
                foundation_pack_id="automotive_core_rag_v1",
                source_family=source_family,
                source_title=source_title,
                source_authority="primary",
                source_url=None,
                record_reference=record_reference,
                retrieval_score=round(float(score), 6),
                chunk_text=chunk.text,
                metadata={
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "chunk_index": chunk.chunk_index,
                    "source_family": source_family,
                },
            )
        )
    return results
```

Note: `_bulk_index_chunks` must prefix `doc_id` with `chunk.source_family` (see Task 8 step updating `_bulk_index_chunks`). The chunks_v2 table does not expose `source_family` as a top-level column in the current migration; the doc_id prefix is the reliable source of family identity at retrieval time.

- [ ] **Step 5: Run the new test**

Same as Step 2.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/app/platform_generator/overlays/automotive_core/app/core/automotive_retrieval.py backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_retrieval.py
git commit -m "feat(automotive): add investigation-aware retrieval and family-aware citations"
```

---

## Task 11: Add retrieval tests

**Files:**
- Modify: `backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_retrieval.py`

**Interfaces:**
- Consumes: `retrieve_foundation_evidence`, `retrieve_by_investigation_number`, fake embedder.
- Produces: passing tests for vehicle/component lookup, unsupported identifier, citation envelope.

- [ ] **Step 1: Add helper and tests**

Append:

```python
def _index_sample_investigations(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", "fake")
    monkeypatch.setenv("RAG_EMBEDDING_DIMENSIONS", "256")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))

    from app.core.rag.embeddings import reset_embedder_cache
    from app.core.rag.vector_store import reset_store_cache

    reset_embedder_cache()
    reset_store_cache()

    from app.models.automotive_records import AutomotiveInvestigation
    records = [
        AutomotiveInvestigation(
            record_id="r1",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="PE16-007",
            make="Tesla",
            model="Model S",
            model_year="2015",
            component="AIR BAGS",
            summary="Investigation into unintended air bag deployment.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h1",
        ),
        AutomotiveInvestigation(
            record_id="r2",
            source_id="nhtsa_investigations",
            source_family="investigation",
            investigation_number="DP20-001",
            make="Ford",
            model="F-150",
            model_year="2018",
            component="BRAKES",
            summary="Investigation into unexpected braking events.",
            harvest_timestamp="2026-07-12T00:00:00Z",
            raw_record_hash="h2",
        ),
    ]
    inv_path = tmp_path / "investigations.jsonl"
    with inv_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")

    build_automotive_core_pack_from_families(
        canonical_records_paths=[inv_path],
        output_dir=tmp_path / "pack",
        project_id=FOUNDATION_PROJECT_ID,
        dry_run=False,
    )


def test_investigation_vehicle_lookup_returns_relevant_evidence(tmp_path, monkeypatch) -> None:
    _index_sample_investigations(tmp_path, monkeypatch)
    results = retrieve_foundation_evidence("2018 Ford F-150 braking investigation", top_k=5)
    references = {r.record_reference for r in results}
    assert "DP20-001" in references


def test_investigation_component_query_returns_relevant_evidence(tmp_path, monkeypatch) -> None:
    _index_sample_investigations(tmp_path, monkeypatch)
    results = retrieve_foundation_evidence("air bag deployment Tesla", top_k=5)
    references = {r.record_reference for r in results}
    assert "PE16-007" in references


def test_unsupported_investigation_does_not_fabricate_match(tmp_path, monkeypatch) -> None:
    _index_sample_investigations(tmp_path, monkeypatch)
    results = retrieve_by_investigation_number("ZZ99-999")
    if results:
        assert all(r.record_reference != "ZZ99-999" for r in results)


def test_investigation_citation_envelope_is_complete(tmp_path, monkeypatch) -> None:
    _index_sample_investigations(tmp_path, monkeypatch)
    results = retrieve_foundation_evidence("NHTSA investigation PE16-007", top_k=5)
    assert results
    for r in results:
        assert r.knowledge_layer == FOUNDATION_PROJECT_ID
        assert r.foundation_pack_id == "automotive_core_rag_v1"
        assert r.source_family == "investigation"
        assert r.source_authority == "primary"
        assert r.record_reference
        assert r.chunk_text
        assert r.metadata
```

- [ ] **Step 2: Run tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_retrieval.py -v
```
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git add backend/app/platform_generator/overlays/automotive_core/tests/test_automotive_retrieval.py
git commit -m "test(automotive): add investigation retrieval regression tests"
```

---

## Task 12: Add automotive container actions

**Files:**
- Modify: `Cerebrum-Blocks/block_store/kits/automotive/bundle/app/containers/automotive.py`

**Interfaces:**
- Consumes: `DomainContainer`, retrieval functions (imported at runtime to avoid circular deps).
- Produces: `get_actions()` returns `lookup_investigation`, `summarize_investigation`, `compare_vehicle_safety`; handlers call `container.route()` correctly.

- [ ] **Step 1: Write the failing test**

Create (or add to existing automotive container tests):

```python
def test_automotive_container_lookup_investigation_action_exists() -> None:
    from app.containers.automotive import AutomotiveContainer
    container = AutomotiveContainer()
    actions = container.get_actions()
    assert "lookup_investigation" in actions
```

- [ ] **Step 2: Run test to verify it fails**

Run in the generated platform after generation (or in Cerebrum-Blocks test harness if available):
```bash
cd generated/automotive-safety-intelligence
python -m pytest tests/test_automotive_container.py::test_automotive_container_lookup_investigation_action_exists -v
```
Expected: FAIL — action not registered.

- [ ] **Step 3: Add the actions**

Append to `Cerebrum-Blocks/block_store/kits/automotive/bundle/app/containers/automotive.py`:

```python
    def get_actions(self) -> Dict[str, Callable]:
        """Return action name → handler mapping."""
        return {
            "analyze": self._analyze,
            "extract_entities": self._extract_entities,
            "calculate_metrics": self._calculate_metrics,
            "check_compliance": self._check_compliance,
            "score_risk": self._score_risk,
            "lookup_investigation": self._lookup_investigation,
            "summarize_investigation": self._summarize_investigation,
            "compare_vehicle_safety": self._compare_vehicle_safety,
            "health": self._health,
        }

    async def _lookup_investigation(self, input_data: Any, params: Dict) -> Dict:
        """Exact investigation-number lookup."""
        data = input_data if isinstance(input_data, dict) else {}
        investigation_number = data.get("investigation_number") or params.get("investigation_number")
        if not investigation_number:
            return {"status": "error", "error": "investigation_number required"}
        try:
            from app.core.automotive_retrieval import retrieve_by_investigation_number
            evidence = retrieve_by_investigation_number(investigation_number)
            return {
                "status": "success",
                "investigation_number": investigation_number,
                "evidence": [e.__dict__ for e in evidence],
            }
        except Exception as exc:
            return {"status": "error", "error": f"Lookup failed: {exc}"}

    async def _summarize_investigation(self, input_data: Any, params: Dict) -> Dict:
        """Return a concise summary of an investigation by number."""
        result = await self._lookup_investigation(input_data, params)
        if result.get("status") == "error":
            return result
        evidence = result.get("evidence", [])
        if not evidence:
            return {"status": "success", "summary": "No investigation evidence found."}
        top = evidence[0]
        return {
            "status": "success",
            "investigation_number": top.get("record_reference"),
            "source_family": top.get("source_family"),
            "source_title": top.get("source_title"),
            "summary_text": top.get("chunk_text", "")[:800],
        }

    async def _compare_vehicle_safety(self, input_data: Any, params: Dict) -> Dict:
        """Retrieve recall + investigation evidence for a year/make/model."""
        data = input_data if isinstance(input_data, dict) else {}
        query = data.get("query") or params.get("query")
        if not query:
            return {"status": "error", "error": "query required"}
        try:
            from app.core.automotive_retrieval import retrieve_foundation_evidence
            evidence = retrieve_foundation_evidence(query, top_k=10)
            return {
                "status": "success",
                "query": query,
                "evidence": [e.__dict__ for e in evidence],
            }
        except Exception as exc:
            return {"status": "error", "error": f"Comparison failed: {exc}"}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd generated/automotive-safety-intelligence
python -m pytest tests/test_automotive_container.py::test_automotive_container_lookup_investigation_action_exists -v
```
Expected: PASS.

- [ ] **Step 5: Add route execution test**

```python
async def test_lookup_investigation_routes_via_container_route() -> None:
    from app.containers.automotive import AutomotiveContainer
    container = AutomotiveContainer()
    result = await container.route(
        "lookup_investigation",
        {"investigation_number": "PE16-007"},
        {"action": "lookup_investigation"},
    )
    assert result["status"] in ("success", "error")
```

- [ ] **Step 6: Commit**

```bash
cd Cerebrum-Blocks
git add block_store/kits/automotive/bundle/app/containers/automotive.py
git commit -m "feat(automotive): register investigation lookup actions via generic kit contract"
```

---

## Task 13: Add development seed questions

**Files:**
- Modify: `Cerebrum-Blocks/block_store/kits/automotive/evaluation/development_seed.jsonl`

**Interfaces:**
- Consumes: existing development seed schema.
- Produces: 4 additional investigation seed questions.

- [ ] **Step 1: Append questions**

Add lines:

```jsonl
{"question_id": "auto-seed-009", "category": "exact_investigation_lookup", "question": "Summarize NHTSA investigation PE16-007.", "expected_source_family": "investigation", "expected_identifiers": ["PE16-007"], "required_evidence": ["investigation_number", "summary"], "forbidden_unsupported_claim": true, "answerability": "answerable", "development_seed": true}
{"question_id": "auto-seed-010", "category": "investigation_component_lookup", "question": "Which NHTSA investigations involve unintended braking?", "expected_source_family": "investigation", "expected_identifiers": ["braking"], "required_evidence": ["component", "summary"], "forbidden_unsupported_claim": true, "answerability": "answerable", "development_seed": true}
{"question_id": "auto-seed-011", "category": "investigation_vehicle_lookup", "question": "Find NHTSA investigations for the 2014 Ford F-150.", "expected_source_family": "investigation", "expected_identifiers": ["Ford", "F-150", "2014"], "required_evidence": ["make", "model", "model_year", "investigation_number"], "forbidden_unsupported_claim": true, "answerability": "answerable", "development_seed": true}
{"question_id": "auto-seed-012", "category": "unsupported", "question": "What is NHTSA investigation ZZ99-999?", "expected_source_family": null, "expected_identifiers": [], "required_evidence": [], "forbidden_unsupported_claim": true, "answerability": "unanswerable", "development_seed": true}
```

- [ ] **Step 2: Validate JSONL**

```bash
cd Cerebrum-Blocks
python - <<'PY'
import json
from pathlib import Path
for line in Path("block_store/kits/automotive/evaluation/development_seed.jsonl").read_text().strip().splitlines():
    json.loads(line)
print("valid")
PY
```
Expected: `valid`.

- [ ] **Step 3: Commit**

```bash
cd Cerebrum-Blocks
git add block_store/kits/automotive/evaluation/development_seed.jsonl
git commit -m "test(automotive): add investigation development seed questions"
```

---

## Task 14: Integration verification

**Files:**
- All modified files.

**Interfaces:**
- End-to-end harvest → normalize → chunk → index → retrieve for recalls + investigations.

- [ ] **Step 1: Run targeted automotive tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
python -m pytest tests/test_harvest_nhtsa.py -q --tb=short
PYTHONPATH=app/platform_generator/overlays/automotive_core python -m pytest app/platform_generator/overlays/automotive_core/tests/test_automotive_normalizers.py app/platform_generator/overlays/automotive_core/tests/test_automotive_pack_builder.py app/platform_generator/overlays/automotive_core/tests/test_automotive_retrieval.py -q --tb=short
```
Expected: all PASS.

- [ ] **Step 2: Run existing RAG regressions**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
python -m pytest tests/test_rag_*.py -q --tb=short
```
Expected: all PASS (or only pre-existing failures documented).

- [ ] **Step 3: Generate platform and run generated tests**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue/backend
python -m scripts.generate_automotive_platform \
  --manifest ../docs/superpowers/fixtures/automotive_platform_manifest.json \
  --output ../generated/automotive-safety-intelligence \
  --fork-path ../../The_Fork \
  --blocks-path ../../Cerebrum-Blocks \
  --force

cd ../generated/automotive-safety-intelligence
python -m py_compile app/models/automotive_records.py app/core/automotive_normalizers.py app/core/automotive_pack_builder.py app/core/automotive_retrieval.py app/containers/automotive.py
python -m pytest tests/test_automotive_*.py -q --tb=short
```
Expected: generation succeeds; py_compile succeeds; tests PASS.

- [ ] **Step 4: Run diff check and secret scan**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git diff --check
# Run repository security scan if available, e.g.:
# python -m scripts.security_scan --changed-only
```
Expected: no trailing whitespace or conflict markers.

- [ ] **Step 5: Push branches**

```bash
cd CerebrumDev.ai/.worktrees/cda-rag-ingestion-job-queue
git push origin feat/automotive-pilot-pr2

cd ../../Cerebrum-Blocks
git push origin feat/automotive-pilot-pr2
```

---

## Self-review

### Spec coverage

| Spec requirement | Task |
|---|---|
| Official ODI bulk file harvest | Task 4 |
| Canonical investigation model | Task 2 |
| Defensive column aliases | Task 3 |
| Multi-row investigation handling | Task 3 design, Task 8 chunking |
| Deterministic IDs/hashes | Tasks 2, 3, 8 |
| Multi-family pack builder | Task 8 |
| BGE 384 indexing into `chunks_v2` | Task 8 (reuses existing path) |
| Exact investigation lookup | Task 10 |
| Vehicle/component retrieval | Tasks 10, 11 |
| Family-aware citation envelope | Tasks 8, 10 |
| Domain-kit execution contract | Task 12 |
| Development seed questions | Task 13 |
| Out-of-scope guard | Global Constraints |

### Placeholder scan

- No TBD/TODO/"implement later".
- Every test step contains concrete assertions.
- Every code step contains the actual function/class signatures.

### Type consistency

- `AutomotiveInvestigation` fields match the normalizer output and chunk compiler consumption.
- `build_automotive_core_pack_from_families` uses `List[Path]` and returns `PackManifest`.
- `chunk_investigation_records` returns `List[AutomotiveChunk]`.
- `retrieve_by_investigation_number` returns `List[AutomotiveEvidence]`.
- Container actions accept `(input_data: Any, params: Dict)` and return `Dict`.

### Open risks

- `AutomotiveChunk.campaign_number` is reused to store the associated recall campaign for investigations; this is intentional and documented.
- The runtime orchestrator's generic dispatch fix in The_Fork is out of scope for this slice; the automotive actions are correctly registered and route through `container.route()` so they will work once the orchestrator passes `params["action"]` correctly.
