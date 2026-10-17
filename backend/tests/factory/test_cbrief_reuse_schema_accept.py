"""C-BRIEF: REUSE keep-path handlers must accept their own schema sample.

Photographed Floor after #346 (tip 5a530b3, sess_bb870f4fb29042f2,
VetCare Hub, all-REUSE): ModuleNotFoundError did not recur. Export
refuse PASS; pilot_zip=no. WRITER stopped at [check:reuse_accept]:

    prescription_management: formula_executor: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)
    billing_and_invoicing: formula_executor: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

#346 harvested the #345 Store roster but formula_executor was missing
from the factory-known default map / harvest path. This is compiler +
emit + harvest + WRITER halt — not a per-cap handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from app.factory.build.brief_compiler import compile_brief, verify_inventory
from app.factory.build.reuse_accept import (
    PRODUCT_UNKNOWN_ACTION_NONE_HALT,
    REUSE_ACCEPT_MISS,
    WRITER_REUSE_ACCEPT_HALT,
    ReuseAcceptHalt,
    apply_default_actions_to_handler,
    assert_reuse_schema_accept,
    harvest_block_default_actions,
    parse_handler_default_actions,
    reuse_accept_acceptance_line,
    reuse_accept_handler_errors,
)
from app.factory.build.roles_handlers import (
    _capability_handler_body,
    _handler_module,
)
from tests.factory.test_coder_session import _require_cli, _usable_kimi_toml


STORE_IDS = {
    "database",
    "validation",
    "event_bus",
    "workflow",
    "analytics",
    "team",
    "formula_executor",
    "vector_search",
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
    summary = "Sample REUSE plan for the formula_executor reuse/accept tests"


#: Sample plan for the mechanism tests below: invented capabilities bound to
#: real Store blocks. Test data only -- production code holds no roster.
SAMPLE_REUSE_PLAN_BLOCKS = {
    "patient_records_management": ["database", "validation", "vector_search"],
    "appointment_scheduling": ["event_bus", "workflow"],
    "prescription_management": ["validation", "formula_executor"],
    "billing_and_invoicing": ["analytics", "formula_executor"],
    "client_communication_portal": ["team"],
}


def _vetcare_reuse_plan() -> _Plan:
    return _Plan(
        *(_Cap(cid, bids) for cid, bids in SAMPLE_REUSE_PLAN_BLOCKS.items())
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


def _plant_store_block_json(root: Path) -> None:
    """Simulate CLONER: vendored block.json with action defaults."""
    planted = {
        "database": ("query", ["query", "insert"]),
        "validation": ("validate", ["validate", "check"]),
        "event_bus": ("publish", ["publish", "notify"]),
        "workflow": ("run", ["run"]),
        "analytics": ("track_event", ["track_event"]),
        "team": ("create_team", ["create_team", "invite_member"]),
        "formula_executor": ("execute", ["execute"]),
        "vector_search": ("search", ["search"]),
    }
    for bid, (default, options) in planted.items():
        dest = root / "vendor" / "blocks" / bid
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "block.json").write_text(
            json.dumps(
                {
                    "id": bid,
                    "inputs": [
                        {
                            "name": "action",
                            "type": "string",
                            "default": default,
                            "options": options,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )


def _schema_sample_exec_script(caps: Sequence[str]) -> str:
    return (
        "import importlib, sys, types\n"
        "sys.path.insert(0, '.')\n"
        "unknown = []\n"
        "calls = []\n"
        "def _execute(block_id, payload=None, action=None, params=None, **kw):\n"
        "    if not (isinstance(action, str) and action.strip()):\n"
        "        unknown.append('%s: Unknown action: %s' % (block_id, action))\n"
        "        return {'status': 'error', 'error': 'Unknown action: %s' % action}\n"
        "    calls.append((block_id, action))\n"
        "    return {'status': 'ok', 'block': block_id, 'action': action}\n"
        "dispatch = types.ModuleType('app.dispatch')\n"
        "dispatch.execute = _execute\n"
        "sys.modules['app.dispatch'] = dispatch\n"
        f"caps = {list(caps)!r}\n"
        "sample = {'reference': 'sample', 'status': 'open'}\n"
        "for cid in caps:\n"
        "    try:\n"
        "        mod = importlib.import_module('app.actions.' + cid)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        raise SystemExit('ModuleNotFoundError: ' + str(exc))\n"
        "    defaults = getattr(mod, 'BLOCK_DEFAULT_ACTIONS', {})\n"
        "    for bid in getattr(mod, 'BLOCK_IDS', []) or []:\n"
        "        action = defaults.get(bid) if isinstance(defaults, dict) else None\n"
        "        if not (isinstance(action, str) and action.strip()):\n"
        "            unknown.append('%s:%s: Unknown action: None' % (cid, bid))\n"
        "    if hasattr(mod, 'execute'):\n"
        "        mod.execute = _execute\n"
        "    try:\n"
        "        result = mod.handle(sample)\n"
        "    except ModuleNotFoundError as exc:\n"
        "        raise SystemExit('ModuleNotFoundError: ' + str(exc))\n"
        "    except Exception as exc:\n"
        "        err = '%s: %s' % (type(exc).__name__, exc)\n"
        "        if 'Unknown action' in err:\n"
        "            unknown.append(cid + ': ' + err[:200])\n"
        "            result = {}\n"
        "        elif 'ModuleNotFound' in err or 'No module named' in err:\n"
        "            raise SystemExit(cid + ': ' + err)\n"
        "        else:\n"
        "            result = {}\n"
        "    err = str((result or {}).get('error') or '')\n"
        "    if 'Unknown action' in err:\n"
        "        unknown.append(cid + ': ' + err[:200])\n"
        "    if 'ModuleNotFoundError' in err or 'No module named' in err:\n"
        "        raise SystemExit(cid + ': ' + err)\n"
        "if unknown:\n"
        "    raise SystemExit('; '.join(unknown))\n"
        "if not calls:\n"
        "    raise SystemExit('schema-sample execute() was never reached')\n"
    )


def test_vetcare_compiled_brief_grounds_reuse_accept():
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    verify_inventory(compiled)
    text = compiled.text
    assert reuse_accept_acceptance_line() in text
    assert PRODUCT_UNKNOWN_ACTION_NONE_HALT in text
    assert REUSE_ACCEPT_MISS in text
    assert "patient_records_management" in text
    assert "formula_executor" in text
    assert "vector_search" in text


def test_empty_block_default_actions_is_reuse_accept_miss():
    body = _capability_handler_body(
        "patient_records_management", ["database", "validation"]
    )
    empty = _handler_module(
        "patient_records_management",
        ["database", "validation"],
        body,
        "factory-grounded persist",
        {},
        entity="patient_records_management",
    )
    assert parse_handler_default_actions(empty) == {}
    # Factory map still resolves these Store ids — the miss is an
    # unbound / unknown block, or an explicit action=None call.
    filled = apply_default_actions_to_handler(
        empty, harvest_block_default_actions(["database", "validation"])
    )
    assert parse_handler_default_actions(filled)["database"] == "query"
    assert parse_handler_default_actions(filled)["validation"] in {
        "validate",
        "validate_pipeline",
    }
    assert reuse_accept_handler_errors(filled, ["database", "validation"]) == []
    ghost = apply_default_actions_to_handler(empty, {"database": "query"})
    errors = reuse_accept_handler_errors(
        ghost, ["database", "not_a_real_block"], capability_id="patient_records_management"
    )
    assert any(REUSE_ACCEPT_MISS in e for e in errors)
    assert any("not_a_real_block" in e for e in errors)


def test_empty_defaults_halt_before_tester(tmp_path):
    compiled = compile_brief(
        _VetCare(), _vetcare_reuse_plan(), store_ids=STORE_IDS
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "patient_records_management.py").write_text(
        "CAPABILITY_ID = 'patient_records_management'\n"
        "BLOCK_IDS = ['not_a_real_block']\n"
        "BLOCK_DEFAULT_ACTIONS = {}\n"
        "def handle(payload):\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    compiled.inventory = [
        item
        for item in compiled.inventory
        if item.capability_id == "patient_records_management"
    ]
    compiled.inventory[0].verified_present = ["not_a_real_block"]
    compiled.inventory[0].block_ids = ["not_a_real_block"]
    with pytest.raises(ReuseAcceptHalt, match=r"reuse_accept") as halted:
        assert_reuse_schema_accept(tmp_path, compiled)
    assert WRITER_REUSE_ACCEPT_HALT in str(halted.value)
    assert "not_a_real_block" in str(halted.value)


