"""C-BRIEF: GENERATE-gap factory-LLM fallthrough after CLI billing miss.

Live tip ed943dd / #342: VetCare sessions sess_c220986f67914681 and
sess_45729bb662cf4a5d reached SUCCESS + pilot_ready with Export allowed
and founding demoted, but the zip stayed thin (~1 agent-written README
via OpenRouter / 23 templated). inventory_gaps included
veterinary_care_core (strategy GENERATE, no block ids). #338 empty-gap
REUSE only harvests dual-registered REUSE caps.

This is compiler + dispatch + WRITER — not a VetCare route patch and not
a live Floor run. Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim
founding or a ≥2h CLI session.
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
    dispatch_compiled_brief,
)
from app.factory.build.level_grade import Level, grade_workspace
from app.factory.build.persist_accept import (
    FACTORY_GROUNDED_PERSIST_SOURCE,
    assert_persist_round_trip_ready,
    persist_round_trip_errors,
)
from app.factory.build.roles import RoleContext, RoleError, run_writer
from app.factory.build.workspace import RoleWorkspace
from tests.factory.coder_stub_bodies import invoking_handler_body
from tests.factory.test_coder_session import _require_cli, _usable_kimi_toml
from tests.factory.test_level_grade import _full_repo

#: A sample keyword-fallback plan -- test data only.
SAMPLE_FALLBACK_CAPS = ("veterinary_care_core", "audit", "dashboard")


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
    summary = "Sample plan — GENERATE core after CLI billing"


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


def _arm_billing_cli(tmp_path, monkeypatch) -> None:
    home = tmp_path / "kimi-home"
    home.mkdir()
    (home / "config.toml").write_text(_usable_kimi_toml(), encoding="utf-8")
    _require_cli(monkeypatch)
    monkeypatch.setenv("FACTORY_CODE_CLI", str(_billing_cli(tmp_path)))
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "1")
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)


def _core_llm_payload(model: str = "openrouter/free"):
    return {
        "specs": {
            "veterinary_care_core": {
                "entity": "veterinary_care_core",
                "fields": [{"name": "reference", "type": "str", "required": True}],
            }
        },
        "handlers": {
            "veterinary_care_core": invoking_handler_body({"agent": True}),
        },
        "model": model,
    }


def test_dispatch_generate_gap_billing_calls_factory_llm_not_oneshot_env(
    tmp_path, monkeypatch
):
    """Billing miss + GENERATE gap: one compiled-brief factory-LLM shot."""
    _arm_billing_cli(tmp_path, monkeypatch)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or _core_llm_payload(),
    )
    plan = _Plan(_Cap("veterinary_care_core", [], "GENERATE"))
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace(BuildRole.WRITER, tmp_path / "build"),
        blueprint=_VetCare(),
        plan=plan,
        state={},
    )
    compiled = compile_brief(_VetCare(), plan, store_ids={"dashboard", "audit"})
    assert [item.capability_id for item in compiled.inventory if item.is_gap] == [
        "veterinary_care_core"
    ]
    ctx.workspace.write_text(Path("docs") / "coder_brief.md", compiled.text)
    ctx.workspace.write_text(Path("docs") / "coder_session.log", "")
    result = dispatch_compiled_brief(ctx, compiled)
    assert result.ok is False
    assert result.via == "cli"
    assert result.blocker == NAMED_BLOCKER_CLI_BILLING
    assert result.reuse_keep_path is False
    assert result.factory_llm_generate_fallthrough is True
    assert result.factory_llm_written_ids == ["veterinary_care_core"]
    assert result.factory_llm_model == "openrouter/free"
    assert "veterinary_care_core" in result.handlers
    assert len(oneshot) == 1
    assert oneshot[0]["capabilities"] == ["veterinary_care_core"]
    assert "GENERATE-GAP FALLTHROUGH" in oneshot[0]["brief"]
    assert "STEP 0 INVENTORY" in oneshot[0]["brief"]
    receipt = json.loads(
        (tmp_path / "build" / "docs" / "coder_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] is None
    assert receipt["inventory_gaps"] == []
    assert receipt["factory_llm_written_ids"] == ["veterinary_care_core"]
    assert "pilot_zip" not in (result.detail or "").lower()


def test_writer_generate_gap_billing_lands_factory_llm_handler(tmp_path, monkeypatch):
    """WRITER uses factory-LLM GENERATE artifacts; CLI honesty stays on the receipt."""
    _arm_billing_cli(tmp_path, monkeypatch)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or _core_llm_payload(),
    )
    plan = _Plan(_Cap("veterinary_care_core", [], "GENERATE"))
    compiled = compile_brief(_VetCare(), plan, store_ids={"dashboard"})
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    out = tmp_path / "vetcare"
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=RoleWorkspace(BuildRole.WRITER, out),
            blueprint=_VetCare(),
            plan=plan,
            state={"resolved_blocks": (), "vendored_blocks": ()},
        )
    )
    assert result.ok, result.detail
    assert oneshot and oneshot[0]["capabilities"] == ["veterinary_care_core"]
    handler = (out / "app" / "actions" / "veterinary_care_core.py").read_text(
        encoding="utf-8"
    )
    assert "coder LLM (openrouter/free)" in handler
    assert '"agent": True' in handler
    assert "deterministic contract template" not in handler
    assert "FACTORY_CODE_CLI" not in handler
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] is None
    assert receipt["factory_llm_generate_fallthrough"] is True
    assert receipt["inventory_gaps"] == []
    assert receipt["factory_llm_written_ids"] == ["veterinary_care_core"]
    assert "pilot_zip" not in (result.detail or "").lower()
    specs = {
        "veterinary_care_core": {
            "entity": "veterinary_care_core",
            "fields": [{"name": "reference", "type": "str"}],
        }
    }
    assert persist_round_trip_errors(out, specs) == []
    assert_persist_round_trip_ready(out, specs)
    assert "_persist_record(" not in handler
    assert "def handle" in handler


def test_writer_mixed_reuse_and_generate_after_billing(tmp_path, monkeypatch):
    """REUSE harvest + GENERATE factory-LLM on the same CLI billing miss."""
    _arm_billing_cli(tmp_path, monkeypatch)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or _core_llm_payload(),
    )
    plan = _Plan(
        _Cap("clinic_dashboard", ["dashboard"], "REUSE"),
        _Cap("veterinary_care_core", [], "GENERATE"),
    )
    store_ids = {"dashboard"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    assert any(
        item.capability_id == "veterinary_care_core" and item.is_gap
        for item in compiled.inventory
    )
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    out = tmp_path / "mixed"
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=RoleWorkspace(BuildRole.WRITER, out),
            blueprint=_VetCare(),
            plan=plan,
            state={
                "resolved_blocks": tuple(store_ids),
                "vendored_blocks": tuple(store_ids),
            },
        )
    )
    assert result.ok, result.detail
    assert oneshot and oneshot[0]["capabilities"] == ["veterinary_care_core"]
    dash = (out / "app" / "actions" / "clinic_dashboard.py").read_text(encoding="utf-8")
    core = (out / "app" / "actions" / "veterinary_care_core.py").read_text(
        encoding="utf-8"
    )
    assert FACTORY_GROUNDED_PERSIST_SOURCE in dash
    assert "_persist_record(" not in dash
    assert "def handle" in dash
    assert "coder LLM (openrouter/free)" in core
    assert '"agent": True' in core
    assert "deterministic contract template" not in core
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert "clinic_dashboard" in receipt["kept_handler_ids"]
    assert "veterinary_care_core" not in receipt["kept_handler_ids"]
    assert receipt["factory_llm_written_ids"] == ["veterinary_care_core"]
    assert receipt["inventory_gaps"] == []
    specs = {
        cid: {"entity": cid, "fields": [{"name": "reference", "type": "str"}]}
        for cid in ("clinic_dashboard", "veterinary_care_core")
    }
    assert persist_round_trip_errors(out, specs) == []
    assert_persist_round_trip_ready(out, specs)
    assert "_persist_record(" not in core
    assert "def handle" in core


def test_writer_photograph_vetcare_generate_round_trip_after_billing(
    tmp_path, monkeypatch
):
    """sess_f358c2e0 roster: GENERATE core + REUSE audit/dashboard persist."""
    _arm_billing_cli(tmp_path, monkeypatch)
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or _core_llm_payload(),
    )
    plan = _Plan(
        _Cap("veterinary_care_core", [], "GENERATE"),
        _Cap("audit", ["audit"], "REUSE"),
        _Cap("dashboard", ["dashboard"], "REUSE"),
    )
    store_ids = {"audit", "dashboard"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    assert sorted(
        item.capability_id for item in compiled.inventory if item.is_gap
    ) == ["veterinary_care_core"]
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    out = tmp_path / "photograph"
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=RoleWorkspace(BuildRole.WRITER, out),
            blueprint=_VetCare(),
            plan=plan,
            state={
                "resolved_blocks": tuple(store_ids),
                "vendored_blocks": tuple(store_ids),
            },
        )
    )
    assert result.ok, result.detail
    assert oneshot and oneshot[0]["capabilities"] == ["veterinary_care_core"]
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["factory_llm_generate_fallthrough"] is True
    assert "veterinary_care_core" not in receipt["kept_handler_ids"]
    assert set(receipt["kept_handler_ids"]) >= {"audit", "dashboard"}
    specs = {
        cid: {"entity": cid, "fields": [{"name": "reference", "type": "str"}]}
        for cid in SAMPLE_FALLBACK_CAPS
    }
    assert persist_round_trip_errors(out, specs) == []
    assert_persist_round_trip_ready(out, specs)
    for cid in SAMPLE_FALLBACK_CAPS:
        text = (out / "app" / "actions" / f"{cid}.py").read_text(encoding="utf-8")
        assert "_persist_record(" not in text, cid
        assert "def handle" in text, cid
    core = (out / "app" / "actions" / "veterinary_care_core.py").read_text(
        encoding="utf-8"
    )
    assert "coder LLM (openrouter/free)" in core
    assert "deterministic contract template" not in core
    assert "pilot_zip" not in (result.detail or "").lower()


def test_writer_generate_llm_empty_still_emits_persist_keep_path(
    tmp_path, monkeypatch
):
    """Empty factory-LLM GENERATE still persist-emits (not a template, not CLI)."""
    _arm_billing_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: {"specs": {}, "handlers": {}, "model": "openrouter/free"},
    )
    plan = _Plan(
        _Cap("veterinary_care_core", [], "GENERATE"),
        _Cap("audit", ["audit"], "REUSE"),
        _Cap("dashboard", ["dashboard"], "REUSE"),
    )
    store_ids = {"audit", "dashboard"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    out = tmp_path / "empty-llm"
    with pytest.raises(RoleError) as exc:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=RoleWorkspace(BuildRole.WRITER, out),
                blueprint=_VetCare(),
                plan=plan,
                state={
                    "resolved_blocks": tuple(store_ids),
                    "vendored_blocks": tuple(store_ids),
                },
            )
        )
    # 0.5: factory-grounded emit is not coding-agent authorship, so this
    # writer pass has zero agent artifacts and refuses -- the old
    # assert result.ok was the false green the gate closes.
    assert "writer_no_output" in str(exc.value)
    core = (out / "app" / "actions" / "veterinary_care_core.py").read_text(
        encoding="utf-8"
    )
    assert "_persist_record(" not in core
    assert "def handle" in core
    assert FACTORY_GROUNDED_PERSIST_SOURCE in core
    assert "deterministic contract template" not in core
    assert "FACTORY_CODE_CLI" not in core
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["factory_llm_written_ids"] == []
    assert "veterinary_care_core" in receipt["generate_persist_ids"]
    assert receipt["inventory_gaps"] == []
    specs = {
        cid: {"entity": cid, "fields": [{"name": "reference", "type": "str"}]}
        for cid in SAMPLE_FALLBACK_CAPS
    }
    assert persist_round_trip_errors(out, specs) == []
    assert_persist_round_trip_ready(out, specs)
    assert "pilot_zip" not in str(exc.value).lower()


def test_writer_staging_leftover_destination_still_emits_generate_persist(
    tmp_path, monkeypatch
):
    """Staging persist-check must not skip GENERATE because destination exists."""
    _arm_billing_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: _core_llm_payload(),
    )
    dest = tmp_path / "dest"
    staging = tmp_path / "staging"
    dest.mkdir()
    leftover = dest / "app" / "actions"
    leftover.mkdir(parents=True)
    (leftover / "veterinary_care_core.py").write_text(
        "def handle(payload):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    plan = _Plan(
        _Cap("veterinary_care_core", [], "GENERATE"),
        _Cap("audit", ["audit"], "REUSE"),
        _Cap("dashboard", ["dashboard"], "REUSE"),
    )
    store_ids = {"audit", "dashboard"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=staging)
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=ws,
            blueprint=_VetCare(),
            plan=plan,
            state={
                "resolved_blocks": tuple(store_ids),
                "vendored_blocks": tuple(store_ids),
            },
            work_list=("audit PRODUCT miss",),
        )
    )
    assert result.ok, result.detail
    specs = {
        cid: {"entity": cid, "fields": [{"name": "reference", "type": "str"}]}
        for cid in SAMPLE_FALLBACK_CAPS
    }
    assert persist_round_trip_errors(staging, specs) == []
    assert_persist_round_trip_ready(staging, specs)
    core = (staging / "app" / "actions" / "veterinary_care_core.py").read_text(
        encoding="utf-8"
    )
    assert "_persist_record(" not in core
    assert "def handle" in core
    assert "coder LLM (openrouter/free)" in core


def test_writer_sess_336246_alias_llm_key_round_trip_after_billing(
    tmp_path, monkeypatch
):
    """sess_336246: GENERATE vetcare_hub_veterinary_core + REUSE audit.

    Factory LLM keyed the body as veterinary_care_core. Tip 5a530b3
    omitted app/actions/vetcare_hub_veterinary_core.py and WRITER halted
    at [check:round_trip] handler missing. Billing keep-path must bind
    the alias and persist-emit.
    """
    _arm_billing_cli(tmp_path, monkeypatch)
    oneshot = []

    def _alias_payload(**kw):
        oneshot.append(kw)
        return {
            "specs": {
                "veterinary_care_core": {
                    "entity": "veterinary_care_core",
                    "fields": [{"name": "reference", "type": "str", "required": True}],
                }
            },
            "handlers": {
                "veterinary_care_core": invoking_handler_body({"agent": True}),
            },
            "model": "openrouter/free",
        }

    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        _alias_payload,
    )
    plan = _Plan(
        _Cap("vetcare_hub_veterinary_core", [], "GENERATE"),
        _Cap("audit", ["audit"], "REUSE"),
    )
    store_ids = {"audit"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    assert [item.capability_id for item in compiled.inventory if item.is_gap] == [
        "vetcare_hub_veterinary_core"
    ]
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    out = tmp_path / "sess-336246-alias"
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=RoleWorkspace(BuildRole.WRITER, out),
            blueprint=_VetCare(),
            plan=plan,
            state={
                "resolved_blocks": tuple(store_ids),
                "vendored_blocks": tuple(store_ids),
            },
        )
    )
    assert result.ok, result.detail
    assert oneshot and oneshot[0]["capabilities"] == ["vetcare_hub_veterinary_core"]
    handler = (
        out / "app" / "actions" / "vetcare_hub_veterinary_core.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in handler
    assert "def handle" in handler
    assert "coder LLM (openrouter/free)" in handler
    assert '"agent": True' in handler
    assert "deterministic contract template" not in handler
    assert "FACTORY_CODE_CLI" not in handler
    audit = (out / "app" / "actions" / "audit.py").read_text(encoding="utf-8")
    assert FACTORY_GROUNDED_PERSIST_SOURCE in audit
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["keep_path"] == KEEP_PATH_FACTORY_GROUNDED_REUSE
    assert receipt["factory_llm_written_ids"] == ["vetcare_hub_veterinary_core"]
    assert "vetcare_hub_veterinary_core" in receipt["generate_persist_ids"]
    assert receipt["inventory_gaps"] == []
    assert "pilot_zip" not in (result.detail or "").lower()
    specs = {
        "vetcare_hub_veterinary_core": {
            "entity": "veterinary_care_core",
            "fields": [{"name": "reference", "type": "str"}],
        },
        "audit": {"entity": "audit", "fields": [{"name": "reference", "type": "str"}]},
    }
    assert persist_round_trip_errors(out, specs) == []
    assert_persist_round_trip_ready(out, specs)


def test_writer_sess_336246_empty_llm_still_round_trips_after_billing(
    tmp_path, monkeypatch
):
    """sess_336246 roster: empty factory-LLM still persist-emits the GENERATE core."""
    _arm_billing_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: {"specs": {}, "handlers": {}, "model": "openrouter/free"},
    )
    plan = _Plan(
        _Cap("vetcare_hub_veterinary_core", [], "GENERATE"),
        _Cap("audit", ["audit"], "REUSE"),
    )
    store_ids = {"audit"}
    compiled = compile_brief(_VetCare(), plan, store_ids=store_ids)
    monkeypatch.setattr(
        "app.factory.build.brief_compiler.compile_brief_from_ctx",
        lambda _ctx: compiled,
    )
    out = tmp_path / "sess-336246-empty"
    with pytest.raises(RoleError) as exc:
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=RoleWorkspace(BuildRole.WRITER, out),
                blueprint=_VetCare(),
                plan=plan,
                state={
                    "resolved_blocks": tuple(store_ids),
                    "vendored_blocks": tuple(store_ids),
                },
            )
        )
    # 0.5: zero agent-authored artifacts refuses the WRITER (the old
    # assert result.ok was the false green). The emit itself still lands.
    assert "writer_no_output" in str(exc.value)
    handler = (
        out / "app" / "actions" / "vetcare_hub_veterinary_core.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in handler
    assert "def handle" in handler
    assert FACTORY_GROUNDED_PERSIST_SOURCE in handler
    assert "deterministic contract template" not in handler
    assert "FACTORY_CODE_CLI" not in handler
    receipt = json.loads((out / "docs" / "coder_receipt.json").read_text(encoding="utf-8"))
    assert receipt["ok"] is False
    assert receipt["blocker"] == NAMED_BLOCKER_CLI_BILLING
    assert receipt["honesty_class"] == NAMED_BLOCKER_CLI_FAILED
    assert receipt["factory_llm_written_ids"] == []
    assert "vetcare_hub_veterinary_core" in receipt["generate_persist_ids"]
    assert receipt["inventory_gaps"] == []
    specs = {
        cid: {"entity": cid, "fields": [{"name": "reference", "type": "str"}]}
        for cid in ("vetcare_hub_veterinary_core", "audit")
    }
    assert persist_round_trip_errors(out, specs) == []
    assert_persist_round_trip_ready(out, specs)
    assert "pilot_zip" not in str(exc.value).lower()


def test_factory_llm_generate_does_not_claim_founding_on_cli_billing(tmp_path):
    """#342: factory-LLM GENERATE writes still demote founding on CLI billing."""
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
            "authorship": {"artifacts": 24, "agent_written": 8, "templated": 16},
            "coder_receipt": {
                "ok": False,
                "blocker": "FACTORY_CODE_CLI_BILLING",
                "honesty_class": "FACTORY_CODE_CLI_FAILED",
                "factory_llm_generate_fallthrough": True,
                "factory_llm_written_ids": ["veterinary_care_core"],
                "detail": "FACTORY_CODE_CLI_BILLING: 429 — insufficient balance",
            },
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert grade["pilot_ready"] is True
    assert any(
        "FACTORY_CODE_CLI_BILLING" in b or "FACTORY_CODE_CLI_FAILED" in b
        for b in grade["blockers"]
    )
