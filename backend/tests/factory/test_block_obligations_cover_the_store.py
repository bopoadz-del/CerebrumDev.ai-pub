"""DISTRIBUTIONS must name every third-party import in the pinned Store.

Live, twice in one day, at the same stage:

    CLONER failed: vendored source imports 'bcrypt' and the factory has no
    PyPI distribution recorded for it; add it to
    block_obligations.DISTRIBUTIONS ...

``DISTRIBUTIONS`` is hand-kept "for every third-party import present in the
Store's block roster", and nothing checked that claim. Moving the Store pin
brought TWELVE import names the table had never seen; ``bcrypt`` was merely
the first one a build tripped over. Refusing is right -- guessing a PyPI name
ships a Dockerfile that fails at ``pip install`` -- but the refusal belongs
here, when the pin moves, not in a customer's build an hour later.

The fix the first time round swept one CLONER step across the registry and
missed this one entirely. So this file sweeps the whole vendorable surface.
"""

from __future__ import annotations

import os

import pytest

from app.factory.build.block_obligations import (
    _LOCAL_ROOTS,
    DISTRIBUTIONS,
    third_party_imports,
)

from .blocks_root import declared_store_sha, real_blocks_root

#: Everything the CLONER can copy into a product.
_VENDORABLE = ("app/blocks", "app/core", "block_registry")


def _store():
    root = real_blocks_root()
    if root is None:
        message = (
            "no Store checkout at the declared pin "
            f"({(declared_store_sha() or 'unknown')[:8]}); set CEREBRUM_BLOCKS_ROOT"
        )
        # CI checks the Store out at the pin, so absence there is a broken
        # pipeline and must not read as a pass.
        if os.getenv("CI"):
            pytest.fail(message)
        pytest.skip(reason=message)
    return root


def _unmapped(root):
    missing = {}
    for sub in _VENDORABLE:
        for py in sorted((root / sub).rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            source = py.read_text(encoding="utf-8", errors="replace")
            for module in third_party_imports(source):
                if module not in DISTRIBUTIONS:
                    missing.setdefault(module, py.relative_to(root).as_posix())
    return missing


def test_every_third_party_import_in_the_pinned_store_has_a_distribution():
    missing = _unmapped(_store())

    assert not missing, (
        "the pinned Store imports modules block_obligations.DISTRIBUTIONS "
        "cannot name, so the CLONER will refuse any product that vendors "
        "them:\n"
        + "\n".join(f"  {mod}  (first seen in {rel})" for mod, rel in sorted(missing.items()))
        + "\nAdd the PyPI distribution -- or, if it is a Store-local package "
        "and not on PyPI at all, add it to _LOCAL_ROOTS instead."
    )


@pytest.mark.parametrize("name", ["block", "block_store", "level"])
def test_store_local_packages_are_never_mapped_to_pypi(name):
    """``block`` and ``level`` are real, unrelated projects on PyPI. Mapping a
    Store-local import to either would make a customer's Dockerfile
    ``pip install`` a stranger's code under a name the platform trusts."""
    assert name in _LOCAL_ROOTS
    assert name not in DISTRIBUTIONS


def test_the_import_that_failed_the_live_build_is_recorded():
    assert DISTRIBUTIONS["bcrypt"] == "bcrypt"
    # import name and distribution name disagree -- the reason this is a table
    assert DISTRIBUTIONS["sentry_sdk"] == "sentry-sdk"
