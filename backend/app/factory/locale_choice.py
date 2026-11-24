"""The intake the USER declared on the Floor: country, currency, build level.

Typed fields, each validated by SHAPE only -- 2 / 3 uppercase letters for the
country and currency (no list of countries or currencies exists anywhere in
the Factory), one of the defined build levels for the level. They persist on
the session's product design and are copied onto the stored blueprint
(``blueprint.locale``, ``blueprint.build_level``), which every build path
reads, so the money contract and the build's bar judge the build against what
the user said and nothing else. Never inferred from the brief's prose.

The Floor chat ASKS for them and may PROPOSE values from the user's answer
(``intake_proposal``). An answer the user changes on the Floor goes onto the
same proposal (``propose_intake_edit``). A proposal stores nothing: only the
typed ``confirm_intake`` action -- the user pressing Confirm -- turns it into
the typed fields. That is the one write path.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from app.factory.build.build_level import BuildLevelError, parse_build_level
from app.factory.build.money_contract import declared_locale, shaped_country, shaped_currency

#: The fields an intake proposal may carry -- the session's typed intake.
INTAKE_FIELDS = ("vertical", "country", "currency", "build_level")


def apply_locale_choice(pd: Any, country: Optional[str], currency: Optional[str]) -> None:
    """Record a typed answer. ``None`` leaves a field as it was; an empty
    string clears it back to undeclared."""
    if country is not None:
        pd.country = shaped_country(country)
    if currency is not None:
        pd.currency = shaped_currency(currency)
    sync_blueprint_intake(pd)


def apply_build_level(pd: Any, level: Optional[str]) -> None:
    """Record the user's typed build level. ``None`` leaves it as it was; an
    empty string clears it back to not chosen. A value the Factory does not
    define raises BuildLevelError -- it is refused, never mapped."""
    if level is None:
        return
    pd.build_level = parse_build_level(level).value if str(level).strip() else None
    sync_blueprint_intake(pd)


def sync_blueprint_intake(pd: Any) -> bool:
    """Make the stored blueprint carry exactly the declared locale pair and
    build level. Returns True when it changed."""
    blueprint = getattr(pd, "blueprint", None)
    if not isinstance(blueprint, dict):
        return False
    wanted_locale = declared_locale(getattr(pd, "country", None), getattr(pd, "currency", None))
    wanted_level = getattr(pd, "build_level", None) or None
    if blueprint.get("locale") == wanted_locale and blueprint.get("build_level") == wanted_level:
        return False
    blueprint["locale"] = wanted_locale
    blueprint["build_level"] = wanted_level
    pd.blueprint = blueprint
    return True


def shaped_proposal(raw: Any) -> Optional[Dict[str, str]]:
    """The model's proposal reduced to the fields that pass their typed shape.

    A value that does not fit its field is dropped, not repaired: the user
    sees exactly what can be confirmed, and supplies the rest themselves.
    """
    if not isinstance(raw, Mapping):
        return None
    shaped: Dict[str, str] = {}
    vertical = str(raw.get("vertical") or "").strip()
    if vertical:
        shaped["vertical"] = vertical
    country = shaped_country(str(raw.get("country") or ""))
    if country:
        shaped["country"] = country
    currency = shaped_currency(str(raw.get("currency") or ""))
    if currency:
        shaped["currency"] = currency
    level = str(raw.get("build_level") or "").strip()
    if level:
        try:
            shaped["build_level"] = parse_build_level(level).value
        except BuildLevelError:
            pass
    return shaped or None


#: What a typed answer must look like, said back when one does not fit.
_EDIT_SHAPES = (
    ("country", shaped_country, "a two-letter country code"),
    ("currency", shaped_currency, "a three-letter currency code"),
)


#: An answer the user cleared on the Floor. It rides on the proposal like any
#: other answer, and Confirm stores it as "not declared".
CLEARED = ""

#: The answers a user may clear; the chat's proposal never carries a clear.
_CLEARABLE = ("country", "currency")


def shaped_intake_edit(
    vertical: Optional[str],
    country: Optional[str],
    currency: Optional[str],
    build_level: Optional[str] = None,
) -> Tuple[Dict[str, str], Optional[str]]:
    """The answers the user sent from the Floor, shaped, and the refusal for
    one that does not fit its field (then nothing is kept at all).

    ``None`` is "not sent". An empty country or currency is the user's
    explicit "clear it" (``CLEARED``); an empty build level is "not sent".
    An empty vertical is the user's explicit "no industry" choice.
    """
    edit: Dict[str, str] = {}
    if vertical is not None:
        from app.factory.store_kits import chosen_vertical

        edit["vertical"] = chosen_vertical(vertical)
    for field, shape, hint in _EDIT_SHAPES:
        raw = {"country": country, "currency": currency}[field]
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            edit[field] = CLEARED
            continue
        value = shape(text)
        if not value:
            return {}, f"{field} {text!r} is not {hint}; nothing was changed."
        edit[field] = value
    level = str(build_level or "").strip()
    if level:
        try:
            edit["build_level"] = parse_build_level(level).value
        except BuildLevelError as exc:
            return {}, f"{exc}; nothing was changed."
    return edit, None


def edit_differs_from_declared(pd: Any, edit: Mapping[str, str]) -> bool:
    """True when the edit would change an answer the session already holds."""
    from app.factory.store_kits import NO_VERTICAL

    for field, value in edit.items():
        held = getattr(pd, field, None)
        if field == "vertical":
            held = held or NO_VERTICAL
        if held != value:
            return True
    return False


def propose_intake_edit(pd: Any, edit: Mapping[str, str]) -> None:
    """The one write path for a changed answer: it goes onto the pending
    proposal, beside the declared fields, and only ``confirm_intake`` stores
    it. A typed answer never lands on the declared fields by itself."""
    if edit:
        pd.intake_proposal = {**(getattr(pd, "intake_proposal", None) or {}), **edit}


def confirm_intake(pd: Any) -> Optional[Dict[str, str]]:
    """The user pressed Confirm: the pending proposal becomes the session's
    typed fields. Returns what was stored; None when nothing was proposed."""
    raw = getattr(pd, "intake_proposal", None)
    proposal = shaped_proposal(raw) or {}
    if isinstance(raw, Mapping):
        proposal.update({k: CLEARED for k in _CLEARABLE if raw.get(k) == CLEARED})
    pd.intake_proposal = None
    if not proposal:
        return None
    if "vertical" in proposal:
        from app.factory.store_kits import NO_VERTICAL, chosen_vertical

        choice = chosen_vertical(proposal["vertical"])
        pd.vertical = None if choice == NO_VERTICAL else choice
    if "country" in proposal or "currency" in proposal:
        apply_locale_choice(pd, proposal.get("country"), proposal.get("currency"))
    if "build_level" in proposal:
        apply_build_level(pd, proposal["build_level"])
    sync_blueprint_intake(pd)
    return proposal


def declared_intake(pd: Any) -> Dict[str, Any]:
    """The typed fields the session holds as the user's declared answers."""
    return {
        "vertical": getattr(pd, "vertical", None),
        "country": getattr(pd, "country", None),
        "currency": getattr(pd, "currency", None),
        "build_level": getattr(pd, "build_level", None),
    }


def intake_state(pd: Any, *, reopened: bool) -> Dict[str, Any]:
    """What the Floor's intake line shows: the declared typed fields and the
    pending proposal (if any), side by side and never merged.

    ``reopened`` is given by the caller, derived from the build
    (platform_chat_flow.intake_reopened): the session's stored mark is never
    shown on its own, so a finished or running build never reads as an open
    brief."""
    return {
        "declared": declared_intake(pd),
        "proposal": shaped_proposal(getattr(pd, "intake_proposal", None)),
        "reopened": bool(reopened),
    }
