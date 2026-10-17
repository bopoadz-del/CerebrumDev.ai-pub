"""Adapt RetailClient hat/agent manifest patterns into product-neutral hats.

RetailClient uses ``retail.base`` + ``retail.hat.*`` manifests. The Factory emits
the same shape for generated products with neutralized ids:
``{vertical}.base`` and ``{vertical}.hat.{discipline}``.

Which discipline a capability belongs to, which hats hand off to which, and
which workflows compose which capabilities are the KIT'S data -- the ``hats``
section of the Store kit that serves the build's vertical. This module holds
the shape and the defaults (a capability is its own discipline; one linear
workflow over the plan) and names no capability, product or vertical.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from app.factory.blueprint import ProductBlueprint
from app.factory.planner import ProductPlan


def discipline_for(capability_id: str, hats: Optional[Mapping[str, Any]] = None) -> str:
    disciplines = (hats or {}).get("disciplines") or {}
    if capability_id in disciplines:
        return str(disciplines[capability_id])
    return capability_id.replace("-", "_")


def build_hat_manifests(
    blueprint: ProductBlueprint,
    plan: ProductPlan,
    hats: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return base + hat manifests adapted from TEK patterns for this plan."""
    vertical = blueprint.vertical.replace("-", "_")
    base_id = f"{vertical}.base"
    action_ids = [
        f"{vertical}.{c.capability_id.replace('-', '_')}" for c in plan.capabilities
    ]

    base: Dict[str, Any] = {
        "manifest_version": "1.0.0",
        "agent_id": base_id,
        "kind": "base",
        "display_name": f"{blueprint.product_name} Base Agent",
        "discipline": "base",
        "extends": None,
        "summary": (
            "Shared product cortex adapted from RetailClient hat patterns: trusted "
            "context, ActionRegistry awareness, honesty policies. Discipline hats "
            "specialize this base — they never replace it."
        ),
        "activation": {
            "mode": "auto",
            "default_enabled": True,
            "triggers": [{"type": "intent", "value": "unknown"}],
            "put_off_by": [f"disable:{base_id}"],
        },
        "context_sources": [
            {
                "source_id": "project_docs",
                "kind": "project_documents",
                "description": "Tenant/project documents via hybrid retrieval",
            }
        ],
        "allowed_actions": action_ids,
        "handoffs": [],
        "memory": {
            "scope": "tenant_project",
            "save": [],
            "pii_policy": "never_store_pii",
        },
        "human_authority": blueprint.human_authority,
        "tags": ["base", "factory-generated", vertical],
    }

    manifests: List[Dict[str, Any]] = [base]
    seen_disciplines: set[str] = set()

    for cap in plan.capabilities:
        discipline = discipline_for(cap.capability_id, hats)
        if discipline in seen_disciplines or discipline == "base":
            continue
        seen_disciplines.add(discipline)
        hat_id = f"{vertical}.hat.{discipline}"
        action_id = f"{vertical}.{cap.capability_id.replace('-', '_')}"
        manifests.append(
            {
                "manifest_version": "1.0.0",
                "agent_id": hat_id,
                "kind": "hat",
                "display_name": f"{blueprint.product_name} {discipline.replace('_', ' ').title()} Hat",
                "discipline": discipline,
                "extends": base_id,
                "summary": (
                    f"Specialized hat for capability '{cap.capability_id}' "
                    f"(strategy={cap.strategy}). Adapted from RetailClient retail "
                    f"hat pattern; product-neutral."
                ),
                "activation": {
                    "mode": "hybrid",
                    "default_enabled": True,
                    "triggers": [
                        {"type": "intent", "value": discipline},
                        {"type": "capability", "value": cap.capability_id},
                    ],
                    "put_off_by": [f"disable:{hat_id}"],
                },
                "allowed_actions": [action_id],
                "block_ids": list(cap.block_ids),
                "strategy": cap.strategy,
                "handoffs": [],
                "memory": {
                    "scope": "tenant_project",
                    "save": [],
                    "pii_policy": "never_store_pii",
                },
                "human_authority": blueprint.human_authority,
                "tags": ["hat", "factory-generated", vertical, discipline],
            }
        )

    # Handoffs the kit declares, wired when both hats exist in this build.
    by_disc = {h["discipline"]: h for h in manifests if h["kind"] == "hat"}
    for handoff in (hats or {}).get("handoffs") or []:
        src, dst = by_disc.get(handoff.get("from")), by_disc.get(handoff.get("to"))
        if src and dst:
            src.setdefault("handoffs", []).append(
                {"to_agent_id": dst["agent_id"], "when": handoff.get("when")}
            )

    return manifests


def _step(step: Mapping[str, Any], blueprint: ProductBlueprint) -> Dict[str, Any]:
    out = dict(step)
    if out.get("required") == "$human_authority":
        out["required"] = blueprint.human_authority
    return out


def build_workflows(
    blueprint: ProductBlueprint,
    plan: ProductPlan,
    hats: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Workflow specs: the kit's declared workflows whose ``when_all``
    capabilities this plan has; else one linear workflow over the plan."""
    vertical = blueprint.vertical.replace("-", "_")
    cap_ids = {c.capability_id for c in plan.capabilities}
    workflows: List[Dict[str, Any]] = []
    for wf in (hats or {}).get("workflows") or []:
        needs = set(wf.get("when_all") or [])
        if not needs or not needs <= cap_ids:
            continue
        workflows.append(
            {
                "workflow_id": f"{vertical}.{wf['id']}",
                "name": wf.get("name") or wf["id"],
                "description": wf.get("description") or "",
                "steps": [_step(s, blueprint) for s in wf.get("steps") or []],
            }
        )

    if not workflows:
        workflows.append(
            {
                "workflow_id": f"{vertical}.capability_sequence",
                "name": "Capability sequence",
                "description": "Default linear workflow over planned capabilities.",
                "steps": [
                    {"capability_id": c.capability_id, "role": "execute"}
                    for c in plan.capabilities
                ],
            }
        )
    return workflows
