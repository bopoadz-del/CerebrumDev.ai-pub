"""Cerebrum Product Delivery Standard — the factory's standing brief for its coder.

The standard prompt (standards/product_delivery_standard.md) is IMMUTABLE:
products never edit it, they attach a short Domain Pack. Its sha256 is pinned
here and asserted by tests — "we keep that prompt unchanged" is enforced,
not hoped for.

render() fills every slot and fails closed: a partial brief is never handed
to the coder.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

STANDARD_PATH = (
    Path(__file__).resolve().parent / "standards" / "product_delivery_standard.md"
)
STANDARD_SHA256 = "b0e15b92bb8f94445308d99ea25bb23da968e0204f5b2960fc802fefba6acc3b"

# Header slots (filled from the domain pack).
_HEADER_SLOTS = {
    "platform_name": "[INSERT PLATFORM NAME]",
    "domain": "[INSERT TARGET DOMAIN]",
    "product_type": "[INSERT PRODUCT TYPE]",
    "target_users": "[INSERT PRIMARY USERS]",
}
_MISSION_SLOT = "[INSERT DOMAIN-SPECIFIC PRODUCT MISSION]"

# Section 1 slots (filled from the platform block).
_PLATFORM_SLOTS = {
    "product_repository": "[OWNER/PRODUCT_REPOSITORY]",
    "default_branch": "[MAIN_OR_MASTER]",
    "working_branch": "[FEATURE_BRANCH]",
    "pull_request": "[PR_NUMBER_OR_NONE]",
    "head_sha": "[CURRENT_HEAD_SHA]",
    "capability_repository": "[BLOCK_STORE_OR_SHARED_LIBRARY_REPOSITORY]",
    "factory_repository": "[FACTORY_REPOSITORY_OR_NOT_APPLICABLE]",
    "reference_repositories": "[REFERENCE_REPOSITORIES]",
    "production_url": "[URL_IF_ALREADY_DEPLOYED_OR_NOT_DEPLOYED]",
    "test_result": "[TEST_RESULT_OR_UNKNOWN]",
    "deployment_state": "[STATE]",
}
_DEPLOYMENT_TARGET_SLOT = "[RENDER / AWS / AZURE / GCP / OTHER]"  # appears twice

# Section 3: fifteen identical [INSERT] markers, filled in document order.
DOMAIN_PACK_FIELDS = (
    "domain_purpose",
    "primary_users",
    "required_roles",
    "required_product_modules",
    "core_business_workflows",
    "authoritative_calculations",
    "domain_rules",
    "high_impact_actions",
    "prohibited_autonomous_actions",
    "data_sources",
    "required_connectors",
    "required_exports",
    "security_regulatory_rules",
    "demo_data_requirements",
    "domain_acceptance_conditions",
)

REQUIRED_PLATFORM_KEYS = tuple(_PLATFORM_SLOTS) + ("deployment_target",)
REQUIRED_DOMAIN_KEYS = (
    "platform_name",
    "domain",
    "product_type",
    "target_users",
    "mission",
) + DOMAIN_PACK_FIELDS

def _slot_markers() -> tuple:
    """Every slot this renderer fills -- the leftover check reads the SAME
    table the renderer writes from, so a new slot can never be missed."""
    return (*_HEADER_SLOTS.values(), _MISSION_SLOT, *_PLATFORM_SLOTS.values(),
            _DEPLOYMENT_TARGET_SLOT, "[INSERT]")


def load_standard() -> str:
    return STANDARD_PATH.read_text(encoding="utf-8")


def standard_sha256() -> str:
    return hashlib.sha256(load_standard().encode("utf-8")).hexdigest()


def _fmt(value: Any) -> str:
    """Strings pass through; lists become markdown bullets; anything else str()."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return "\n".join(f"- {str(item).strip()}" for item in value)
    return str(value).strip()


def render(platform: Mapping[str, Any], domain_pack: Mapping[str, Any]) -> str:
    """Assemble the coder's brief: standard + platform variables + domain pack.

    Raises ValueError naming every missing key — fail closed, never partial.
    """
    missing = [k for k in REQUIRED_PLATFORM_KEYS if not _fmt(platform.get(k, ""))]
    missing += [
        f"domain_pack.{k}" for k in REQUIRED_DOMAIN_KEYS if not _fmt(domain_pack.get(k, ""))
    ]
    if missing:
        raise ValueError(
            "delivery-standard brief is incomplete: " + ", ".join(missing)
        )
    text = load_standard()
    for key, marker in _HEADER_SLOTS.items():
        text = text.replace(marker, _fmt(domain_pack[key]))
    text = text.replace(_MISSION_SLOT, _fmt(domain_pack["mission"]))
    text = text.replace(_DEPLOYMENT_TARGET_SLOT, _fmt(platform["deployment_target"]))
    for key, marker in _PLATFORM_SLOTS.items():
        text = text.replace(marker, _fmt(platform[key]))
    for field in DOMAIN_PACK_FIELDS:  # fifteen bare [INSERT] markers, in order
        text = text.replace("[INSERT]", _fmt(domain_pack[field]), 1)
    leftovers = [m for m in _slot_markers() if m in text]
    if leftovers:  # pragma: no cover — defensive; missing keys are caught above
        raise ValueError("unfilled slots remain: " + ", ".join(leftovers))
    return text
