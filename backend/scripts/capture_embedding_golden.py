"""Capture the embedding-probe golden fixture under the SHIPPED stack.

This is the single source of truth for the frozen probe set and the golden
vectors consumed by ``backend/tests/test_embedding_probe_determinism.py``. It
runs the real production embedder (``upload_processor._embed_with_onnx``) over
the probes and writes ``backend/tests/fixtures/embedding_probe_golden.json``.

MUST be run in CI on a GitHub Actions runner with ``backend/requirements.lock``
installed (fastembed 0.9.1 + onnxruntime 1.29.0 -- the stack the production
image bakes), never on a developer box: fastembed/onnxruntime embeddings are
only bit-stable within one identical stack, so a locally captured golden does
not match CI's runtime. The capture workflow pins exactly that lock.

Usage (from backend/):
    python scripts/capture_embedding_golden.py [output_path]

The gate the fixture feeds is the retrieval invariant (cosine >= 1 - 1e-6) plus
an absolute sanity bound, not raw float equality -- see the test's header for
why. Re-run only when the lock's embedding stack intentionally changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.core import upload_processor

# Frozen probe set -- the one source of these strings. The test reads them back
# out of the emitted golden, so they are never duplicated as literals there.
PROBES = [
    "The quick brown fox jumps over the lazy dog.",
    "Cerebrum builds platforms from certified blocks.",
    "Retrieval augmented generation needs stable embeddings.",
]

DEFAULT_OUT = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "embedding_probe_golden.json"
)


def main(out_path: Path) -> None:
    vectors, meta = upload_processor._embed_with_onnx(PROBES)
    vectors = [list(map(float, v)) for v in vectors]

    golden = {
        "model": meta["model"],
        "dimensions": meta["dimensions"],
        "probes": PROBES,
        "vectors": vectors,
    }
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(golden, fh, indent=2)
        fh.write("\n")

    print(
        f"captured golden: provider={meta['provider']} model={meta['model']} "
        f"dims={meta['dimensions']} probes={len(PROBES)} -> {out_path}"
    )


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    main(out)
