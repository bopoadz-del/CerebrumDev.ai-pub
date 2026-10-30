"""_pack_serves: a pack serves a vertical when its manifest domain_id or a
serves_verticals entry slugs to it. Synthetic manifests only."""
from __future__ import annotations

import json

import pytest

from app.factory.build.formula_resolution import _pack_serves, store_coverage_index


def test_pack_serves_matches_domain_id_and_serves_verticals():
    m = {"domain_id": "Alpha Vertical", "serves_verticals": ["beta-vertical"]}
    assert _pack_serves(m, "alpha_vertical") is True
    assert _pack_serves(m, "beta_vertical") is True
    assert _pack_serves({"domain_id": "zeta"}, "zeta") is True


def test_pack_serves_refuses_unrelated_vertical():
    m = {"domain_id": "zeta", "serves_verticals": ["eta"]}
    assert _pack_serves(m, "theta") is False
    assert _pack_serves({}, "theta") is False


@pytest.mark.parametrize("vertical", ["", None])
def test_empty_vertical_never_reaches_pack_scan(tmp_path, vertical):
    # The only caller returns [] for an empty vertical before any pack is read,
    # which is why _pack_serves carries no empty-want guard of its own.
    pack = tmp_path / "domain_packs" / "p1"
    pack.mkdir(parents=True)
    (pack / "pack.json").write_text(json.dumps(
        {"manifest": {"domain_id": ""}, "formulas": [{"formula_id": "f_one"}]}))
    assert store_coverage_index(vertical, store_root=tmp_path) == []
