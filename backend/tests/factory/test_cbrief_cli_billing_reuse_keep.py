"""C-BRIEF empty-gap REUSE keep-path after FACTORY_CODE_CLI billing miss.

Photographed Floor (2026-09-05, sess_d5789a91d53b4bae, VetCare Hub /
veterinary-care, tip 327b4ae): inventory_gaps=[], all five caps REUSE,
FACTORY_CODE_CLI exited 1 with Moonshot ``429 ... suspended due to
insufficient balance``. Receipt kept_handler_ids=[] and budget_inspect
hard-stopped at stub_rate≈0.833 / SCAFFOLD.

This is compiler + emit + harvest + inspect — not a VetCare route patch.
Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.coder_session import (
    KEEP_PATH_FACTORY_GROUNDED_REUSE,
    NAMED_BLOCKER_CLI_BILLING,
    NAMED_BLOCKER_CLI_FAILED,
)
from app.factory.build.persist_accept import (
    FACTORY_GROUNDED_PERSIST_SOURCE,
    assert_persist_round_trip_ready,
    persist_round_trip_errors,
)
from app.factory.build.roles import RoleContext, RoleError, run_writer
from app.factory.build.workflow_accept import FACTORY_GROUNDED_EVENT_BUS_SOURCE
from app.factory.build.workspace import RoleWorkspace
from tests.factory.test_coder_session import _require_cli, _usable_kimi_toml


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
    summary = "Sample plan — empty-gap REUSE after CLI billing"


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


def test_writer_empty_gap_billing_fail_allows_product_round_trip(
    tmp_path, monkeypatch
):
    """Empty-gap REUSE + CLI billing fail still persists — PRODUCT path open."""
    script = _billing_cli(tmp_path)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or {"specs": {}, "handlers": {}, "model": "x"},
    )

    plan = _Plan(
        _Cap("appointment_scheduling", ["event_bus", "workflow", "database"]),
        _Cap("clinic_dashboard", ["dashboard"]),
        _Cap("pet_records_management", ["database"]),
    )
    store_ids = {"event_bus", "workflow", "database", "dashboard"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    assert all(not item.is_gap for item in compiled.inventory)

    out = tmp_path / "vetcare"
    ws = RoleWorkspace(BuildRole.WRITER, out)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    with pytest.raises(RoleError) as exc:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=ws,
                blueprint=_VetCare(),
                plan=plan,
                state={
                    "resolved_blocks": tuple(store_ids),
                    "vendored_blocks": tuple(store_ids),
                },
            )
        )
    # 0.5: the keep-path emit is factory-grounded, not coding-agent
    # authorship -- zero agent artifacts refuses the WRITER. The old
    # assert result.ok was the false green the gate closes; the emit
    # itself still lands and round-trips.
    assert "writer_no_output" in str(exc.value)
    assert oneshot == []
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["inventory_gaps"] == []
    assert receipt["kept_handler_ids"]
    for cid, want in (
        ("appointment_scheduling", FACTORY_GROUNDED_EVENT_BUS_SOURCE),
        ("clinic_dashboard", FACTORY_GROUNDED_PERSIST_SOURCE),
        ("pet_records_management", FACTORY_GROUNDED_PERSIST_SOURCE),
    ):
        text = (out / "app" / "actions" / f"{cid}.py").read_text(encoding="utf-8")
        assert "_persist_record(" not in text, cid
        assert "def handle" in text, cid
        assert want in text, (cid, want)
        assert "deterministic contract template" not in text
    # CI has no CEREBRUM_BLOCKS_ROOT / CLONER shims, so Store execute()
    # of event_bus/workflow is not the contract. Persist harness + writer
    # not RoleError-ing is the PRODUCT path this C-BRIEF opens.
    specs = {
        cid: {"entity": cid, "fields": [{"name": "reference", "type": "str"}]}
        for cid in (
            "appointment_scheduling",
            "clinic_dashboard",
            "pet_records_management",
        )
    }
    assert persist_round_trip_errors(out, specs) == []
    assert_persist_round_trip_ready(out, specs)
    assert "pilot_zip" not in str(exc.value).lower()


def test_writer_nonempty_gaps_billing_fail_stays_fail_closed(tmp_path, monkeypatch):
    """GENERATE gap + billing miss + factory-LLM fail: templates, no fake keep-path."""
    script = _billing_cli(tmp_path)
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    from app.factory.coder import CoderError

    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: (_ for _ in ()).throw(CoderError("model refused")),
    )

    plan = _Plan(_Cap("novel_clinic_ai", [], "GENERATE"))
    compiled = compile_brief(_VetCare(), plan, store_ids={"database"})
    assert any(item.is_gap for item in compiled.inventory)
    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "gap")
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    with pytest.raises(RoleError) as exc:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=ws,
                blueprint=_VetCare(),
                plan=plan,
                state={"resolved_blocks": (), "vendored_blocks": ()},
            )
        )
    # 0.5: GENERATE gap + billing miss + factory-LLM fail lands zero
    # agent-authored artifacts -- the refusal is the named reason, and
    # the receipt/handler evidence still lands before it.
    assert "writer_no_output" in str(exc.value)
    receipt = json.loads(
        (tmp_path / "gap" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] is None
    assert receipt["factory_llm_generate_fallthrough"] is True
    assert receipt["inventory_gaps"] == []
    assert receipt["factory_llm_written_ids"] == []
    assert receipt["kept_handler_ids"] == []
    assert "novel_clinic_ai" in receipt["generate_persist_ids"]
    handler = (tmp_path / "gap" / "app" / "actions" / "novel_clinic_ai.py").read_text(
        encoding="utf-8"
    )
    assert "_persist_record(" not in handler
    assert "def handle" in handler
    assert "deterministic contract template" not in handler
    assert "FACTORY_CODE_CLI" not in handler
    assert "pilot_zip" not in str(exc.value).lower()
