"""A block that answers "partial" has not succeeded.

Live, from the booted sess_6400b6c zip. ``punch_list_tracking`` returned

    {"capability": "punch_list_tracking", "ok": true,
     "results": {"workflow": {"status": "partial", "pipeline_id": "0bccff63",
       "run_id": "57ee8572", "step_count": 2,
       "results": [{"step_id": "step_0", "block": "database",
         "status": "failed",
         "error": "DatabaseBlock.__init__() missing 2 required positional
                   arguments: 'hal_block' and 'config'"}]}}}

``ok: true``. The pipeline's only real step had failed and nothing was
written. ``execute()`` looked for ``status == "error"`` and ``ok is False``,
and "partial" is neither -- so the failure was laundered into a success by
the one layer whose job is to not do that.

"partial" is not an in-progress marker. Both blocks that emit it mean "some
of this failed": ``workflow`` sets it on every step that raises, times out,
names an unknown block, or returns an error, and ``notification`` sets it
when a channel could not be reached.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.factory.build.roles import _DISPATCH_RUNTIME

LIVE_WORKFLOW_PARTIAL: Dict[str, Any] = {
    "status": "partial",
    "pipeline_id": "0bccff63",
    "run_id": "57ee8572",
    "step_count": 2,
    "results": [
        {
            "step_id": "step_0",
            "block": "database",
            "status": "failed",
            "error": (
                "DatabaseBlock.__init__() missing 2 required positional "
                "arguments: 'hal_block' and 'config'"
            ),
        },
        {"step_id": "step_1", "block": "notification", "status": "success"},
    ],
}


@pytest.fixture()
def dispatch(tmp_path, monkeypatch):
    """The emitted dispatch module with one stub block vendored."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    ns: Dict[str, Any] = {"__file__": str(app_dir / "dispatch.py")}
    exec(compile(_DISPATCH_RUNTIME, "<dispatch_runtime>", "exec"), ns)
    ns["BLOCK_CONTRACTS"] = {}          # no allow-list: payload passes through
    return ns


def _run_returning(ns, result):
    """Point dispatch at a block whose run() returns `result`."""
    module = type("M", (), {"run": staticmethod(lambda **kw: result)})
    ns["load_block"] = lambda block_id: module
    return ns["execute"]


# ── the live failure ─────────────────────────────────────────────────────────


def test_partial_pipeline_is_not_success(dispatch):
    execute = _run_returning(dispatch, LIVE_WORKFLOW_PARTIAL)
    out = execute("workflow", {"steps": []}, action="run")
    assert out["ok"] is False, out
    assert out["status"] == "partial"


def test_the_failed_step_is_named_in_the_error(dispatch):
    """A caller must not have to walk the pipeline to learn what broke."""
    execute = _run_returning(dispatch, LIVE_WORKFLOW_PARTIAL)
    out = execute("workflow", {"steps": []}, action="run")
    assert "step_0" in out["error"]
    assert "database" in out["error"]
    assert "hal_block" in out["error"]


def test_a_failed_step_under_a_success_status_still_fails(dispatch):
    """The nested check is not redundant with the status check."""
    execute = _run_returning(dispatch, {
        "status": "success",
        "results": [{"step_id": "s1", "block": "database", "status": "failed",
                     "error": "boom"}],
    })
    out = execute("workflow", {}, action="run")
    assert out["ok"] is False
    assert "boom" in out["error"]


@pytest.mark.parametrize("status", ["error", "failed", "partial"])
def test_every_failure_word_fails_closed(dispatch, status):
    execute = _run_returning(dispatch, {"status": status})
    assert execute("database", {}, action="insert")["ok"] is False


def test_notification_partial_is_a_failure(dispatch):
    """`notification` says "partial" when a channel could not be reached."""
    execute = _run_returning(dispatch, {"status": "partial", "sent": ["email"]})
    assert execute("notification", {}, action="send")["ok"] is False


# ── what must NOT change ─────────────────────────────────────────────────────


def test_a_clean_result_is_returned_untouched(dispatch):
    clean = {"status": "success", "rows": [{"id": 1}], "count": 1}
    execute = _run_returning(dispatch, clean)
    assert execute("database", {}, action="query") == clean


def test_a_result_with_no_status_is_not_invented_into_one(dispatch):
    """Blocks that answer with bare data must keep working."""
    plain = {"inserted": True, "id": 7, "rows_affected": 1}
    execute = _run_returning(dispatch, plain)
    assert execute("database", {}, action="insert") == plain


def test_all_steps_succeeded_is_still_success(dispatch):
    ok = {
        "status": "success",
        "results": [
            {"step_id": "s1", "block": "database", "status": "success"},
            {"step_id": "s2", "block": "notification", "status": "success"},
        ],
    }
    execute = _run_returning(dispatch, ok)
    assert execute("workflow", {}, action="run") == ok


def test_ok_false_is_still_a_failure(dispatch):
    execute = _run_returning(dispatch, {"ok": False, "error": "refused"})
    out = execute("team", {}, action="get_team")
    assert out["ok"] is False
    assert out["status"] == "error"


def test_an_existing_error_message_is_not_overwritten(dispatch):
    execute = _run_returning(dispatch, {
        "status": "partial",
        "error": "the block said this",
        "results": [{"step_id": "s1", "status": "failed", "error": "detail"}],
    })
    assert execute("workflow", {}, action="run")["error"] == "the block said this"
