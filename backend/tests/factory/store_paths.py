"""Where tests find blocks: the pinned Store, never a Factory-local copy.

The Factory holds no blocks. Tests that used to read
``app/factory/vendor_blocks_mirror/<id>/`` read the same block from the
Store checkout the build itself clones from (``CEREBRUM_BLOCKS_ROOT`` in CI,
the sibling checkout or pinned clone locally).
"""

from __future__ import annotations

from pathlib import Path


def store_root() -> Path:
    from app.factory.blocks_source import resolve_blocks_root

    root = resolve_blocks_root()
    assert root is not None and (Path(root) / "block_registry").is_dir(), (
        "no Store checkout available -- set CEREBRUM_BLOCKS_ROOT"
    )
    return Path(root)


def store_registry() -> Path:
    return store_root() / "block_registry"


def store_block(block_id: str) -> Path:
    return store_registry() / block_id
