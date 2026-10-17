"""Vendored capture must not silently echo input as a successful capture."""

from __future__ import annotations

from pathlib import Path
from tests.factory.store_paths import store_block  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
MIRROR_CAPTURE = store_block("capture") / "block.py"


def test_vendor_mirror_capture_is_real_adapter():
    text = MIRROR_CAPTURE.read_text(encoding="utf-8")
    assert "get_block" in text
    assert "Factory-generated platform block: capture" not in text
    assert '"status": "ok"' not in text or "get_block" in text
