"""Byte-identical embedding probe gate for the fastembed 0.9.1 / Pillow 13 bump.

The migration moves ``fastembed==0.9.0`` -> ``0.9.1`` (which relaxes the
transitive Pillow constraint to ``<14`` and lets Pillow reach 13.x, clearing
the 37 image-codec CVEs) and pins ``pillow==13.0.0`` in the lock. The ONNX
text model (``BAAI/bge-small-en-v1.5``) and the onnxruntime pin are unchanged,
and Pillow is never invoked on the text-embedding path, so the embeddings this
backend emits must not move.

This test is the proof. It runs the *real* production embedder
(``upload_processor._embed_with_onnx``) over a frozen probe set and asserts the
output matches a golden fixture captured before the bump, to within 1e-6. If a
future dependency change perturbs the embedding space, this goes red first.

The golden was captured from the real model; this test downloads the same
~67 MB ONNX weights once (no mock) so it measures the shipped behaviour, not a
stand-in. It is deliberately not mocked, unlike the unit tests in
``test_upload_processor.py``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import upload_processor

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "embedding_probe_golden.json"
TOLERANCE = 1e-6


@pytest.fixture(scope="module")
def golden() -> dict:
    with GOLDEN_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_probe_embeddings_match_golden_within_tolerance(golden: dict) -> None:
    probes = golden["probes"]
    expected = golden["vectors"]

    embeddings, meta = upload_processor._embed_with_onnx(probes)

    assert meta["provider"] == "fastembed"
    assert meta["backend"] == "fastembed"
    assert meta["model"] == golden["model"]
    assert meta["dimensions"] == golden["dimensions"]
    assert len(embeddings) == len(probes)

    for i, (got, want) in enumerate(zip(embeddings, expected)):
        assert len(got) == golden["dimensions"], f"probe {i}: wrong dimension"
        max_abs = max(abs(a - b) for a, b in zip(got, want))
        assert max_abs <= TOLERANCE, (
            f"probe {i}: embedding drifted by {max_abs:.3e} (> {TOLERANCE:.0e}); "
            "the dependency bump changed the embedding space"
        )
