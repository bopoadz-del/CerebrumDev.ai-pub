"""A running build is identified by its workspace of record, never by a name
two tenants share.

Live 2026-10-06 (879ed1e1): two accounts each drafted a platform with no
vertical, so both blueprints had the product id "product". Account B's
runner thread was named ``build-product``; account A's approve then found a
live thread by that NAME, answered "already running" and started nothing --
and left A's session "approved, no generation", where approve said "no
feature list waiting" and the intake said "approved ... frozen". These tests
reproduce that with a real thread named the way the old code named it.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path

from app.factory import build_jobs, platform_chat_flow
from app.models.session import SessionState

SHARED_PRODUCT_ID = "product"


@contextmanager
def _running_build(output_dir: Path):
    """A live runner for ``output_dir``, named exactly as the old code named
    every runner (``build-<product id>``), registered under its workspace."""
    stop = threading.Event()
    thread = threading.Thread(
        target=stop.wait, name=f"build-{SHARED_PRODUCT_ID}", daemon=True
    )
    thread.start()
    build_jobs.register_runner_thread(output_dir, thread)
    try:
        yield thread
    finally:
        stop.set()
        thread.join(timeout=2)


def _drafted(session_id: str, user_id: str) -> SessionState:
    state = SessionState(session_id=session_id, user_id=user_id)
    platform_chat_flow.draft_from_chat(state, "build me a platform for private estates")
    # Neither tenant declared a vertical: both get the shared product id.
    state.product_design.blueprint["product_id"] = SHARED_PRODUCT_ID
    state.product_design.build_level = "production"
    return state


def _stub_generate(monkeypatch, started: list):
    def fake_generate(bp, output_dir, **_kwargs):
        started.append(Path(output_dir))
        return {
            "ok": True,
            "engine": "runner",
            "output_dir": str(output_dir),
            "product_id": bp.product_id,
            "inputs_hash": "h" * 64,
            "build": {"state": "building"},
            "already_running": False,
        }

    monkeypatch.setattr(platform_chat_flow, "generate_product", fake_generate)


def test_two_workspaces_with_one_product_id_have_distinct_runner_identities(tmp_path):
    a = tmp_path / "sessions" / "sess_a" / SHARED_PRODUCT_ID
    b = tmp_path / "sessions" / "sess_b" / SHARED_PRODUCT_ID
    assert build_jobs.runner_thread_name(a) != build_jobs.runner_thread_name(b)
    with _running_build(a):
        assert platform_chat_flow._live_build_thread(a) is not None
        assert platform_chat_flow._live_build_thread(b) is None


def test_another_tenants_running_build_never_blocks_this_approve(tmp_path, monkeypatch):
    started: list = []
    _stub_generate(monkeypatch, started)
    tenant_a = _drafted("sess_tenant_a", "acct_a")
    tenant_b = _drafted("sess_tenant_b", "acct_b")
    # Production roots every session at sessions/<session>/<product id>; an
    # explicit output_root drops the session segment, so give each tenant its
    # own root to keep the two workspaces distinct exactly as they are live.
    root_a, root_b = tmp_path / "sess_tenant_a", tmp_path / "sess_tenant_b"
    a_out = platform_chat_flow._session_output(
        tenant_a.session_id, SHARED_PRODUCT_ID, root_a
    )
    b_out = platform_chat_flow._session_output(
        tenant_b.session_id, SHARED_PRODUCT_ID, root_b
    )

    with _running_build(a_out):
        result = platform_chat_flow.approve_and_generate(tenant_b, output_root=root_b)

    assert result.get("already_running") is not True, result
    assert started == [b_out], "B must start its own build in its own workspace"
    assert tenant_b.product_design.generation is not None
    assert tenant_b.product_design.blueprint_approved is True


def test_the_same_platform_started_twice_is_still_refused(tmp_path, monkeypatch):
    started: list = []
    _stub_generate(monkeypatch, started)
    state = _drafted("sess_same", "acct_same")
    own_out = platform_chat_flow._session_output(
        state.session_id, SHARED_PRODUCT_ID, tmp_path
    )

    with _running_build(own_out):
        result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)

    assert result.get("already_running") is True
    assert started == [], "a second runner for the same workspace must never start"


def test_an_approve_that_starts_nothing_leaves_the_feature_list_approvable(
    tmp_path, monkeypatch
):
    started: list = []
    _stub_generate(monkeypatch, started)
    state = _drafted("sess_stuck", "acct_stuck")
    own_out = platform_chat_flow._session_output(
        state.session_id, SHARED_PRODUCT_ID, tmp_path
    )

    with _running_build(own_out):
        platform_chat_flow.approve_and_generate(state, output_root=tmp_path)

    # Nothing of this session's was started or recorded: never "approved,
    # no generation" -- the approve button must still work.
    assert started == []
    assert state.product_design.generation is None
    assert state.product_design.blueprint_approved is False
    assert platform_chat_flow.has_pending_blueprint(state) is True


def test_a_lint_rejected_approve_leaves_the_feature_list_approvable(tmp_path, monkeypatch):
    started: list = []
    _stub_generate(monkeypatch, started)
    state = _drafted("sess_lint", "acct_lint")

    class _Bad:
        ok = False
        errors = ["unresolved block id"]

        def to_dict(self):
            return {"ok": False, "errors": self.errors}

    monkeypatch.setattr(
        platform_chat_flow,
        "_compile_and_lint_approved",
        lambda state, bp: {
            "lint": _Bad(),
            "plan": type("P", (), {"to_dict": lambda self: {}})(),
            "plain_language": "",
        },
    )
    result = platform_chat_flow.approve_and_generate(state, output_root=tmp_path)

    assert result["ok"] is False
    assert result["blueprint_approved"] is False
    assert started == []
    assert platform_chat_flow.has_pending_blueprint(state) is True


def test_the_runner_is_registered_under_its_workspace_when_started():
    import inspect

    src = inspect.getsource(build_jobs.start_runner_build)
    assert "register_runner_thread(out, thread)" in src
    assert "name=runner_thread_name(out)" in src
    assert 'name=f"build-{' not in src
