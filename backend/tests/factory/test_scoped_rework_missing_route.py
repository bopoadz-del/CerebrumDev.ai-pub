"""A declared capability whose UI call has no backend route becomes ONE scoped
writer task per route, and its rework budget is keyed per route -- not a
whole-phase retry that stops on SAME_FAILURE_TWICE the moment two routes are
missing (ORDERS 2026-10-27 #2).

Two synthetic blueprints (two different capability/route pairs) prove the
mechanism generically: nothing here is specific to any product or brief.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.factory.build import findings as _findings
from app.factory.build import runner
from app.factory.build.ui_e2e import GATE_NAME, UI_NOT_WIRED


def _missing_route(cap: str, line: int) -> _findings.Finding:
    return _findings.Finding(
        f"the UI calls /v1/{cap}/ and the platform answers 404",
        gate=GATE_NAME,
        check_id=GATE_NAME,
        capability_id=cap,
        file="app/static/index.html",
        line=line,
        finding_shape="missing_route",
    )


def _verdict(findings) -> SimpleNamespace:
    return SimpleNamespace(
        findings=list(findings),
        gate="WRITER",
        reason=UI_NOT_WIRED,
        payload={},
        detail=str(findings[0]) if findings else "",
    )


def test_two_missing_routes_are_two_distinct_per_route_keys():
    # Blueprint A route "exports", Blueprint B route "reports": two missing
    # routes in one phase must be two different failures, so neither trips
    # SAME_FAILURE_TWICE against the other.
    verdict = _verdict([_missing_route("exports", 40), _missing_route("reports", 51)])
    keys = runner._failure_keys(verdict)
    assert set(keys) == {
        f"{GATE_NAME}:missing_route:exports",
        f"{GATE_NAME}:missing_route:reports",
    }


def test_same_route_twice_is_the_same_key_but_a_different_route_is_not():
    a = runner._failure_keys(_verdict([_missing_route("exports", 40)]))
    a_again = runner._failure_keys(_verdict([_missing_route("exports", 40)]))
    b = runner._failure_keys(_verdict([_missing_route("reports", 51)]))
    assert a == a_again  # the same route is the same task
    assert set(a).isdisjoint(set(b))  # a different route is a different task


def test_each_missing_route_becomes_one_scoped_implement_task():
    verdict = _verdict([_missing_route("exports", 40), _missing_route("reports", 51)])
    items = [str(i) for i in runner._writer_gate_items(verdict)]
    exports = next(i for i in items if "'exports'" in i)
    reports = next(i for i in items if "'reports'" in i)
    # The task names the route to implement, the capability, and the call site,
    # and forbids a stub answer.
    assert "/v1/exports" in exports and "app/static/index.html:40" in exports
    assert "/v1/reports" in reports and "app/static/index.html:51" in reports
    for task in (exports, reports):
        assert "implement the backend route" in task
        assert "handler" in task and "schema" in task and "test" in task
        assert "never 404" in task and "canned or empty" in task


def test_a_non_route_finding_keeps_its_generic_key_alongside_the_route_keys():
    # A plain (untyped) ui finding in the same verdict keeps one generic key;
    # the missing route still gets its own per-route key.
    verdict = _verdict([
        _missing_route("exports", 40),
        "the served UI drives 1 of 3 capabilities",  # plain string, not scoped
    ])
    keys = set(runner._failure_keys(verdict))
    assert f"{GATE_NAME}:missing_route:exports" in keys
    assert any(k.startswith(f"{GATE_NAME}:") and ":missing_route:" not in k for k in keys)
