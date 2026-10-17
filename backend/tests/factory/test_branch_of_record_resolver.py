"""ONE resolver for a build's branch of record (live regression, f396d687).

A platform build pushed its phases to ``build/<platform_id>``, passed TESTER,
reached the Store step -- and the N3 store-gate looked for
``build/<session>-*`` ("N3 store-gate: no build/sess_44b549c6eab74efa-*
branch"). Every reader now resolves the branch through
``builds_push.build_refs_of_record``: the platform's branch when the run's
ledger records a platform, the legacy session branch when it does not.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from app.factory.build import n3_store_gate
from app.factory.build.builds_push import BuildsPushError, build_refs_of_record
from app.factory.build.ledger import PLATFORM_ID_KEY, BuildLedger, EventKind
from app.factory.build.platform_identity import (
    branch_of_record,
    mint_platform_id,
    recorded_platform_id,
)

ENV = {"CEREBRUM_BUILDS_GITHUB_TOKEN": "t", "CEREBRUM_BUILDS_REPO": "acme/builds"}
SHA = "a" * 40
LEGACY_SHA = "b" * 40


class _Resp(io.BytesIO):
    def __init__(self, status, payload):
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def getcode(self):
        return self.status


class FakeGitHub:
    """The two ref endpoints the resolver reads, over a fixed set of branches."""

    def __init__(self, branches):
        self.branches = dict(branches)
        self.paths = []

    def __call__(self, request, timeout=None):
        path = unquote(urlsplit(request.full_url).path)
        self.paths.append(path)
        if "/git/ref/heads/" in path:
            name = path.split("/git/ref/heads/", 1)[1]
            if name in self.branches:
                return _Resp(200, {"ref": f"refs/heads/{name}", "object": {"sha": self.branches[name]}})
            from urllib.error import HTTPError

            raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        if "/git/matching-refs/heads/" in path:
            prefix = path.split("/git/matching-refs/heads/", 1)[1]
            rows = [
                {"ref": f"refs/heads/{n}", "object": {"sha": s}}
                for n, s in self.branches.items()
                if n.startswith(prefix)
            ]
            return _Resp(200, rows)
        raise AssertionError(f"unexpected GitHub call {path}")


def _ledger(tmp_path, *, platform_id=None, builds_branch=None):
    out = tmp_path / "ws"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="product", inputs_hash="h")
    if platform_id:
        ledger.append(EventKind.NOTE, detail="platform", payload={PLATFORM_ID_KEY: platform_id})
    if builds_branch:
        ledger.append(EventKind.NOTE, detail="handoff", payload={"handoff": "x", "builds_branch": builds_branch})
    return out


def test_a_platform_build_reaches_n3_on_its_branch_of_record(tmp_path):
    pid = mint_platform_id()
    out = _ledger(tmp_path, platform_id=pid)
    gh = FakeGitHub({branch_of_record(pid): SHA})
    assert recorded_platform_id(out) == pid
    target = n3_store_gate.resolve_builds_target(out, env=ENV, session_id="sess_44b549c6eab74efa", opener=gh)
    assert target.branch == branch_of_record(pid)
    assert target.sha == SHA
    assert not any("matching-refs" in p for p in gh.paths), "a platform never lists session refs"


def test_the_recorded_handoff_branch_wins_without_any_lookup(tmp_path):
    pid = mint_platform_id()
    out = _ledger(tmp_path, platform_id=pid, builds_branch=branch_of_record(pid))

    class Commits(FakeGitHub):
        def __call__(self, request, timeout=None):
            path = unquote(urlsplit(request.full_url).path)
            self.paths.append(path)
            assert "/commits/" in path
            return _Resp(200, {"sha": SHA})

    gh = Commits({})
    target = n3_store_gate.resolve_builds_target(out, env=ENV, session_id="sess_x", opener=gh)
    assert target.branch == branch_of_record(pid) and target.sha == SHA


def test_a_legacy_session_keeps_its_legacy_branch(tmp_path):
    out = _ledger(tmp_path)  # no platform recorded: a run from before platforms
    gh = FakeGitHub({"build/sess_0011223344aa-1a2b3c4d": LEGACY_SHA})
    target = n3_store_gate.resolve_builds_target(out, env=ENV, session_id="sess_0011223344aa", opener=gh)
    assert target.branch == "build/sess_0011223344aa-1a2b3c4d"
    assert target.sha == LEGACY_SHA


def test_a_platform_without_its_branch_falls_back_to_the_legacy_branch(tmp_path):
    # A session approved before platforms existed, resumed after the deploy:
    # it has a platform id now, but its build so far lives on the legacy branch.
    pid = mint_platform_id()
    gh = FakeGitHub({"build/sess_0011223344aa-1a2b3c4d": LEGACY_SHA})
    refs = build_refs_of_record("acme", "builds", platform_id=pid, session_id="sess_0011223344aa",
                                token="t", opener=gh)
    assert refs == [("build/sess_0011223344aa-1a2b3c4d", LEGACY_SHA)]


def test_no_branch_anywhere_names_the_platform_branch(tmp_path):
    pid = mint_platform_id()
    out = _ledger(tmp_path, platform_id=pid)
    with pytest.raises(BuildsPushError) as err:
        n3_store_gate.resolve_builds_target(out, env=ENV, session_id="sess_x", opener=FakeGitHub({}))
    assert branch_of_record(pid) in str(err.value)


def test_the_handoff_note_records_the_gate_branch():
    src = (Path(__file__).resolve().parents[2] / "app" / "factory" / "build" / "runner.py").read_text(
        encoding="utf-8"
    )
    assert '"builds_branch": gate_branch' in src
