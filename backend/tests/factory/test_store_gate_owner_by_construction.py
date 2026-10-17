"""The store gate names an owner for every failure, by construction.

Live 2026-09-30 -> 10-01, automotive-aiops, nine rounds: the product scored
20/21 on every one of the first eight, the only red line being the Factory's
own authorship counter; round nine's gate workflow broke before scoring. The
Floor said "store gate failed" nine times. Two defects let that happen:

* the Factory only ever learned a SCORE from the store-gate status, never
  which check failed -- so it could not say whose fault it was; and
* no check carried an owner, so a Factory-owned miss failed the product.

These tests pin the fix: the status itemises what failed; every floor check
declares what it judges; the owner of a line is DERIVED from that and from
who rendered the subject (never from a list of check names); the product is
scored on what it owns; and a Factory-owned failure is terminal for the
Factory, not the product -- no rework, no re-run, one honest sentence.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.request import Request

import pytest

from app.factory.build import acceptance_floor as floor
from app.factory.build.acceptance_floor import FACTORY, PRODUCT, SUBJECT_RE, owner_of
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.n3_store_gate import (
    HANDOFF_TO_N3,
    N3_STORE_GATE_FACTORY_OWED,
    N3_STORE_GATE_FAILED,
    N3_STORE_GATE_GREEN,
    BuildsTarget,
    fetch_store_gate_status,
    handoff_awaiting_n3,
    ingest_n3_store_gate,
    report_from_snapshot,
)
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
    factory_rendered_paths,
    stamp_acceptance_into_path,
)
from app.factory.build_jobs import build_status, is_build_complete

SHA = "081a52874bb0141c5eb730b01f26dcc0bf8d0fa2"
BRANCH = "build/sess_7c1d1c1e049c4c4a-3117a9fb"
ENV = {
    "CEREBRUM_BUILDS_GITHUB_TOKEN": "builds-test-token",
    "CEREBRUM_BUILDS_REPO": "bopoadz-del/cerebrum-builds",
}
TARGET = BuildsTarget(owner="bopoadz-del", repo="cerebrum-builds", sha=SHA, branch=BRANCH)


class _Resp:
    def __init__(self, status: int, body):
        self.status = status
        self._body = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _store_gate_zip(failing) -> bytes:
    """The gate run's typed artifact: one line per floor check."""
    import io
    import zipfile

    lines = [
        {"name": name, "status": "FAIL" if name in failing else "PASS", "detail": "measured"}
        for name in ACCEPTANCE_CHECK_NAMES
        if name not in failing
    ] + [{"name": name, "status": "FAIL", "detail": "measured"} for name in failing]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("store_gate.json", json.dumps({"lines": lines}))
    return buf.getvalue()


class StatusOpener:
    """GitHub, reduced to the calls the gate makes: the status, and -- when
    the run published one -- its typed store_gate.json artifact."""

    def __init__(self, *, state: str, description: str, failing=None):
        self.state = state
        self.description = description
        self.failing = failing

    def __call__(self, req: Request, timeout=None):
        url = req.full_url
        if "/actions/artifacts" in url:
            arts = [] if self.failing is None else [
                {"name": "store-gate", "expired": False, "workflow_run": {"head_sha": "0" * 40},
                 "archive_download_url": "https://api.github.test/artifact/0/zip"},
                {"name": "store-gate", "expired": False, "workflow_run": {"head_sha": SHA},
                 "archive_download_url": "https://api.github.test/artifact/1/zip"},
            ]
            return _Resp(200, {"total_count": len(arts), "artifacts": arts})
        if url.endswith("/artifact/0/zip"):
            raise AssertionError("read another sha's artifact")
        if url.endswith("/artifact/1/zip"):
            return _Resp(200, _store_gate_zip(list(self.failing or [])))
        if "/statuses" in url:
            return _Resp(
                200,
                [{"context": "store-gate", "state": self.state, "description": self.description}],
            )
        if "/matching-refs/" in url:
            return _Resp(200, [{"ref": f"refs/heads/{BRANCH}", "object": {"sha": SHA, "type": "commit"}}])
        if "/commits/" in url:
            return _Resp(200, {"sha": SHA})
        raise AssertionError(f"unexpected GitHub URL {url}")


def _handoff(tmp_path: Path) -> Path:
    out = tmp_path / "sessions" / "sess_7c1d1c1e049c4c4a" / "automotive-aiops"
    out.mkdir(parents=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="automotive-aiops", inputs_hash="handoff-hash")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        ledger.append(EventKind.GATE_PASSED, role=role, detail="ok", payload={"gate": "seed"})
    payload = {
        "honesty": HANDOFF_TO_N3,
        "seam": "cli_pivot",
        "next": "n3_gate",
        "green": False,
        "cli_authored_ids": ["a", "b", "c", "d", "e"],
        "builds_sha": SHA,
        "builds_branch": BRANCH,
        "builds_owner": "bopoadz-del",
        "builds_repo": "cerebrum-builds",
    }
    ledger.append(EventKind.NOTE, role=BuildRole.WRITER, detail="HANDOFF_TO_N3", payload=payload)
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="HANDOFF_TO_N3: store-gate next",
        payload={**payload, "outcome": HANDOFF_TO_N3, "cycle": "code", "pilot_ready": False},
    )
    return out


def _last_run_failed(out: Path):
    events = [e for e in BuildLedger(out / "build_ledger.jsonl").events() if e.kind is EventKind.RUN_FAILED]
    return events[-1]


# -- the floor declares what each check judges -----------------------------


def test_every_floor_check_declares_a_valid_subject():
    for check in floor.checks():
        assert SUBJECT_RE.match(str(check.get("subject") or "")), check["id"]


def test_a_check_without_a_subject_is_refused(tmp_path, monkeypatch):
    data = json.loads(floor.floor_path().read_text(encoding="utf-8"))
    del data["checks"][0]["subject"]
    bad = tmp_path / "floor.json"
    bad.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(floor, "floor_path", lambda: bad)
    floor._load.cache_clear()
    try:
        with pytest.raises(ValueError, match="subject"):
            floor._load()
    finally:
        floor._load.cache_clear()


def test_subjects_name_no_product_vocabulary():
    """The guard the floor already lives under: nothing in it may name one
    product's domain or columns. A subject is a platform path, nothing more."""
    field_shape = re.compile(r"(?<![a-z0-9_])[a-z]+_(?:at|id|no|ref|code|date|url)(?![a-z0-9_])")
    for check in floor.checks():
        subject = str(check["subject"])
        assert not field_shape.search(subject), (check["id"], subject)


# -- the owner is derived, never listed -------------------------------------


def test_factory_rendered_paths_is_exactly_what_the_factory_stamps(tmp_path):
    stamp_acceptance_into_path(tmp_path, product_name="Auto Ops", cap_ids=("x",))
    written = {
        str(p.relative_to(tmp_path)).replace("\\", "/")
        for p in tmp_path.rglob("*")
        if p.is_file()
    }
    rendered = set(factory_rendered_paths())
    assert written <= rendered, written - rendered
    # and the files the Factory owns without stamping them here
    assert ".github/workflows/store-gate.yml" in rendered
    assert "requirements.txt" in rendered


def test_owner_follows_the_subject():
    assert owner_of("no_token_401") == PRODUCT  # runtime: the product answered
    assert owner_of("cross_tenant_404") == PRODUCT
    assert owner_of("authorship_floor") == FACTORY  # the Factory's own bookkeeping
    # a file the Factory renders is the Factory's, whatever the check
    assert owner_of("ci_present_and_full_suite") == FACTORY
    assert owner_of("openapi_committed") == FACTORY
    assert owner_of("audit_clean") == FACTORY
    # a directory the coder fills is the product's
    assert owner_of("negative_floor") == PRODUCT
    assert owner_of("single_persistence_root") == PRODUCT
    assert owner_of("no_token_literal") == PRODUCT


def test_a_detail_naming_a_factory_file_is_the_factorys_whatever_the_check():
    assert owner_of("no_token_literal", "literal token at app/tenancy.py:12") == FACTORY
    assert owner_of("no_token_literal", "literal token at app/handlers/leads.py:40") == PRODUCT


def test_a_bare_basename_in_a_score_line_does_not_flip_ownership():
    assert owner_of("no_token_401", "acceptance.py in Docker 3/22") == PRODUCT


def test_no_check_name_appears_in_any_owner_rule():
    """The whole point: adding a Factory-owned check tomorrow must be owned
    correctly without anyone editing a list. There is no such list."""
    src = Path(floor.__file__).read_text(encoding="utf-8")
    body = src.split("def owner_of", 1)[1]
    for name in ACCEPTANCE_CHECK_NAMES:
        assert f'"{name}"' not in body and f"'{name}'" not in body, name


# -- the seam carries which check failed -----------------------------------


def test_itemised_status_becomes_lines_and_a_product_score():
    snap = fetch_store_gate_status(
        TARGET,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description=f"acceptance.py in Docker {ACCEPTANCE_REQUIRED - 1}/{ACCEPTANCE_REQUIRED} FAIL:authorship_floor",
            failing=['authorship_floor'],
        ),
    )
    assert snap.harness_ran is True
    assert {l.name for l in snap.lines} == set(ACCEPTANCE_CHECK_NAMES)
    assert [l.name for l in snap.lines if l.status == "FAIL"] == ["authorship_floor"]
    report = report_from_snapshot(snap)
    assert report.factory_owed == ["authorship_floor"]
    assert report.product_ok is True
    factory_owned = sum(1 for n in ACCEPTANCE_CHECK_NAMES if owner_of(n) == FACTORY)
    assert report.product_total == ACCEPTANCE_REQUIRED - factory_owned
    assert report.product_passed == report.product_total


def test_a_red_status_with_no_score_means_the_harness_never_ran():
    snap = fetch_store_gate_status(
        TARGET,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description="acceptance.py in Docker no score (the image or the script did not run)",
        ),
    )
    assert snap.harness_ran is False
    assert report_from_snapshot(snap).product_ok is False


def test_unknown_check_names_in_the_artifact_are_dropped_not_guessed():
    snap = fetch_store_gate_status(
        TARGET,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description=f"acceptance.py in Docker 19/{ACCEPTANCE_REQUIRED}",
            failing=["cross_tenant_404", "zorblat_check"],
        ),
    )
    assert [l.name for l in snap.lines if l.status == "FAIL"] == ["cross_tenant_404"]


def test_the_description_text_decides_nothing():
    """FAIL names written into the display description itemise nothing: the
    typed artifact is the only source."""
    snap = fetch_store_gate_status(
        TARGET,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description=f"acceptance.py in Docker 19/{ACCEPTANCE_REQUIRED} FAIL:cross_tenant_404",
        ),
    )
    assert snap.lines == []


# -- a Factory-owned failure never fails the product ------------------------


def test_factory_owned_failure_routes_to_the_factory_lane_not_the_product(tmp_path):
    out = _handoff(tmp_path)
    result = ingest_n3_store_gate(
        out,
        wait=False,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description=f"acceptance.py in Docker {ACCEPTANCE_REQUIRED - 1}/{ACCEPTANCE_REQUIRED} FAIL:authorship_floor",
            failing=['authorship_floor'],
        ),
    )
    assert result.ok is False
    assert result.honesty == N3_STORE_GATE_FACTORY_OWED
    assert "not your product" in result.detail
    assert "authorship_floor" in result.detail
    assert "do not re-run" in result.detail
    event = _last_run_failed(out)
    assert event.payload["failure_owner"] == FACTORY
    assert event.payload["factory_owed"] == ["authorship_floor"]
    assert event.payload["next"] == "factory_fix"
    assert event.payload["rework"] is False
    assert event.payload["product_ok"] is True
    assert event.payload["generator"].startswith("app/factory/build/")
    assert handoff_awaiting_n3(out) is False
    assert is_build_complete(out) is False
    status = build_status(out)
    assert status["pilot_ready"] is False
    assert "not your product" in status["detail"]
    report = json.loads((out / "docs" / "store_acceptance.json").read_text(encoding="utf-8"))
    assert report["product_ok"] is True
    assert report["factory_owed"] == ["authorship_floor"]


def test_gate_that_never_scored_is_the_factorys(tmp_path):
    out = _handoff(tmp_path)
    result = ingest_n3_store_gate(
        out,
        wait=False,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description="acceptance.py in Docker no score (the image or the script did not run)",
        ),
    )
    assert result.honesty == N3_STORE_GATE_FACTORY_OWED
    assert "store-gate.yml" in result.detail
    assert "not your product" in result.detail
    event = _last_run_failed(out)
    assert event.payload["failure_owner"] == FACTORY
    assert event.payload["harness_ran"] is False


def test_product_owned_failure_still_routes_to_the_product(tmp_path):
    out = _handoff(tmp_path)
    result = ingest_n3_store_gate(
        out,
        wait=False,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description=f"acceptance.py in Docker {ACCEPTANCE_REQUIRED - 2}/{ACCEPTANCE_REQUIRED} FAIL:cross_tenant_404,authorship_floor",
            failing=['cross_tenant_404', 'authorship_floor'],
        ),
    )
    assert result.honesty == N3_STORE_GATE_FAILED
    assert "cross_tenant_404" in result.detail
    assert "your product failed 1" in result.detail
    assert "authorship_floor" in result.detail and "not yours" in result.detail
    event = _last_run_failed(out)
    assert event.payload["failure_owner"] == PRODUCT
    assert event.payload["product_failed"] == ["cross_tenant_404"]
    assert event.payload["factory_owed"] == ["authorship_floor"]


def test_an_unitemised_status_is_not_attributed_on_a_guess(tmp_path):
    """A workflow that predates the itemised status: fail closed as before,
    and say that the check could not be named -- never route to the factory
    lane on a fabricated line."""
    out = _handoff(tmp_path)
    result = ingest_n3_store_gate(
        out,
        wait=False,
        env=ENV,
        opener=StatusOpener(
            state="failure",
            description=f"acceptance.py in Docker {ACCEPTANCE_REQUIRED - 1}/{ACCEPTANCE_REQUIRED}",
        ),
    )
    assert result.honesty == N3_STORE_GATE_FAILED
    assert "did not itemise" in result.detail


def test_a_full_pass_is_still_green_and_owes_nothing(tmp_path):
    out = _handoff(tmp_path)
    result = ingest_n3_store_gate(
        out,
        wait=False,
        env=ENV,
        opener=StatusOpener(
            state="success",
            description=f"acceptance.py in Docker {ACCEPTANCE_REQUIRED}/{ACCEPTANCE_REQUIRED}",
        ),
    )
    assert result.honesty == N3_STORE_GATE_GREEN
    assert result.ok is True
    report = json.loads((out / "docs" / "store_acceptance.json").read_text(encoding="utf-8"))
    assert report["factory_owed"] == []
    assert report["product_ok"] is True
