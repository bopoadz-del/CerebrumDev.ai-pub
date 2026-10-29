"""Turn a stored upload into located chunks and advance its parse job (F-1b).

F-1a stored the raw blob and left a parse job at ``queued``. F-1b is the
PARSER: :func:`parse_object` reads the tenant's own stored bytes, picks the
type-appropriate parser, and reuses
``cerebrum_product_kernel/ingestion/chunker.chunk_document`` to split each
structural unit (a PDF page, a spreadsheet row, a DOCX paragraph, …) into the
RetrievalEngine-shaped chunk dicts. The parser wrapper then ADDS two keys to
every chunk — ``file`` (the source name) and ``location`` (a structured
``{page|sheet|row|paragraph|line|heading}`` dict) — so each chunk can be traced
back to exactly where in the document it came from. ``chunk_document`` itself is
never modified or weakened: its output keys are left exactly as F-1a tests pin
them, and this module only extends each returned dict.

Six types are parsed for real (no 501, no "not yet"):

====  ======================================  ==============================
type  unit                                    location
====  ======================================  ==============================
PDF   one page (pypdf, per-page text)         ``{"page": N}``
DOCX  one paragraph (headings + body)         ``{"paragraph": i, "heading": h}``
XLSX  one row of one sheet (openpyxl)         ``{"sheet": name, "row": R}``
CSV   one row (stdlib csv)                    ``{"sheet": filename, "row": R}``
TXT   one blank-line paragraph                ``{"line": start}``
MD    one blank-line paragraph                ``{"line": start}``
====  ======================================  ==============================

The whole flow is tenant-isolated by construction: :func:`parse_object` reads
the blob through :mod:`object_store` (scoped to the identity's digest dir),
resolves the job through :mod:`parse_job` (same digest dir), and persists the
produced chunks under that tenant's own ``jobs/<job_id>/chunks.json``. Parsing
one tenant's object can never read or write under another tenant's digest.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import object_store, parse_job

logger = logging.getLogger(__name__)
from .chunker import chunk_document
from .parse_job import ParseJob, ParseStatus

#: A parser returns ``(chunks, counts)`` where ``counts`` carries the shape
#: metrics the job records (any of ``pages`` / ``sheets`` / ``rows``).
_Unit = Tuple[str, Dict[str, Any]]
_Chunks = List[Dict[str, Any]]
_Parser = Callable[..., Tuple[_Chunks, Dict[str, Optional[int]]]]


def _ext(source_name: str) -> str:
    return Path(str(source_name or "")).suffix.lower()


def _loc_suffix(location: Dict[str, Any]) -> str:
    """A stable, location-derived tag folded into ``chunk_document``'s
    ``source_name`` so chunks from different units get distinct ids."""
    return "&".join(f"{k}={location[k]}" for k in sorted(location))


def _units_to_chunks(
    units: List[_Unit],
    *,
    source_name: str,
    object_id: str,
    tenant_id: str,
) -> _Chunks:
    """Chunk each structural unit and tag every chunk with ``file`` + ``location``.

    ``chunk_document`` is called exactly as F-1a defines it (its five output
    keys are untouched); this wrapper only ADDS ``file`` and ``location`` to
    each returned dict so the chunk carries where it came from.
    """
    out: _Chunks = []
    for unit_text, location in units:
        if not unit_text or not str(unit_text).strip():
            continue
        tagged_source = f"{source_name}#{_loc_suffix(location)}"
        for chunk in chunk_document(
            text=unit_text,
            source_name=tagged_source,
            layer=2,
            tenant_id=tenant_id,
            object_id=object_id,
        ):
            chunk["file"] = source_name
            chunk["location"] = dict(location)
            out.append(chunk)
    return out


def parse_pdf(data: bytes, *, source_name: str, object_id: str, tenant_id: str):
    """PDF -> one unit per page, text extracted with pypdf (mirrors
    ``core.upload_processor._parse_pdf_local``, but per-page with its number)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    units: List[_Unit] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        units.append((text, {"page": page_number}))
    chunks = _units_to_chunks(
        units, source_name=source_name, object_id=object_id, tenant_id=tenant_id
    )
    return chunks, {"pages": len(reader.pages)}


#: python-docx's own built-in style ids for the heading/title styles (the
#: ``w:styleId`` Word assigns, stable across locales and display names):
#: ``Title`` and ``Heading1``..``Heading9``. A paragraph is a heading when its
#: style is a built-in style (``style.builtin``) whose structural id is one of
#: these -- read from the style's identity, never matched against the lower-
#: cased display name. ``WD_BUILTIN_STYLE`` enumerates the same set; the ids
#: here are that enum's ``style_id`` values.
_HEADING_STYLE_IDS = frozenset(
    {"Title"} | {f"Heading{level}" for level in range(1, 10)}
)


def _is_heading_paragraph(paragraph) -> bool:
    """True when the paragraph's style is a built-in Word heading/title style.

    Decided structurally from python-docx: the style must report
    ``builtin`` True and carry one of the built-in heading/title ``style_id``
    values -- the ``w:styleId`` in the document's style part, not a substring
    of the human-facing style name.
    """
    style = getattr(paragraph, "style", None)
    if style is None:
        return False
    if not bool(getattr(style, "builtin", False)):
        return False
    style_id = getattr(style, "style_id", None)
    return style_id in _HEADING_STYLE_IDS


def parse_docx(data: bytes, *, source_name: str, object_id: str, tenant_id: str):
    """DOCX -> one unit per paragraph, tagged by paragraph index and the
    nearest preceding heading (mirrors ``_parse_docx_local``; no page concept)."""
    import docx

    document = docx.Document(io.BytesIO(data))
    units: List[_Unit] = []
    heading: Optional[str] = None
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text or ""
        is_heading = _is_heading_paragraph(paragraph)
        if is_heading and text.strip():
            heading = text.strip()
        if not text.strip():
            continue
        units.append((text, {"paragraph": index, "heading": heading}))
    chunks = _units_to_chunks(
        units, source_name=source_name, object_id=object_id, tenant_id=tenant_id
    )
    return chunks, {}


def parse_xlsx(data: bytes, *, source_name: str, object_id: str, tenant_id: str):
    """XLSX -> one unit per non-empty row of each sheet (openpyxl, values only)."""
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        units: List[_Unit] = []
        rows = 0
        sheets = 0
        for sheet in workbook.worksheets:
            sheets += 1
            for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = ["" if cell is None else str(cell) for cell in row]
                if not any(cell.strip() for cell in cells):
                    continue
                rows += 1
                text = " | ".join(cells).strip(" |")
                units.append((text, {"sheet": sheet.title, "row": row_number}))
    finally:
        workbook.close()
    chunks = _units_to_chunks(
        units, source_name=source_name, object_id=object_id, tenant_id=tenant_id
    )
    return chunks, {"sheets": sheets, "rows": rows}


def parse_csv(data: bytes, *, source_name: str, object_id: str, tenant_id: str):
    """CSV -> one unit per non-empty row (stdlib csv), tagged sheet=<filename>."""
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    units: List[_Unit] = []
    rows = 0
    for row_number, row in enumerate(reader, start=1):
        cells = [str(cell) for cell in row]
        # A row whose every cell is blank (",,") is skipped like an empty line;
        # its row number still counts, so later rows keep their true number.
        if not any(cell.strip() for cell in cells):
            continue
        line = " | ".join(cells).strip()
        rows += 1
        units.append((line, {"sheet": source_name, "row": row_number}))
    chunks = _units_to_chunks(
        units, source_name=source_name, object_id=object_id, tenant_id=tenant_id
    )
    return chunks, {"rows": rows}


def parse_text(data: bytes, *, source_name: str, object_id: str, tenant_id: str):
    """TXT / MD -> one unit per blank-line paragraph, tagged with its start line."""
    text = data.decode("utf-8", errors="replace")
    units: List[_Unit] = []
    current: List[str] = []
    para_start: Optional[int] = None
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if raw.strip() == "":
            if current:
                units.append(("\n".join(current), {"line": para_start}))
                current = []
                para_start = None
        else:
            if para_start is None:
                para_start = line_number
            current.append(raw)
    if current:
        units.append(("\n".join(current), {"line": para_start}))
    chunks = _units_to_chunks(
        units, source_name=source_name, object_id=object_id, tenant_id=tenant_id
    )
    return chunks, {}


#: Extension -> parser. All six F-1 accepted types resolve to a real parser, so
#: ``unsupported`` is never reached for them; it stays a true answer for a type
#: that has no parser rather than a 501/stub.
_PARSERS: Dict[str, _Parser] = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".xlsx": parse_xlsx,
    ".csv": parse_csv,
    ".txt": parse_text,
    ".md": parse_text,
}


def supported_extensions() -> frozenset:
    return frozenset(_PARSERS)


def _resolve_job(identity: str, object_id: str, job_id: Optional[str]) -> ParseJob:
    """Find the job for ``object_id`` under ``identity`` — scoped to its digest.

    A tenant with no such job is refused by name (so parsing another tenant's
    object, whose job lives under a different digest, cannot resolve here).
    """
    if job_id:
        job = parse_job.get_job(identity, job_id)
        if job is None or job.object_id != object_id:
            raise ValueError(
                f"no parse job {job_id!r} for object {object_id!r} under this tenant"
            )
        return job
    candidates = [j for j in parse_job.list_jobs(identity) if j.object_id == object_id]
    if not candidates:
        raise ValueError(f"no parse job for object {object_id!r} under this tenant")
    # Prefer a job still awaiting the parser; otherwise the most recent.
    pending = [
        j
        for j in candidates
        if j.status in (ParseStatus.QUEUED, ParseStatus.NOT_STARTED)
    ]
    pool = pending or candidates
    return sorted(pool, key=lambda j: j.created_at)[-1]


def parse_object(
    identity: str, object_id: str, *, job_id: Optional[str] = None
) -> ParseJob:
    """Parse one stored object and move its job ``queued`` -> ``parsed``.

    Reads the tenant's own bytes, runs the type-appropriate parser, persists the
    produced chunks under ``jobs/<job_id>/chunks.json`` (tenant-scoped), and sets
    the job ``parsed`` with its shape counts. A parse error sets ``failed`` with
    ``last_error`` (it never raises out, so a route calling this never 500s); a
    type with no parser sets ``unsupported``. Returns the final :class:`ParseJob`.
    """
    identity_value = str(identity or "").strip()
    if not identity_value:
        raise object_store.ObjectStoreError(
            "tenant identity must be resolved by the caller, never defaulted"
        )

    # Resolving the job is the tenant gate: a job for this object exists only
    # under the identity that uploaded it, so a cross-tenant call fails here.
    job = _resolve_job(identity_value, object_id, job_id)

    parse_job.set_status(identity_value, job.job_id, ParseStatus.PARSING)
    try:
        data = object_store.read_object(identity_value, object_id)
        parser = _PARSERS.get(_ext(job.source_name))
        if parser is None:
            return parse_job.set_status(
                identity_value,
                job.job_id,
                ParseStatus.UNSUPPORTED,
                last_error=f"no parser for type '{_ext(job.source_name)}'",
            )
        chunks, counts = parser(
            data,
            source_name=job.source_name,
            object_id=object_id,
            tenant_id=identity_value,
        )
        parse_job.write_chunks(identity_value, job.job_id, chunks)
        # F-1c: the parsed chunks reach retrieval here — ingested (layer 2)
        # into THIS tenant's own RetrievalEngine collection so retrieve() can
        # return them with their file + location. Best-effort: a retrieval
        # backend that is absent or raises must never flip a genuinely-parsed
        # job to FAILED, so the index call is isolated from the parse verdict.
        from . import retrieval_index

        try:
            outcome = retrieval_index.ingest_parsed_chunks(identity_value, chunks)
            index_state = outcome.index_state
        except Exception as exc:  # noqa: BLE001 — indexing never fails the parse
            logger.exception(
                "ingest-on-parse failed for job %s; chunks persisted, parse "
                "stands PARSED",
                job.job_id,
            )
            # Still SURFACED on the job, never a silent swallow.
            index_state = (
                f"{retrieval_index.INDEX_STATE_NOT_INDEXED}: "
                f"{type(exc).__name__}: {exc}"
            )
        return parse_job.set_status(
            identity_value,
            job.job_id,
            ParseStatus.PARSED,
            pages=counts.get("pages"),
            sheets=counts.get("sheets"),
            rows=counts.get("rows"),
            index_state=index_state,
        )
    except Exception as exc:  # never crash the caller/route
        return parse_job.set_status(
            identity_value,
            job.job_id,
            ParseStatus.FAILED,
            last_error=f"{type(exc).__name__}: {exc}",
        )
