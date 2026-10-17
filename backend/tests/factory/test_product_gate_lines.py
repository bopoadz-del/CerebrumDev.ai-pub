"""The product gate measures a generated product, whatever product it is."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import product_gate as pg  # noqa: E402

GOLDEN = next(
    (p for p in sorted((ROOT / "blueprints").rglob("*.yaml"))
     if "serves_verticals:" in p.read_text(encoding="utf-8")
     and "steward" not in p.as_posix()),
    None,
)


@pytest.fixture(scope="module")
def product(tmp_path_factory):
    if GOLDEN is None:
        pytest.skip(reason="no second golden blueprint on disk")
    out = tmp_path_factory.mktemp("gate") / "product"
    pg.generate(GOLDEN, out)
    return out


def test_lines_pass_on_a_generated_product(product):
    for line in (pg.store_runtime, pg.agent_scope, pg.workflow_approvals, pg.doc_honesty):
        result = line(product)
        assert result["status"] == pg.PASS, (line.__name__, result)


def test_the_lines_fail_when_the_mechanism_is_missing(product, tmp_path):
    """Control in the failing direction: strip the mechanism, the line fails."""
    broken = tmp_path / "broken"
    shutil.copytree(product, broken)
    (broken / "app" / "block_runtime.py").unlink()
    assert pg.store_runtime(broken)["status"] == pg.FAIL
    (broken / "README.md").write_text("Ready: 87% complete.\n", encoding="utf-8")
    assert pg.doc_honesty(broken)["status"] == pg.FAIL


def test_only_timestamp_shaped_values_may_differ():
    a = {"generated_at": "2026-10-04T08:02:15.332901+00:00", "n": 1}
    b = {"generated_at": "2026-10-04T08:03:09.559549+00:00", "n": 1}
    assert pg._scrub(a) == pg._scrub(b)
    assert pg._scrub({"n": 1}) != pg._scrub({"n": 2})
    assert pg._scrub({"when": "yesterday"}) != pg._scrub({"when": "today"})


def test_live_suites_are_withheld_with_their_declared_needs():
    line = pg.live(None, "A")
    assert line["status"] == pg.WITHHELD
    assert line["detail"]["needs"] and line["detail"]["declared_in"] == "artifacts/blockers.json"
    declared = json.loads((ROOT / "artifacts" / "blockers.json").read_text(encoding="utf-8"))
    assert declared["live_suites"]["class"] == "c"
