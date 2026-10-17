"""One place decides the backend, and /metrics is measured not assumed.

A live export read ``DATABASE_URL`` in the generated kernel's config and used
it nowhere: ``app/store.py`` was ``import sqlite3`` against a hardcoded path.
An operator setting the variable got a platform that accepted it without
complaint and wrote SQLite onto the container disk -- worse than not
supporting Postgres, because it looks supported.

The Factory cannot fix that by writing ``store.py``; the coder authors that
file and overwriting it destroys the capability work. So the Factory owns
``app/db.py`` and the floor obliges the author to route through it.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib

from app.factory.build.acceptance_floor import checks
from app.factory.build.data_lifecycle import emit_writer_artifacts, platform_substrate
from app.factory.build.engine_switch import render_db_module
from app.factory.build.store_acceptance import render_acceptance_script


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _Http:
    def __init__(self, status_code=200, text=""):
        self._resp = _Resp(status_code, text)

    def request(self, *_a, **_k):
        return self._resp


def _harness(root: pathlib.Path):
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "acceptance.py").write_text(
        render_acceptance_script(), encoding="utf-8"
    )
    spec = importlib.util.spec_from_file_location(
        "acc_" + root.name, root / "scripts" / "acceptance.py"
    )
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    return module


def _product(root: pathlib.Path, store_src: str, *, with_db: bool = True):
    (root / "app").mkdir(parents=True, exist_ok=True)
    if with_db:
        (root / "app" / "db.py").write_text(render_db_module(), encoding="utf-8")
    (root / "app" / "store.py").write_text(store_src, encoding="utf-8")
    return _harness(root)


class TestTheFactoryShipsTheSwitch:
    def test_db_module_is_valid_python(self):
        compile(render_db_module(), "app/db.py", "exec")

    def test_it_reaches_both_writer_paths(self):
        """backup.sh had to learn this the hard way: substrate added to the
        CodeWhale backfill only produced a build that the in-process writer
        could not satisfy."""
        import inspect

        assert any(rel == "app/db.py" for rel, _ in platform_substrate())
        assert "db.py" in inspect.getsource(emit_writer_artifacts)

    def test_it_refuses_rather_than_degrades(self):
        """engine() must not quietly fall back to SQLite -- that IS the bug."""
        source = render_db_module()
        assert "raise RuntimeError" in source
        assert "DATABASE_URL" in source


class TestTheGateMeasuresTheStore:
    def test_a_store_that_opens_its_own_database_fails(self, tmp_path):
        """The live defect, exactly."""
        module = _product(tmp_path, "import sqlite3\ndef connect():\n    return sqlite3.connect('x')\n")
        os.environ["STORE_POSTGRES_BOOT"] = "200"

        status, detail = module.check_postgres_boot_200(_Http())

        assert status == "FAIL"
        assert "read and ignored" in detail

    def test_a_store_that_routes_through_app_db_passes(self, tmp_path):
        module = _product(tmp_path, "from app.db import connect\ndef save():\n    pass\n")
        os.environ["STORE_POSTGRES_BOOT"] = "200"

        status, _ = module.check_postgres_boot_200(_Http())

        assert status == "PASS"

    def test_routing_and_also_opening_its_own_is_still_a_fail(self, tmp_path):
        """Two deciders disagree the moment one of them changes."""
        module = _product(
            tmp_path,
            "from app.db import connect\nimport sqlite3\ndef c():\n    return sqlite3.connect('x')\n",
        )
        os.environ["STORE_POSTGRES_BOOT"] = "200"

        status, detail = module.check_postgres_boot_200(_Http())

        assert status == "FAIL"
        assert "one place must decide" in detail

    def test_a_missing_switch_is_named(self, tmp_path):
        module = _product(tmp_path, "from app.db import connect\n", with_db=False)

        status, detail = module.check_postgres_boot_200(_Http())

        assert status == "FAIL"
        assert "app/db.py missing" in detail


class TestMetricsIsMeasuredNotInferred:
    def test_a_module_that_is_never_mounted_fails_and_says_so(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
        module = _harness(tmp_path)

        status, detail = module.check_metrics_served(_Http(404))

        assert status == "FAIL"
        assert "never calls mount_observability" in detail

    def test_mounted_but_silent_is_distinguished_from_not_mounted(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text(
            "from app.observability import mount_observability\nmount_observability(app)\n",
            encoding="utf-8",
        )
        module = _harness(tmp_path)

        _status, detail = module.check_metrics_served(_Http(500))

        assert "mount_observability is called but" in detail

    def test_200_without_a_count_is_not_a_pass(self, tmp_path):
        module = _harness(tmp_path)

        status, detail = module.check_metrics_served(_Http(200, "# nothing useful\n"))

        assert status == "FAIL"
        assert "no request count" in detail

    def test_counts_and_latency_pass(self, tmp_path):
        module = _harness(tmp_path)
        body = 'http_requests_total{path="/v1/x"} 3\nhttp_request_duration_seconds_total 0.5\n'

        status, _ = module.check_metrics_served(_Http(200, body))

        assert status == "PASS"


class TestTheFloorSaysWhatTheGateDoes:
    def _text(self, check_id: str) -> str:
        return next(c["requirement_text"] for c in checks() if c["id"] == check_id)

    def test_the_coder_is_told_to_route_through_app_db(self):
        text = self._text("postgres_boot_200")
        assert "app.db" in text
        assert "MUST NOT open its own database" in text

    def test_the_coder_is_told_mounting_is_the_bar(self):
        text = self._text("metrics_served")
        assert "mount_observability" in text
        assert "no mount, no pass" in text.lower()
