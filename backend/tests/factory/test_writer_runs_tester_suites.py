"""TESTER samples the product's DECLARED models; the writer can run TESTER's suites.

Live bf1e0e5c, sess_1fbea5094c2a4a57: the WRITER gate's probe (payloads from
the live MODELS) was green, then TESTER refused "a payload built from its own
schema: <field> is required" -- its suites sampled ``state["model_specs"]``,
a spec captured once, so a field the writer declared in its rework round
never reached them and the same test failed again (SAME_FAILURE_TWICE).

Invented capability and field names only.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.declared_specs import merge_declared_specs, specs_from_product_models
from app.factory.build.product_suites import (
    DOMAIN_SUITE,
    PRODUCT_SUITES,
    ROUTES_SUITE,
    SMOKE_SUITE,
)
from app.factory.build.roles import RoleContext, run_tester
from app.factory.build.workspace import RoleWorkspace
from app.factory.build.writer_behaviour import render_self_check

CAP = "kiln_board"
NEEDED = "kiln_ref"


class _Cap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = ()
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Kiln Probe"
    product_id = "kiln-probe"
    vertical = "kiln_probe"


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _product(root: Path, *, declares_needed: bool) -> None:
    """A minimal product whose handler REQUIRES ``NEEDED``; the model may or
    may not declare it."""
    fields = [NEEDED, "reference"] if declares_needed else ["reference"]
    annotations = "\n".join(f"    {f}: str = ''" for f in fields)
    _write(root, "app/__init__.py", "")
    _write(
        root,
        "app/models.py",
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\nclass KilnBoard:\n"
        f"    ENTITY = {CAP!r}\n"
        f"    FIELDS = {fields!r}\n"
        "    CONSTRAINTS = {}\n"
        f"{annotations}\n\n\n"
        f"MODELS = {{{CAP!r}: KilnBoard}}\n",
    )
    _write(root, "app/actions/__init__.py", "")
    _write(
        root,
        f"app/actions/{CAP}.py",
        f"CAPABILITY_ID = {CAP!r}\n\n\n"
        "def handle(payload):\n"
        f"    if not payload.get({NEEDED!r}):\n"
        f"        return {{'ok': False, 'error': {NEEDED!r} + ' is required'}}\n"
        "    return {'ok': True, 'record': dict(payload)}\n",
    )
    _write(root, "app/migrations.py", "def upgrade_head(*a, **k):\n    return None\n")
    _write(root, "app/main.py", "from fastapi import FastAPI\n\napp = FastAPI()\n")
    _write(
        root,
        "app/dispatch.py",
        "def load_block(block_id):\n    return None\n\n\n"
        "def execute(block_id, payload, action=None):\n    return {}\n",
    )


def _ctx(root: Path, state_specs: dict) -> RoleContext:
    return RoleContext(
        role=BuildRole.TESTER,
        workspace=RoleWorkspace(BuildRole.TESTER, root),
        blueprint=_Blueprint(),
        plan=_Plan(_Cap(CAP)),
        state={"model_specs": state_specs, "vendored_blocks": ()},
    )


#: The spec captured before the writer's rework: it never knew NEEDED.
STALE = {CAP: {"entity": CAP, "fields": [{"name": "reference", "type": "str"}]}}


def _self_check_ns() -> dict:
    ns: dict = {"__name__": "factory_checks"}
    exec(compile(render_self_check(), "factory_checks.py", "exec"), ns)
    return ns


def _env() -> dict:
    return dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")


def test_merge_takes_the_products_fields_and_keeps_mined_contracts():
    state = {
        CAP: {
            "entity": CAP,
            "fields": [{"name": "reference", "type": "str", "allowed_values": ["a", "b"]}],
        },
        "glaze_log": {"entity": "glaze_log", "fields": [{"name": "batch", "type": "str"}]},
    }
    product = {
        CAP: {
            "entity": CAP,
            "fields": [{"name": NEEDED, "type": "str"}, {"name": "reference", "type": "str"}],
        }
    }
    merged = merge_declared_specs(state, product)
    names = [f["name"] for f in merged[CAP]["fields"]]
    assert names == [NEEDED, "reference"]
    assert merged[CAP]["fields"][1]["allowed_values"] == ["a", "b"]
    # A capability the product does not model keeps its spec; no product: unchanged.
    assert merged["glaze_log"] == state["glaze_log"]
    assert merge_declared_specs(state, {}) == state


def test_tester_samples_the_field_the_product_declares_not_the_stale_spec(tmp_path):
    root = tmp_path / "build"
    _product(root, declares_needed=True)
    assert specs_from_product_models(root)[CAP]["fields"][0]["name"] == NEEDED

    assert run_tester(_ctx(root, STALE)).ok
    smoke = (root / SMOKE_SUITE).read_text(encoding="utf-8")
    assert f"'{NEEDED}'" in smoke, "TESTER sampled the stale spec, not the model"
    # The suites the self-check runs are exactly where TESTER wrote them.
    for rel in (SMOKE_SUITE, ROUTES_SUITE, DOMAIN_SUITE):
        assert (root / rel).is_file(), rel


def test_self_check_runs_the_suites_on_disk_and_names_the_failing_test(tmp_path):
    ns = _self_check_ns()
    assert ns["SUITES"] == PRODUCT_SUITES
    root = tmp_path / "build"
    # First writer pass: nothing stamped yet -> nothing to report, no crash.
    root.mkdir()
    assert ns["run_suites"](str(root), _env()) == []

    # The writer's handler requires NEEDED but the model never declared it:
    # TESTER's suite (rendered from the model) sends no NEEDED -> red, and the
    # self-check names the failing product-gate test before TESTER judges.
    _product(root, declares_needed=False)
    assert run_tester(_ctx(root, STALE)).ok
    ns["SUITES"] = (SMOKE_SUITE,)
    failures = ns["run_suites"](str(root), _env())
    assert any("test_every_capability_executes_end_to_end" in f for f in failures), failures

    # The writer declares the field in its model; TESTER re-stamps from the
    # model; the same self-check is now clean and TESTER's suite passes.
    _product(root, declares_needed=True)
    assert run_tester(_ctx(root, STALE)).ok
    assert ns["run_suites"](str(root), _env()) == []
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-m", "pilot or not pilot", SMOKE_SUITE],
        cwd=root, capture_output=True, text=True, env=_env(), timeout=600,
    )
    assert proc.returncode == 0, proc.stdout[-2000:]


def test_tester_rework_item_carries_the_recheck_command_and_failing_tests():
    from app.factory.build.runner import _tester_recheck_item
    from app.factory.build.writer_behaviour import SELF_CHECK_COMMAND

    class _Verdict:
        gate = "product_green"
        reason = "suite_red"
        findings = (
            f"FAILED {SMOKE_SUITE}::test_every_capability_executes_end_to_end - "
            f"AssertionError: {CAP} rejected a payload built from its own schema: "
            f"{NEEDED} is required",
        )
        payload = {
            "failing_tests": [
                {"nodeid": f"{SMOKE_SUITE}::test_every_capability_executes_end_to_end",
                 "outcome": "failure"}
            ]
        }

    item = _tester_recheck_item(_Verdict())
    assert SELF_CHECK_COMMAND in item
    assert "FIELDS" in item
    # Typed: a recheck item, never counted among the findings.
    from app.factory.build.product_suites import finding_items, recheck_items

    work = (_Verdict.findings[0], item)
    assert recheck_items(work) == (item,)
    assert finding_items(work) == (_Verdict.findings[0],)
