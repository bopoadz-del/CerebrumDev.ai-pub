"""F-8b: the UI grader script is blueprint-derived and a skipped run fails."""

from __future__ import annotations

import json

import pytest

from app.factory.build.product_grader import ui_grader as ug

BPS = [
    {"product_id": "lettings-desk", "product_name": "Lettings Desk", "summary": "tenancy files",
     "ui_modules": ["tenant_portal"], "capabilities": [{"id": "rent_roll", "description": "Roll rent."}, {"id": "notice_log", "description": "Log notices."}]},
    {"product_id": "site-ops", "product_name": "Site Ops", "summary": "inspections",
     "ui_modules": ["command_center", "map"], "capabilities": [{"id": "defect_register", "description": "Raise defects."}]},
]


def _report(names, failed=()):
    return {"suites": [{"specs": [{"title": n, "tests": [{"results": [{"status": "failed" if n in failed else "passed"}]}]} for n in names]}]}


@pytest.mark.parametrize("bp", BPS)
def test_plan_covers_every_declared_route_and_screen(bp):
    names = [s.name for s in ug.ui_plan(bp)]
    for c in bp["capabilities"]:
        assert f"route:/v1/{c['id']}" in names
    for m in bp["ui_modules"]:
        assert f"screen:{m}" in names
    assert {s.kind for s in ug.ui_plan(bp)} >= set(ug.STEP_KINDS)


def test_two_blueprints_two_scripts():
    a, b = (ug.render_spec(bp) for bp in BPS)
    assert a != b
    assert "rent_roll" in a and "rent_roll" not in b


@pytest.mark.parametrize("bp", BPS)
def test_all_passed_is_ok(bp):
    g = ug.parse_report(_report([s.name for s in ug.ui_plan(bp)]), bp)
    assert g.ok


@pytest.mark.parametrize("bp", BPS)
def test_failed_or_missing_step_is_named(bp):
    names = [s.name for s in ug.ui_plan(bp)]
    g = ug.parse_report(_report(names[1:], failed=[names[-1]]), bp)
    assert not g.ok and g.failing == [names[0], names[-1]]


@pytest.mark.parametrize("base_url", [None, ""])
def test_grader_mutant_skip_ui_run_fails(tmp_path, base_url):
    g = ug.grade_ui(BPS[0], tmp_path, base_url=base_url)
    assert not g.ok and not g.ran


def test_run_without_report_fails(tmp_path):
    g = ug.grade_ui(BPS[1], tmp_path, base_url="http://x", run=lambda *a, **k: None)
    assert not g.ok


def test_run_with_report_is_parsed(tmp_path):
    bp = BPS[1]

    def fake(cmd, **kw):
        names = [s.name for s in ug.ui_plan(bp)]
        open(kw["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"], "w").write(json.dumps(_report(names)))

    g = ug.grade_ui(bp, tmp_path, base_url="http://x", run=fake)
    assert g.ok
    assert (tmp_path / "fixtures" / "fixture.pdf").read_bytes().startswith(b"%PDF")
    assert (tmp_path / "fixtures" / "fixture.xlsx").is_file()


@pytest.mark.parametrize("bp", BPS)
def test_fixtures_come_from_blueprint(tmp_path, bp):
    fx = ug.write_fixtures(bp, tmp_path)
    assert bp["product_name"].encode() in fx["pdf"].read_bytes()
