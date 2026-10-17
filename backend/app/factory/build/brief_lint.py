"""BRIEF LINT — reject before FACTORY_CODE_CLI session opens.

A brief that fails lint never reaches the coder. The lint checks SHAPE, never
wording:

* no template slot is left unfilled, and every claimed block resolved;
* every block contract field has a manifest;
* every acceptance bullet names the harness check that runs it (``[check:id]``);
* a budget is stated;
* every line is one the compiler itself wrote (its recorded provenance) --
  a line planted after compiling is refused;
* the brief cites no build session, and carries no other platform's machine
  identity -- a capability id, product id or platform id another platform
  declared and this blueprint did not (the known identities are loaded from
  the Store and from prior builds, never listed here). Display names are
  never evidence: two platforms may share a name, and the user's own words
  may say it.

There is no list of phrases a brief must contain. What the contracts say is
the compiler's job and its tests'; the lint only refuses a brief whose shape
is wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set

from app.factory.build.brief_lines import content_lines, line_key

SLOT_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
BUDGET_RE = re.compile(r"\b(?:budget|wall)[^\n]{0,40}?(\d+)\s*s\b", re.I)
CHECK_TAG_RE = re.compile(r"\[check:([a-z0-9_]+)\]", re.I)
SESSION_ID_RE = re.compile(r"\bsess_[0-9a-f]{6,}\b", re.I)


class BriefLintError(ValueError):
    """The compiled brief is rejected. The coder session must not open."""


@dataclass
class BriefLintResult:
    ok: bool
    errors: List[str] = field(default_factory=list)
    checks: Dict[str, Any] = field(default_factory=dict)

    def raise_if_failed(self) -> None:
        if not self.ok:
            raise BriefLintError(
                "BRIEF_LINT_REJECTED — session never opens: " + "; ".join(self.errors)
            )

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors), "checks": dict(self.checks)}


#: The compiler's slot key for the harness-run acceptance section.
ACCEPTANCE_SLOT = "ACCEPTANCE"


def _acceptance_bullets(compiled: Any) -> List[str]:
    """The ACCEPTANCE section's bullets, taken from the compiled brief's own
    ``slots`` record -- never by searching its text for a heading. A brief
    that carries no slots was not compiled by the Factory and has none."""
    slots = getattr(compiled, "slots", None) or {}
    body = str(slots.get(ACCEPTANCE_SLOT) or "") if isinstance(slots, dict) else ""
    return [
        line.strip()
        for line in body.splitlines()
        if line.strip().startswith("-")
    ]


def acceptance_check_ids(compiled: Any) -> tuple:
    """The harness check ids the compiled ACCEPTANCE section declares (its
    ``[check:<id>]`` tags), in first-seen order -- the checks this brief
    turns on."""
    seen: List[str] = []
    for bullet in _acceptance_bullets(compiled):
        for match in CHECK_TAG_RE.finditer(bullet):
            check = match.group(1).lower()
            if check not in seen:
                seen.append(check)
    return tuple(seen)


def _kit_manifest_errors(manifests: Any) -> List[str]:
    """A kit manifest in a brief is identity + the claimed blocks' contract.

    Anything else -- the product the kit was carved from, its capability list,
    its blueprint path, blocks this build did not claim -- is another
    product's record leaking into this build's brief.
    """
    from app.factory.kit_pack import (
        KIT_BRIEF_CONTRACT_KEYS,
        KIT_BRIEF_IDENTITY_KEYS,
        kit_brief_view,
    )

    allowed = set(KIT_BRIEF_IDENTITY_KEYS) | set(KIT_BRIEF_CONTRACT_KEYS)
    out: List[str] = []
    if not isinstance(manifests, dict):
        return out
    for kit_id, kit in sorted(manifests.items()):
        if not isinstance(kit, dict):
            continue
        extra = sorted(set(kit) - allowed)
        if extra:
            out.append(f"kit manifest {kit_id} carries non-contract keys: " + ", ".join(extra))
        view = kit_brief_view(kit, [str(b) for b in (kit.get("product_blocks") or [])])
        wider = [k for k in KIT_BRIEF_CONTRACT_KEYS if k in kit and kit[k] != view.get(k)]
        if wider:
            out.append(
                f"kit manifest {kit_id} names blocks this build did not claim under: "
                + ", ".join(wider)
            )
    return out


def _known_literals() -> FrozenSet[str]:
    from app.factory.build.product_literals import default_roots, known_product_literals

    return known_product_literals(**default_roots())


def _own_names(compiled: Any) -> Set[str]:
    own: Set[str] = set()
    for attr in ("product_id", "platform_id", "product_name", "vertical"):
        value = getattr(compiled, attr, None)
        if value:
            own.add(str(value))
    own.update(str(c) for c in (getattr(compiled, "capabilities", None) or []))
    for item in getattr(compiled, "inventory", None) or []:
        if getattr(item, "capability_id", None):
            own.add(str(item.capability_id))
        own.update(str(b) for b in (getattr(item, "block_ids", None) or []))
    own.update(str(b) for b in (getattr(compiled, "store_ids", None) or []))
    return own


def lint_brief(
    compiled: Any,
    *,
    known_literals: Optional[FrozenSet[str]] = None,
) -> BriefLintResult:
    """Reject a brief whose shape is not ready to dispatch."""
    errors: List[str] = []
    text = compiled.text if hasattr(compiled, "text") else str(compiled)

    slots = SLOT_RE.findall(text)
    if slots:
        errors.append("unfilled template slot: " + ", ".join(sorted(set(slots))))

    missing = list(getattr(compiled, "missing_reuse", ()) or [])
    for item in getattr(compiled, "inventory", ()) or []:
        missing.extend(getattr(item, "missing", ()) or [])
    if missing:
        errors.append(
            "unresolved block id: " + ", ".join(sorted({str(m) for m in missing}))
        )

    contracts = getattr(compiled, "contracts", None) or {}
    manifests = getattr(compiled, "kit_manifests", None) or {}
    manifest_fields: Set[str] = set()
    for kit in manifests.values() if isinstance(manifests, dict) else []:
        if not isinstance(kit, dict):
            continue
        for key in ("blocks", "inputs", "fields", "reads", "writes"):
            values = kit.get(key)
            if isinstance(values, list):
                for entry in values:
                    if isinstance(entry, dict):
                        name = entry.get("id") or entry.get("name")
                        if name:
                            manifest_fields.add(str(name))
                    elif entry:
                        manifest_fields.add(str(entry))
            elif isinstance(values, dict):
                manifest_fields.update(str(k) for k in values)
    orphan_fields: List[str] = []
    if isinstance(contracts, dict):
        for bid, contract in contracts.items():
            if not isinstance(contract, dict):
                continue
            for item in contract.get("declared_inputs") or []:
                name = item.get("name") if isinstance(item, dict) else item
                if not name:
                    continue
                in_manifest = (
                    str(name) in manifest_fields
                    or str(bid) in manifest_fields
                    or bool(contract.get("block_id"))
                    or bool(contract.get("from_block_json"))
                    or bool(contract.get("declared_inputs"))
                )
                if not in_manifest:
                    orphan_fields.append(f"{bid}.{name}")
    if orphan_fields:
        errors.append(
            "contract field without manifest: " + ", ".join(sorted(set(orphan_fields)))
        )

    kit_errors = _kit_manifest_errors(manifests)
    errors.extend(kit_errors)

    bullets = _acceptance_bullets(compiled)
    untagged = [b for b in bullets if not CHECK_TAG_RE.search(b)]
    if not bullets:
        errors.append("acceptance line without executable check: (none written)")
    elif untagged:
        errors.append(
            "acceptance line without executable check: " + "; ".join(untagged[:5])
        )

    budget_s = getattr(compiled, "budget_s", None)
    if budget_s in (None, "", 0, 0.0):
        errors.append("missing budget")

    recorded = getattr(compiled, "emitted_lines", None)
    unsourced: List[str] = []
    if not recorded:
        errors.append("brief carries no provenance record (not compiled by the Factory)")
    else:
        for line in content_lines(text):
            if line_key(line) not in recorded:
                unsourced.append(line)
        if unsourced:
            errors.append(
                "orphan line (not written by the compiler): " + unsourced[0][:160]
            )

    sessions = sorted(set(SESSION_ID_RE.findall(text)))
    if sessions:
        errors.append("brief cites a build session: " + ", ".join(sessions[:4]))

    from app.factory.build.product_literals import foreign_identities_in

    known = known_literals if known_literals is not None else _known_literals()
    foreign = foreign_identities_in(text, known, _own_names(compiled))
    if foreign:
        errors.append(
            "brief carries another product's identity (capability / product / platform id): "
            + ", ".join(foreign[:6])
        )

    return BriefLintResult(
        ok=not errors,
        errors=errors,
        checks={
            "slots": slots,
            "missing_reuse": sorted({str(m) for m in missing}),
            "acceptance_bullets": len(bullets),
            "budget_s": budget_s,
            "unsourced": unsourced[:8],
            "sessions": sessions,
            "foreign": foreign,
        },
    )


def lint_or_raise(compiled: Any, **kwargs: Any) -> BriefLintResult:
    result = lint_brief(compiled, **kwargs)
    result.raise_if_failed()
    return result
