"""A product must boot even when its request log cannot be opened.

Live 2026-10-07 (01a1eed7, co-op repro, build/plt_0b355947d7a1494e): the
Factory-stamped app/observe.py opened STORAGE_PATH/request.jsonl at import
and the gate's container died with ``PermissionError: [Errno 13] Permission
denied: '/app/storage/request.jsonl'`` before acceptance.py ran. Observability
never stops the service: /health reports the disk honestly instead.

A boot crash is owned by whoever wrote the crashing file, read from the
build's factory receipt -- the Factory-stamped deploy modules are on it.
"""

from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

import pytest

from app.factory.build import deploy
from app.factory.build.acceptance_floor import FACTORY, PRODUCT, checks, image_check_ids
from app.factory.build.factory_receipt import RECEIPT_REL, factory_stamped_paths, record_receipt
from app.factory.build.n3_store_gate import ROW_BOOT_CRASH, split_image_by_origin
from app.factory.build.store_acceptance import AcceptanceLine, AcceptanceReport, finalize_owners


def _render_app(root: Path) -> None:
    pkg = root / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    for rel, text in deploy.deploy_substrate():
        if rel.startswith("app/"):
            (root / rel).write_text(text, encoding="utf-8")


@pytest.fixture
def product(tmp_path, monkeypatch):
    root = tmp_path / "product"
    _render_app(root)
    storage = tmp_path / "storage"
    storage.mkdir()
    # The request log cannot be opened: a directory sits where the file goes.
    (storage / "request.jsonl").mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.syspath_prepend(str(root))
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        monkeypatch.delitem(sys.modules, name)
    root_logger = logging.getLogger()
    saved = list(root_logger.handlers)
    yield root
    root_logger.handlers[:] = saved
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        sys.modules.pop(name, None)


def test_an_unopenable_request_log_never_stops_the_service(product, capsys):
    observe = importlib.import_module("app.observe")
    observe.configure_logging()  # raised on master: the container died here
    out = capsys.readouterr().out
    assert "request log disabled" in out
    assert all(not isinstance(h, logging.FileHandler) for h in logging.getLogger().handlers)


def test_the_app_still_boots_and_health_answers_per_its_contract(product):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    observe = importlib.import_module("app.observe")
    health = importlib.import_module("app.health")
    app = FastAPI()
    observe.install_observability(app)

    @app.get("/health")
    def _health():
        return health.health_response()

    response = TestClient(app).get("/health")
    body = response.json()
    assert response.status_code in (200, 503)
    assert [c["name"] for c in body["checks"]][: len(deploy.HEALTH_CHECK_NAMES)] == list(
        deploy.HEALTH_CHECK_NAMES
    )
    assert body["status"] in (deploy.HEALTH_STATUS_OK, deploy.HEALTH_STATUS_NOT_READY)


def test_a_writable_disk_still_gets_the_request_log(tmp_path, monkeypatch):
    root = tmp_path / "product"
    _render_app(root)
    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.syspath_prepend(str(root))
    for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
        monkeypatch.delitem(sys.modules, name)
    saved = list(logging.getLogger().handlers)
    try:
        importlib.import_module("app.observe").configure_logging()
        handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.FileHandler)]
        assert handlers and Path(handlers[0].baseFilename) == storage / "request.jsonl"
        for h in handlers:
            h.close()
    finally:
        logging.getLogger().handlers[:] = saved
        for name in [m for m in sys.modules if m == "app" or m.startswith("app.")]:
            sys.modules.pop(name, None)


def test_the_receipt_records_the_stamped_deploy_modules(tmp_path):
    _render_app(tmp_path)
    receipt = record_receipt(tmp_path)
    for rel in deploy.FACTORY_OWNED_DEPLOY_MODULES:
        assert rel in factory_stamped_paths()
        assert rel in receipt["files"], rel
    assert (tmp_path / RECEIPT_REL).is_file()


def _crash_report(crash_file: str) -> AcceptanceReport:
    image = image_check_ids()[0]
    lines = [
        AcceptanceLine(
            name=image,
            status="FAIL",
            detail="the container died at boot",
            evidence_rows=[
                {"kind": ROW_BOOT_CRASH, "file": crash_file, "line": 1, "text": "PermissionError"}
            ],
        )
    ]
    return finalize_owners(AcceptanceReport(passed=0, total=1, lines=lines))


def test_a_boot_crash_in_factory_stamped_code_is_factory_owed(tmp_path):
    _render_app(tmp_path)
    record_receipt(tmp_path)
    report = _crash_report("app/observe.py")
    moved = split_image_by_origin(tmp_path, report)
    assert moved == [image_check_ids()[0]]
    assert report.lines[0].owner == FACTORY
    assert report.factory_owed == [image_check_ids()[0]]


def test_a_boot_crash_in_writer_code_stays_the_products(tmp_path):
    _render_app(tmp_path)
    (tmp_path / "app" / "main.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    record_receipt(tmp_path)
    report = _crash_report("app/main.py")
    assert split_image_by_origin(tmp_path, report) == []
    assert report.lines[0].owner == PRODUCT
    assert "app/main.py" in report.lines[0].evidence


def test_an_image_line_without_crash_rows_keeps_its_owner(tmp_path):
    record_receipt(tmp_path)
    image = image_check_ids()[0]
    report = finalize_owners(
        AcceptanceReport(lines=[AcceptanceLine(name=image, status="FAIL", detail="docker build exit 1")])
    )
    before = report.lines[0].owner
    assert split_image_by_origin(tmp_path, report) == []
    assert report.lines[0].owner == before


def test_the_floor_tells_the_writer_the_persistence_root_must_be_writable():
    line = next(c for c in checks() if c["id"] == "single_persistence_root")
    assert "writable by the user the service runs as" in line["requirement_text"]
    assert line["brief_render"] == f"- single_persistence_root: {line['requirement_text']}"
    json.dumps(line)
