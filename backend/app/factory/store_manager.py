"""Block Store Manager authority — schemas for Kimi Code as Store Manager.

Kimi Code is not a mere consumer of the Block Store; it is the Store Manager
under CerebrumDev governance. Major publishes and deletes require user approval.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class StoreOp(str, Enum):
    STORE_READ = "STORE_READ"
    STORE_INSTALL = "STORE_INSTALL"
    STORE_CREATE_BLOCK = "STORE_CREATE_BLOCK"
    STORE_UPDATE_BLOCK = "STORE_UPDATE_BLOCK"
    STORE_CREATE_VARIANT = "STORE_CREATE_VARIANT"
    STORE_VERSION_BLOCK = "STORE_VERSION_BLOCK"
    STORE_PUBLISH_PATCH = "STORE_PUBLISH_PATCH"
    STORE_PUBLISH_MINOR = "STORE_PUBLISH_MINOR"
    STORE_PUBLISH_MAJOR = "STORE_PUBLISH_MAJOR"
    STORE_DEPRECATE = "STORE_DEPRECATE"
    STORE_DELETE_BLOCK = "STORE_DELETE_BLOCK"
    STORE_UPDATE_DOMAIN_KIT = "STORE_UPDATE_DOMAIN_KIT"
    STORE_UPDATE_REGISTRY = "STORE_UPDATE_REGISTRY"
    STORE_RUN_COMPATIBILITY = "STORE_RUN_COMPATIBILITY"
    STORE_ROLLBACK = "STORE_ROLLBACK"


# Ops Kimi Code may run autonomously after gates pass
AUTONOMOUS_OPS = frozenset(
    {
        StoreOp.STORE_READ,
        StoreOp.STORE_INSTALL,
        StoreOp.STORE_CREATE_BLOCK,
        StoreOp.STORE_UPDATE_BLOCK,
        StoreOp.STORE_CREATE_VARIANT,
        StoreOp.STORE_VERSION_BLOCK,
        StoreOp.STORE_PUBLISH_PATCH,
        StoreOp.STORE_PUBLISH_MINOR,
        StoreOp.STORE_DEPRECATE,
        StoreOp.STORE_UPDATE_DOMAIN_KIT,
        StoreOp.STORE_UPDATE_REGISTRY,
        StoreOp.STORE_RUN_COMPATIBILITY,
        StoreOp.STORE_ROLLBACK,
    }
)

# Require explicit human approval
USER_APPROVAL_OPS = frozenset(
    {
        StoreOp.STORE_PUBLISH_MAJOR,
        StoreOp.STORE_DELETE_BLOCK,
    }
)


class ImprovementClass(str, Enum):
    PRODUCT_CONFIGURATION = "PRODUCT_CONFIGURATION"
    PRODUCT_OVERLAY = "PRODUCT_OVERLAY"
    DOMAIN_VARIANT = "DOMAIN_VARIANT"
    UNIVERSAL_BLOCK_IMPROVEMENT = "UNIVERSAL_BLOCK_IMPROVEMENT"
    NEW_REUSABLE_BLOCK = "NEW_REUSABLE_BLOCK"


class PromotionDecision(str, Enum):
    KEEP_PRODUCT_ONLY = "KEEP_PRODUCT_ONLY"
    PUBLISH_DOMAIN_VARIANT = "PUBLISH_DOMAIN_VARIANT"
    PROMOTE_AS_NEW_BLOCK = "PROMOTE_AS_NEW_BLOCK"
    UPGRADE_EXISTING_BLOCK = "UPGRADE_EXISTING_BLOCK"
    REJECT_CANDIDATE = "REJECT_CANDIDATE"


STORE_UPGRADE_CHECKLIST = (
    "generic_reusable_value",
    "contract_validation",
    "security_validation",
    "store_test_suite_passing",
    "source_product_tests_passing",
    "independent_fixture_passing",
    "no_customer_specific_data_or_logic",
    "version_and_migration_documented",
    "rollback_available",
)


class StoreAuthorityError(PermissionError):
    """Store op not allowed without approval or failed gates."""


def requires_user_approval(op: StoreOp | str) -> bool:
    return StoreOp(op) in USER_APPROVAL_OPS


def assert_store_op_allowed(op: StoreOp | str, *, user_approved: bool = False) -> None:
    op = StoreOp(op)
    if op in USER_APPROVAL_OPS and not user_approved:
        raise StoreAuthorityError(
            f"{op.value} requires explicit user approval"
        )
    if op not in AUTONOMOUS_OPS and op not in USER_APPROVAL_OPS:
        raise StoreAuthorityError(f"unknown store op {op}")


class ComparisonReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str
    store_version: Optional[str] = None
    product_version: Optional[str] = None
    classification: ImprovementClass
    decision: PromotionDecision
    evidence: List[str] = Field(default_factory=list)
    checklist: Dict[str, bool] = Field(default_factory=dict)
    notes: str = ""


class PublishGateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    op: StoreOp
    missing: List[str] = Field(default_factory=list)
    requires_user_approval: bool = False


def evaluate_publish_gates(
    op: StoreOp | str,
    checklist: Dict[str, bool],
    *,
    user_approved: bool = False,
) -> PublishGateResult:
    """Fail closed: every STORE_UPGRADE_CHECKLIST item must be true for publish."""
    op = StoreOp(op)
    missing = [k for k in STORE_UPGRADE_CHECKLIST if not checklist.get(k)]
    needs_user = requires_user_approval(op)
    allowed = not missing and (user_approved if needs_user else True)
    if op not in {
        StoreOp.STORE_PUBLISH_PATCH,
        StoreOp.STORE_PUBLISH_MINOR,
        StoreOp.STORE_PUBLISH_MAJOR,
    }:
        # Non-publish ops: authority check only
        try:
            assert_store_op_allowed(op, user_approved=user_approved)
            return PublishGateResult(
                allowed=True, op=op, requires_user_approval=needs_user
            )
        except StoreAuthorityError:
            return PublishGateResult(
                allowed=False, op=op, missing=["user_approval"], requires_user_approval=needs_user
            )
    return PublishGateResult(
        allowed=allowed and (not needs_user or user_approved),
        op=op,
        missing=missing + (["user_approval"] if needs_user and not user_approved else []),
        requires_user_approval=needs_user,
    )


def health_cycle_steps() -> List[str]:
    return [
        "Scan generated products",
        "Identify product-level block changes",
        "Compare them with Store versions",
        "Detect duplicated capabilities",
        "Detect weak or obsolete blocks",
        "Run benchmarks",
        "Recommend consolidation",
        "Publish safe upgrades",
        "Flag major changes for approval",
        "Regenerate affected reference products",
        "Record results",
    ]


def store_manager_manifest() -> Dict[str, Any]:
    return {
        "role": "Kimi Code — Factory Engineer + Block Store Manager",
        "autonomous_ops": sorted(o.value for o in AUTONOMOUS_OPS),
        "user_approval_ops": sorted(o.value for o in USER_APPROVAL_OPS),
        "improvement_classes": [c.value for c in ImprovementClass],
        "promotion_decisions": [d.value for d in PromotionDecision],
        "upgrade_checklist": list(STORE_UPGRADE_CHECKLIST),
        "health_cycle": health_cycle_steps(),
        "rule": (
            "Kimi Code does not simply pull from the Block Store. "
            "He builds products, learns from products, manages the Block Store, "
            "and feeds every proven improvement back into the factory ecosystem."
        ),
    }
