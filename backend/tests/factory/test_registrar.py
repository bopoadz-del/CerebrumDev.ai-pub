"""The Store Manager's books must answer honestly, including "I don't know".

New-shape tests for the read-only registrar. The dangerous failure here is not
a crash — it is a confident wrong answer. Two shapes specifically:

* Reporting a clone as "current" when the comparison was never possible. It is
  precisely the mirror-sourced clones, which carry stub code rather than real
  block logic, whose revision cannot be compared to a Store commit — so a
  cheerful "current" would bless exactly the ones that most need flagging.
* Missing that two platforms are running different code behind the same block
  name. Nothing in a delivered artifact reveals that; only the ledgers do.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.ledger import BuildLedger
from app.factory.build.registrar import (
    check_staleness,
    read_inventory,
    registrar_report,
)
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

HEAD = "a" * 40
OLD = "b" * 40


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _ledger(root: Path, product: str, clones) -> BuildLedger:
    led = BuildLedger(root / product / "build_ledger.jsonl")
    led.start_run(product_id=product, inputs_hash=product * 4)
    for block_id, revision, origin in clones:
        led.record_clone(
            block_id=block_id,
            source_commit=revision,
            store_repo=origin,
            vendored_path=f"vendor/blocks/{block_id}",
        )
    return led


def test_the_inventory_indexes_by_platform_and_by_block(tmp_path):
    _ledger(tmp_path, "alpha", [("web", HEAD, "cerebrum-blocks"),
                                ("analytics", HEAD, "cerebrum-blocks")])
    _ledger(tmp_path, "beta", [("web", OLD, "cerebrum-blocks")])

    inv = read_inventory(tmp_path)
    assert len(inv.records) == 3
    assert set(inv.by_product()) == {"alpha", "beta"}
    assert set(inv.by_block()) == {"web", "analytics"}
    assert [r.block_id for r in inv.by_product()["alpha"]] == ["analytics", "web"]


def test_divergence_is_surfaced(tmp_path):
    """Two platforms on different revisions of one block is the estate
    problem the registrar exists to find; nothing in a delivered artifact
    reveals it."""
    _ledger(tmp_path, "alpha", [("web", HEAD, "cerebrum-blocks")])
    _ledger(tmp_path, "beta", [("web", OLD, "cerebrum-blocks")])
    _ledger(tmp_path, "gamma", [("web", HEAD, "cerebrum-blocks")])

    inv = read_inventory(tmp_path)
    assert inv.revisions_of("web") == sorted([HEAD, OLD])
    assert set(inv.diverged_blocks()) == {"web"}


def test_no_divergence_when_every_platform_agrees(tmp_path):
    _ledger(tmp_path, "alpha", [("web", HEAD, "cerebrum-blocks")])
    _ledger(tmp_path, "beta", [("web", HEAD, "cerebrum-blocks")])
    assert read_inventory(tmp_path).diverged_blocks() == {}


def test_staleness_against_a_known_head(tmp_path):
    _ledger(tmp_path, "alpha", [("web", HEAD, "cerebrum-blocks")])
    _ledger(tmp_path, "beta", [("web", OLD, "cerebrum-blocks")])

    reports = {r.product_id: r for r in check_staleness(read_inventory(tmp_path), store_head=HEAD)}
    assert reports["alpha"].status == "current"
    assert reports["beta"].status == "stale"
    assert HEAD[:12] in reports["beta"].detail


def test_a_content_pinned_clone_is_unknown_not_current(tmp_path):
    """The load-bearing honesty case.

    Mirror clones carry a content digest, which cannot be compared to a Store
    commit. Calling them "current" would bless the stub-backed platforms — the
    ones that most need flagging.
    """
    _ledger(tmp_path, "alpha", [("web", "sha256:deadbeef", "factory-vendor-mirror")])

    report = check_staleness(read_inventory(tmp_path), store_head=HEAD)[0]
    assert report.status == "unknown"
    assert "content digest" in report.detail


def test_no_reference_means_unknown_not_current(tmp_path):
    _ledger(tmp_path, "alpha", [("web", HEAD, "cerebrum-blocks")])
    report = check_staleness(read_inventory(tmp_path))[0]
    assert report.status == "unknown"
    assert "no store head" in report.detail


def test_staleness_can_be_scoped_to_named_blocks(tmp_path):
    _ledger(tmp_path, "alpha", [("web", HEAD, "s"), ("analytics", OLD, "s")])
    reports = check_staleness(read_inventory(tmp_path), store_head=HEAD, blocks=["analytics"])
    assert [r.block_id for r in reports] == ["analytics"]


def test_the_report_counts_every_status(tmp_path):
    _ledger(tmp_path, "alpha", [("web", HEAD, "s"), ("old", OLD, "s"),
                                ("mirror", "sha256:abc", "factory-vendor-mirror")])
    report = registrar_report(tmp_path, store_head=HEAD)

    assert report["platforms"] == 1
    assert report["blocks"] == 3
    assert report["clones"] == 3
    assert report["status_counts"] == {"current": 1, "stale": 1, "unknown": 1}


def test_an_empty_root_reports_nothing_rather_than_failing(tmp_path):
    report = registrar_report(tmp_path / "nothing-here", store_head=HEAD)
    assert report["clones"] == 0
    assert report["platforms"] == 0
    assert report["status_counts"] == {}


def test_the_registrar_reads_a_real_build(tmp_path, stub_coder):
    """End to end against an actual runner artifact, not a synthetic ledger."""
    out = tmp_path / "platforms" / "runner-smoke"
    assert RoleRunner(load_blueprint(SMOKE), out).run().ok

    inv = read_inventory(tmp_path / "platforms")
    assert {r.block_id for r in inv.records} == {
        "estate_registry",
        "estate_maintenance",
    }
    assert all(r.product_id == "runner-smoke" for r in inv.records)
    # 1f: nothing is unpinned. There is no Factory mirror any more: every clone
    # comes from the Store and is pinned to the Store commit it came from.
    assert all(r.revision and r.revision != "unpinned" for r in inv.records)
    assert all(r.origin == "cerebrum-blocks" for r in inv.records), [
        (r.block_id, r.origin) for r in inv.records
    ]

    # Store-sourced clones carry the Store commit, so staleness is DECIDABLE --
    # a mirror-sourced build could only ever answer "unknown".
    reports = check_staleness(inv, store_head=HEAD)
    assert {r.status for r in reports} == {"stale"}, [(r.block_id, r.status) for r in reports]
    pinned = {r.revision for r in inv.records}
    assert len(pinned) == 1, pinned
    current = check_staleness(inv, store_head=pinned.pop())
    assert {r.status for r in current} == {"current"}
