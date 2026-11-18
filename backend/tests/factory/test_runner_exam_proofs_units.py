"""F-2 runner helpers, one fast unit test each (no build run).

The two module-level helpers the proofs probe relies on are called directly
and their exact result asserted: the staging tree and the agent's root list.
"""

from __future__ import annotations


import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.runner import (
    agent_rw_roots_for,
    role_staging_dir,
)


# -- role_staging_dir --------------------------------------------------------


@pytest.mark.parametrize("product_rel", ["volume/build", "other/product-two"])
def test_role_staging_dir_stages_only_the_writer(tmp_path, product_rel):
    product = tmp_path / product_rel
    expected = product.parent / f".{product.name}.staging-writer"
    assert role_staging_dir(product, BuildRole.WRITER) == expected
    for role in BuildRole:
        if role is not BuildRole.WRITER:
            assert role_staging_dir(product, role) is None


# -- agent_rw_roots_for ------------------------------------------------------


@pytest.mark.parametrize("product_rel", ["volume/build", "other/product-two"])
def test_agent_rw_roots_for_is_the_product_and_the_writers_staging(tmp_path, product_rel):
    product = tmp_path / product_rel
    staging = product.parent / f".{product.name}.staging-writer"
    assert agent_rw_roots_for(product) == (product, staging)
    assert agent_rw_roots_for(str(product)) == (product, staging)
