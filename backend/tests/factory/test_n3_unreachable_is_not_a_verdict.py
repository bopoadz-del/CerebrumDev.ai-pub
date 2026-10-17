"""Not being able to ask GitHub is not an answer about the build.

Live 2026-09-19: the Factory's GitHub token was revoked. The boot waiter asked
for a session's build branch, got "GitHub API down: matching-refs HTTP 401",
and wrote N3_STORE_GATE_MISSING -- a terminal failure -- on a platform whose
branch had passed.
"""

from __future__ import annotations

import pytest

from app.factory.build import n3_store_gate as n3
from app.factory.build.authority import BuildRole
from app.factory.build.builds_push import BuildsPushError
from app.factory.build.ledger import BuildLedger, EventKind


def _awaiting(tmp_path):
    out = tmp_path / "product"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(
        EventKind.NOTE, role=BuildRole.STORE_MANAGER, detail="handed off",
        payload={"honesty": n3.HANDOFF_TO_N3},
    )
    assert n3.handoff_awaiting_n3(out)
    return out


def _github_answering(status, body=b"[]"):
    """An opener that returns ``status`` -- the real HTTP path, no message."""
    import io
    from urllib.error import HTTPError

    def opener(req, *a, **k):
        if status >= 400:
            raise HTTPError(req.full_url, status, "x", {}, io.BytesIO(b"{}"))
        resp = io.BytesIO(body)
        resp.status = status
        return resp

    return opener


@pytest.mark.parametrize("status", [401, 503])
def test_the_real_github_client_types_an_unreachable_answer(status):
    """The raise site types it -- the reader never re-reads the message."""
    from app.factory.build import builds_push

    with pytest.raises(BuildsPushError) as err:
        builds_push.fetch_commit_sha(
            "o", "r", "main", token="t", opener=_github_answering(status)
        )
    assert err.value.unreachable is True
    assert n3.is_infrastructure_error(err.value)


def test_a_missing_token_is_unreachable():
    with pytest.raises(BuildsPushError) as err:
        n3.resolve_builds_target("unused", env={})
    assert err.value.unreachable is True


@pytest.mark.parametrize("unreachable", [True])
def test_an_unreachable_github_leaves_the_handoff_open(tmp_path, monkeypatch, unreachable):
    out = _awaiting(tmp_path)

    def boom(*a, **k):
        raise BuildsPushError("zorblat transport failure", unreachable=unreachable)

    monkeypatch.setattr(n3, "resolve_builds_target", boom)

    result = n3.ingest_n3_store_gate(out, wait=False)

    assert result.pending is True and result.ok is False
    assert result.honesty == n3.HANDOFF_TO_N3
    assert n3.handoff_awaiting_n3(out), "the build must still be waiting, not failed"


def test_a_401_on_the_status_read_is_not_a_verdict_either(tmp_path, monkeypatch):
    out = _awaiting(tmp_path)
    monkeypatch.setattr(
        n3, "resolve_builds_target",
        lambda *a, **k: n3.BuildsTarget(owner="o", repo="r", sha="a" * 40, branch="build/x"),
    )
    monkeypatch.setattr(
        n3, "fetch_store_gate_status",
        lambda *a, **k: n3.StoreGateSnapshot(missing=True, unreachable=True, detail="statuses read refused"),
    )

    result = n3.ingest_n3_store_gate(out, wait=False)

    assert result.pending is True
    assert n3.handoff_awaiting_n3(out)


def test_a_session_with_no_build_branch_is_still_a_real_missing(tmp_path, monkeypatch):
    """GitHub answered; there is nothing there. That IS a verdict."""
    out = _awaiting(tmp_path)

    def none_there(*a, **k):
        raise BuildsPushError("N3 store-gate: no build/sess_x-* branch on o/r")  # GitHub answered

    monkeypatch.setattr(n3, "resolve_builds_target", none_there)

    result = n3.ingest_n3_store_gate(out, wait=False)

    assert result.honesty == n3.N3_STORE_GATE_MISSING
    assert not n3.handoff_awaiting_n3(out)


def test_a_timeout_made_entirely_of_401s_is_not_a_verdict(tmp_path, monkeypatch):
    out = _awaiting(tmp_path)
    monkeypatch.setattr(
        n3, "resolve_builds_target",
        lambda *a, **k: n3.BuildsTarget(owner="o", repo="r", sha="a" * 40, branch="build/x"),
    )
    monkeypatch.setattr(
        n3, "wait_for_store_gate",
        lambda *a, **k: n3.StoreGateSnapshot(
            timeout=True, missing=False, unreachable=True,
            detail="N3 store-gate poll timed out",
        ),
    )

    result = n3.ingest_n3_store_gate(out, wait=True)

    assert result.pending is True
    assert n3.handoff_awaiting_n3(out)


def test_a_real_timeout_is_still_a_timeout(tmp_path, monkeypatch):
    """The gate was reachable and simply never finished: that IS a verdict."""
    out = _awaiting(tmp_path)
    monkeypatch.setattr(
        n3, "resolve_builds_target",
        lambda *a, **k: n3.BuildsTarget(owner="o", repo="r", sha="a" * 40, branch="build/x"),
    )
    monkeypatch.setattr(
        n3, "wait_for_store_gate",
        lambda *a, **k: n3.StoreGateSnapshot(
            timeout=True, pending=True, missing=False, detail="store-gate still pending; poll timed out",
        ),
    )

    result = n3.ingest_n3_store_gate(out, wait=True)

    assert result.honesty == n3.N3_STORE_GATE_TIMEOUT
    assert not n3.handoff_awaiting_n3(out)
