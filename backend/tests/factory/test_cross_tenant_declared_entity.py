"""The Store gate's cross_tenant_404 creates through a DECLARED persisted entity.

Live 2026-10-07 (9de69276, vineyard smoke build, Store gate 21/22): the only
FAIL was ``cross_tenant_404 -- tenant A create returned no stored id``. The
Factory-rendered route answers a refused create with HTTP 200 and
``{"ok": false, "error": ...}``; the harness took any 200 as a successful
create, indexed ``stored`` and reported "no stored id". The writer was handed
the tenancy line, never the product's refusal, and the build stopped
SAME_FAILURE_TWICE.

These tests run the RENDERED harness in a subprocess against an invented
product package (the harness imports ``app.*``, which in-process is the
Factory itself), with a fake HTTP client answering in the live shapes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.store_acceptance import render_acceptance_script

DRIVER = textwrap.dedent(
    '''
    import importlib.util, json, sys
    from pathlib import Path

    root = Path(sys.argv[1])
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("acc", root / "scripts" / "acceptance.py")
    acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acc)

    plan = json.loads((root / "plan.json").read_text())
    calls = []


    class Resp:
        def __init__(self, status, body, headers=None):
            self.status_code = status
            self._body = body
            self.headers = headers or {}

        def json(self):
            return self._body


    class Http:
        def request(self, method, path, json=None, headers=None, **_kw):
            calls.append([method, path, json])
            parts = path.strip("/").split("/")
            cap = parts[1]
            rule = plan[cap]
            if method == "post":
                for field, allowed in (rule.get("allowed") or {}).items():
                    if (json or {}).get(field) not in allowed:
                        return Resp(200, {"ok": False, "error": field + " must be one of",
                                          "rejected_field": field,
                                          "rejection_reason": "not_allowed",
                                          "allowed_values": allowed})
                if rule["post"] == "refuse":
                    return Resp(200, {"ok": False, "error": rule["error"]})
                return Resp(200, {"ok": True, "capability": cap, "result": {},
                                  "stored": {"id": 7, **(json or {})}})
            return Resp(rule.get("cross_read", 404), {})


    status, detail = acc.check_cross_tenant_404(Http())
    print(json.dumps({"status": status, "detail": detail, "calls": calls}))
    '''
)


def _product(tmp_path: Path, *, route_entities, models_src, plan, jobs=None) -> Path:
    root = tmp_path / "product"
    (root / "app").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "routes.py").write_text(
        "ROUTE_ENTITIES = %r\n" % (route_entities,), encoding="utf-8"
    )
    (root / "app" / "models.py").write_text(models_src, encoding="utf-8")
    (root / "app" / "placeholders.py").write_text(
        "PLACEHOLDER_CONNECTORS = {}\n", encoding="utf-8"
    )
    (root / "app" / "jobs.py").write_text(
        "CAPABILITIES = %r\n" % (jobs or [{"id": c} for c in plan],), encoding="utf-8"
    )
    (root / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (root / "scripts" / "acceptance.py").write_text(render_acceptance_script(), encoding="utf-8")
    (root / "driver.py").write_text(DRIVER, encoding="utf-8")
    return root


def _run(root: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, str(root / "driver.py"), str(root)],
        capture_output=True, text=True, timeout=120, cwd=str(root),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


MODELS = textwrap.dedent(
    '''
    from dataclasses import dataclass

    @dataclass
    class Tank:
        reference: str = ""
        status: str = ""
        FIELDS = ["reference", "status"]
        CONSTRAINTS = {"reference": {"required": True},
                       "status": {"allowed_values": ["open", "closed"], "required": True}}

    @dataclass
    class Barrel:
        reference: str = ""
        FIELDS = ["reference"]
        CONSTRAINTS = {"reference": {"required": True}}

    @dataclass
    class Board:
        FIELDS = []
        CONSTRAINTS = {}

    MODELS = {"tank_log": Tank, "barrel_book": Barrel, "cellar_board": Board}
    '''
)


def test_a_refused_create_is_never_read_as_no_stored_id(tmp_path):
    # The live shape: the first persisting capability answers 200 ok:false.
    root = _product(
        tmp_path,
        route_entities={"tank_log": "tank_entries", "barrel_book": "barrel_rows"},
        models_src=MODELS,
        plan={
            "tank_log": {"post": "refuse", "error": "block run failed: sensor feed absent"},
            "barrel_book": {"post": "accept"},
        },
    )
    result = _run(root)
    assert result["status"] == "PASS", result
    assert "barrel_book" in result["detail"]


def test_when_every_persisting_capability_refuses_the_fail_carries_the_refusal(tmp_path):
    root = _product(
        tmp_path,
        route_entities={"tank_log": "tank_entries"},
        models_src=MODELS,
        plan={"tank_log": {"post": "refuse", "error": "block run failed: sensor feed absent"}},
    )
    result = _run(root)
    assert result["status"] == "FAIL", result
    assert "block run failed: sensor feed absent" in result["detail"]
    assert "no stored id" not in result["detail"]


def test_a_read_only_capability_is_never_the_one_created_through(tmp_path):
    # cellar_board declares no persisted entity (None): never posted to.
    root = _product(
        tmp_path,
        route_entities={"cellar_board": None, "barrel_book": "barrel_rows"},
        models_src=MODELS,
        plan={"cellar_board": {"post": "accept"}, "barrel_book": {"post": "accept"}},
        jobs=[{"id": "cellar_board"}, {"id": "barrel_book"}],
    )
    result = _run(root)
    assert result["status"] == "PASS", result
    posted = [c[1] for c in result["calls"] if c[0] == "post"]
    assert posted == ["/v1/barrel_book"], posted


def test_no_persisting_capability_is_unjudged_not_failed(tmp_path):
    root = _product(
        tmp_path,
        route_entities={"cellar_board": None},
        models_src=MODELS,
        plan={"cellar_board": {"post": "accept"}},
    )
    result = _run(root)
    assert result["status"] == "SKIP", result
    assert "persisted entity" in result["detail"]
    assert not [c for c in result["calls"] if c[0] == "post"]


def test_the_payload_comes_from_the_shared_builder(tmp_path):
    # Required fields filled from the product's own model; a rejection stated
    # as data is corrected in the named field.
    root = _product(
        tmp_path,
        route_entities={"tank_log": "tank_entries"},
        models_src=MODELS,
        plan={"tank_log": {"post": "accept", "allowed": {"status": ["open", "closed"]}}},
    )
    result = _run(root)
    assert result["status"] == "PASS", result
    first_post = next(c for c in result["calls"] if c[0] == "post")
    assert first_post[2]["status"] == "open"
    assert first_post[2]["reference"]


def test_a_leaking_tenant_read_still_fails(tmp_path):
    root = _product(
        tmp_path,
        route_entities={"barrel_book": "barrel_rows"},
        models_src=MODELS,
        plan={"barrel_book": {"post": "accept", "cross_read": 200}},
    )
    result = _run(root)
    assert result["status"] == "FAIL", result
    assert "want 404" in result["detail"]


def test_the_route_and_the_check_share_one_create_contract():
    import inspect

    from app.factory.build import roles_handlers, store_acceptance
    from app.factory.build.rejection_contract import OK_KEY, RECORD_ID_KEY, STORED_RECORD_KEY

    harness = store_acceptance.render_acceptance_script()
    route_src = inspect.getsource(roles_handlers._templated_route_body)
    assert "STORED_RECORD_KEY" in route_src and "OK_KEY" in route_src
    assert repr(STORED_RECORD_KEY) in harness and repr(RECORD_ID_KEY) in harness
    assert repr(OK_KEY) in harness
