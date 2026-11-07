"""P2-G0 drift guard: the Store's program_engine mirror must stay byte-identical
to the Factory's program kernel.

Source of truth is the Factory kernel
(``app.cerebrum_product_kernel.programs``). The Store block
(``app/blocks/program_engine``) is a byte-identical mirror. This test fails when
the two diverge — one implementation, not two.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_KERNEL_FILES = (
    "__init__.py",
    "schema.py",
    "modes.py",
    "calendar.py",
    "rubric.py",
    "prompts.py",
    "export.py",
)


def _kernel_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "app" / "cerebrum_product_kernel" / "programs"


def _store_mirror_dir() -> Path | None:
    root = os.getenv("CEREBRUM_BLOCKS_ROOT")
    if not root:
        return None
    mirror = Path(root) / "app" / "blocks" / "program_engine"
    return mirror if mirror.is_dir() else None


def test_store_program_engine_mirror_matches_factory_kernel():
    mirror = _store_mirror_dir()
    if mirror is None:
        pytest.skip("no Store checkout with app/blocks/program_engine (CEREBRUM_BLOCKS_ROOT)")
    kernel = _kernel_dir()
    drifted = [
        name
        for name in _KERNEL_FILES
        if (kernel / name).read_bytes() != (mirror / name).read_bytes()
    ]
    assert not drifted, f"program_engine mirror drifted: {drifted}"
