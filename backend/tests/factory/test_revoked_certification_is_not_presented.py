"""An export whose Store-gate certification was withdrawn is not presented as
certified.

Live 2026-10-08: build/plt_5ac16f50c9384536 (cycle 2 smoke B) was certified
22/22 by a gate run that judged the checkout instead of the image. The
revocation is data in cerebrum-builds (revoked_certifications.json); its
workflow withdraws the ``store-gate`` commit status the run wrote. The Factory
reads that same status again when it packages, so a withdrawn certification
blocks the certified export -- named, with the gate's own description.
"""

from __future__ import annotations

from app.factory.build import n3_store_gate
from app.factory.build.builds_push import BUILDS_TOKEN_ENV
from app.factory.build.n3_store_gate import BuildsTarget, StoreGateSnapshot, certification_withdrawn

TARGET = BuildsTarget(owner="o", repo="r", sha="a" * 40, branch="build/plt_x")


def _snap(**kw):
    base = dict(sha=TARGET.sha, branch=TARGET.branch, missing=False)
    base.update(kw)
    return StoreGateSnapshot(**base)


def _withdrawn(snap, monkeypatch):
    monkeypatch.setattr(n3_store_gate, "resolve_builds_target", lambda *a, **k: TARGET)
    monkeypatch.setattr(n3_store_gate, "fetch_store_gate_status", lambda *a, **k: snap)
    return certification_withdrawn("/nowhere", env={BUILDS_TOKEN_ENV: "t"})


def test_a_withdrawn_status_blocks_the_certified_export(monkeypatch):
    snap = _snap(state="failure", description="certification revoked: older harness judged the checkout")
    reason = _withdrawn(snap, monkeypatch)
    assert reason and "certification revoked: older harness judged the checkout" in reason
    assert TARGET.branch in reason


def test_a_success_status_certifies(monkeypatch):
    assert _withdrawn(_snap(state="success", description="acceptance.py in Docker 22/22"), monkeypatch) is None


def test_no_answer_from_github_is_not_a_withdrawal(monkeypatch):
    # Unreachable is no verdict about the build; the gate already certified it.
    snap = _snap(state="", missing=True, unreachable=True, detail="GitHub statuses HTTP 503")
    assert _withdrawn(snap, monkeypatch) is None


def test_builds_not_armed_reads_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(n3_store_gate, "resolve_builds_target", lambda *a, **k: called.append(1))
    assert certification_withdrawn("/nowhere", env={}) is None
    assert called == []


def test_the_package_route_consults_it_before_certifying():
    import inspect

    from app.routers import session_product

    src = inspect.getsource(session_product.download_product_package)
    assert src.index("certification_withdrawn") < src.index('manifest["certified"] = True')
