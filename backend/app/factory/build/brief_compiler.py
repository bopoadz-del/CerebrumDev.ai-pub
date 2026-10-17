"""Compile ONE gated coding-agent brief — deterministic fill, no LLM.

TEMPLATE (factory/standards/BRIEF_TEMPLATE.md) is the owner aviation shape.
FILL is registry + block.json + domain pack + intake blueprint. An LLM
never writes or edits this text: it is the brief's CONTRACT. The architect's
NARRATIVE (build/architect.py) is a separate field, prepended at dispatch.

Staged cuts from the one compiled brief:

    CUT 1 INVENTORY — read-only REUSE / GAPS, then STOP
    CUT 2 VALIDATE  — runner checks claimed ids against the registry
    CUT 3 BUILD     — confirmed gaps, contracts, READS/WRITES/NEVER
    PHASE 1–3 DO/ACCEPTANCE — backend → frontend+RAG → integration
    ACCEPTANCE      — harness, not the coder
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import re

from app.factory.build.authorship import (
    full_pilot_authorship_acceptance_line,
    full_pilot_authorship_forbidden_lines,
    full_pilot_authorship_rules_text,
    n_required_capabilities_from,
)
from app.factory.build.block_obligations import ENVELOPE_STATUS_VALUES
from app.factory.build.brief_gates import PRODUCT_GATE_CHECK, SUITE_CHECK
from app.factory.build.persist_accept import (
    FACTORY_GROUNDED_PERSIST_SOURCE,
    PRODUCT_ROUND_TRIP_CHECK,
    persist_accept_acceptance_line,
    persist_accept_forbidden_lines,
    persist_accept_rules_text,
)
from app.factory.build.reuse_accept import (
    reuse_accept_acceptance_line,
    reuse_accept_forbidden_lines,
    reuse_accept_rules_text,
)
from app.factory.build.schema_accept import (
    schema_accept_acceptance_line,
    schema_accept_rules_text,
)
from app.factory.build.workflow_accept import (
    FACTORY_GROUNDED_EVENT_BUS_SOURCE,
    event_bus_workflow_capability_ids,
    needs_grounded_event_bus_handler,
    workflow_accept_acceptance_line,
    workflow_accept_forbidden_lines,
    workflow_accept_rules_text,
)
from app.factory.build.intake_blueprint import (
    field_source_index,
    intake_from_product_blueprint,
    validate_intake,
)
from app.factory.build.product_gate import GATE_SCOPES
from app.factory.build.reuse_lookup import (
    ReuseRecord,
    is_store_exact_id_miss,
    resolve_store_presence,
)
from app.factory.build.writer_brief import CODING_AGENT_BRIEF, level_exit_condition
from app.factory.build.writer_phases import writer_phase_slot_bodies
from app.factory.build.brief_lines import emitted_line_set
from app.factory.coder import coder_budget_s
from app.factory.delivery_standard import DOMAIN_PACK_FIELDS
from app.factory.dual_registry import dual_registered_ids
from app.factory.kit_pack import (
    find_kit_manifest,
    kit_brief_view,
    kits_for_blocks,
    render_kit_manifest,
)

STANDARDS = Path(__file__).resolve().parents[1] / "standards"
TEMPLATE_PATH = STANDARDS / "BRIEF_TEMPLATE.md"
TEMPLATE_REVISION = "2026-09-04"


class InventoryHalt(RuntimeError):
    """A claimed REUSE is not in the Store registry. Halt before WRITER builds."""


class BriefCompileError(ValueError):
    """The template could not be filled. Unfilled slot, not a default."""


@dataclass
class InventoryItem:
    capability_id: str
    strategy: str
    block_ids: List[str] = field(default_factory=list)
    verified_present: List[str] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)
    dropped_reuse: List[str] = field(default_factory=list)
    notes: str = ""
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    never: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)
    handler_source: str = ""

    @property
    def is_reuse(self) -> bool:
        return (
            bool(self.verified_present)
            and not self.missing
            and bool(self.handler_source)
        )

    @property
    def is_gap(self) -> bool:
        return not self.block_ids or self.strategy in {"GENERATE", "STUB", "GAP"}


@dataclass
class CompiledBrief:
    """One brief plus the inventory the runner must check before build."""

    text: str
    product_name: str
    vertical: str
    product_id: str
    inventory: List[InventoryItem]
    store_ids: List[str]
    kit_manifests: Dict[str, Any]
    domain_pack: Dict[str, Any]
    missing_reuse: List[str]
    capabilities: List[str]
    intake: Dict[str, Any] = field(default_factory=dict)
    contracts: Dict[str, Any] = field(default_factory=dict)
    reuse_records: Dict[str, Any] = field(default_factory=dict)
    line_sources: Dict[str, str] = field(default_factory=dict)
    #: Every content line the compiler wrote. The lint refuses any line
    #: not in this record, so provenance is by construction, not by phrase.
    emitted_lines: FrozenSet[str] = field(default_factory=frozenset)
    #: The rendered section bodies (TARGET, BUILD, ACCEPTANCE, ...), kept as
    #: data so a reader takes a section by key -- never by searching ``text``
    #: for its heading.
    slots: Dict[str, str] = field(default_factory=dict)
    budget_s: float = 0.0
    template_revision: str = TEMPLATE_REVISION
    #: The architect's NARRATIVE (build/architect.py). Never part of ``text``:
    #: ``text`` is the CONTRACT this compiler wrote and the lint vouches for;
    #: the coder receives ``architect.dispatch_text`` = NARRATIVE + CONTRACT.
    narrative: str = ""

    @property
    def acceptance_checks(self) -> Tuple[str, ...]:
        """The check ids this brief turns on (its ACCEPTANCE ``[check:id]``
        tags). A gate outside them -- and outside the floor checks the brief
        enforces -- is factory-invented (brief_gates)."""
        from app.factory.build.brief_lint import acceptance_check_ids

        return acceptance_check_ids(self)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_name": self.product_name,
            "vertical": self.vertical,
            "product_id": self.product_id,
            "capabilities": list(self.capabilities),
            "store_ids": list(self.store_ids),
            "missing_reuse": list(self.missing_reuse),
            "inventory": [asdict(item) for item in self.inventory],
            "kit_manifests": dict(self.kit_manifests),
            "domain_pack": dict(self.domain_pack),
            "intake": dict(self.intake),
            "budget_s": self.budget_s,
            "template_revision": self.template_revision,
            "text": self.text,
        }


def load_brief_template() -> str:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    if TEMPLATE_REVISION not in text:
        raise BriefCompileError(
            f"BRIEF_TEMPLATE.md is missing dated revision {TEMPLATE_REVISION}"
        )
    return text


def store_registry_ids(
    blocks_root: Optional[Path] = None,
    factory_shelf: Optional[Path] = None,
) -> Set[str]:
    """Exact block ids the Store / dual registry currently provides."""
    return set(dual_registered_ids(blocks_root, factory_shelf))


def compile_inventory(
    plan: Any,
    store_ids: Iterable[str],
    *,
    reuse_records: Optional[Mapping[str, ReuseRecord]] = None,
) -> List[InventoryItem]:
    """Classify each planned capability as verified REUSE, gap, or missing.

    Never assume a block exists. A claimed id that is not in ``store_ids``
    is flagged missing — the runner must halt before the build step.

    A Store exact-id ``present: false`` is different: the architect invented
    REUSE (often from the local vendor mirror / dual-registry). Drop the
    claim so the capability is a named GAP. Do not HALT the session and do
    not keep the REUSE line.
    """
    known = {str(b) for b in store_ids if str(b).strip()}
    records = dict(reuse_records or {})
    items: List[InventoryItem] = []
    for cap in getattr(plan, "capabilities", ()) or ():
        cid = str(getattr(cap, "capability_id", "") or getattr(cap, "id", "") or "")
        strategy = str(getattr(cap, "strategy", "") or "GENERATE").upper()
        claimed = [str(b) for b in (getattr(cap, "block_ids", None) or []) if str(b).strip()]
        present: List[str] = []
        missing: List[str] = []
        dropped: List[str] = []
        reads: List[str] = []
        writes: List[str] = []
        never: List[str] = []
        acceptance: List[str] = []
        for bid in claimed:
            rec = records.get(bid)
            if rec is not None:
                if rec.present:
                    present.append(bid)
                    reads.extend(rec.reads)
                    writes.extend(rec.writes)
                    never.extend(rec.never)
                    acceptance.extend(rec.acceptance)
                elif is_store_exact_id_miss(rec):
                    dropped.append(bid)
                else:
                    missing.append(bid)
            elif bid in known:
                present.append(bid)
            else:
                missing.append(bid)
        honest_claimed = [bid for bid in claimed if bid not in dropped]
        notes = str(getattr(cap, "notes", "") or "")
        if dropped:
            drop_note = (
                "unverified REUSE dropped (Store exact-id present=false): "
                + ", ".join(dropped)
            )
            notes = f"{notes} ({drop_note})" if notes else drop_note
        handler_source = ""
        if present:
            handler_source = (
                FACTORY_GROUNDED_EVENT_BUS_SOURCE
                if needs_grounded_event_bus_handler(cid, present)
                else FACTORY_GROUNDED_PERSIST_SOURCE
            )
        if missing:
            label = "MISSING"
        elif present and handler_source:
            label = "REUSE" if strategy in {"", "REUSE", "COMPOSE", "ADAPT"} else strategy
        elif dropped:
            label = "GAP"
        else:
            label = "GAP" if strategy in {"", "REUSE", "COMPOSE"} else strategy
        if label == "REUSE" and not handler_source:
            label = "GAP"
        items.append(
            InventoryItem(
                capability_id=cid,
                strategy=label,
                block_ids=honest_claimed,
                verified_present=present,
                missing=missing,
                dropped_reuse=dropped,
                notes=notes,
                reads=sorted(set(reads)),
                writes=sorted(set(writes)),
                never=sorted(set(never)),
                acceptance=sorted(set(acceptance)),
                handler_source=handler_source,
            )
        )
    return items


def verify_inventory(compiled: CompiledBrief) -> None:
    """Halt if any inventory row claims REUSE for an id the Store does not have.

    This is CUT 2 — the runner check that must fire BEFORE the WRITER build
    step opens — not at CLONER, and not after the coder has already guessed.
    """
    missing = list(compiled.missing_reuse)
    for item in compiled.inventory:
        missing.extend(f"{item.capability_id}:{bid}" for bid in item.missing)
        if item.strategy == "REUSE" and not item.handler_source:
            missing.append(f"{item.capability_id}:no-handler-source")
    seen: Set[str] = set()
    ordered: List[str] = []
    for name in missing:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    if ordered:
        raise InventoryHalt(
            "claimed REUSE is not in the Store registry or has no "
            "registry-verified handler source — HALT before WRITER "
            "build (not at CLONER): " + ", ".join(ordered)
        )


def _capability_description(cap: Any) -> str:
    for attr in ("notes", "description", "customer_words"):
        value = getattr(cap, attr, None)
        if value:
            return str(value)
    data = getattr(cap, "__dict__", {}) or {}
    return str(data.get("description") or data.get("notes") or "")


def _load_kit_manifests(
    block_ids: Sequence[str],
    *,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    grouped = kits_for_blocks(block_ids)
    out: Dict[str, Any] = {}
    for kit_id, bids in grouped.items():
        existing: Optional[Mapping[str, Any]] = None
        src = find_kit_manifest(kit_id, blocks_root)
        if src is not None:
            try:
                existing = json.loads(src.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = None
        out[kit_id] = kit_brief_view(
            render_kit_manifest(
                kit_id,
                bids,
                source_kind="brief-compiler",
                existing=existing,
            ),
            bids,
        )
    return out


def synthesize_domain_pack(blueprint: Any, plan: Any) -> Dict[str, Any]:
    """Honest pack from the blueprint. Empty slots stay named, never invented."""
    name = str(getattr(blueprint, "product_name", "") or "platform")
    vertical = str(getattr(blueprint, "vertical", "") or "product")
    summary = str(getattr(blueprint, "summary", "") or "")
    caps = list(getattr(plan, "capabilities", ()) or getattr(blueprint, "capabilities", ()) or [])
    cap_ids = [
        str(getattr(c, "capability_id", "") or getattr(c, "id", "") or "")
        for c in caps
    ]
    cap_ids = [c for c in cap_ids if c]
    users = f"{vertical.replace('_', ' ').replace('-', ' ')} operators"
    pack = {
        "platform_name": name,
        "domain": vertical,
        "product_type": "operations platform",
        "target_users": users,
        "mission": summary or f"A booted {vertical} platform with the listed capabilities.",
        "domain_purpose": summary or f"System of record for {vertical}.",
        "primary_users": [users],
        "required_roles": ["operator", "admin"],
        "required_product_modules": cap_ids,
        "core_business_workflows": [
            f"one-record round-trip for {cid}" for cid in cap_ids
        ] or ["one-record create/read round-trip"],
        "authoritative_calculations": ["none claimed beyond persisted fields"],
        "domain_rules": [
            f"status vocabulary is schema-enforced: {', '.join(ENVELOPE_STATUS_VALUES)}",
            "reserved-keyword fields are refused",
        ],
        "high_impact_actions": ["create", "update", "delete"],
        "prohibited_autonomous_actions": [
            "deploy",
            "store publish",
            "export when the pilot suite is red",
        ],
        "data_sources": ["vendored Store blocks", "local sqlite"],
        "required_connectors": [],
        "required_exports": ["pilot_candidate zip after PRODUCT+STORE green"],
        "security_regulatory_rules": [
            "offline platform — no network, no HTTP store callbacks",
        ],
        "demo_data_requirements": ["one persisted record per capability"],
        "domain_acceptance_conditions": [
            "product boots",
            "own gates green",
            "one-record round-trip per capability",
            "envelope vocab open|in_progress|closed enforced by schema, not prose",
        ],
    }
    return pack


def _section_lines(*parts: str) -> str:
    return "\n".join(part for part in parts if part is not None)


def store_gate_acceptance_line() -> str:
    """The Store gate scores the product on its floor checks with the harness
    the Factory stamps before the writer starts. Tell the writer to run THAT
    harness -- not a script of its own -- and fix every FAIL; the gate's id
    makes a product-owned Store-gate failure brief-defined."""
    from app.factory.build.store_acceptance import (
        ACCEPTANCE_SCRIPT_REL,
        ACCEPTANCE_SELF_CHECK_COMMAND,
        GATE_NAME,
    )

    script = ACCEPTANCE_SCRIPT_REL.as_posix()
    return (
        f"- the Store gate scores this product with {script}, the Factory's harness "
        "stamped before you start: run "
        f"`{ACCEPTANCE_SELF_CHECK_COMMAND}` (the same checks the gate scores; inputs "
        "only the gate can measure print SKIP) and fix every FAIL before declaring "
        f"done; never edit or replace {script} -- the Factory re-stamps it  "
        f"[check:{GATE_NAME}]"
    )


def writer_gate_acceptance_lines() -> Tuple[str, ...]:
    """What the WRITER gate measures, as what to build -- one line per sub-check,
    tagged with the id its verdict carries, so a WRITER-gate failure is
    brief-defined (it goes back to the writer, never advisory) and the writer
    is told the structure before the gate checks it."""
    from app.factory.build.brief_gates import (
        UI_END_TO_END_CHECK,
        UI_SURFACE_CHECK,
        WORKSPACE_COMPILES_CHECK,
        WRITER_BEHAVIOUR_CHECK,
        WRITER_CONTRACT_CHECK,
    )
    from app.factory.build.money_contract import MONEY_SETTINGS_MODULE
    from app.factory.build.writer_behaviour import SELF_CHECK_COMMAND

    return (
        "- every module under app/ parses and the app imports  "
        f"[check:{WORKSPACE_COMPILES_CHECK}]",
        "- the capability handlers are agent-authored, and no currency code or "
        f"tax rate is a literal in code: money is read from {MONEY_SETTINGS_MODULE}  "
        f"[check:{WRITER_CONTRACT_CHECK}]",
        "- every capability fails closed when a block it calls fails: it returns "
        "the block's refusal (ok=false) and persists nothing -- never ok=true over "
        "a failed block (F1) -- and it calls every block it declares (F11); run "
        f"`{SELF_CHECK_COMMAND}` (the WRITER gate's own probe) and fix every "
        f"record it prints before declaring done  [check:{WRITER_BEHAVIOUR_CHECK}]",
        f"- the declared UI modules exist and are served  [check:{UI_SURFACE_CHECK}]",
        "- the served UI drives the capabilities and every route it calls "
        f"answers  [check:{UI_END_TO_END_CHECK}]",
    )


PREFLIP_SCOPE = (
    "READS/WRITES/NEVER/ACCEPTANCE not declared on block.json (pre-flip) "
    "— do not invent scopes"
)


def _record_as_dict(rec: Any) -> Dict[str, Any]:
    if rec is None:
        return {}
    if isinstance(rec, dict):
        return rec
    if hasattr(rec, "to_dict"):
        return rec.to_dict()
    return {}


def scope_line_for(block_id: str, rec: Any) -> str:
    """One BUILD line per verified id. Inventing scopes is forbidden."""
    data = _record_as_dict(rec)
    if data.get("scope_declared"):
        return (
            f"- {block_id} READS={data.get('reads') or ['(none)']} "
            f"WRITES={data.get('writes') or ['(none)']} "
            f"NEVER={data.get('never') or ['(none)']} "
            f"ACCEPTANCE={data.get('acceptance') or ['(none)']}"
        )
    return f"- {block_id}: {PREFLIP_SCOPE}"


def acceptance_line_for(block_id: str, rec: Any) -> str:
    """ACCEPTANCE cut: pull block.json acceptance or name the pre-flip gap."""
    data = _record_as_dict(rec)
    if data.get("scope_declared"):
        acc = data.get("acceptance") or []
        detail = ", ".join(str(a) for a in acc) if acc else "(none declared)"
        return f"- {block_id}: {detail}  [check:block_acceptance]"
    return (
        f"- {block_id}: not declared on block.json (pre-flip) — "
        f"do not invent scopes  [check:block_acceptance]"
    )


def render_slot_bodies(
    *,
    blueprint: Any,
    plan: Any,
    inventory: Sequence[InventoryItem],
    store_ids: Sequence[str],
    kit_manifests: Mapping[str, Any],
    domain_pack: Mapping[str, Any],
    contracts: Optional[Mapping[str, Any]] = None,
    work_list: Optional[Sequence[str]] = None,
    intake: Optional[Mapping[str, Any]] = None,
    reuse_records: Optional[Mapping[str, Any]] = None,
    budget_s: float = 0.0,
) -> Dict[str, str]:
    """Deterministic slot fill. No LLM. Returns template slot → body."""
    n_required = n_required_capabilities_from(plan=plan, blueprint=blueprint)
    name = str(getattr(blueprint, "product_name", "") or "platform")
    vertical = str(getattr(blueprint, "vertical", "") or "product")
    summary = str(getattr(blueprint, "summary", "") or "")
    packed_intake = dict(intake or {})

    cap_lines: List[str] = []
    inv_by_cid = {item.capability_id: item for item in inventory}
    for cap in getattr(plan, "capabilities", ()) or ():
        cid = str(getattr(cap, "capability_id", "") or getattr(cap, "id", "") or "")
        desc = _capability_description(cap) or cid
        item = inv_by_cid.get(cid)
        if item is not None:
            bids = list(item.block_ids)
            strategy = item.strategy
            if item.dropped_reuse:
                desc = item.notes or desc
        else:
            bids = [str(b) for b in (getattr(cap, "block_ids", None) or []) if str(b).strip()]
            strategy = str(getattr(cap, "strategy", "") or "")
        cap_lines.append(
            f"- {cid} [{strategy or 'planned'}]: {desc}"
            + (f"  blocks={bids}" if bids else "  (no block ids — genuine gap)")
        )

    reuse_lines = []
    gap_lines = []
    work_lines = []
    missing_lines = []
    for item in inventory:
        if item.missing:
            missing_lines.append(
                f"- {item.capability_id}: claimed {item.block_ids} but NOT in Store: "
                + ", ".join(item.missing)
            )
        if item.verified_present and item.handler_source:
            reuse_lines.append(
                f"- {item.capability_id}: REUSE {item.verified_present} "
                "(verified present in Store registry; handler source "
                f"{item.handler_source}; emit app/actions/{item.capability_id}.py)"
            )
        if item.dropped_reuse and not item.verified_present:
            gap_lines.append(
                f"- {item.capability_id}: GAP — unverified REUSE dropped "
                f"(Store exact-id present=false): {', '.join(item.dropped_reuse)}"
            )
        elif item.is_gap or (not item.verified_present and not item.missing):
            gap_lines.append(
                f"- {item.capability_id}: GAP — author this logic "
                f"({item.notes or item.strategy or 'no verified block'})"
            )
            work_lines.append(
                f"- {item.capability_id}: GENERATE/GAP — author this logic"
            )
        elif item.verified_present:
            work_lines.append(
                f"- {item.capability_id}: REUSE hole-fill — bind persist / "
                "event_bus / BLOCK_DEFAULT_ACTIONS; do not skip because "
                "inventory_gaps is empty"
            )

    users = (packed_intake.get("users") or {}).get("value") or domain_pack.get("primary_users") or []
    roles = (packed_intake.get("roles") or {}).get("value") or domain_pack.get("required_roles") or []
    done_when = (packed_intake.get("done_when") or {}).get("value") or []

    target = _section_lines(
        f"Booted {vertical} platform: {name}.",
        summary,
        "",
        "Who it is for: " + ", ".join(str(u) for u in users),
        "Roles: " + ", ".join(str(r) for r in roles),
        "",
        "Capabilities:",
        "\n".join(cap_lines) or "- (none)",
    )

    inventory_body = _section_lines(
        "Coder: list what the Store already provides. REUSE by exact block id, "
        "verified present — flag missing, never assume.",
        "",
        "Store registry (exact ids, verified): " + ", ".join(sorted(store_ids)),
        "",
        "REUSE (verified present):",
        "\n".join(reuse_lines) or "- (none)",
        "",
        "GAPS (you author; do not invent a block id):",
        "\n".join(gap_lines) or "- (none)",
        "",
        "WORK ITEMS (C-BRIEF hole-fill; GENERATE gaps plus REUSE that still need handlers):",
        "\n".join(work_lines) or "- (none)",
        "",
        "MISSING claimed REUSE (runner HALTS here if any):",
        "\n".join(missing_lines) or "- (none)",
        "",
        "CUT 1 is read-only. Stop after this inventory. Do not build yet.",
    )

    validate_body = _section_lines(
        "CUT 2 — runner validates ids against the registry (not the coder).",
        "Claimed REUSE that is not present HALTS before WRITER build, not at CLONER.",
        "Verified present: "
        + ", ".join(
            bid
            for item in inventory
            for bid in item.verified_present
        )
        or "(none)",
        "Missing: "
        + ", ".join(bid for item in inventory for bid in item.missing)
        or "(none)",
    )

    do_lines = [
        "C-BRIEF / FACTORY_CODE_CLI owns this workspace even when STEP 0 "
        "is 100% REUSE/COMPOSE (no GENERATE gaps). Bind and write real "
        "handlers for every capability — deepen REUSE/COMPOSE (persist, "
        "constructed block inputs, BLOCK_DEFAULT_ACTIONS, prepared "
        "event_bus steps). Do not leave deterministic templates. Do not "
        "skip the CLI because inventory_gaps is empty. Do not re-implement "
        "a verified Store block from scratch — bind the registry-verified ids.",
        "Every verified REUSE row must emit a loadable "
        "app/actions/{capability_id}.py (factory persist / event_bus "
        "envelope — the registry-verified handler source). A REUSE claim "
        "without that source is a GAP or HALT — do not reach "
        "writer_behaviour with ModuleNotFoundError.",
        "Invocation contracts: pass action= as a keyword, never inside the payload dict.",
        "Prefer action=BLOCK_DEFAULT_ACTIONS.get(block_id).",
        "REUSE keep-path emit MUST populate BLOCK_DEFAULT_ACTIONS — "
        "execute() with action=None is Unknown action: None.",
        "Call execute() for EVERY id in BLOCK_IDS.",
        "Call execute() from app.dispatch only. Do not import app.actions, "
        "app.routes, or app.main from a handler. The factory owns "
        "app/actions/__init__.py — do not rewrite it with "
        "'from app.actions import <capability>' eager re-exports. That "
        "circular import makes "
        "writer_behaviour halt as workspace does not import before route honesty.",
        "If you assign a block, you feed it — construct block inputs; do not demand "
        "block-specific keys (topic, sql/table, file paths, team_id, channel, steps) "
        "from the caller.",
        "Declare vocabularies on the spec (schema), not in prose.",
        f"Envelope status vocabulary (schema-enforced): {' | '.join(ENVELOPE_STATUS_VALUES)}.",
        "",
        schema_accept_rules_text(),
        "",
        reuse_accept_rules_text(
            capability_ids=[
                item.capability_id for item in inventory if getattr(item, "is_reuse", False)
            ]
        ),
        "",
        persist_accept_rules_text([item.capability_id for item in inventory]),
        "",
        workflow_accept_rules_text(
            capability_ids=event_bus_workflow_capability_ids(inventory)
        ),
        "",
        full_pilot_authorship_rules_text(n_required),
        "",
        "Three tests per block are already owned by the harness (TESTER is not an LLM role).",
        "Scope READS / WRITES / NEVER explicitly in each handler you author.",
        f"Budget wall: {int(budget_s)}s (FACTORY_CODER_BUDGET_S / staged wall).",
        "",
        writer_phase_slot_bodies()["BUILD"],
        "",
        "Block scopes (from block.json; report-only until L2.2 flip — do not invent):",
    ]
    packed_reuse = {
        bid: (rec if isinstance(rec, dict) else rec.to_dict())
        for bid, rec in dict(reuse_records or {}).items()
    }
    seen_scope: Set[str] = set()
    for item in inventory:
        for bid in item.verified_present:
            if bid in seen_scope:
                continue
            seen_scope.add(bid)
            do_lines.append(scope_line_for(bid, packed_reuse.get(bid)))
    findings = [str(item) for item in (work_list or []) if str(item).strip()]
    if findings:
        do_lines += [
            "",
            "A previous attempt failed these checks — keep what works and fix only these:",
        ]
        # Live 2026-09-30 (automotive): every row was "[error]" -- pytest
        # died during SETUP (a missing shared fixture), one break, not one
        # defect per row. Handed the raw list, the writer chased it test by
        # test and G5 stopped the run on SAME_FAILURE_TWICE. Name the shape.
        setup_errors = [item for item in findings if "[error]" in item]
        if setup_errors:
            do_lines += [
                f"{len(setup_errors)} of {len(findings)} rows are [error]: those tests "
                "never ran — pytest died during SETUP. That is one broken shared "
                "dependency (a fixture the tests request, tests/conftest.py, or an "
                "import it performs), not a defect in each test. Fix the shared "
                "setup FIRST, then re-check the remaining [failure] rows. Do not "
                "rewrite the [error] tests one by one.",
            ]
        # A route that still refused after the tester adopted the exact values
        # the route named is a contradiction inside the product, not a bad test
        # value. Say so, and say what a fix is NOT -- the coder must not reach
        # for the quick green of loosening the guard (the owner's concern: a
        # "minor change for the platform to pass" that weakens validation).
        tester_tried = [item for item in findings if "tester already tried" in item]
        if tester_tried:
            do_lines += [
                f"{len(tester_tried)} row(s) say the tester already tried the values "
                "the route itself named and the route STILL refused. The test value "
                "is not the problem — the product contradicts itself: a route guard, "
                "a handler, and the block disagree about the same field's vocabulary. "
                "Reconcile them so a value the capability declares valid passes every "
                "layer. Do NOT fix this by widening, dropping, or short-circuiting a "
                "validation to make it pass — see testing_errors_protocol.md.",
            ]
        do_lines += [f"- {item}" for item in findings]
        do_lines += [
            "",
            "Fix the cause of each row, not the symptom. Never weaken, delete, or "
            "loosen a validation, a route check, or a test assertion to turn a row "
            "green (testing_errors_protocol.md). If a value was rejected, declare the "
            "accepted vocabulary on the spec (allowed_values) rather than making the "
            "route accept anything.",
        ]
    do_lines += [
        "",
        "Kit manifests (Factory shelf + on-disk packs):",
        json.dumps(kit_manifests, indent=2, sort_keys=True),
    ]
    if contracts:
        do_lines += [
            "",
            "Block contracts (invoke only actions they support):",
            json.dumps(dict(contracts), indent=2, sort_keys=True),
        ]
    if packed_reuse:
        do_lines += [
            "",
            "REUSE records (present/reuse + reads/writes/never/acceptance):",
            json.dumps(packed_reuse, indent=2, sort_keys=True),
        ]
    if domain_pack:
        packed = {
            key: domain_pack.get(key)
            for key in ("mission", "domain_purpose", "domain_acceptance_conditions")
            + DOMAIN_PACK_FIELDS
            if domain_pack.get(key) not in (None, "", [])
        }
        if packed:
            do_lines += ["", "Domain pack (binding fields):", json.dumps(packed, indent=2, sort_keys=True)]
    if done_when:
        do_lines += ["", "Done when (from intake blueprint):", *[f"- {item}" for item in done_when]]
    # Money: the locale the USER declared on the Floor and the structure to
    # build around it (money_contract measures the same structure).
    from app.factory.build.money_contract import money_brief_lines

    do_lines += ["", *money_brief_lines(blueprint)]
    # Placeholder connectors: which capabilities answer the typed unavailable
    # refusal, and what their tests assert instead of a round trip.
    from app.factory.build.placeholder_connectors import brief_lines as placeholder_brief_lines

    placeholder = placeholder_brief_lines(blueprint)
    if placeholder:
        do_lines += ["", *placeholder]

    # The user's BUILD LEVEL decides which rungs this run owes: a prototype
    # stops at CODE, and the authorship floor ("thin SUCCESS is a failure")
    # binds from pilot up. No level declared: every rung, nothing lowered.
    from app.factory.build.build_level import bar_for

    bar = bar_for(blueprint)
    climbs = bar is None or bar.reaches_pilot
    thin_binds = bar is None or bar.thin_success_is_failure
    ladder_lines = (
        [
            f"- PRODUCT gate: {GATE_SCOPES['PRODUCT']}  [check:{PRODUCT_GATE_CHECK}]",
            f"- STORE gate: {GATE_SCOPES['STORE']}  [check:store_gate]",
            "- scripts/acceptance.py ≥12 measured checks k/k inside the Store-built image  [check:store_acceptance]",
            "- ledger records pilot_ready=true  [check:ledger]",
        ]
        if climbs
        else [
            f"- BUILD LEVEL {bar.level.value}: the run is DONE at CODE_GREEN; "
            "PRODUCT and STORE are not run at this level  [check:build_level]",
        ]
    )
    acceptance = _section_lines(
        "Fails loud. The run is not done until ALL of these are true. "
        "ACCEPTANCE is run by the harness, not the coder.",
        "- the product boots  [check:boot]",
        f"- own gates green  [check:{SUITE_CHECK}]",
        "- one-record round-trip per capability (POST creates, GET returns it); the store entity "
        "read back is the one its route declares it saves to (ROUTE_ENTITIES in the Factory's "
        "app/routes.py) -- your migration's table and your handler's ENTITY use that same "
        "name; a capability calling a declared placeholder connector answers HTTP 503 "
        f"error_kind unavailable instead and is not judged  [check:{PRODUCT_ROUND_TRIP_CHECK}]",
        persist_accept_acceptance_line(),
        schema_accept_acceptance_line(),
        *writer_gate_acceptance_lines(),
        store_gate_acceptance_line(),
        reuse_accept_acceptance_line(),
        workflow_accept_acceptance_line(
            capability_ids=event_bus_workflow_capability_ids(inventory)
        ),
        "- the domain pack's domain_acceptance_conditions hold  [check:domain_acceptance]",
        f"- envelope vocab {', '.join(ENVELOPE_STATUS_VALUES)} enforced by schema, not prose  [check:envelope_schema]",
        *ladder_lines,
        *([full_pilot_authorship_acceptance_line(n_required)] if thin_binds else []),
        writer_phase_slot_bodies()["ACCEPTANCE"],
        "",
        "The harness's acceptance IS the tester. Do not write decorative tests. "
        + (
            "Do not treat thin SUCCESS / templates-only / stubbed capabilities / "
            "authorship below the launching-ready full-pilot floor as done."
            if thin_binds
            else "Stubbed capabilities are not done; Factory-template handlers "
            "are accepted at this build level."
        ),
        "",
        "Block-level acceptance (from block.json, report-only until flip):",
        "\n".join(
            acceptance_line_for(bid, packed_reuse.get(bid))
            for bid in seen_scope
        )
        or "- (no verified REUSE blocks)  [check:block_acceptance]",
    )

    forbidden = _section_lines(
        "- HARD RULE: sealed vendor is read-only — do not write vendor/**, "
        "vendor_blocks/**, vendor_blocks_mirror/**, blocks.lock.json, "
        "build_ledger.jsonl, .git/**, or anything under SEALED_AFTER_CLONER; "
        "implement domain in app/** and tests/** only as allowed by "
        "ba_allowed_globs / Hybrid C for BA; prefer docs/openapi.json and "
        "root openapi.json only — never vendor/**; never patch vendored "
        "blocks — call Store blocks via execute(action=)",
        *(["- thin SUCCESS (code-cycle green, pilot_ready=false)"] if climbs else []),
        *([full_pilot_authorship_forbidden_lines(n_required)] if thin_binds else []),
        "- decorative tests",
        "- reserved-keyword fields (action inside the payload dict, id as a domain field)",
        "- unlisted blocks (ids not in the Store registry / inventory)",
        "- assuming a REUSE id is present when STEP 0 flagged it missing",
        "- claiming REUSE without a loadable app/actions/{capability_id}.py "
        "handler source",
        "- inventing READS/WRITES/NEVER/ACCEPTANCE when block.json has not declared them",
        "- inventing a stricter accept-contract than the spec (writer_behaviour schema-accept)",
        "- eager from app.actions import re-exports in app/actions/__init__.py "
        "(circular import; writer_behaviour workspace does not import)",
        "- importing app.actions / app.routes / app.main from a capability handler "
        "(writer_behaviour workspace does not import)",
        persist_accept_forbidden_lines(),
        reuse_accept_forbidden_lines(),
        workflow_accept_forbidden_lines(),
        "- one handle() / one spec / one route at a time — this brief is the whole job",
        writer_phase_slot_bodies()["FORBIDDEN"],
        "- weakening honesty or exporting when the pilot suite is red",
    )

    return {
        "TARGET": target,
        "INVENTORY": inventory_body,
        "VALIDATE": validate_body,
        "BUILD": "\n".join(do_lines),
        "ACCEPTANCE": acceptance,
        "FORBIDDEN": forbidden,
    }


def fill_template(template: str, slots: Mapping[str, str]) -> str:
    """Replace {{SLOT}} markers. Leftover markers are a compile error, not a default."""
    text = template
    for key, body in slots.items():
        marker = "{{" + key + "}}"
        if marker not in text:
            raise BriefCompileError(f"BRIEF_TEMPLATE.md is missing slot {marker}")
        text = text.replace(marker, body.strip() + "\n")
    leftovers = [m.group(0) for m in re.finditer(r"\{\{[A-Z0-9_]+\}\}", text)]
    if leftovers:
        raise BriefCompileError(
            "unfilled template slot (lint failure, not a default): "
            + ", ".join(sorted(set(leftovers)))
        )
    return text if text.endswith("\n") else text + "\n"


def _line_sources_for(
    slots: Mapping[str, str],
    *,
    intake: Mapping[str, Any],
    domain_pack: Mapping[str, Any],
    inventory: Sequence[InventoryItem],
    store_ids: Sequence[str],
    exit_condition: str = "",
) -> Dict[str, str]:
    sources = field_source_index(intake)
    for key, value in domain_pack.items():
        if isinstance(value, str) and value.strip():
            sources[value.strip()[:80]] = f"domain_pack.{key}"
        elif isinstance(value, list):
            for item in value:
                if str(item).strip():
                    sources[str(item).strip()[:80]] = f"domain_pack.{key}"
    for bid in store_ids:
        sources[str(bid)] = f"manifest.store.{bid}"
    for item in inventory:
        if item.capability_id:
            sources[item.capability_id] = f"blueprint.capabilities.{item.capability_id}"
        for bid in item.block_ids + item.verified_present + item.dropped_reuse:
            sources[bid] = f"manifest.block.{bid}"
        if item.notes:
            sources[item.notes.strip()[:80]] = f"blueprint.capabilities.{item.capability_id}"
    for key, body in slots.items():
        sources[f"slot:{key}"] = f"template.{key}"
        for line in body.splitlines():
            stripped = line.strip()
            if stripped:
                sources[stripped[:80]] = f"template.{key}"
    sources[CODING_AGENT_BRIEF[:80]] = "standard.CODING_AGENT_BRIEF"
    for line in CODING_AGENT_BRIEF.splitlines():
        stripped = line.strip()
        if stripped:
            sources[stripped[:80]] = "standard.CODING_AGENT_BRIEF"
    # The exit condition is rendered from the blueprint's typed build level.
    for line in exit_condition.splitlines():
        stripped = line.strip()
        if stripped:
            sources[stripped[:80]] = "blueprint.build_level"
    return sources


def render_gated_brief(
    *,
    blueprint: Any,
    plan: Any,
    inventory: Sequence[InventoryItem],
    store_ids: Sequence[str],
    kit_manifests: Mapping[str, Any],
    domain_pack: Mapping[str, Any],
    contracts: Optional[Mapping[str, Any]] = None,
    work_list: Optional[Sequence[str]] = None,
    intake: Optional[Mapping[str, Any]] = None,
    reuse_records: Optional[Mapping[str, Any]] = None,
    budget_s: float = 0.0,
) -> str:
    """Fill BRIEF_TEMPLATE.md. LLM never writes this text."""
    slots = render_slot_bodies(
        blueprint=blueprint,
        plan=plan,
        inventory=inventory,
        store_ids=store_ids,
        kit_manifests=kit_manifests,
        domain_pack=domain_pack,
        contracts=contracts,
        work_list=work_list,
        intake=intake,
        reuse_records=reuse_records,
        budget_s=budget_s,
    )
    filled = fill_template(load_brief_template(), slots)
    return (
        CODING_AGENT_BRIEF
        + "\n\n"
        + level_exit_condition(blueprint)
        + "\n\n"
        + filled
    )


def compile_brief(
    blueprint: Any,
    plan: Any,
    *,
    blocks_root: Optional[Path] = None,
    factory_shelf: Optional[Path] = None,
    store_ids: Optional[Iterable[str]] = None,
    domain_pack: Optional[Mapping[str, Any]] = None,
    contracts: Optional[Mapping[str, Any]] = None,
    work_list: Optional[Sequence[str]] = None,
    intake: Optional[Mapping[str, Any]] = None,
    chat_turns: Optional[Sequence[Mapping[str, Any]]] = None,
    brief: str = "",
    budget_s: Optional[float] = None,
    reuse_http_get=None,
) -> CompiledBrief:
    """Compile one brief from the intake blueprint, domain pack, and Store registry."""
    known = set(store_ids) if store_ids is not None else store_registry_ids(
        blocks_root, factory_shelf
    )
    claimed = []
    for cap in getattr(plan, "capabilities", ()) or ():
        claimed.extend(str(b) for b in (getattr(cap, "block_ids", None) or []) if str(b).strip())
    # Explicit store_ids (tests / offline compile) skip live HTTP unless
    # the caller passed a getter. Floor/WRITER leave store_ids unset so
    # STEP 0 feature-detects Blocks #106.
    skip_http = store_ids is not None and reuse_http_get is None
    records = resolve_store_presence(
        claimed,
        local_ids=known,
        http_get=(lambda *_a, **_k: None) if skip_http else reuse_http_get,
        blocks_root=blocks_root,
    )
    # HTTP present=true counts as verified even if the local shelf lagged.
    for bid, rec in records.items():
        if rec.present:
            known.add(bid)
    inventory = compile_inventory(plan, known, reuse_records=records)
    missing = [bid for item in inventory for bid in item.missing]
    kits = _load_kit_manifests(claimed, blocks_root=blocks_root)
    pack = dict(domain_pack) if domain_pack else synthesize_domain_pack(blueprint, plan)
    packed_intake = dict(intake) if intake else intake_from_product_blueprint(
        blueprint,
        plan=plan,
        chat_turns=chat_turns,
        brief=brief,
        domain_pack=pack,
    )
    validate_intake(packed_intake)
    wall = float(budget_s) if budget_s is not None else float(coder_budget_s())
    slots = render_slot_bodies(
        blueprint=blueprint,
        plan=plan,
        inventory=inventory,
        store_ids=sorted(known),
        kit_manifests=kits,
        domain_pack=pack,
        contracts=contracts,
        work_list=work_list,
        intake=packed_intake,
        reuse_records=records,
        budget_s=wall,
    )
    exit_condition = level_exit_condition(blueprint)
    text = (
        CODING_AGENT_BRIEF
        + "\n\n"
        + exit_condition
        + "\n\n"
        + fill_template(load_brief_template(), slots)
    )
    line_sources = _line_sources_for(
        slots,
        intake=packed_intake,
        domain_pack=pack,
        inventory=inventory,
        store_ids=sorted(known),
        exit_condition=exit_condition,
    )
    return CompiledBrief(
        text=text,
        emitted_lines=emitted_line_set(text),
        product_name=str(getattr(blueprint, "product_name", "") or "platform"),
        vertical=str(getattr(blueprint, "vertical", "") or "product"),
        product_id=str(getattr(blueprint, "product_id", "") or ""),
        inventory=inventory,
        store_ids=sorted(known),
        kit_manifests=kits,
        domain_pack=pack,
        missing_reuse=missing,
        capabilities=[item.capability_id for item in inventory],
        intake=packed_intake,
        contracts=dict(contracts or {}),
        reuse_records={bid: rec.to_dict() for bid, rec in records.items()},
        line_sources=line_sources,
        slots=dict(slots),
        budget_s=wall,
    )


def compile_brief_from_ctx(ctx: Any) -> CompiledBrief:
    """RoleContext → compiled brief. Store ids from dual registry + REUSE HTTP."""
    contracts: Dict[str, Any] = {}
    vendored = [b for b in (getattr(ctx, "state", {}) or {}).get("vendored_blocks", ()) if b]
    if vendored and hasattr(ctx, "workspace"):
        try:
            from app.factory.build.roles_handlers import _block_contract

            contracts = {b: _block_contract(ctx, b) for b in vendored}
        except Exception:  # noqa: BLE001 — brief must still compile without contracts
            contracts = {}
    intake = (getattr(ctx, "state", {}) or {}).get("intake_blueprint")
    turns = (getattr(ctx, "state", {}) or {}).get("chat_turns")
    # The brief names the configured wall, not remaining seconds — leftover
    # time is non-deterministic and would make two compiles disagree.
    return compile_brief(
        ctx.blueprint,
        ctx.plan,
        blocks_root=getattr(ctx, "blocks_root", None),
        contracts=contracts or None,
        work_list=list(getattr(ctx, "work_list", ()) or ()),
        intake=intake if isinstance(intake, dict) else None,
        chat_turns=turns if isinstance(turns, list) else None,
    )


def brief_fingerprint(compiled: CompiledBrief) -> Dict[str, Any]:
    """Identity the lettings golden must reproduce after a template revision."""
    return {
        "template_revision": compiled.template_revision,
        "capabilities": list(compiled.capabilities),
        "inventory": [
            {
                "capability_id": item.capability_id,
                "strategy": item.strategy,
                "block_ids": list(item.block_ids),
                "verified_present": list(item.verified_present),
                "missing": list(item.missing),
                "handler_source": item.handler_source,
            }
            for item in compiled.inventory
        ],
        "missing_reuse": list(compiled.missing_reuse),
        "budget_s": compiled.budget_s,
    }
