"""F-1c: attached chunks reach the answer, with the right citation.

The claims pinned here, against the real kernel helpers (no mock of the engine
or the parser; two genuine attached documents):

* TWO-INPUT: a PDF about topic A and a CSV about topic B are ingested for ONE
  tenant (via the real parse-on-upload path). Two different queries return two
  different answers, each CITING the right file + page/row. A's answer cites A's
  file + location, never B's. Dropping the chunk's location (the mutation) makes
  the citation assertion fail.
* TENANT ISOLATION: tenant A's attached chunks are not retrievable under tenant
  B — a B query over A's content grounds to nothing.
"""

from __future__ import annotations

import io
from typing import List

import pytest

from app.cerebrum_product_kernel.answer import answer_query
from app.cerebrum_product_kernel.ingestion import object_store, parse_job, retrieval_index
from app.cerebrum_product_kernel.ingestion.parsers import parse_object
from app.cerebrum_product_kernel.retrieval_engine import (
    InMemoryVectorStore,
    RetrievalEngine,
    RetrievalEngineError,
)


# A deterministic SEMANTIC embedder: topic-A words and topic-B words embed to
# orthogonal axes, so an A-query retrieves only A's chunk even with zero lexical
# overlap. This is semantic-in-miniature; production injects the real provider.
_TOPIC_VECTORS = {
    # topic A — pricing
    "price": [1.0, 0.0, 0.0],
    "cost": [1.0, 0.0, 0.0],
    "charge": [1.0, 0.0, 0.0],
    "forty": [1.0, 0.0, 0.0],
    # topic B — inventory
    "stock": [0.0, 1.0, 0.0],
    "inventory": [0.0, 1.0, 0.0],
    "widget": [0.0, 1.0, 0.0],
    "quantity": [0.0, 1.0, 0.0],
}


class TopicEmbedder:
    semantic = True

    def __call__(self, text: str) -> List[float]:
        vec = [0.0, 0.0, 0.0]
        for word in text.lower().replace(",", " ").split():
            for key, v in _TOPIC_VECTORS.items():
                if key in word:
                    vec = [vec[i] + v[i] for i in range(3)]
        norm = sum(x * x for x in vec) ** 0.5
        if norm:
            vec = [x / norm for x in vec]
        return vec


@pytest.fixture(autouse=True)
def _tenant_root(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    yield


@pytest.fixture
def engine(monkeypatch):
    """A fixture engine wired through the real ingest-on-parse provider seam.

    One shared engine + in-memory store; isolation comes from the per-tenant
    COLLECTION name (the tenant digest), exactly as production isolates on a
    per-tenant Chroma collection.
    """
    eng = RetrievalEngine(embedder=TopicEmbedder(), store=InMemoryVectorStore())
    retrieval_index.set_engine_provider(lambda identity: eng)
    yield eng
    retrieval_index.set_engine_provider(None)


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


def _upload_and_parse(identity, source_name, data):
    stored = object_store.save_object(identity, source_name, data)
    job = parse_job.create_job(
        identity, object_id=stored.object_id, source_name=stored.source_name
    )
    final = parse_object(identity, stored.object_id, job_id=job.job_id)
    assert final.status is parse_job.ParseStatus.PARSED
    return stored, final


def test_two_attached_docs_cite_their_own_file_and_location(engine):
    """A's answer cites A's file+page; B's answer cites B's file+row; neither
    citation names the other's file."""
    # topic A — pricing — PDF, page 2 carries the priced fact.
    _upload_and_parse(
        "tenant-a", "pricing.pdf",
        _make_pdf(["intro only", "The service price is forty per charge."]),
    )
    # topic B — inventory — CSV, row 2 carries the stock fact.
    _upload_and_parse(
        "tenant-a", "stock.csv", b"item,quantity\nwidget stock,5\ngadget stock,9\n",
    )

    a = answer_query(
        "tenant-a", "how much does it cost to charge", "It costs forty.",
        engine=engine,
    )
    b = answer_query(
        "tenant-a", "how much widget inventory in stock", "There are five.",
        engine=engine,
    )

    # Two different answers, each grounded in its own document.
    assert a.improvised and b.improvised
    assert a.citations and b.citations
    a_files = {c.file for c in a.citations}
    b_files = {c.file for c in b.citations}
    assert a_files == {"pricing.pdf"}, a_files
    assert b_files == {"stock.csv"}, b_files
    # A's citation names A's file + its PDF page; never B's file.
    assert "stock.csv" not in a_files
    top_a = a.citations[0]
    assert top_a.file == "pricing.pdf"
    assert top_a.location.get("page") == 2
    assert "row" not in top_a.location
    # B's citations name B's file + its CSV sheet/row; never A's file. (Every
    # CSV row embeds to the same inventory axis under the miniature embedder,
    # so rank within B is not meaningful — what matters is that the widget row
    # IS cited, with its real row 2 and sheet, and that no page sneaks in.)
    assert "pricing.pdf" not in b_files
    assert all(c.file == "stock.csv" for c in b.citations)
    widget_row = [
        c for c in b.citations
        if c.location.get("row") == 2 and c.location.get("sheet") == "stock.csv"
    ]
    assert widget_row, [c.to_dict() for c in b.citations]
    assert all("page" not in c.location for c in b.citations)


def test_citation_location_is_the_chunk_location_mutation(engine):
    """MUTATION: dropping the chunk's location (so the citation carries no
    page) makes this assertion fail. It pins that the citation location is the
    retrieved chunk's real location, not an empty default."""
    _upload_and_parse(
        "tenant-a", "pricing.pdf",
        _make_pdf(["intro only", "The service price is forty per charge."]),
    )
    a = answer_query(
        "tenant-a", "how much does it cost to charge", "It costs forty.",
        engine=engine,
    )
    assert a.citations
    # This is the line a location-dropping mutation turns RED.
    assert a.citations[0].location.get("page") == 2


def test_tenant_a_chunks_not_retrievable_under_tenant_b(engine):
    """TENANT ISOLATION: A's attached pricing doc is unreachable from B — a B
    query over A's content grounds to nothing and is refused."""
    _upload_and_parse(
        "tenant-a", "pricing.pdf",
        _make_pdf(["intro only", "The service price is forty per charge."]),
    )
    # Under A the content is answerable.
    a = answer_query(
        "tenant-a", "how much does it cost to charge", "It costs forty.",
        engine=engine,
    )
    assert a.citations and a.citations[0].file == "pricing.pdf"

    # Under B the same query retrieves nothing — the grounding gate refuses it.
    with pytest.raises(RetrievalEngineError):
        answer_query(
            "tenant-b", "how much does it cost to charge", "It costs forty.",
            engine=engine,
        )
