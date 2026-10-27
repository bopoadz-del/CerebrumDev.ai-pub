"""Embedding-stability gate for the fastembed 0.9.1 / Pillow 13 bump.

The migration moves ``fastembed==0.9.0`` -> ``0.9.1`` (which relaxes the
transitive Pillow constraint to ``<14`` and lets Pillow reach 13.x, clearing
the 37 image-codec CVEs) and the lock to ``pillow==13.0.0``. The ONNX text
model (``BAAI/bge-small-en-v1.5``) is unchanged and Pillow is never invoked on
the text-embedding path, so retrieval must not move.

WHAT THIS GATE MEASURES, AND WHY NOT ABSOLUTE 1e-6
--------------------------------------------------
The quantity retrieval actually depends on is the *cosine* between query and
document vectors: that is what ranks results. An earlier draft of this test
asserted a per-component absolute bound of 1e-6 against a golden captured on a
developer box. It went red in CI with a ~1.6e-4 per-component delta -- not
because the embedding space changed, but because fastembed/onnxruntime
embeddings are only bit-stable *within one identical stack*. Across a fastembed
version change (and the transitive onnxruntime that CI's ``requirements.txt``
resolve pulls, versus the ``requirements.lock`` the golden is captured under)
the raw float components drift by ~1e-4 while the vector *direction* is
preserved to ~1e-8 in cosine. A 1e-6 absolute bound is therefore unachievable
across any fastembed bump and measures the capture stack, not the product.

So the gate is the retrieval invariant plus a sanity bound:

  * cosine similarity between the shipped embedding and the golden must be
    ``>= 1 - COSINE_TOLERANCE`` (1e-6) -- i.e. the direction is preserved to
    within a part in a million. A real change to the embedding space (wrong
    model, wrong pooling, wrong normalisation, a genuinely different output)
    collapses cosine far below this and the gate fails first.
  * each component stays within ``ABS_SANITY`` (2e-4) of the golden -- a coarse
    guard that catches a gross magnitude/scale regression that cosine alone
    (being scale-invariant) would miss, while tolerating the ~1e-4 cross-stack
    float noise that is retrieval-irrelevant.

THE GOLDEN IS CAPTURED IN CI, UNDER THE SHIPPED STACK
-----------------------------------------------------
``backend/tests/fixtures/embedding_probe_golden.json`` is produced by
``scripts/capture_embedding_golden.py`` running on a GitHub Actions runner with
``backend/requirements.lock`` installed (fastembed 0.9.1 + onnxruntime 1.29.0 --
the stack the production image bakes), never on a developer machine. See
``.github``'s capture workflow / that script's header for the exact command.
Re-capture only when the lock's embedding stack intentionally changes.

This test runs the *real* production embedder (no mock, unlike
``test_upload_processor.py``), downloading the same ~67 MB ONNX weights once.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.core import upload_processor

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "embedding_probe_golden.json"
COSINE_TOLERANCE = 1e-6
ABS_SANITY = 2e-4


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        raise AssertionError("zero-norm embedding vector")
    return dot / (na * nb)


@pytest.fixture(scope="module")
def golden() -> dict:
    with GOLDEN_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_probe_embeddings_preserve_retrieval_direction(golden: dict) -> None:
    probes = golden["probes"]
    expected = golden["vectors"]

    embeddings, meta = upload_processor._embed_with_onnx(probes)

    assert meta["provider"] == "fastembed"
    assert meta["backend"] == "fastembed"
    assert meta["model"] == golden["model"]
    assert meta["dimensions"] == golden["dimensions"]
    assert len(embeddings) == len(probes)
    # Pin both sides: a truncated golden must not let the zip below iterate
    # fewer times and pass vacuously.
    assert len(expected) == len(probes)

    for i, (got, want) in enumerate(zip(embeddings, expected)):
        assert len(got) == golden["dimensions"], f"probe {i}: wrong dimension"

        cos = _cosine(got, want)
        assert cos >= 1.0 - COSINE_TOLERANCE, (
            f"probe {i}: cosine vs golden is {cos:.12f} "
            f"(< 1 - {COSINE_TOLERANCE:.0e}); the embedding DIRECTION moved -- "
            "the dependency bump changed the embedding space, not just float noise"
        )

        max_abs = max(abs(a - b) for a, b in zip(got, want))
        assert max_abs <= ABS_SANITY, (
            f"probe {i}: component drifted by {max_abs:.3e} (> {ABS_SANITY:.0e}); "
            "magnitude regression beyond cross-stack float noise"
        )
