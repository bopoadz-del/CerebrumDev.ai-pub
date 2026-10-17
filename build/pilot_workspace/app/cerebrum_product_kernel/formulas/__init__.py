"""Base-tier business definitions, and the overlay precedence rule.

This is a kernel-tier proposal implemented here so every generated product
receives it. It is not a port of an existing engine.

WHAT THIS IS NOT
----------------
* Product Delivery Standard Section 15 is EXPORTS AND REPORTING. There is no
  Section 15 precedence engine.
* ``encoding sheet`` is not a source in this repository (zero hits).
* ``docs/REASONING_KERNEL.md`` names AuthorityResolver and ConflictDetector
  among 22 planned components. Neither symbol exists. This module is not
  those components and does not implement them.

WHY THIS SHIPS IN THE KERNEL COPYTREE
-------------------------------------
These definitions are the floor every generated platform stands on. A floor
is not a peer that other things declare a dependency on, so this ships the
way the kernel contract ships: ``ProductGenerator._write_app`` copytrees
``cerebrum_product_kernel`` into every product (RoleRunner's
``_vendor_product_kernel`` copies the same tree file-by-file). No extra
registry, kit, or shelf entry.

Dead routes that must not carry this set:

* ``kit_pack.stock_kits`` reaches a kit only through ``factory_blocks.json``.
  ``formula_executor_v2`` is a Store runtime alias for the dual-registered
  ``formula_executor`` budget block. It is not a Factory kit and does not
  carry this set. Do not add it to the shelf to "gate" it.
* ``universal_kernel`` is a GOLD/awaiting kit-library destination, not the
  product kernel. Putting base-tier content there would contradict shipping
  in every platform.
* Blocks ``_GENERIC_BLOCK_DEFS`` / ``_EXTENDED_BLOCK_DEFS`` and the Fork
  whole-repo-copy are not the path that reaches every Factory product.
  Auth is kit-provided in the one real generated product
  (``app/steward/auth.py``); billing is extended-def / legacy-boot-only.

Product Q&A is :func:`answer_definition`. It reads :func:`definition_index`,
so an override's address, provenance, reason, and tier appear on the answer
path that copytrees into every product. Domain overlays are ingested by
``scripts/intake_formulas.py``, which runs the same precedence contract.

THE OVERLAY CONTRACT
--------------------
A domain layer may do exactly two things to this set, and must say which:

* **extend** -- contribute a definition the base does not have (waste factors,
  commission maths, CPM quantities). Its id must not collide with a base id.
* **override** -- replace a base definition, naming the exact base address it
  replaces (``overrides: "universal:gross_margin_ratio_v1"``), with its own
  provenance and a stated reason.

Anything else is refused. Specifically, an overlay definition whose id
collides with a base definition but declares no ``overrides`` is an error, not
a silent replacement -- see :class:`PrecedenceError`.

Position is not authority. Which layer wins here is stated in the data, and a
conflict nobody declared stops the resolve instead of being settled by load
order. Resolution reports the tier that answered -- ``base``,
``domain-extension``, or ``domain-override of base`` -- so a caller can always
say which layer a number came from and on whose provenance.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional

__all__ = [
    "BASE_SET_ID",
    "PrecedenceError",
    "ResolvedDefinition",
    "answer_definition",
    "definition_index",
    "load_base_definitions",
    "resolve_definitions",
]

BASE_SET_ID = "universal"

#: Tier labels. These are reported to callers, so they are part of the
#: contract rather than log strings.
TIER_BASE = "base"
TIER_EXTENSION = "domain-extension"
TIER_OVERRIDE = "domain-override of base"

_DEFINITIONS_FILE = os.path.join(os.path.dirname(__file__), "universal_definitions.json")


class PrecedenceError(Exception):
    """An overlay tried to win a conflict it never declared.

    Raised rather than resolved. A definition silently shadowing another is
    the failure this module exists to prevent: the arithmetic changes, every
    caller keeps working, and nothing in the output says which layer answered.
    """


class ResolvedDefinition(dict):
    """A definition plus the tier that supplied it.

    A dict subclass so callers can treat it as the definition it is, while
    ``tier`` / ``overrides`` / ``supersedes`` stay available for attribution.
    """

    @property
    def tier(self) -> str:
        return self["tier"]

    @property
    def supersedes(self) -> Optional[str]:
        """The base address this definition replaced, if it replaced one."""
        return self.get("supersedes")


def load_base_definitions(path: str = _DEFINITIONS_FILE) -> Dict[str, Any]:
    """Load the base set. Raises if it is absent -- it is not optional."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _address(set_id: str, definition: Dict[str, Any]) -> str:
    return "%s:%s_v%s" % (set_id, definition["id"], definition["definition_version"])


def _require(definition: Dict[str, Any], field: str, where: str) -> Any:
    value = definition.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise PrecedenceError(
            "%s definition %r declares no %s; an overlay that cannot say where "
            "its figures came from cannot outrank one that can"
            % (where, definition.get("id", "<unnamed>"), field)
        )
    return value


def resolve_definitions(
    base: Optional[Dict[str, Any]] = None,
    overlays: Iterable[Dict[str, Any]] = (),
) -> Dict[str, ResolvedDefinition]:
    """Merge domain overlays onto the base set under an explicit authority rule.

    Returns a mapping of definition id -> :class:`ResolvedDefinition`.

    Raises :class:`PrecedenceError` when an overlay:

    * collides with a base id without declaring ``overrides``;
    * declares ``overrides`` naming an address that is not in the base set
      (which is what happens when a base definition has moved to ``_v2`` and
      the override was written against ``_v1`` -- the override must be
      re-reviewed, not silently re-applied to different arithmetic);
    * declares ``overrides`` but carries no ``provenance`` or no ``reason``;
    * collides with another overlay's id.
    """
    base = load_base_definitions() if base is None else base
    base_set_id = base.get("set_id", BASE_SET_ID)

    resolved: Dict[str, ResolvedDefinition] = {}
    addresses: Dict[str, str] = {}  # base address -> id

    for definition in base.get("definitions", []) + base.get("conventions", []):
        entry = ResolvedDefinition(definition)
        entry["tier"] = TIER_BASE
        entry["origin"] = base_set_id
        entry["address"] = _address(base_set_id, definition)
        resolved[definition["id"]] = entry
        addresses[entry["address"]] = definition["id"]

    claimed_by: Dict[str, str] = {}

    for overlay in overlays:
        origin = overlay.get("set_id", "<unnamed overlay>")
        for definition in overlay.get("definitions", []):
            ident = definition.get("id")
            if not ident:
                raise PrecedenceError("%s contributed a definition with no id" % origin)

            if ident in claimed_by and claimed_by[ident] != origin:
                raise PrecedenceError(
                    "%r is defined by both %s and %s; two domain layers "
                    "claiming one definition is a conflict to settle, not a "
                    "race to win" % (ident, claimed_by[ident], origin)
                )

            target = definition.get("overrides")

            if target is None:
                if ident in resolved and resolved[ident].tier == TIER_BASE:
                    raise PrecedenceError(
                        "%s defines %r, which the base set already defines as "
                        "%s. To replace it, declare overrides=%r with a reason "
                        "and provenance; shadowing it silently is refused."
                        % (
                            origin,
                            ident,
                            _address(base_set_id, resolved[ident]),
                            _address(base_set_id, resolved[ident]),
                        )
                    )
                entry = ResolvedDefinition(definition)
                entry["tier"] = TIER_EXTENSION
                entry["origin"] = origin
                entry["address"] = _address(origin, definition)
                resolved[ident] = entry
                claimed_by[ident] = origin
                continue

            if target not in addresses:
                raise PrecedenceError(
                    "%s overrides %r, which is not a base address. Either it "
                    "never existed, or the base definition has been revised "
                    "and this override was written against arithmetic that is "
                    "no longer there -- re-review it rather than re-point it. "
                    "Known base addresses include: %s"
                    % (origin, target, ", ".join(sorted(addresses)[:3]) + ", ...")
                )

            if addresses[target] != ident:
                raise PrecedenceError(
                    "%s names %r as overriding %r, but that address belongs to "
                    "%r. An override must replace the definition it is named "
                    "for." % (origin, ident, target, addresses[target])
                )

            _require(definition, "provenance", origin)
            _require(definition, "reason", origin)

            entry = ResolvedDefinition(definition)
            entry["tier"] = TIER_OVERRIDE
            entry["origin"] = origin
            entry["address"] = _address(origin, definition)
            entry["supersedes"] = target
            resolved[ident] = entry
            claimed_by[ident] = origin

    return resolved


def _index_row(ident: str, entry: ResolvedDefinition) -> Dict[str, Any]:
    """One answer-surface row: who defined this, at which address, why."""
    provenance = entry.get("provenance") or {}
    return {
        "id": ident,
        "address": entry.get("address") or "",
        "tier": entry["tier"],
        "expression": entry.get("expression", ""),
        "supersedes": entry.get("supersedes") or "",
        "provenance": provenance,
        "reason": entry.get("reason") or "",
        "origin": entry.get("origin") or "",
    }


def definition_index(
    resolved: Optional[Dict[str, ResolvedDefinition]] = None,
) -> List[Dict[str, Any]]:
    """A flat listing of what is defined and which tier defined it.

    Consumed by :func:`answer_definition` (the product Q&A path that ships
    via kernel copytree). An override row carries the overlay address, the
    base address it supersedes, provenance, reason, and tier -- so the
    answer surface cannot report a number without saying which layer
    produced it.
    """
    resolved = resolve_definitions() if resolved is None else resolved
    return [_index_row(ident, entry) for ident, entry in sorted(resolved.items())]


def answer_definition(
    ident: str,
    *,
    resolved: Optional[Dict[str, ResolvedDefinition]] = None,
    overlays: Iterable[Dict[str, Any]] = (),
) -> Optional[Dict[str, Any]]:
    """Product Q&A: look up a definition on the resolved set.

    Returns the index row (address, provenance, reason, tier) or ``None``
    when the id is not defined. Absence stays absence -- a missing
    definition must not fall through to an invented derivation.

    Overlay resolve still refuses silent shadowing; this function does not
    invent a second merge rule.
    """
    if resolved is None:
        resolved = resolve_definitions(overlays=overlays)
    index = {row["id"]: row for row in definition_index(resolved)}
    return index.get(ident)
