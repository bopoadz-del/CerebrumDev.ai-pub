"""F-1d GOLDEN CITATION FIXTURES: a two-input cited-answer flow, end to end.

With a REAL embedder injected (a deterministic, no-network test provider) and
the kernel's own parse-on-upload + retrieval path, this exercises the GENERATED
PRODUCT answer flow over HTTP:

* uploads a REAL PDF and a REAL XLSX for one tenant through the product's
  ``/v1/product/ingestion/upload`` route (the real validate -> store -> parse
  -> index pipeline), then
* queries each through the product answer surfaces and asserts the answer CITES
  the right file + location: the PDF answer cites the PDF's file + PAGE, the
  XLSX answer cites the XLSX's file + SHEET/ROW, and NEITHER cites the other's.

Two answer surfaces are pinned, both the real path (NOT the keyword matcher):

1. the kernel ingestion ``POST /answer`` verb rendered into every product, and
2. the RE-POINTED ``GET /v1/rag/query`` keep-path surface, proving it now routes
   through the shared ``answer_question`` -> ``answer_query`` helper.

MUTATION PINS (both must redden this file):
* drop the citation (or its location) on the answer path, and
* route ``/v1/rag/query`` back to the keyword-overlap placeholder instead of the
  real answer path.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import types
from typing import List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cerebrum_product_kernel.ingestion import retrieval_index
from app.cerebrum_product_kernel.ingestion.router import product_router
from app.cerebrum_product_kernel.retrieval_engine import (
    InMemoryVectorStore,
    RetrievalEngine,
)

PDF_MIME = "application/pdf"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# A deterministic SEMANTIC embedder: pricing words and inventory words embed to
# orthogonal axes, so a pricing query retrieves only the PDF chunk and an
# inventory query only the XLSX chunk, with zero lexical overlap. No network.
_TOPIC_VECTORS = {
    # topic A — pricing (the PDF)
    "price": [1.0, 0.0, 0.0],
    "cost": [1.0, 0.0, 0.0],
    "charge": [1.0, 0.0, 0.0],
    "forty": [1.0, 0.0, 0.0],
    # topic B — inventory (the XLSX)
    "stock": [0.0, 1.0, 0.0],
    "inventory": [0.0, 1.0, 0.0],
    "widget": [0.0, 1.0, 0.0],
    "quantity": [0.0, 1.0, 0.0],
}


class TopicEmbedder:
    semantic = True

    def __call__(self, text: str) -> List[float]:
        vec = [0.0, 0.0, 0.0]
        for word in text.lower().replace(",", " ").replace("|", " ").split():
            for key, v in _TOPIC_VECTORS.items():
                if key in word:
                    vec = [vec[i] + v[i] for i in range(3)]
        norm = sum(x * x for x in vec) ** 0.5
        if norm:
            vec = [x / norm for x in vec]
        return vec


def _make_pdf(pages_text):
    """A minimal multi-page PDF whose pages carry extractable text (pypdf)."""
    n_pages = len(pages_text)
    parts = {}
    next_id = 3
    kids_ids = []
    page_to_content = {}
    content_text = {}
    for text in pages_text:
        page_id = next_id
        next_id += 1
        content_id = next_id
        next_id += 1
        kids_ids.append(page_id)
        page_to_content[page_id] = content_id
        content_text[content_id] = text
    parts[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{k} 0 R" for k in kids_ids).encode()
    parts[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(n_pages).encode() + b" >>"
    )
    for page_id, content_id in page_to_content.items():
        parts[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica >> >> >> /Contents "
            + str(content_id).encode() + b" 0 R >>"
        )
    for content_id, text in content_text.items():
        stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode() + b") Tj ET"
        parts[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        )
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for i in range(1, next_id):
        offsets[i] = out.tell()
        out.write(str(i).encode() + b" 0 obj\n" + parts[i] + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(b"xref\n0 " + str(next_id).encode() + b"\n")
    out.write(b"0000000000 65535 f \n")
    for i in range(1, next_id):
        out.write(f"{offsets[i]:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(next_id).encode() + b" /Root 1 0 R >>\n")
    out.write(b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n")
    return out.getvalue()


def _make_xlsx(sheet_title, rows):
    """A real .xlsx (openpyxl) with one worksheet and the given rows."""
    import openpyxl

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_title
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


_PDF_BYTES = _make_pdf(["intro only", "The service price is forty per charge."])
_XLSX_BYTES = _make_xlsx(
    "Inventory", [["item", "quantity"], ["widget stock", 5], ["gadget stock", 9]]
)


def _stub_tenancy(monkeypatch, token_to_tenant):
    """Stub the product's single tenancy path (app.tenancy is a product file)."""
    mod = types.ModuleType("app.tenancy")

    class TenantRefused(Exception):
        pass

    class _Tenant:
        def __init__(self, tid):
            self.tenant_id = tid
            self.roles = []

    def resolve_tenant(headers):
        auth = (headers or {}).get("authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        tid = token_to_tenant.get(token)
        if tid is None:
            raise TenantRefused(token)
        return _Tenant(tid)

    mod.TenantRefused = TenantRefused
    mod.resolve_tenant = resolve_tenant
    monkeypatch.setitem(sys.modules, "app.tenancy", mod)


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """Install the REAL engine seam with a deterministic (no-network) embedder."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    eng = RetrievalEngine(embedder=TopicEmbedder(), store=InMemoryVectorStore())
    retrieval_index.set_engine_provider(lambda identity: eng)
    yield eng
    retrieval_index.set_engine_provider(None)


def _upload(client, headers, name, data, mime):
    resp = client.post(
        "/v1/product/ingestion/upload",
        headers=headers,
        files={"files": (name, data, mime)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "parsed", resp.text
    return resp.json()


def _answer(client, headers, query, answer_text):
    resp = client.post(
        "/v1/product/ingestion/answer",
        headers=headers,
        json={"query": query, "answer_text": answer_text},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_golden_pdf_and_xlsx_answers_cite_their_own_file_and_location(engine, monkeypatch):
    """The generated-product /answer flow: a PDF answer cites the PDF file+page,
    an XLSX answer cites the XLSX file+sheet/row, and never the other's."""
    _stub_tenancy(monkeypatch, {"good-token": "tenant-a"})
    app = FastAPI()
    app.include_router(product_router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer good-token"}

    _upload(client, headers, "pricing.pdf", _PDF_BYTES, PDF_MIME)
    _upload(client, headers, "inventory.xlsx", _XLSX_BYTES, XLSX_MIME)

    pdf_answer = _answer(client, headers, "how much does it cost to charge", "It costs forty.")
    xlsx_answer = _answer(client, headers, "how much widget inventory in stock", "There are five.")

    # The PDF answer is cited to the PDF, at its real page; never the XLSX.
    pdf_cites = pdf_answer["citations"]
    assert pdf_cites, pdf_answer
    pdf_files = {c["file"] for c in pdf_cites}
    assert pdf_files == {"pricing.pdf"}, pdf_files
    assert "inventory.xlsx" not in pdf_files
    top_pdf = pdf_cites[0]
    assert top_pdf["file"] == "pricing.pdf"
    assert top_pdf["location"].get("page") == 2, top_pdf
    assert "sheet" not in top_pdf["location"] and "row" not in top_pdf["location"]

    # The XLSX answer is cited to the XLSX, at its real sheet/row; never the PDF.
    xlsx_cites = xlsx_answer["citations"]
    assert xlsx_cites, xlsx_answer
    xlsx_files = {c["file"] for c in xlsx_cites}
    assert xlsx_files == {"inventory.xlsx"}, xlsx_files
    assert "pricing.pdf" not in xlsx_files
    widget_row = [
        c for c in xlsx_cites
        if c["location"].get("sheet") == "Inventory" and c["location"].get("row") == 2
    ]
    assert widget_row, xlsx_cites
    assert all("page" not in c["location"] for c in xlsx_cites)


def _load_repointed_rag_module(tmp_path):
    """Emit the factory-grounded keep-path and import the generated module."""
    from app.factory.build.rag_surface import RAG_ROUTES_REL, emit_factory_grounded_rag_surface
    from tests.factory.test_writer_phases import _compiled_steward_dual_rag

    planted = emit_factory_grounded_rag_surface(tmp_path, _compiled_steward_dual_rag())
    assert planted == ["rag_ingest", "rag_query"]
    path = tmp_path / RAG_ROUTES_REL
    spec = importlib.util.spec_from_file_location("f1d_repointed_rag_routes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_repointed_rag_query_routes_to_the_real_cited_answer(engine, tmp_path, monkeypatch):
    """The RE-POINTED /v1/rag/query reaches the real answer path and carries the
    PDF's file+page citation. Reverting it to the keyword matcher (no citations)
    reddens this."""
    _stub_tenancy(monkeypatch, {"good-token": "tenant-a"})
    rag_module = _load_repointed_rag_module(tmp_path)

    app = FastAPI()
    app.include_router(product_router)  # the real /upload to index the corpus
    app.include_router(rag_module.router)  # the re-pointed /v1/rag/query
    client = TestClient(app)
    headers = {"Authorization": "Bearer good-token"}

    _upload(client, headers, "pricing.pdf", _PDF_BYTES, PDF_MIME)

    resp = client.get(
        "/v1/rag/query",
        params={"q": "how much does it cost to charge", "answer_text": "It costs forty."},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The real answer shape, NOT the keyword matcher's {hits, hit_count}.
    assert "hit_count" not in body and "hits" not in body
    assert body["citations"], body
    top = body["citations"][0]
    assert top["file"] == "pricing.pdf"
    assert top["location"].get("page") == 2, top
