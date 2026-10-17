"""Resident Engineer + Product DNA emitters (generated with every product).

RE is created immediately after blueprint approval / at the start of generate —
never bolted on after the platform ships. Maturity starts at APPRENTICE.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import yaml

from app.factory.blueprint import ProductBlueprint
from app.factory.planner import ProductPlan
from app.factory.store_manager import store_manager_manifest

RE_MATURITY_CREATED = "CREATED"
RE_MATURITY_APPRENTICE = "APPRENTICE"

MATURITY_PATH = [
    "CREATED",
    "APPRENTICE",
    "BUILD_OBSERVER",
    "SHADOW_OPERATOR",
    "CERTIFICATION_PENDING",
    "RESIDENT_READY",
    "DEPLOYED",
]


def _dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def write_resident_engineer(
    out: Path,
    blueprint: ProductBlueprint,
    plan: ProductPlan,
    *,
    blocks_commit: str = "unknown",
    factory_commit: str = "unknown",
) -> Dict[str, Any]:
    """Emit product-agent/ scaffold. Call first in generate order."""
    root = out / "product-agent"
    (root / "repair_catalog").mkdir(parents=True, exist_ok=True)
    (root / "product_dna").mkdir(parents=True, exist_ok=True)
    (root / "build_events").mkdir(parents=True, exist_ok=True)

    charter = {
        "product_id": blueprint.product_id,
        "product_name": blueprint.product_name,
        "role": "Resident Engineer",
        "mission": (
            f"Observe, explain, and perform controlled minor repairs for "
            f"{blueprint.product_name}; never edit source, Git, or the Block Store."
        ),
        "factory_scenario": blueprint.factory_scenario.value,
        "maturity": RE_MATURITY_APPRENTICE,
        "maturity_path": MATURITY_PATH,
        "human_authority": blueprint.human_authority,
    }
    (root / "agent_charter.yaml").write_text(_dump_yaml(charter), encoding="utf-8")

    identity = {
        "agent_id": f"{blueprint.product_id}.resident_engineer",
        "kind": "resident_engineer",
        "maturity": RE_MATURITY_APPRENTICE,
        "product_id": blueprint.product_id,
        "vertical": blueprint.vertical,
    }
    (root / "agent_identity.json").write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    scenarios = {
        "factory_scenario": blueprint.factory_scenario.value,
        "operating_scenarios": [
            {
                "id": "health_diagnosis",
                "description": "Diagnose platform health from approved signals",
            },
            {
                "id": "minor_repair",
                "description": "Execute predefined minor repair contracts only",
            },
            {
                "id": "refuse_unsupported",
                "description": "Refuse source/Git/Store edits and unsupported ops",
            },
            {
                "id": "draft_expansion_plan",
                "description": "Draft structured expansion/repair requests to Factory",
            },
        ],
    }
    (root / "supported_scenarios.yaml").write_text(_dump_yaml(scenarios), encoding="utf-8")

    authority = {
        "may": [
            "health_monitor",
            "approved_logs_audit",
            "diagnose",
            "explain",
            "health_checks",
            "approved_regression",
            "compare_store_versions",
            "execute_predefined_minor_repairs",
            "draft_repair_or_expansion_plans",
            "submit_structured_requests_to_cerebrumdev",
        ],
        "must_not": [
            "edit_source",
            "push_github",
            "publish_store",
            "schema_migrations",
            "change_permissions_or_security",
            "expand_own_authority",
            "deploy",
            "arbitrary_shell",
            "modify_own_repair_catalogue",
        ],
        "fail_closed": True,
    }
    (root / "authority_policy.yaml").write_text(_dump_yaml(authority), encoding="utf-8")

    security = {
        "pii_in_learning": False,
        "edge_profile": blueprint.edge_profile,
        "human_authority_required": blueprint.human_authority,
    }
    (root / "security_boundaries.yaml").write_text(_dump_yaml(security), encoding="utf-8")

    tools = {
        "allowed_tools": ["health", "logs_read", "repair_catalog_execute", "factory_request"],
        "denied_tools": ["shell", "git_write", "store_publish", "filesystem_write_source"],
    }
    (root / "tool_permissions.yaml").write_text(_dump_yaml(tools), encoding="utf-8")

    knowledge = {
        "approved_sources": [
            "validated_blueprint",
            "certified_build_events",
            "manifests",
            "passing_tests",
            "approved_config",
            "verified_outcomes",
            "certified_store_releases",
        ],
        "forbidden_sources": [
            "private_model_reasoning",
            "uncontrolled_terminal_dumps",
            "unverified_chat",
            "failed_experiments",
        ],
    }
    (root / "knowledge_ingestion_policy.yaml").write_text(
        _dump_yaml(knowledge), encoding="utf-8"
    )

    evaluation = {
        "gates": ["PRODUCT_CERTIFIED", "RESIDENT_AGENT_CERTIFIED"],
        "status": {
            "PRODUCT_CERTIFIED": "pending",
            "RESIDENT_AGENT_CERTIFIED": "pending",
        },
        "scenarios_to_evaluate": [s["id"] for s in scenarios["operating_scenarios"]],
    }
    (root / "agent_evaluation_plan.yaml").write_text(
        _dump_yaml(evaluation), encoding="utf-8"
    )

    (root / "repair_catalog" / "README.md").write_text(
        "# Repair catalog\n\nVersioned minor-repair contracts only. Fail closed.\n",
        encoding="utf-8",
    )

    # Initial certified build event: RE created
    event = {
        "event": "resident_engineer_created",
        "maturity": RE_MATURITY_APPRENTICE,
        "product_id": blueprint.product_id,
        "factory_scenario": blueprint.factory_scenario.value,
        "factory_commit": factory_commit,
        "blocks_commit": blocks_commit,
    }
    (root / "build_events" / "0001_resident_engineer_created.json").write_text(
        json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    write_product_dna(
        root / "product_dna",
        blueprint,
        plan,
        blocks_commit=blocks_commit,
        factory_commit=factory_commit,
    )
    return {"maturity": RE_MATURITY_APPRENTICE, "path": str(root)}


def write_product_dna(
    dna: Path,
    blueprint: ProductBlueprint,
    plan: ProductPlan,
    *,
    blocks_commit: str,
    factory_commit: str,
) -> None:
    dna.mkdir(parents=True, exist_ok=True)
    lockfile = {
        "blocks_commit": blocks_commit,
        "factory_commit": factory_commit,
        "pins": [
            {"block_id": bid, "source": "dual_registry"}
            for bid in sorted(plan.dual_registered_blocks)
        ],
    }
    (dna / "block_lockfile.json").write_text(
        json.dumps(lockfile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (dna / "blueprint.json").write_text(
        json.dumps(blueprint.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (dna / "capability_resolution.json").write_text(
        json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (dna / "generation_manifest.json").write_text(
        json.dumps(
            {
                "product_id": blueprint.product_id,
                "factory_scenario": blueprint.factory_scenario.value,
                "ui_modules": blueprint.ui_modules,
                "connectors": blueprint.connectors,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (dna / "source_provenance.json").write_text(
        json.dumps(
            {"factory_commit": factory_commit, "blocks_commit": blocks_commit},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (dna / "store_manager.json").write_text(
        json.dumps(store_manager_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (dna / "known_limitations.yaml").write_text(
        _dump_yaml(
            {
                "limitations": [
                    "Resident Engineer starts APPRENTICE — dual certification pending",
                    "Minor repairs require predefined catalog contracts",
                ]
            }
        ),
        encoding="utf-8",
    )


def write_store_docs(out: Path) -> None:
    docs = out / "docs" / "store"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "store_manager_manifest.json").write_text(
        json.dumps(store_manager_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (docs / "comparison_report_template.yaml").write_text(
        _dump_yaml(
            {
                "block_id": "",
                "store_version": "",
                "product_version": "",
                "classification": "UNIVERSAL_BLOCK_IMPROVEMENT",
                "decision": "REJECT_CANDIDATE",
                "evidence": [],
                "checklist": {
                    "generic_reusable_value": False,
                    "contract_validation": False,
                    "security_validation": False,
                    "store_test_suite_passing": False,
                    "source_product_tests_passing": False,
                    "independent_fixture_passing": False,
                    "no_customer_specific_data_or_logic": False,
                    "version_and_migration_documented": False,
                    "rollback_available": False,
                },
                "notes": "",
            }
        ),
        encoding="utf-8",
    )
