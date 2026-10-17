"""The kernel roster TESTER asserts is stamped on the CodeWhale path too.

Live 2026-10-06 (vineyard repro on dd1b353d): ``run_writer`` stamped
``app/jobs.py`` only below the CodeWhale branch it returns at, so the agent
wrote its own copy with ``CATALOG = {}`` / ``GATES = {}`` and TESTER's
emitted ``test_kernel_jobs_roster`` died on ``KeyError: 'kernel'`` -- a
FACTORY_FAULT no writer rework could fix.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.factory.build.authority import BuildRole, role_contract
from app.factory.build.kernel_publish import (
    JOBS_REL,
    declared_capabilities,
    render_roster_test,
    roster_titles,
    stamp_roster,
)

# The live agent-written shape: Factory header, empty CATALOG/GATES, and a
# capability manifest whose entity differs from the capability id.
AGENT_JOBS = '''"""Kernel job descriptions shipped with this platform."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, Optional

JOBS = []
CATALOG = {}
CAPABILITIES = [{"id": "tank_log", "entity": "tank_reading", "source": "agent"}]
GATES = {}
'''


class _Workspace:
    def __init__(self, root: Path):
        self.workspace = root

    def write_text(self, rel, text):
        path = self.workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


def _ctx(root: Path):
    plan = SimpleNamespace(
        capabilities=[SimpleNamespace(capability_id="tank_log", block_ids=["store"])]
    )
    return SimpleNamespace(workspace=_Workspace(root), state={}, plan=plan)


def _load_jobs(root: Path) -> dict:
    path = root / JOBS_REL
    ns: dict = {"__file__": str(path)}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), ns)
    return ns


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body


def _client_for(ns: dict):
    """Serves exactly what the Factory's kernel routes serve (routes.py)."""
    routes = {
        "/v1/jobs": lambda: {"jobs": ns["JOBS"]},
        "/v1/catalog": lambda: ns["CATALOG"],
        "/v1/inventory": lambda: ns["inventory"](),
        "/v1/capabilities": lambda: {"items": ns["CAPABILITIES"]},
        "/v1/gates": lambda: ns["GATES"],
        "/v1/provenance": lambda: ns["provenance"](),
    }
    return SimpleNamespace(get=lambda path, headers=None: _Resp(routes[path]()))


def _run_emitted_roster_test(ns: dict) -> None:
    src = "\n".join(render_roster_test())
    scope = {"client": _client_for(ns), "AUTH": {}}
    exec(compile(src, "test_routes.py", "exec"), scope)
    scope["test_kernel_jobs_roster"]()


def test_the_live_agent_shape_fails_the_emitted_roster_test(tmp_path):
    # Control: the live tree, unstamped, reproduces the FACTORY_FAULT.
    (tmp_path / JOBS_REL).parent.mkdir(parents=True)
    (tmp_path / JOBS_REL).write_text(
        AGENT_JOBS + "\ndef inventory():\n    return {}\n\ndef provenance():\n    return {}\n",
        encoding="utf-8",
    )
    ns = _load_jobs(tmp_path)
    try:
        _run_emitted_roster_test(ns)
    except (KeyError, AssertionError):
        return
    raise AssertionError("the unstamped live shape should fail the roster test")


def test_the_codewhale_stamp_makes_the_emitted_roster_test_pass(tmp_path):
    (tmp_path / JOBS_REL).parent.mkdir(parents=True)
    (tmp_path / JOBS_REL).write_text(AGENT_JOBS, encoding="utf-8")
    assert stamp_roster(_ctx(tmp_path)) is True
    ns = _load_jobs(tmp_path)
    assert ns["CATALOG"]["kernel"] == BuildRole.COLLECTOR.value
    assert ns["GATES"]["kernel"] == BuildRole.TESTER.value
    _run_emitted_roster_test(ns)


def test_the_stamp_keeps_the_product_s_declared_capability_manifest(tmp_path):
    # CAPABILITIES is a declared-entity source for the round-trip probes;
    # the stamp never invents an entity (no capability-id fallback).
    (tmp_path / JOBS_REL).parent.mkdir(parents=True)
    (tmp_path / JOBS_REL).write_text(AGENT_JOBS, encoding="utf-8")
    stamp_roster(_ctx(tmp_path))
    ns = _load_jobs(tmp_path)
    assert ns["CAPABILITIES"] == [{"id": "tank_log", "entity": "tank_reading", "source": "agent"}]


def test_no_existing_manifest_declares_nothing(tmp_path):
    stamp_roster(_ctx(tmp_path))
    ns = _load_jobs(tmp_path)
    assert ns["CAPABILITIES"] == []
    assert declared_capabilities("CAPABILITIES = some_call()") == []
    assert declared_capabilities("not python (") == []


def test_a_roster_that_lacks_a_kernel_still_fails(tmp_path):
    # The emitted test still judges: a roster missing a kernel is red.
    stamp_roster(_ctx(tmp_path))
    ns = _load_jobs(tmp_path)
    ns["JOBS"] = [j for j in ns["JOBS"] if j["kernel"] != BuildRole.CLONER.value]
    try:
        _run_emitted_roster_test(ns)
    except AssertionError:
        return
    raise AssertionError("a roster missing a kernel must fail the emitted test")


def test_the_emitted_assertions_come_from_the_role_contracts():
    src = "\n".join(render_roster_test())
    for role, title in roster_titles().items():
        assert title == role_contract(role).title
        assert repr(title) in src
    # Byte-stable: the same contracts render the same suite.
    assert render_roster_test() == render_roster_test()
