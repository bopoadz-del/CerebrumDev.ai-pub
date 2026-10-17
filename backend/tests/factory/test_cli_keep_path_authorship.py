"""CLI keep-path handlers are coding-agent writes — not templated zeros.

Photographed sess_4e1ec7afa3894dc8 / tip 160af0e: FACTORY_CODE_CLI
(/usr/local/bin/kimi) completed and harvest kept four handlers. Receipt
via=cli ok=true. writer_contract still said ``0 by the coding agent, 27
templated``. budget_inspect listed the same four ids in caps_written AND
caps_templated (stub_rate=0.5). Floor painted coder idle.

#368 unused-CLI thin SUCCESS refuse must stay intact.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.factory.build.authorship import (
    DualListedAuthorshipError,
    coding_agent_artifact_ids,
    exclusive_authorship_caps,
    is_coding_agent_source,
    promote_cli_keep_ids,
    refuse_dual_listed_caps,
    writer_authorship_counts,
    writer_contract_role_detail,
)
from app.factory.build.authority import BuildRole
from app.factory.build.budget_inspect import inspect_build
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles_handlers import run_writer
from app.factory.build_jobs import _authorship


LETTINGS_KEEP = (
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
)
CLI_SOURCE = "coder CLI (/usr/local/bin/kimi)"


def _lettings_sources(*, cli_kept: bool) -> dict:
    """27-artifact writer map matching the photographed role_detail shape."""
    sources = {
        cap: CLI_SOURCE if cli_kept else "deterministic contract template"
        for cap in LETTINGS_KEEP
    }
    for cap in LETTINGS_KEEP:
        sources[f"model:{cap}"] = "deterministic contract template"
        sources[f"route:{cap}"] = "kernel execute_action template"
    for extra in (
        "jobs",
        "readme",
        "entrypoint",
        "requirements",
        "release_gate",
        "deploy_scaffold",
        "network_posture",
        "sbom",
        "permissions",
        "domain_pack",
        "persistence",
        "migrations",
        "deploy_observe",
        "domain_acceptance",
        "emitter_parity",
    ):
        sources[extra] = "deterministic contract template"
    assert len(sources) == 27
    return sources


def test_kept_cli_handler_is_coding_agent_not_template():
    assert is_coding_agent_source(CLI_SOURCE) is True
    assert is_coding_agent_source("coder LLM (deepseek-v4-pro)") is True
    assert is_coding_agent_source("FACTORY_CODE_CLI") is True
    assert is_coding_agent_source("harvested workspace handler") is True
    assert is_coding_agent_source("deterministic contract template") is False
    assert is_coding_agent_source("factory-grounded persist") is False
    sources = _lettings_sources(cli_kept=True)
    ids = coding_agent_artifact_ids(sources)
    assert list(ids) == sorted(LETTINGS_KEEP)
    counts = writer_authorship_counts(sources)
    assert counts["agent_written"] == 4
    assert counts["templated"] == 23
    detail = writer_contract_role_detail(list(LETTINGS_KEEP), sources)
    assert "4 by the coding agent" in detail
    assert "23 templated" in detail
    assert "0 by the coding agent" not in detail


def test_templated_only_sources_stay_zero_agent_written():
    sources = _lettings_sources(cli_kept=False)
    counts = writer_authorship_counts(sources)
    assert counts["agent_written"] == 0
    assert counts["templated"] == 27
    detail = writer_contract_role_detail(list(LETTINGS_KEEP), sources)
    assert "0 by the coding agent, 27 templated" in detail


def test_authorship_status_counts_cli_keep_and_surfaces_kept_ids(tmp_path):
    root = tmp_path / "build"
    (root / "docs").mkdir(parents=True)
    sources = _lettings_sources(cli_kept=True)
    (root / "docs" / "build_provenance.json").write_text(
        __import__("json").dumps(
            {
                "artifact_sources": sources,
                "brief_dispatch": {
                    "via": "cli",
                    "ok": True,
                    "kept_handler_ids": list(LETTINGS_KEEP),
                },
                "coder_failures": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    status = _authorship(root)
    auth = status["authorship"]
    assert auth["agent_written"] == 4
    assert auth["templated"] == 23
    assert auth["artifacts"] == 27
    assert set(auth["agent_artifacts"]) == set(LETTINGS_KEEP)
    assert set(auth["kept_handler_ids"]) == set(LETTINGS_KEEP)


def test_refuse_dual_listed_caps_kills_keep_path_overlap():
    with pytest.raises(DualListedAuthorshipError, match="unit_registry"):
        refuse_dual_listed_caps(list(LETTINGS_KEEP), list(LETTINGS_KEEP))
    written, templated = exclusive_authorship_caps(
        list(LETTINGS_KEEP), list(LETTINGS_KEEP)
    )
    assert written == list(LETTINGS_KEEP)
    assert templated == []
    refuse_dual_listed_caps(written, templated)


def _ledger(tmp_path: Path) -> BuildLedger:
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="residential-lettings", inputs_hash="abc")
    return ledger


def test_budget_inspect_cli_keep_is_not_also_templated(tmp_path):
    """Photographed pilot_open shape: same four caps template then CLI keep."""
    ledger = _ledger(tmp_path)
    for cap in LETTINGS_KEEP:
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"wrote handler {cap} (deterministic contract template)",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": "deterministic contract template",
            },
        )
    for cap in LETTINGS_KEEP:
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"kept CLI handler {cap} ({CLI_SOURCE})",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": CLI_SOURCE,
            },
        )
    snap = inspect_build(
        ledger,
        tmp_path / "build",
        {
            "brief_dispatch": {
                "via": "cli",
                "ok": True,
                "kept_handler_ids": list(LETTINGS_KEEP),
            }
        },
    )
    assert set(snap["caps_written"]) == set(LETTINGS_KEEP)
    assert set(snap["caps_templated"]).isdisjoint(LETTINGS_KEEP)
    assert snap["agent_written"] == 4
    assert snap["templated"] == 0
    assert snap["stub_rate"] == 0.0
    refuse_dual_listed_caps(snap["caps_written"], snap["caps_templated"])


def test_budget_inspect_empty_cli_authored_does_not_credit_kept_fill(tmp_path):
    """#374: via=cli ok=true + empty harvest must not promote factory-fill kept ids."""
    ledger = _ledger(tmp_path)
    for cap in LETTINGS_KEEP:
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"wrote handler {cap} (deterministic contract template)",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": "deterministic contract template",
            },
        )
    snap = inspect_build(
        ledger,
        tmp_path / "build",
        {
            "brief_dispatch": {
                "via": "cli",
                "ok": True,
                "cli_authored_ids": [],
                "kept_handler_ids": list(LETTINGS_KEEP),
            }
        },
    )
    assert snap["agent_written"] == 0
    assert snap["cli_or_llm_written"] == 0
    assert snap["templated"] == 4
    assert snap["stub_rate"] == 1.0
    assert set(snap["caps_written"]) == set()
    assert set(snap["caps_templated"]) == set(LETTINGS_KEEP)


def test_budget_inspect_unused_cli_templates_still_thin(tmp_path):
    """#368 shape: CLI ready but unused — do not credit keep-path writes."""
    ledger = _ledger(tmp_path)
    for cap in LETTINGS_KEEP:
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"wrote handler {cap} (deterministic contract template)",
            payload={
                "stage": "handlers",
                "capability": cap,
                "source": "deterministic contract template",
            },
        )
    snap = inspect_build(
        ledger,
        tmp_path / "build",
        {"brief_dispatch": {"via": "skipped", "ok": False, "kept_handler_ids": []}},
    )
    assert snap["agent_written"] == 0
    assert snap["templated"] == 4
    assert snap["stub_rate"] == 1.0
    assert set(snap["caps_written"]) == set()
    assert set(snap["caps_templated"]) == set(LETTINGS_KEEP)


def test_mutation_writer_counts_coder_cli_not_only_llm():
    src = inspect.getsource(run_writer)
    assert "writer_contract_role_detail" in src
    assert "coding_agent_artifact_ids" in src
    assert 'startswith("coder LLM")' not in src


def test_mutation_inspect_refuses_dual_listed_caps():
    src = inspect.getsource(inspect_build)
    assert "exclusive_authorship_caps" in src
    assert "refuse_dual_listed_caps" in src
    assert "promote_cli_keep_ids" in src


def test_empty_cli_authored_ids_does_not_promote_kept_fill():
    """#374: exit 0 + factory fill in kept_handler_ids is not CLI authorship."""
    dispatch = {
        "via": "cli",
        "ok": True,
        "cli_authored_ids": [],
        "kept_handler_ids": list(LETTINGS_KEEP),
    }
    assert promote_cli_keep_ids(dispatch) == []
    assert promote_cli_keep_ids({"brief_dispatch": dispatch}) == []
    # #375 receipts without cli_authored_ids still promote kept ids.
    legacy = {"via": "cli", "ok": True, "kept_handler_ids": list(LETTINGS_KEEP)}
    assert promote_cli_keep_ids(legacy) == list(LETTINGS_KEEP)
