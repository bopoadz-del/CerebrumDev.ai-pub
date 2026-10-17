"""INTAKE CHAT → intake_blueprint.v1. COLLECTOR output is this object.

Fill is deterministic. Every field carries the chat turn it came from.
Unfilled schema fields are a lint failure, not a silent default.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

STANDARDS = Path(__file__).resolve().parents[1] / "standards"
SCHEMA_PATH = STANDARDS / "BLUEPRINT_SCHEMA.json"
LETTINGS_CHAT_PATH = STANDARDS / "golden" / "lettings_session_chat.json"
SCHEMA_VERSION = "intake_blueprint.v1"


class IntakeBlueprintError(ValueError):
    """The intake object is not a valid BLUEPRINT_SCHEMA instance."""


def load_schema() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _sourced(value: Any, turn: int, text: str = "") -> Dict[str, Any]:
    return {
        "value": value,
        "source_turn": int(turn),
        "source_text": (text or "")[:400],
    }


def _first_user_turn(turns: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    for item in turns:
        role = str(item.get("role") or "").lower()
        if role in {"user", "human", "customer"}:
            return dict(item)
    return dict(turns[0]) if turns else {"turn": 0, "text": ""}


def _turn_no(item: Mapping[str, Any], default: int = 0) -> int:
    try:
        return int(item.get("turn") or default)
    except (TypeError, ValueError):
        return default


def _turn_text(item: Mapping[str, Any]) -> str:
    return str(item.get("text") or item.get("content") or item.get("message") or "")


def chat_turns_from_session(state: Any) -> List[Dict[str, Any]]:
    """Normalise SessionState.chat_history / product brief into numbered turns."""
    history = list(getattr(state, "chat_history", None) or [])
    brief = ""
    pd = getattr(state, "product_design", None)
    if pd is not None:
        brief = str(getattr(pd, "brief", "") or "")
    turns: List[Dict[str, Any]] = []
    for i, raw in enumerate(history, start=1):
        if isinstance(raw, str):
            turns.append({"turn": i, "role": "user", "text": raw})
            continue
        if not isinstance(raw, dict):
            continue
        turns.append(
            {
                "turn": _turn_no(raw, i),
                "role": str(raw.get("role") or "user"),
                "text": _turn_text(raw),
            }
        )
    if not turns and brief.strip():
        turns.append({"turn": 1, "role": "user", "text": brief.strip()})
    return turns


def intake_from_product_blueprint(
    blueprint: Any,
    *,
    plan: Any = None,
    chat_turns: Optional[Sequence[Mapping[str, Any]]] = None,
    brief: str = "",
    domain_pack: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """COLLECTOR fill: ProductBlueprint + chat provenance → intake_blueprint.v1."""
    turns = list(chat_turns or [])
    if not turns and brief.strip():
        turns = [{"turn": 1, "role": "user", "text": brief.strip()}]
    user = _first_user_turn(turns)
    turn = _turn_no(user, 1 if turns else 0)
    text = _turn_text(user) or brief or str(getattr(blueprint, "summary", "") or "")
    pack = dict(domain_pack or {})

    caps: List[Dict[str, Any]] = []
    raw_caps = list(getattr(blueprint, "capabilities", ()) or [])
    if not raw_caps and plan is not None:
        raw_caps = list(getattr(plan, "capabilities", ()) or [])
    for cap in raw_caps:
        cid = str(getattr(cap, "id", "") or getattr(cap, "capability_id", "") or "")
        if not cid:
            continue
        words = str(
            getattr(cap, "description", "")
            or getattr(cap, "notes", "")
            or cid.replace("_", " ")
        )
        hint = getattr(cap, "strategy_hint", None) or getattr(cap, "strategy", None)
        strategy = str(hint.value if hasattr(hint, "value") else (hint or "REUSE"))
        caps.append(
            {
                "customer_words": words,
                "id": cid,
                "block_ids": [str(b) for b in (getattr(cap, "block_ids", None) or []) if str(b).strip()],
                "strategy": strategy,
                "source_turn": turn,
                "source_text": text[:400],
            }
        )
    if not caps:
        raise IntakeBlueprintError("intake blueprint has no capabilities")

    users = pack.get("primary_users") or pack.get("target_users")
    if isinstance(users, str):
        users = [users]
    users = [str(u) for u in (users or []) if str(u).strip()] or [
        f"{str(getattr(blueprint, 'vertical', 'product')).replace('_', ' ')} operators"
    ]
    roles = pack.get("required_roles") or ["operator", "admin"]
    roles = [str(r) for r in roles if str(r).strip()]
    data_sources = pack.get("data_sources") or ["vendored Store blocks", "local sqlite"]
    data_sources = [str(x) for x in data_sources if str(x).strip()]
    integrations = pack.get("required_connectors") or list(
        getattr(blueprint, "connectors", None) or []
    )
    integrations = [str(x) for x in integrations if str(x).strip()] or ["none claimed"]
    constraints = pack.get("domain_rules") or pack.get("security_regulatory_rules") or [
        "offline platform — no network, no HTTP store callbacks",
    ]
    constraints = [str(x) for x in constraints if str(x).strip()]
    done_when = pack.get("domain_acceptance_conditions") or [
        "product boots",
        "own gates green",
        "one-record round-trip per capability",
    ]
    done_when = [str(x) for x in done_when if str(x).strip()]

    intake = {
        "schema_version": SCHEMA_VERSION,
        "product_name": _sourced(
            str(getattr(blueprint, "product_name", "") or "platform"), turn, text
        ),
        "product_id": _sourced(
            str(getattr(blueprint, "product_id", "") or "product"), turn, text
        ),
        "summary": _sourced(
            str(getattr(blueprint, "summary", "") or text or "platform"), turn, text
        ),
        "vertical": _sourced(
            str(getattr(blueprint, "vertical", "") or "product"), turn, text
        ),
        "capabilities": caps,
        "roles": _sourced(roles, turn, text),
        "users": _sourced(users, turn, text),
        "data_sources": _sourced(data_sources, turn, text),
        "integrations": _sourced(integrations, turn, text),
        "constraints": _sourced(constraints, turn, text),
        "done_when": _sourced(done_when, turn, text),
    }
    validate_intake(intake)
    return intake


def validate_intake(intake: Mapping[str, Any]) -> Dict[str, Any]:
    """Fail closed against BLUEPRINT_SCHEMA required fields. No defaults here."""
    if not isinstance(intake, Mapping):
        raise IntakeBlueprintError("intake blueprint must be a mapping")
    if intake.get("schema_version") != SCHEMA_VERSION:
        raise IntakeBlueprintError(
            f"unsupported schema_version {intake.get('schema_version')!r}"
        )
    required = (
        "vertical",
        "capabilities",
        "roles",
        "users",
        "data_sources",
        "integrations",
        "constraints",
        "done_when",
    )
    missing = [key for key in required if key not in intake]
    if missing:
        raise IntakeBlueprintError(
            "intake blueprint missing required fields: " + ", ".join(missing)
        )
    vertical = intake.get("vertical") or {}
    if not isinstance(vertical, Mapping) or not str(vertical.get("value") or "").strip():
        raise IntakeBlueprintError("vertical.value is required")
    caps = intake.get("capabilities") or []
    if not isinstance(caps, list) or not caps:
        raise IntakeBlueprintError("capabilities must be a non-empty list")
    for i, cap in enumerate(caps):
        if not isinstance(cap, Mapping):
            raise IntakeBlueprintError(f"capabilities[{i}] must be an object")
        if not str(cap.get("id") or "").strip():
            raise IntakeBlueprintError(f"capabilities[{i}].id is required")
        if not str(cap.get("customer_words") or "").strip():
            raise IntakeBlueprintError(f"capabilities[{i}].customer_words is required")
        if "source_turn" not in cap:
            raise IntakeBlueprintError(
                f"capabilities[{i}] missing source_turn (requirement provenance)"
            )
    for key in ("roles", "users", "data_sources", "integrations", "constraints", "done_when"):
        field = intake.get(key) or {}
        if not isinstance(field, Mapping) or "source_turn" not in field:
            raise IntakeBlueprintError(f"{key} must carry source_turn")
        values = field.get("value")
        if not isinstance(values, list) or not values:
            raise IntakeBlueprintError(f"{key}.value must be a non-empty list")
    return dict(intake)


def load_lettings_golden_chat() -> Dict[str, Any]:
    return json.loads(LETTINGS_CHAT_PATH.read_text(encoding="utf-8"))


def reconstruct_intake_from_chat(
    turns: Sequence[Mapping[str, Any]],
    *,
    use_llm: bool = False,
    vertical_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Replay intake chat through the architect, then stamp provenance.

    The vertical is the user's typed choice -- ``vertical_hint``, else the
    ``vertical`` field the Floor recorded on the customer's turn -- never
    read from the brief's words.

    Lettings golden chat reconstructs the golden YAML roster — proof the
    compiler is honest, not a second capability list.
    """
    from app.factory.build.brief_compiler import synthesize_domain_pack
    from app.factory.product_architect import draft_blueprint_from_brief, plan_blueprint

    user = _first_user_turn(turns)
    brief = _turn_text(user)
    if not brief.strip():
        raise IntakeBlueprintError("golden chat has no customer brief")
    chosen = vertical_hint or (user.get("vertical") if isinstance(user, Mapping) else None)
    bp = draft_blueprint_from_brief(brief, use_llm=use_llm, vertical_hint=chosen or None)
    plan = plan_blueprint(bp)
    pack = synthesize_domain_pack(bp, plan)
    return intake_from_product_blueprint(
        bp, plan=plan, chat_turns=turns, brief=brief, domain_pack=pack
    )


def intake_capability_ids(intake: Mapping[str, Any]) -> List[str]:
    return [
        str(cap.get("id") or "")
        for cap in (intake.get("capabilities") or [])
        if str(cap.get("id") or "").strip()
    ]


def render_plain_language(intake: Mapping[str, Any]) -> str:
    """Floor-facing prose. Approve is the only event that spends."""
    name = str((intake.get("product_name") or {}).get("value") or "platform")
    vertical = str((intake.get("vertical") or {}).get("value") or "")
    summary = str((intake.get("summary") or {}).get("value") or "")
    users = (intake.get("users") or {}).get("value") or []
    roles = (intake.get("roles") or {}).get("value") or []
    done = (intake.get("done_when") or {}).get("value") or []
    caps = intake.get("capabilities") or []
    lines = [
        f"{name} ({vertical}).",
        summary,
        "Who it is for: " + ", ".join(str(u) for u in users),
        "Roles: " + ", ".join(str(r) for r in roles),
        "Capabilities:",
    ]
    for cap in caps:
        lines.append(
            f"- {cap.get('customer_words') or cap.get('id')} [{cap.get('id')}]"
        )
    lines.append("Done when:")
    for item in done:
        lines.append(f"- {item}")
    lines.append("Approve the feature list to compile the brief and start the coding agent.")
    return "\n".join(line for line in lines if str(line).strip())


def field_source_index(intake: Mapping[str, Any]) -> Dict[str, str]:
    """Map a stable field path → 'blueprint:<path>' for orphan-line lint."""
    index: Dict[str, str] = {}
    for key in (
        "product_name",
        "product_id",
        "summary",
        "vertical",
        "roles",
        "users",
        "data_sources",
        "integrations",
        "constraints",
        "done_when",
    ):
        if key in intake:
            index[key] = f"blueprint.{key}"
    for cap in intake.get("capabilities") or []:
        cid = str(cap.get("id") or "")
        if cid:
            index[cid] = f"blueprint.capabilities.{cid}"
            index[str(cap.get("customer_words") or "")] = f"blueprint.capabilities.{cid}"
    return index
