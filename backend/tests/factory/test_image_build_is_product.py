"""A product image that does not build is the PRODUCT's Store-gate failure.

Live 2026-10-06 (build/plt_50b41be2cb184931): the writer's Dockerfile ran the
test suite inside ``docker build``; the Factory-rendered release gate refused
without pytest and the image never built. The gate run had no score, so the
Factory read it as "the Factory's own workflow failed before scoring" -- a
Factory defect -- and the writer was never told. The gate now scores a failed
image build (the floor's ``stage: image`` checks FAIL, the rest NOT_RUN, the
log tail as evidence), and the Factory routes that to a writer rework.
"""

from __future__ import annotations

import ast
import io
import json
import zipfile
from urllib.request import Request

from app.factory.build.acceptance_floor import (
    PRODUCT,
    STAGE_IMAGE,
    checks,
    image_check_ids,
    owner_of,
    subject_of,
)
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.n3_store_gate import (
    STORE_GATE_ARTIFACT_FILE,
    BuildsTarget,
    apply_store_gate_failure,
    fetch_store_gate_status,
    report_from_snapshot,
    report_from_store_gate_payload,
    store_gate_verdict,
)
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
    NOT_RUN,
    render_acceptance_script,
)

SHA = "5c44844f5be343568cd6cdc856f036c4aaaaaaaa"
ENV = {
    "CEREBRUM_BUILDS_GITHUB_TOKEN": "builds-test-token",
    "CEREBRUM_BUILDS_REPO": "bopoadz-del/cerebrum-builds",
}
#: The live build log's shape: the failing instruction names a script the
#: Factory renders. It is EVIDENCE, never the detail ownership reads.
LOG_TAIL = (
    "#16 [8/9] RUN python3 scripts/release_gate.py\n"
    "#16 0.301 pytest is not installed, so the suite cannot be run.\n"
    "#16 0.301 VERDICT: CANNOT RUN\n"
    "ERROR: failed to build: exit code: 2"
)


def _image_failed_payload() -> dict:
    """store_gate.json exactly as the gate writes it for a failed image build
    (cerebrum-builds .github/store_gate/score_failed_build.py + the parse step)."""
    image = set(image_check_ids())
    lines = []
    for name in ACCEPTANCE_CHECK_NAMES:
        if name in image:
            lines.append(
                {
                    "name": name,
                    "status": "FAIL",
                    "detail": "the image did not build from Dockerfile "
                    "(docker build exit 1); nothing could run inside it",
                    "evidence": LOG_TAIL,
                }
            )
        else:
            lines.append(
                {"name": name, "status": NOT_RUN, "detail": "not measured: the image did not build"}
            )
    return {
        "schema": "store_gate.v2",
        "passed": 0,
        "total": ACCEPTANCE_REQUIRED,
        "ok": False,
        "score": f"0/{ACCEPTANCE_REQUIRED}",
        "lines": lines,
        "via": "image-build-failed",
    }


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


class _GateOpener:
    """GitHub as the gate leaves it: a red status, and the typed artifact."""

    def __init__(self, description: str, payload: dict | None):
        self.description = description
        self.payload = payload

    def __call__(self, req: Request, timeout=None):
        url = req.full_url
        if "/statuses" in url:
            return _Resp(
                200,
                [{"context": "store-gate", "state": "failure", "description": self.description}],
            )
        if "/actions/artifacts?" in url:
            if self.payload is None:
                return _Resp(200, {"artifacts": []})
            return _Resp(
                200,
                {
                    "artifacts": [
                        {
                            "expired": False,
                            "workflow_run": {"head_sha": SHA},
                            "archive_download_url": "https://example.invalid/zip",
                        }
                    ]
                },
            )
        if url == "https://example.invalid/zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                zf.writestr(STORE_GATE_ARTIFACT_FILE, json.dumps(self.payload))
            return _Resp(200, buf.getvalue())
        raise AssertionError(f"unexpected request {url}")


def _target() -> BuildsTarget:
    return BuildsTarget(
        owner="bopoadz-del", repo="cerebrum-builds", branch="build/plt_0000000000000000", sha=SHA
    )


# -- the floor declares which checks a failed image build fails --------------


def test_the_floor_declares_the_image_stage_on_a_product_owned_check():
    image = image_check_ids()
    assert image, "no check declares stage: image"
    for cid in image:
        assert subject_of(cid) == "runtime"
        assert owner_of(cid, "the image did not build from Dockerfile") == PRODUCT


def test_the_harness_carries_the_floors_image_checks():
    tree = ast.parse(render_acceptance_script())
    consts = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"IMAGE_CHECKS", "CHECKS", "REQUIRED"}
    }
    assert consts["IMAGE_CHECKS"] == list(image_check_ids())
    assert set(consts["IMAGE_CHECKS"]) <= set(consts["CHECKS"])
    assert consts["REQUIRED"] == ACCEPTANCE_REQUIRED


def test_the_image_check_tells_the_writer_what_to_build():
    for row in checks():
        if row.get("stage") == STAGE_IMAGE:
            line = str(row["brief_render"])
            assert line.startswith(f"- {row['id']}:")
            assert "never runs the test suite" in line
            assert "dev dependencies" in line
            assert "tests run in CI" in line


# -- the gate's scored failure is the product's -------------------------------


def test_a_failed_image_build_is_a_scored_product_failure_not_a_gate_fault():
    image = list(image_check_ids())
    snap = fetch_store_gate_status(
        _target(),
        env=ENV,
        opener=_GateOpener(
            f"acceptance.py in Docker 0/{ACCEPTANCE_REQUIRED} FAIL:{','.join(image)}",
            _image_failed_payload(),
        ),
    )
    assert snap.harness_ran is True
    report = report_from_snapshot(snap)
    failed = [line.name for line in report.lines if line.failed]
    assert failed == image
    for line in report.lines:
        if line.name in image:
            assert line.owner == PRODUCT
            assert "release_gate.py" in line.evidence
        else:
            assert line.status == NOT_RUN and not line.failed and not line.satisfied
    # NOT_RUN starves no one: nothing is owed by the Factory, and the product
    # is not certified either.
    assert report.factory_owed == []
    assert report.product_ok is False


def test_evidence_naming_a_factory_script_never_moves_ownership():
    # The same line with the log tail in DETAIL would be read as the Factory's
    # (the detail names scripts/release_gate.py, which the Factory renders) --
    # which is why the gate keeps the tail in evidence.
    cid = image_check_ids()[0]
    assert owner_of(cid, LOG_TAIL) != PRODUCT
    report = report_from_store_gate_payload(_image_failed_payload())
    assert [line.owner for line in report.lines if line.failed] == [PRODUCT]


def test_an_unscored_run_is_still_the_gates_own_fault():
    # Checkout, runner, a Postgres service: no score at all -> harness_ran is
    # False and no product check is blamed.
    snap = fetch_store_gate_status(
        _target(),
        env=ENV,
        opener=_GateOpener("acceptance.py in Docker no score (the image or the script did not run)", None),
    )
    assert snap.harness_ran is False


# -- the runner rule gets a typed rework item ---------------------------------


def test_the_product_owned_image_fail_reaches_the_writer_as_a_typed_rework_item(tmp_path):
    image = list(image_check_ids())
    snap = fetch_store_gate_status(
        _target(),
        env=ENV,
        opener=_GateOpener(
            f"acceptance.py in Docker 0/{ACCEPTANCE_REQUIRED} FAIL:{','.join(image)}",
            _image_failed_payload(),
        ),
    )
    report = report_from_snapshot(snap)
    apply_store_gate_failure(tmp_path, snap, honesty="N3_STORE_GATE_FAILED", report=report)

    terminal = [e for e in BuildLedger(tmp_path / "build_ledger.jsonl").events() if e.kind is EventKind.RUN_FAILED][-1]
    assert terminal.payload["failure_owner"] == PRODUCT
    assert terminal.payload["product_failed"] == image
    assert set(terminal.payload["product_failed_evidence"]) == set(image)

    verdict = store_gate_verdict(terminal.payload)
    assert verdict is not None and verdict.ok is False
    assert verdict.payload["finding_checks"] == image
    item = verdict.findings[0]
    assert item.startswith(f"[{image[0]}] Store gate FAIL")
    assert "Dockerfile" in item
    assert "never runs the test suite" in item  # what to build, from the floor
    assert "VERDICT: CANNOT RUN" in item  # the gate's evidence tail
    assert "--self-check" in item  # the re-check command


# -- the writer's self-check: SKIP with a reason, never a local measurement ---


def test_the_self_check_never_measures_the_image_itself():
    # Owner rule: gate-only legs print SKIP+reason locally, never PASS -- and
    # never a local docker run either, daemon or not. The harness spawns no
    # process; the reason carries the build command as advice.
    src = render_acceptance_script()
    assert "_self_check_image_build" not in src
    assert '"docker", "build"' not in src
    assert "docker build ." in src  # advice in the SKIP reason
