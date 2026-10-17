"""The gate follows the prompt: it grades a build against what its brief asked
for. Universal checks apply to every platform; conditional checks apply only
when the brief declares their subject, so a build is never rejected over a
capability it never asked for (e.g. a RAG round-trip with no retrieval, or a
security scan on a build whose chosen level is below production).

No brief in hand == the strictest reading (every signal raised), so a bare
re-render never silently lowers the bar.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.factory.build.acceptance_floor import (
    advisory_ids,
    brief_signals,
    check_ids,
    enforced_ids,
    is_production_grade,
    render_for_prompt,
)


def _bp(**kw):
    kw.setdefault("summary", "")
    kw.setdefault("capabilities", [])
    kw.setdefault("connectors", [])
    return SimpleNamespace(**kw)


def _cap(cid, desc="", block_ids=()):
    return SimpleNamespace(id=cid, description=desc, block_ids=list(block_ids))


def test_no_brief_is_the_strictest_reading():
    """None == every signal raised == only the static-advisory three are advisory."""
    assert advisory_ids(None) == advisory_ids()
    enf, adv = set(enforced_ids(None)), set(advisory_ids(None))
    assert enf.isdisjoint(adv)
    assert enf | adv == set(check_ids())


def test_universal_checks_apply_to_every_brief():
    core = {
        "postgres_boot_200", "ui_served_200", "authorship_floor",
        "no_token_literal", "cross_tenant_404",
    }
    for bp in (_bp(), _bp(build_level="prototype"), _bp(capabilities=[_cap("x")])):
        assert core <= set(enforced_ids(bp)), core - set(enforced_ids(bp))


def test_rag_check_is_skipped_when_no_retrieval_requested():
    """The automotive lesson, generalised: don't reject over un-requested RAG."""
    bp = _bp(capabilities=[_cap("ops_dashboards", "per-brand dashboards")])
    assert "rag_roundtrip_hit" in advisory_ids(bp)
    assert "retrieval" not in brief_signals(bp)


def _store_with_blocks(root, blocks):
    """A fixture Store root: block_registry/<id>/block.json with ``reads``."""
    import json

    for bid, reads in blocks.items():
        path = root / "block_registry" / bid / "block.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"id": bid, "reads": reads}), encoding="utf-8")
    return root


def test_rag_check_is_enforced_when_a_bound_block_declares_the_vector_read(
    tmp_path, monkeypatch
):
    """Retrieval is raised by STRUCTURE the Store declares: a capability binds
    a block whose block.json reads the database at vector scope. The words of
    the brief and the name of the block decide nothing -- so this uses a
    fixture Store root with invented blocks (the PR does not depend on the
    real registry or on Store #140's merge timing). Was: block_ids=
    ['rag_retrieval'], a block the Store does not have, raising the signal by
    the old word list."""
    store = _store_with_blocks(tmp_path / "store", {
        "zorblat_lookup": [{"kind": "database", "scope": "vector"}],
        "quux_ledger": [{"kind": "database", "scope": "rows"}],
    })
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(store))

    retrieves = _bp(capabilities=[_cap("frob_desk", "plain words",
                                       block_ids=["zorblat_lookup"])])
    assert "retrieval" in brief_signals(retrieves)
    assert "rag_roundtrip_hit" in enforced_ids(retrieves)

    # Control: retrieval words, and a block that reads rows -- no signal.
    control = _bp(capabilities=[_cap("rag_search", "semantic search over SOPs",
                                     block_ids=["quux_ledger"])])
    assert "retrieval" not in brief_signals(control)
    assert "rag_roundtrip_hit" in advisory_ids(control)


def test_audit_scan_is_advisory_for_a_declared_test_platform():
    assert "audit_clean" in advisory_ids(_bp(build_level="prototype"))
    assert "audit_clean" in advisory_ids(_bp(build_level="light"))
    # Production adds the floor in full; pilot is still below it.
    assert "audit_clean" in advisory_ids(_bp(build_level="pilot"))


def test_audit_scan_is_enforced_for_a_production_brief():
    assert is_production_grade(_bp())  # no level declared == the full floor
    assert "audit_clean" in enforced_ids(_bp())
    assert "audit_clean" in enforced_ids(_bp(build_level="production"))


def test_always_advisory_checks_stay_advisory_under_any_brief():
    for bp in (None, _bp(), _bp(build_level="production", connectors=["x"])):
        adv = set(advisory_ids(bp))
        assert {"one_live_connector", "backup_restore_roundtrip", "bench_p95"} <= adv


def test_the_prompt_marks_what_this_brief_does_not_require():
    proto = render_for_prompt(_bp(build_level="prototype"))
    assert "[not required by this brief]" in proto
    # a production brief with retrieval requires audit and rag -> fewer marks
    full = render_for_prompt(_bp(build_level="production",
                                 capabilities=[_cap("s", block_ids=["rag"])]))
    assert proto != full
