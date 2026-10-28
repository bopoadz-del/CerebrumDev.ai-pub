"""F-1c: kernel-first answering — the NUMBER is the kernel's, or it refuses.

The 0725Z rule plus BAR 3 (ORDERS_28OCT_1530Z), pinned here:

* KERNEL COMPUTE: a kernel-covered query is answered by the KERNEL path — its
  number is the certified definition's ``expression`` COMPUTED over inputs
  grounded from the tenant's retrieved chunks. The model's prose number is
  NEVER passed through (prose "0.99" vs a kernel that computes 0.40 -> the
  answer shows 0.40, never 0.99). Restoring the prose pass-through reddens the
  mutation test.
* UNKNOWN REFUSAL: a kernel-covered query whose inputs are NOT in the retrieved
  chunks is refused, naming the missing inputs — never a fabricated number.
* OUT-OF-KERNEL: a query no kernel covers is answered as a LABELLED improvisation
  (the visible "no kernel covers ..." banner + cited sources), through the
  grounding gate, and appends exactly ONE tenant-scoped new-domain-candidate row.
* NO ENGINE: with no retrieval backend, answer_query refuses with the surfaced
  "retrieval backend not configured" message.
"""

from __future__ import annotations

from typing import List

import pytest

from app.cerebrum_product_kernel.answer import (
    KIND_IMPROVISED,
    KIND_KERNEL,
    answer_query,
    kernel_coverage,
    read_new_domain_candidates,
)
from app.cerebrum_product_kernel.formulas import answer_definition
from app.cerebrum_product_kernel.ingestion import object_store, parse_job, retrieval_index
from app.cerebrum_product_kernel.ingestion.parsers import parse_object
from app.cerebrum_product_kernel.retrieval_engine import InMemoryVectorStore, RetrievalEngine


# A miniature SEMANTIC embedder: finance words (incl. the formula inputs) embed
# to one axis, leave-policy words to another, so a finance query retrieves the
# finance doc (and its revenue/cogs figures) with zero lexical tricks.
_VECTORS = {
    # finance / margin axis — includes the gross_margin_ratio inputs
    "gross": [1.0, 0.0, 0.0],
    "margin": [1.0, 0.0, 0.0],
    "ratio": [1.0, 0.0, 0.0],
    "revenue": [1.0, 0.0, 0.0],
    "cogs": [1.0, 0.0, 0.0],
    "profit": [1.0, 0.0, 0.0],
    # leave-policy axis
    "sabbatical": [0.0, 1.0, 0.0],
    "leave": [0.0, 1.0, 0.0],
    "policy": [0.0, 1.0, 0.0],
    "eligible": [0.0, 1.0, 0.0],
}


class _Embedder:
    semantic = True

    def __call__(self, text: str) -> List[float]:
        vec = [0.0, 0.0, 0.0]
        for word in text.lower().replace(".", " ").replace(",", " ").split():
            for key, v in _VECTORS.items():
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
def engine():
    eng = RetrievalEngine(embedder=_Embedder(), store=InMemoryVectorStore())
    retrieval_index.set_engine_provider(lambda identity: eng)
    yield eng
    retrieval_index.set_engine_provider(None)


def _attach(identity, source_name, data):
    stored = object_store.save_object(identity, source_name, data)
    job = parse_job.create_job(
        identity, object_id=stored.object_id, source_name=stored.source_name
    )
    final = parse_object(identity, stored.object_id, job_id=job.job_id)
    assert final.status is parse_job.ParseStatus.PARSED


# ── kernel compute path ────────────────────────────────────────────────────────


def test_kernel_covered_query_number_is_computed_not_prose(engine):
    """BAR 3 + its mutation: the kernel-covered answer's number is COMPUTED from
    grounded inputs (0.40), NEVER the prose number (0.99). Restoring the prose
    pass-through turns this red."""
    _attach(
        "tenant-a", "finance.md",
        b"Gross margin inputs.\n\nRevenue is 100. COGS is 60.\n",
    )
    ans = answer_query(
        "tenant-a",
        "what is the gross margin ratio",
        # The model's prose carries a WRONG number — it must never surface.
        "The gross margin ratio is 0.99.",
        engine=engine,
    )
    assert ans.kind == KIND_KERNEL
    assert ans.improvised is False
    assert ans.unknown is False
    # The kernel computed (100 - 60) / 100 = 0.40 from the grounded inputs.
    assert ans.kernel_value == pytest.approx(0.40)
    assert "0.4" in ans.text
    # The prose number is GONE — this is the line the prose-pass-through mutation
    # turns red.
    assert "0.99" not in ans.text
    # The number moves with the kernel definition (it is the kernel's own).
    assert ans.kernel["expression"] == "(revenue - cogs) / revenue"
    assert ans.kernel["expression"] == answer_definition("gross_margin_ratio")["expression"]
    # A kernel answer logs no new-domain candidate.
    assert read_new_domain_candidates("tenant-a") == []


def test_kernel_covered_query_refuses_when_inputs_missing(engine):
    """A kernel-covered query whose inputs are not in the retrieved chunks is
    refused, NAMING the missing inputs — never a fabricated number."""
    # A finance doc that is retrievable but carries no revenue/cogs figures.
    _attach("tenant-a", "note.md", b"Gross margin ratio is an important metric.\n")
    ans = answer_query(
        "tenant-a",
        "what is the gross margin ratio",
        "The gross margin ratio is 0.99.",
        engine=engine,
    )
    assert ans.kind == KIND_KERNEL
    assert ans.unknown is True
    assert ans.kernel_value is None
    assert "revenue" in ans.missing_inputs and "cogs" in ans.missing_inputs
    assert "0.99" not in ans.text
    assert "revenue" in ans.text and "cogs" in ans.text


def test_kernel_coverage_detects_known_and_rejects_unknown():
    assert kernel_coverage("compute the gross margin ratio please") is not None
    assert kernel_coverage("gross_margin_ratio") is not None
    assert kernel_coverage("what is our sabbatical leave policy") is None


def test_no_engine_answer_refuses_with_surfaced_message():
    """With no retrieval backend installed, answer_query refuses with the
    surfaced "retrieval backend not configured" message."""
    retrieval_index.set_engine_provider(None)
    with pytest.raises(RuntimeError) as exc:
        answer_query("tenant-a", "what is the gross margin ratio", "0.99")
    assert retrieval_index.RETRIEVAL_BACKEND_NOT_CONFIGURED in str(exc.value)


# ── out-of-kernel improvisation ───────────────────────────────────────────────


def test_uncovered_query_is_labelled_improvisation_with_one_candidate_row(engine):
    """A query no kernel covers is answered as a labelled, grounded improvisation
    and logs exactly one new-domain-candidate row for the tenant."""
    _attach(
        "tenant-a", "handbook.md",
        b"Sabbatical leave policy.\n\nStaff are eligible for sabbatical leave "
        b"after five years.\n",
    )
    ans = answer_query(
        "tenant-a",
        "what is our sabbatical leave policy",
        "Staff are eligible after five years.",
        engine=engine,
    )
    assert ans.kind == KIND_IMPROVISED
    assert ans.improvised is True
    # The visible banner names the uncovered subject.
    assert ans.label.startswith("no kernel covers")
    assert "sabbatical leave policy" in ans.label
    assert "improvised" in ans.label
    # Grounded: the improvisation cites the attached handbook.
    assert ans.citations
    assert any(c.file == "handbook.md" for c in ans.citations)

    # Exactly one candidate row, tenant-scoped, carrying the query + reason.
    rows = read_new_domain_candidates("tenant-a")
    assert len(rows) == 1
    assert rows[0]["query"] == "what is our sabbatical leave policy"
    assert rows[0]["reason"] == "no_kernel_coverage"
    # Another tenant has no view of that candidate.
    assert read_new_domain_candidates("tenant-b") == []
