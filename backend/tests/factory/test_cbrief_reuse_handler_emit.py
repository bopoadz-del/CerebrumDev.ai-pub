"""C-BRIEF: REUSE must emit loadable app.actions handlers.

Photographed Floor after #344 (sess_bc0527bad93f4c66, VetCare Hub):
five capabilities marked REUSE, COLLECTOR+CLONER green, WRITER stopped
at writer_behaviour with ModuleNotFoundError for
app.actions.appointment_scheduling / automated_reminders /
clinic_dashboard / pet_records_management.

Keep-path wrote handlers into WRITER staging via Path.write_text;
commit() only copies workspace.written, so destination routes imported
modules that were never copied. This is compiler + emit + staged
commit — not a per-capability handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief, verify_inventory
from app.factory.build.coder_session import (
    KEEP_PATH_FACTORY_GROUNDED_REUSE,
    NAMED_BLOCKER_CLI_BILLING,
    NAMED_BLOCKER_CLI_FAILED,
    emit_factory_grounded_reuse_keep_path,
)
from app.factory.build.gates import GateContext, gate_writer_behaviour
from app.factory.build.persist_accept import (
    FACTORY_GROUNDED_PERSIST_SOURCE,
    persist_round_trip_errors,
)
from app.factory.build.roles import RoleContext, RoleError, run_writer
from app.factory.build.workflow_accept import FACTORY_GROUNDED_EVENT_BUS_SOURCE
from app.factory.build.workspace import RoleWorkspace
from tests.factory.test_coder_session import _require_cli, _usable_kimi_toml


VETCARE_REUSE_CAPS = (
    "appointment_scheduling",
    "automated_reminders",
    "clinic_dashboard",
    "pet_records_management",
    "veterinarian_availability_tracking",
)

VETCARE_REUSE_BLOCKS = {
    "appointment_scheduling": ["event_bus", "workflow", "database"],
    "automated_reminders": ["notification", "event_bus"],
    "clinic_dashboard": ["dashboard"],
    "pet_records_management": ["database"],
    "veterinarian_availability_tracking": ["database"],
}

STORE_IDS = {
    "event_bus",
    "workflow",
    "notification",
    "dashboard",
    "database",
}


class _Cap:
    def __init__(self, cid, block_ids, strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _VetCare:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Sample plan — REUSE must emit handlers"


def _vetcare_reuse_plan() -> _Plan:
    return _Plan(
        *(_Cap(cid, bids) for cid, bids in VETCARE_REUSE_BLOCKS.items())
    )


def _billing_cli(tmp_path: Path) -> Path:
    script = tmp_path / "kimi"
    script.write_text(
        "#!/bin/sh\n"
        "echo '429 Too Many Requests — this account has been suspended "
        "due to insufficient balance'\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _arm_billing_cli(tmp_path: Path, monkeypatch) -> None:
    script = _billing_cli(tmp_path)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)


def _assert_handlers_importable(root: Path) -> None:
    script = (
        "import importlib, sys\n"
        f"caps = {list(VETCARE_REUSE_CAPS)!r}\n"
        "for cid in caps:\n"
        "    mod = importlib.import_module('app.actions.' + cid)\n"
        "    assert hasattr(mod, 'handle'), cid\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def _assert_writer_behaviour_posts_without_modulenotfound(root: Path) -> None:
    script = (
        "import os, sys, tempfile\n"
        "os.environ['STORAGE_PATH'] = tempfile.mkdtemp(prefix='reuse-emit-')\n"
        "sys.path.insert(0, os.getcwd())\n"
        "from fastapi.testclient import TestClient\n"
        "from app.models import MODELS\n"
        "from app.main import app\n"
        "client = TestClient(app)\n"
        "with client:\n"
        f"    caps = {list(VETCARE_REUSE_CAPS)!r}\n"
        "    for cid in caps:\n"
        "        cls = MODELS[cid]\n"
        "        body = {}\n"
        "        for name in getattr(cls, 'FIELDS', []):\n"
        "            con = getattr(cls, 'CONSTRAINTS', {}).get(name, {})\n"
        "            allowed = con.get('allowed_values')\n"
        "            if allowed:\n"
        "                body[name] = allowed[0]\n"
        "            elif name == 'status' or name.endswith('_status'):\n"
        "                body[name] = 'open'\n"
        "            else:\n"
        "                body[name] = 'sample'\n"
        "        resp = client.post('/v1/' + cid, json=body)\n"
        "        data = resp.json() if resp.content else {}\n"
        "        err = str(data.get('error') or '')\n"
        "        if 'ModuleNotFoundError' in err or 'No module named' in err:\n"
        "            raise SystemExit(cid + ': ' + err)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


def test_compiler_vetcare_reuse_rows_have_handler_source():
    compiled = compile_brief(_VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS)
    verify_inventory(compiled)
    assert compiled.missing_reuse == []
    assert {item.capability_id for item in compiled.inventory} == set(
        VETCARE_REUSE_CAPS
    )
    for item in compiled.inventory:
        assert item.is_reuse, item.capability_id
        assert item.handler_source, item.capability_id
        assert "app/actions/" + item.capability_id + ".py" in compiled.text
    assert FACTORY_GROUNDED_EVENT_BUS_SOURCE in {
        item.handler_source for item in compiled.inventory
    }
    assert FACTORY_GROUNDED_PERSIST_SOURCE in {
        item.handler_source for item in compiled.inventory
    }


def test_staged_keep_path_commit_copies_reuse_handlers(tmp_path):
    """RoleRunner staging: keep-path files must land on destination."""
    dest = tmp_path / "dest"
    staging = tmp_path / "staging"
    dest.mkdir()
    compiled = compile_brief(_VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS)
    emitted = emit_factory_grounded_reuse_keep_path(staging, compiled)
    assert set(emitted) == set(VETCARE_REUSE_CAPS)
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=staging)
    for cid in VETCARE_REUSE_CAPS:
        rel = Path("app") / "actions" / f"{cid}.py"
        assert (staging / rel).is_file(), cid
        ws.write_text(rel, (staging / rel).read_text(encoding="utf-8"))
    ws.commit()
    for cid in VETCARE_REUSE_CAPS:
        assert (dest / "app" / "actions" / f"{cid}.py").is_file(), cid
        text = (dest / "app" / "actions" / f"{cid}.py").read_text(encoding="utf-8")
        assert "_persist_record(" not in text, cid
        assert "def handle(" in text, cid


def test_writer_staged_vetcare_reuse_emits_importable_handlers(
    tmp_path, monkeypatch
):
    """Live photograph roster: staged WRITER commit must copy REUSE handlers."""
    _arm_billing_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: {"specs": {}, "handlers": {}, "model": "x"},
    )
    plan = _vetcare_reuse_plan()
    compiled = compile_brief(_VetCare(), plan, store_ids=STORE_IDS)
    assert all(item.is_reuse for item in compiled.inventory)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    dest = tmp_path / "dest"
    staging = tmp_path / "staging"
    dest.mkdir()
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=staging)
    with pytest.raises(RoleError) as exc:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=ws,
                blueprint=_VetCare(),
                plan=plan,
                state={
                    "resolved_blocks": tuple(STORE_IDS),
                    "vendored_blocks": tuple(STORE_IDS),
                },
            )
        )
    # 0.5: the REUSE keep-path emit is factory-grounded, not coding-agent
    # authorship -- zero agent artifacts refuses the WRITER. The staged
    # commit and its evidence still land; only the false green flips.
    assert "writer_no_output" in str(exc.value)
    receipt = json.loads(
        (staging / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["inventory_gaps"] == []
    ws.commit()
    for cid in VETCARE_REUSE_CAPS:
        path = dest / "app" / "actions" / f"{cid}.py"
        assert path.is_file(), cid
        text = path.read_text(encoding="utf-8")
        assert "_persist_record(" not in text, cid
        assert "def handle(" in text, cid
        assert "deterministic contract template" not in text
    _assert_handlers_importable(dest)
    _assert_writer_behaviour_posts_without_modulenotfound(dest)
    gate = gate_writer_behaviour(
        GateContext(workspace=dest, role=BuildRole.WRITER, cycle="pilot")
    )
    joined = " ".join(gate.findings or []) + " " + (gate.detail or "")
    assert "ModuleNotFoundError" not in joined
    assert "No module named" not in joined
    specs = {
        cid: {"entity": cid, "fields": [{"name": "reference", "type": "str"}]}
        for cid in VETCARE_REUSE_CAPS
    }
    assert persist_round_trip_errors(dest, specs) == []
    assert "pilot_zip" not in str(exc.value).lower()


def test_false_reuse_fails_closed_before_writer_behaviour(tmp_path, monkeypatch):
    """No registry handler source → HALT, not writer_behaviour ModuleNotFound."""
    _arm_billing_cli(tmp_path, monkeypatch)
    plan = _Plan(_Cap("appointment_scheduling", ["not_a_real_block"], "REUSE"))
    compiled = compile_brief(_VetCare(), plan, store_ids=STORE_IDS)
    assert compiled.missing_reuse == ["not_a_real_block"]
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    dest = tmp_path / "dest"
    staging = tmp_path / "staging"
    dest.mkdir()
    with pytest.raises(RoleError, match="not_a_real_block") as halted:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=RoleWorkspace(BuildRole.WRITER, dest, staging=staging),
                blueprint=_VetCare(),
                plan=plan,
                state={"resolved_blocks": (), "vendored_blocks": ()},
            )
        )
    assert "writer_behaviour" not in str(halted.value).lower()
    assert "ModuleNotFoundError" not in str(halted.value)
    assert not (dest / "app" / "actions" / "appointment_scheduling.py").is_file()

    planted = compile_brief(
        _VetCare(),
        _Plan(_Cap("clinic_intake", [], "GENERATE")),
        store_ids=STORE_IDS,
    )
    planted.inventory[0].strategy = "REUSE"
    planted.inventory[0].handler_source = ""
    planted.inventory[0].verified_present = []
    planted.inventory[0].missing = []
    planted.missing_reuse = []
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: planted,
    )
    with pytest.raises(RoleError, match="no-handler-source|handler source") as lying:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=RoleWorkspace(
                    BuildRole.WRITER, dest / "planted", staging=staging / "planted"
                ),
                blueprint=_VetCare(),
                plan=_Plan(_Cap("clinic_intake", [], "GENERATE")),
                state={"resolved_blocks": (), "vendored_blocks": ()},
            )
        )
    assert "ModuleNotFoundError" not in str(lying.value)
    assert "writer_behaviour" not in str(lying.value).lower()
    assert "pilot_zip" not in str(lying.value).lower()
