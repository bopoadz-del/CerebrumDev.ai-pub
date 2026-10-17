"""Capability planner — resolves REUSE/ADAPT/COMPOSE/GENERATE/STUB/UNSUPPORTED."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.factory.blueprint import CapabilitySpec, CapabilityStrategyHint, ProductBlueprint
from app.factory.dual_registry import DualRegistryError, assert_dual_registered, dual_registered_ids

logger = logging.getLogger(__name__)


@dataclass
class PlannedCapability:
    capability_id: str
    strategy: str
    block_ids: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ProductPlan:
    product_id: str
    capabilities: List[PlannedCapability]
    unsupported: List[str] = field(default_factory=list)
    dual_registered_blocks: List[str] = field(default_factory=list)
    #: True for every plan that passed the UNSUPPORTED gate. ``survey()``
    #: returns False, and generation refuses such a plan. This travels with
    #: the plan rather than being a caller's argument so that a tolerated
    #: plan cannot be handed to a builder that never asked how it was made.
    fail_closed: bool = True
    #: Capabilities that resolved to blocks, but none domain-relevant for
    #: the vertical's kit (the vet-clinic generic-plumbing substitution).
    inventory_domain_gaps: List[Dict[str, Any]] = field(default_factory=list)
    #: Blocks a blueprint named that its vertical may not attach, each with
    #: the reason. See app.factory.vertical_scope: dropped, never silently.
    vertical_scoped_out: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capabilities": [asdict(c) for c in self.capabilities],
            "dual_registered_blocks": list(self.dual_registered_blocks),
            "fail_closed": self.fail_closed,
            "product_id": self.product_id,
            "unsupported": list(self.unsupported),
            "inventory_domain_gaps": list(self.inventory_domain_gaps),
            "vertical_scoped_out": list(self.vertical_scoped_out),
        }


def assert_generatable(plan: ProductPlan) -> ProductPlan:
    """Refuse to build from a plan that was never gated.

    Both engines accept an injected ``plan=``, so a survey result could
    otherwise reach a builder that assumed every plan it is handed already
    passed the UNSUPPORTED gate. Checked at the injection point rather than
    trusted at the call site.
    """
    if not getattr(plan, "fail_closed", True):
        raise DualRegistryError(
            "refusing to generate from a survey plan (fail_closed=False): "
            "survey() tolerates UNSUPPORTED capabilities and is for "
            "inspection only. Use plan() to build. Unsupported: "
            + (", ".join(plan.unsupported) or "none recorded")
        )
    return plan


class CapabilityPlanner:
    """Fail-closed planner: unknown or non-dual-registered blocks → UNSUPPORTED."""

    def __init__(self, blocks_root=None, factory_shelf=None):
        self.blocks_root = blocks_root
        self.factory_shelf = factory_shelf
        self._dual = dual_registered_ids(blocks_root, factory_shelf)
        self._vertical_scopes: Optional[Dict[str, Any]] = None

    def _resolve(self, blueprint: ProductBlueprint):
        planned: List[PlannedCapability] = []
        unsupported: List[str] = []
        used_blocks: List[str] = []
        scoped_out: List[Dict[str, Any]] = []
        vertical = getattr(blueprint, "vertical", "")

        for cap in blueprint.capabilities:
            cap, dropped = self._scope_to_vertical(cap, vertical)
            scoped_out.extend(dropped)
            item = self._plan_one(cap)
            planned.append(item)
            used_blocks.extend(item.block_ids)
            if item.strategy == CapabilityStrategyHint.UNSUPPORTED.value:
                unsupported.append(cap.id)

        self._last_scoped_out = scoped_out
        return planned, unsupported, used_blocks

    def _scope_to_vertical(self, cap: CapabilitySpec, vertical: Any):
        """Drop blocks this vertical may not attach. The one chokepoint.

        COLLECTOR reads block ids from the plan and CLONER reads them from
        COLLECTOR, so a block removed here is never cloned. The capability is
        copied, never mutated: the blueprint is the architect's document.
        """
        if not cap.block_ids:
            return cap, []
        from app.factory.dual_registry import _default_blocks_root
        from app.factory.vertical_scope import block_verticals, scope_blocks

        if self._vertical_scopes is None:
            try:
                root = self.blocks_root or _default_blocks_root()
            except Exception:  # noqa: BLE001 — no Store, so nothing is scoped
                root = None
            self._vertical_scopes = block_verticals(root)
        kept, dropped = scope_blocks(cap.block_ids, vertical, self._vertical_scopes)
        if not dropped:
            return cap, []
        for row in dropped:
            row["capability_id"] = cap.id
            logger.info("vertical scope: %s", row["reason"])
        return cap.model_copy(update={"block_ids": list(kept)}), dropped

    def plan(self, blueprint: ProductBlueprint) -> ProductPlan:
        """Resolve a blueprint, refusing any UNSUPPORTED capability.

        There is deliberately no way to ask this method not to fail. It used
        to take ``fail_on_unsupported=True``, which meant the fail-closed
        behaviour was a default rather than an invariant: any present or
        future caller could switch the gate off by passing one keyword, and
        nothing in the build path would know it had happened. No caller ever
        passed it, which is exactly why removing it costs nothing and why it
        was worth removing before someone did.

        Callers that need to *see* unsupported capabilities without building
        want :meth:`survey`.
        """
        planned, unsupported, used_blocks = self._resolve(blueprint)

        if unsupported:
            raise DualRegistryError(
                "UNSUPPORTED capabilities (fail closed): " + ", ".join(unsupported)
            )

        # Final dual gate for every block referenced
        if used_blocks:
            assert_dual_registered(used_blocks, self.blocks_root, self.factory_shelf)

        return ProductPlan(
            product_id=blueprint.product_id,
            capabilities=planned,
            unsupported=unsupported,
            dual_registered_blocks=sorted(set(used_blocks)),
            fail_closed=True,
            inventory_domain_gaps=self._domain_gaps(blueprint, planned),
            vertical_scoped_out=list(getattr(self, "_last_scoped_out", [])),
        )

    def survey(self, blueprint: ProductBlueprint) -> ProductPlan:
        """Resolve a blueprint for inspection. Never raises on UNSUPPORTED.

        This exists for diagnostics — a gate reporting *which* capabilities a
        blueprint cannot support is more useful than one reporting only that
        planning threw. The result is marked ``fail_closed=False`` and
        generation refuses it, so tolerance cannot leak into a build: the
        distinction is carried by the returned object, not by a flag the
        builder would have to remember to check.

        Deliberately not env-gated. An environment variable is a dashboard
        setting, and this repo already ruled (``harvest.py``) that a dashboard
        setting is not authority.
        """
        planned, unsupported, used_blocks = self._resolve(blueprint)
        return ProductPlan(
            product_id=blueprint.product_id,
            capabilities=planned,
            unsupported=unsupported,
            dual_registered_blocks=sorted(set(used_blocks)),
            fail_closed=False,
            inventory_domain_gaps=self._domain_gaps(blueprint, planned),
            vertical_scoped_out=list(getattr(self, "_last_scoped_out", [])),
        )

    @staticmethod
    def _domain_gaps(
        blueprint: ProductBlueprint, planned: List[PlannedCapability]
    ) -> List[Dict[str, Any]]:
        """2b: capabilities that resolved to blocks, but none domain-relevant."""
        try:
            from app.factory.inventory import domain_gaps

            caps = [
                {"capability_id": c.capability_id, "block_ids": list(c.block_ids)}
                for c in planned
            ]
            return domain_gaps(
                str(getattr(blueprint, "vertical", "") or ""), caps
            )
        except Exception:  # noqa: BLE001 — inventory advice never fails a plan
            return []

    def _plan_one(self, cap: CapabilitySpec) -> PlannedCapability:
        hint = cap.strategy_hint
        if hint == CapabilityStrategyHint.UNSUPPORTED:
            return PlannedCapability(cap.id, "UNSUPPORTED", [], "explicit UNSUPPORTED hint")

        if not cap.block_ids:
            strategy = (hint or CapabilityStrategyHint.GENERATE).value
            if strategy == "UNSUPPORTED":
                return PlannedCapability(cap.id, "UNSUPPORTED", [], "no blocks and unsupported")
            return PlannedCapability(
                cap.id,
                strategy if strategy in {"GENERATE", "STUB", "ADAPT"} else "GENERATE",
                [],
                "kernel/template generation (no block ids)",
            )

        missing = [b for b in cap.block_ids if b not in self._dual]
        if missing:
            return PlannedCapability(
                cap.id,
                "UNSUPPORTED",
                [],
                f"blocks not dual-registered: {', '.join(missing)}",
            )

        if hint:
            strategy = hint.value
        elif len(cap.block_ids) > 1:
            strategy = "COMPOSE"
        else:
            strategy = "REUSE"

        if strategy == "UNSUPPORTED":
            return PlannedCapability(cap.id, "UNSUPPORTED", [], "hint UNSUPPORTED")

        return PlannedCapability(cap.id, strategy, list(cap.block_ids), "dual-registered")
