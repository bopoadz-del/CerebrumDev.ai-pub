"""F-1c: kernel-first answering with labelled out-of-kernel improvisation.

The 0725Z rule, and the S-7 pattern, pinned here:

* KERNEL-DETECTION: a kernel-covered query is answered by the KERNEL path — its
  number is the certified definition's expression, so mutating the definition
  moves the answer. A covered query carries NO improvisation label and logs no
  new-domain candidate.
* OUT-OF-KERNEL: a query no kernel covers is answered as a LABELLED improvisation
  (the visible "no kernel covers ..." banner + cited sources), through the
  grounding gate, and appends exactly ONE tenant-scoped new-domain-candidate row.
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


_VECTORS = {
    "sabbatical": [1.0, 0.0, 0.0],
    "leave": [1.0, 0.0, 0.0],
    "policy": [1.0, 0.0, 0.0],
    "eligible": [1.0, 0.0, 0.0],
}


class _Embedder:
    semantic = True

    def __call__(self, text: str) -> List[float]:
        vec = [0.0, 0.0, 0.0]
        for word in text.lower().split():
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


# ── kernel path ──────────────────────────────────────────────────────────────


def test_kernel_covered_query_is_answered_by_the_kernel(engine):
    """A query naming a kernel definition is answered by the kernel: the
    number is the definition's expression (mutate the kernel -> it moves), and
    there is no improvisation label and no candidate logged."""
    ans = answer_query(
        "tenant-a",
        "what is the gross margin ratio",
        "The gross margin ratio is revenue minus cogs over revenue.",
        engine=engine,
    )
    assert ans.kind == KIND_KERNEL
    assert ans.improvised is False
    assert ans.label == ""
    # The number/expression is the kernel's own — the answer MOVES if the
    # definition is mutated (the verifier edits universal_definitions.json and
    # this equality breaks).
    assert ans.kernel is not None
    assert ans.kernel["id"] == "gross_margin_ratio"
    assert ans.kernel["expression"] == "(revenue - cogs) / revenue"
    assert ans.kernel["expression"] == answer_definition("gross_margin_ratio")["expression"]
    # A kernel answer logs no new-domain candidate.
    assert read_new_domain_candidates("tenant-a") == []


def test_kernel_coverage_detects_known_and_rejects_unknown():
    assert kernel_coverage("compute the gross margin ratio please") is not None
    assert kernel_coverage("gross_margin_ratio") is not None
    assert kernel_coverage("what is our sabbatical leave policy") is None


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
