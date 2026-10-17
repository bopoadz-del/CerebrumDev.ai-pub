"""Session-scoped Design Product API — Steward golden path."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.session_store import create_session, get_session, update_session
from tests.factory.blocks_root import real_blocks_root


ROOT = Path(__file__).resolve().parents[3]
BLOCKS = real_blocks_root()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(BLOCKS))
    monkeypatch.setenv("ENV", "test")
    # auth module caches the key at import time — clear the cached value for tests
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # No master key now means "refuse", not "let everyone in", so an
    # unauthenticated fixture has to ask for the dev principal.
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def _confirm_level(session_id: str, level: str) -> None:
    """The user's level, confirmed on the Floor's intake line (the typed
    confirm_intake action) -- the build refuses to start without one."""
    from app.factory.locale_choice import confirm_intake

    state = get_session(session_id)
    assert state is not None
    state.product_design.intake_proposal = {"build_level": level}
    confirm_intake(state.product_design)
    update_session(session_id, state)


def test_session_product_steward_golden_flow(client, monkeypatch, tmp_path, stub_coder):
    """The TEMPLATE path's golden flow (provenance.json, kernel ActionOutcome
    actions, command_center.tsx) -- all template-only artifacts.

    Pinned to that engine. Note for the next reader: before pinning, this
    test PASSED locally and failed in CI, because a stale output directory
    from an earlier template-era run still satisfied the assertions on a
    developer machine while a fresh CI container told the truth. The runner
    path's session contract is covered by test_production_uses_the_runner.py
    and the build-status endpoint test below.
    """
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")
    # Goldens are chosen by structural overlap, not words. The deterministic
    # draft shares only its vertical with the golden, so this flow lowers
    # the configured threshold to let that unique overlap select it.
    monkeypatch.setenv("FACTORY_GOLDEN_MIN_OVERLAP", "0.05")
    create_session("sess_product_1", "tester")
    out = tmp_path / "gen"
    # draft
    r = client.post(
        "/v1/sessions/sess_product_1/product/draft",
        json={
            "brief": "Generate Projectsix private estate operations",
            "vertical_hint": "estate",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["blueprint"]["product_id"] == "projectsix"
    assert body["source"] == "golden"

    r = client.post("/v1/sessions/sess_product_1/product/plan")
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert plan["product_id"] == "projectsix"
    strategies = {c["capability_id"]: c["strategy"] for c in plan["capabilities"]}
    assert strategies["estate_registry"] == "COMPOSE"
    assert "UNSUPPORTED" not in strategies.values()

    _confirm_level("sess_product_1", "prototype")
    r = client.post(
        "/v1/sessions/sess_product_1/product/approve", json={"approve": True}
    )
    assert r.status_code == 200

    # No caller-supplied output_dir: an absolute path chosen by the client was
    # a recursive-delete primitive, so the server picks the location and the
    # test asserts against what it reports back.
    r = client.post(
        "/v1/sessions/sess_product_1/product/generate",
        json={},
    )
    assert r.status_code == 200, r.text
    gen = r.json()["generation"]
    assert gen["product_id"] == "projectsix"
    out = Path(gen["output_dir"])
    assert (out / "app" / "main.py").exists()
    assert (out / "docs" / "provenance" / "provenance.json").exists()
    # kernel-shaped action (not bare echo-only)
    action = (out / "app" / "actions" / "estate_registry.py").read_text()
    assert "ActionOutcome" in action
    assert "ActionEvidence" in action
    ui = (out / "frontend" / "src" / "modules" / "command_center.tsx").read_text()
    assert "CAPABILITIES" in ui
    assert "data-module" in ui

    st = client.get("/v1/sessions/sess_product_1/product")
    assert st.status_code == 200
    assert st.json()["blueprint_approved"] is True
    assert st.json()["generation"]["inputs_hash"]


def test_generate_requires_approval(client):
    create_session("sess_product_2", "tester")
    client.post(
        "/v1/sessions/sess_product_2/product/draft",
        json={"brief": "Build steward estate platform"},
    )
    r = client.post("/v1/sessions/sess_product_2/product/generate", json={})
    assert r.status_code == 400

def test_runner_build_reports_progress_and_gates_the_download(client, monkeypatch):
    """The production HTTP contract for a runner build.

    The UI depends on exactly this: generate returns immediately with a
    building state, build-status reports progress off the ledger, and the
    package endpoint refuses (409) until the build has passed its gates --
    never handing over a half-written tree.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.delenv("FACTORY_BUILD_ENGINE", raising=False)
    create_session("sess_runner_1", "tester")

    r = client.post(
        "/v1/sessions/sess_runner_1/product/draft",
        json={"brief": "Build a warehouse operations platform"},
    )
    assert r.status_code == 200, r.text
    assert client.post("/v1/sessions/sess_runner_1/product/plan").status_code == 200
    _confirm_level("sess_runner_1", "prototype")
    assert (
        client.post(
            "/v1/sessions/sess_runner_1/product/approve", json={"approve": True}
        ).status_code
        == 200
    )

    r = client.post("/v1/sessions/sess_runner_1/product/generate", json={})
    assert r.status_code == 200, r.text
    gen = r.json()["generation"]
    # The client must be able to tell "started" from "finished".
    assert gen["engine"] == "runner"
    assert gen["build"]["state"] in ("building", "succeeded", "failed")

    status_res = client.get("/v1/sessions/sess_runner_1/product/build-status")
    assert status_res.status_code == 200, status_res.text
    build = status_res.json()["build"]
    assert build["state"] in ("building", "succeeded", "failed")
    assert build["phases_total"] == 5

    # While building, the download must be refused rather than shipping a
    # splice of two writer passes. Re-read the state alongside the call
    # instead of trusting the earlier read: the build runs on a background
    # thread and can finish between the two, which made an earlier version
    # of this assertion flaky (it demanded 409 from an already-finished
    # build). The invariant is the PAIRING, not a fixed status code.
    pkg = client.get("/v1/sessions/sess_runner_1/product/package")
    state_now = client.get("/v1/sessions/sess_runner_1/product/build-status").json()[
        "build"
    ]["state"]
    if pkg.status_code == 409:
        detail = pkg.json()["detail"]
        assert ("still being built" in detail) or ("did not pass its gates" in detail)
        assert state_now in ("building", "failed", "stalled"), state_now
    else:
        # The only way a download may succeed is a build that passed.
        assert pkg.status_code == 200, pkg.text
        assert state_now == "succeeded", state_now


def test_stalled_build_cannot_be_downloaded(client, tmp_path):
    """A dead runner thread must not ship a torn tree.

    build_status reports "stalled" after the ledger goes quiet; the package
    endpoint used to only refuse building/failed, so a stalled artifact
    zipped as if it were a finished product.
    """
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import _STALL_AFTER_S

    create_session("sess_stalled_dl", "tester")
    out = tmp_path / "stalled-product"
    out.mkdir()
    (out / "README.md").write_text("torn", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="stalled-product", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    old = time.time() - (_STALL_AFTER_S + 120)
    os.utime(out / "build_ledger.jsonl", (old, old))

    state = get_session("sess_stalled_dl")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "stalled-product",
        "inputs_hash": "abc",
        "engine": "runner",
    }
    update_session("sess_stalled_dl", state)

    status = client.get("/v1/sessions/sess_stalled_dl/product/build-status")
    assert status.status_code == 200, status.text
    assert status.json()["build"]["state"] == "stalled"

    pkg = client.get("/v1/sessions/sess_stalled_dl/product/package")
    assert pkg.status_code == 409, pkg.text
    detail = pkg.json()["detail"]
    assert "will not be shipped" in detail
    assert "full-pilot" in detail or "stalled" in detail.lower()


def test_product_get_rereads_terminal_ledger_onto_generation_build(client, tmp_path):
    """GET /product must not keep the session-start building snapshot.

    Live sess_14e690829d1f4282: generation.build stayed state=building,
    current_phase=COLLECTOR, phases_done=0, last_event_age_s≈0.7 while
    Floor correctly showed CODING AGENT STOPPED off the live ledger.
    """
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import BuildLedger, EventKind

    create_session("sess_frozen_gen", "tester")
    out = tmp_path / "veterinary-care"
    out.mkdir()
    (out / "README.md").write_text("torn", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="veterinary-care", inputs_hash="vet-hash")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.COLLECTOR, detail="COLLECTOR")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.COLLECTOR, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.CLONER, detail="CLONER")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.CLONER, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(EventKind.GATE_PASSED, role=BuildRole.WRITER, detail="ok")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
    ledger.append(EventKind.GATE_FAILED, role=BuildRole.TESTER, detail="pilot red")
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.TESTER,
        detail=(
            "rework budget of 3 exhausted; TESTER gate still failing: "
            "PRODUCT (pilot-marked suite): suite is red: schema sample refused; "
            "schema sample refused (event_bus workflow step); "
            "accept-payload persisted nothing — FAILED "
            "tests/test_routes.py::test_every_capability_route_accepts_payload"
        ),
        payload={"cycle": "pilot", "outcome": "FAILED_BUDGET_SPENT", "rework_used": 3},
    )

    state = get_session("sess_frozen_gen")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "veterinary-care",
        "inputs_hash": "vet-hash",
        "engine": "runner",
        "build": {
            "state": "building",
            "current_phase": {"id": "COLLECTOR", "label": "Binding surveyor"},
            "phases_done": 0,
            "phases_total": 5,
            "last_event_age_s": 0.7,
            "stale": False,
        },
        "phases_done": 0,
    }
    update_session("sess_frozen_gen", state)

    product = client.get("/v1/sessions/sess_frozen_gen/product")
    assert product.status_code == 200, product.text
    gen = product.json()["generation"]
    assert gen["build"]["state"] == "failed"
    assert gen["build"]["phases_done"] >= 2
    assert gen["phases_done"] == gen["build"]["phases_done"]
    assert "rework budget of 3 exhausted" in str(gen["build"].get("detail") or "")

    status = client.get("/v1/sessions/sess_frozen_gen/product/build-status")
    assert status.status_code == 200
    assert status.json()["build"]["state"] == "failed"


def test_product_export_omits_tester_caches(tmp_path):
    """shutil.make_archive shipped the TESTER's __pycache__ and .pytest_cache
    in the live winery-hospitality zip (146 files, lots of bytecode)."""
    import zipfile

    from app.routers.session_product import zip_generated_product

    out = tmp_path / "winery-hospitality"
    (out / "app").mkdir(parents=True)
    (out / "app" / "main.py").write_text("ok\n", encoding="utf-8")
    (out / "app" / "__pycache__").mkdir()
    (out / "app" / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00")
    (out / ".pytest_cache").mkdir()
    (out / ".pytest_cache" / "CACHEDIR.TAG").write_text("tag\n", encoding="utf-8")
    (out / "README.md").write_text("hi\n", encoding="utf-8")

    zpath = zip_generated_product(out, tmp_path / "winery-hospitality-export")
    names = zipfile.ZipFile(zpath).namelist()
    assert "README.md" in names
    assert "app/main.py" in names
    assert not any("__pycache__" in n for n in names)
    assert not any(".pytest_cache" in n for n in names)
    assert not any(n.endswith(".pyc") for n in names)


def test_product_export_zips_epoch_zero_files(tmp_path):
    """Kit/kernel copies in this environment have mtime 0; ZIP forbids that."""
    import os
    import zipfile

    from app.routers.session_product import zip_generated_product

    out = tmp_path / "product"
    (out / "kits" / "platform").mkdir(parents=True)
    target = out / "kits" / "platform" / "manifest.json"
    target.write_text("{}\n", encoding="utf-8")
    os.utime(target, (0, 0))
    zpath = zip_generated_product(out, tmp_path / "epoch-export")
    names = zipfile.ZipFile(zpath).namelist()
    assert "kits/platform/manifest.json" in names


def test_product_export_zip_lists_app_blocks_and_kits(tmp_path):
    """The Floor download must look like a Factory product tree.

    The live winery-hospitality zip had app/ + vendor/ and no kits/. A
    cache-free zipper is not enough — generation must stock kit packs
    (and vendor/blocks) so the next export is a platform, not a runner.
    """
    import zipfile

    from app.factory.blueprint import load_blueprint
    from app.factory.generator import ProductGenerator
    from app.routers.session_product import zip_generated_product

    bp = load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    out = tmp_path / "winery-shaped"
    ProductGenerator(
        bp, blocks_root=BLOCKS, factory_commit="t", blocks_commit="t"
    ).generate(out)
    zpath = zip_generated_product(out, tmp_path / "winery-hospitality-export")
    names = zipfile.ZipFile(zpath).namelist()
    tops = {n.split("/")[0] for n in names}
    assert "app" in tops
    assert any(n.startswith("vendor/blocks/") for n in names) or "blocks" in tops
    assert any(n.startswith("kits/") for n in names)
    assert any(n.endswith("manifest.json") and n.startswith("kits/") for n in names)


def _failed_workspace(tmp_path, session_id: str, detail: str):
    """A terminal-failed runner workspace wired into a session."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import BuildLedger, EventKind

    create_session(session_id, "tester")
    out = tmp_path / f"{session_id}-product"
    out.mkdir()
    (out / "README.md").write_text("the tree as the gates saw it", encoding="utf-8")
    (out / "docs").mkdir()
    (out / "docs" / "writer_prompt.txt").write_text("in-house", encoding="utf-8")
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=session_id, inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER, detail="TESTER")
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.TESTER,
        detail=detail,
        payload={"outcome": "FAILED_GATE", "findings": [detail]},
    )
    state = get_session(session_id)
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": session_id,
        "inputs_hash": "abc",
        "engine": "runner",
    }
    update_session(session_id, state)
    return out


def test_failed_build_downloads_as_is_only_when_asked(client, tmp_path):
    """Owner's order: a gate-failed build IS downloadable -- labeled, never
    certified, and only on the explicit ``as_is`` ask. The default refusal
    stays exactly as it was."""
    import io
    import zipfile as _zipfile

    detail = "TESTER gate 'suite_green' failed: suite is red"
    _failed_workspace(tmp_path, "sess_asis_dl", detail)

    status = client.get("/v1/sessions/sess_asis_dl/product/build-status")
    assert status.json()["build"]["state"] == "failed"

    # Default contract unchanged: refused.
    pkg = client.get("/v1/sessions/sess_asis_dl/product/package")
    assert pkg.status_code == 409, pkg.text

    # Explicit as-is: shipped, loudly labeled, no certification paperwork.
    pkg = client.get("/v1/sessions/sess_asis_dl/product/package?as_is=1")
    assert pkg.status_code == 200, pkg.text
    assert "FAILED-GATES-as-is" in pkg.headers.get("content-disposition", "")
    zf = _zipfile.ZipFile(io.BytesIO(pkg.content))
    names = set(zf.namelist())
    assert "EXPORTED-AS-IS.md" in names, "the as-is marker must ship in the zip"
    note = zf.read("EXPORTED-AS-IS.md").decode("utf-8")
    assert "suite is red" in note, "the marker names the gate that rejected it"
    # Owner rule (2026-10-05): a failed build is still a product. Its zip
    # carries a MANIFEST.json that says so -- certified:false and the exact
    # failing gate/check/finding -- never a certification claim.
    import json as _json

    manifest = _json.loads(zf.read("MANIFEST.json"))
    assert manifest["certified"] is False
    assert manifest["failed"]["gate"] == "TESTER"
    assert "suite is red" in manifest["failed"]["finding"]
    assert "release_tag" not in manifest and "ci_run" not in manifest
    # The in-house exclusions still hold: same is_exported() filter.
    assert "docs/writer_prompt.txt" not in names
    assert "README.md" in names


def test_building_is_never_downloadable_even_as_is(client, tmp_path):
    """Mid-build is not a build: a splice of two writer passes has no honest
    as-is to ship. The flag changes labeling, never this."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import BuildLedger, EventKind

    create_session("sess_asis_building", "tester")
    out = tmp_path / "asis-building-product"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="asis-building", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    state = get_session("sess_asis_building")
    assert state is not None
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "asis-building",
        "inputs_hash": "abc",
        "engine": "runner",
    }
    update_session("sess_asis_building", state)

    pkg = client.get("/v1/sessions/sess_asis_building/product/package?as_is=1")
    assert pkg.status_code == 409, pkg.text
