"""The build level: the bar a build is measured against, chosen by the user.

The owner's ruling (2026-10-05): the bar comes from the brief. The Floor asks
for the level as a TYPED field -- the same pattern as the vertical and the
locale -- and the session and the blueprint both carry it. Nothing here reads
the brief's wording, and there is no default: a Floor build cannot start until
the user has chosen one (``floor_actions.require_build_level``).

The level is the single input that decides how far up the
CODE -> PRODUCT -> STORE ladder a run goes and how strict its top rung is:

=========== ============ ======================== ==================================
level       stops at     thin SUCCESS is failure  acceptance floor
=========== ============ ======================== ==================================
prototype   CODE         no                       not run (STORE is not reached)
light       STORE        no                       production-only checks advisory
pilot       STORE        yes                      production-only checks advisory
production  STORE        yes                      the full floor, every check enforced
=========== ============ ======================== ==================================

* **prototype** is DONE at CODE_GREEN: the code-phase suite passes and the run
  ends there. No pilot cycle opens; PRODUCT and STORE are NOT RUN, and the
  ledger says so.
* **light** climbs the whole ladder -- the pilot suite runs against the booted
  product and the Store gate measures acceptance -- but a pass whose handlers
  are mostly Factory templates still counts: a working demo, authorship not
  required. Light differs from pilot in strictness, not in reach, so the
  PRODUCT and STORE verdicts it reports are the same gates a pilot runs.
* **pilot** adds "thin SUCCESS is a failure": the authorship floor applies.
* **production** adds the acceptance floor in full: the production-only
  checks (the security scan) are enforced instead of advisory.

A blueprint that declares no level only reaches the runner from a direct,
non-Floor caller (the CLI, a test). Nothing is lowered for it: every gate the
run reaches is enforced in full, and how far it climbs stays the operator's
``FACTORY_AUTO_PILOT`` setting, exactly as before levels existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


class BuildLevel(str, Enum):
    """The levels the Floor offers, lowest bar first."""

    PROTOTYPE = "prototype"
    LIGHT = "light"
    PILOT = "pilot"
    PRODUCTION = "production"


class BuildLevelError(ValueError):
    """A level the Factory does not define."""


@dataclass(frozen=True)
class LevelBar:
    """What one level asks of a run."""

    level: BuildLevel
    #: The last gate the run goes through: "CODE" or "STORE".
    stop_gate: str
    #: Thin SUCCESS (templates-only handlers) is refused as a failure.
    thin_success_is_failure: bool
    #: The acceptance floor's production-only checks are enforced.
    full_floor: bool

    @property
    def reaches_pilot(self) -> bool:
        """True when the run continues past code-cycle SUCCESS."""
        return self.stop_gate != "CODE"

    def to_json(self) -> dict:
        return {
            "build_level": self.level.value,
            "stop_gate": self.stop_gate,
            "thin_success_is_failure": self.thin_success_is_failure,
            "full_floor": self.full_floor,
        }


BARS: Mapping[BuildLevel, LevelBar] = {
    BuildLevel.PROTOTYPE: LevelBar(BuildLevel.PROTOTYPE, "CODE", False, False),
    BuildLevel.LIGHT: LevelBar(BuildLevel.LIGHT, "STORE", False, False),
    BuildLevel.PILOT: LevelBar(BuildLevel.PILOT, "STORE", True, False),
    BuildLevel.PRODUCTION: LevelBar(BuildLevel.PRODUCTION, "STORE", True, True),
}


def parse_build_level(raw: Optional[str]) -> BuildLevel:
    """The typed level, or BuildLevelError. Never a guess, never a default."""
    try:
        return BuildLevel(str(raw or "").strip())
    except ValueError as exc:
        raise BuildLevelError(
            f"unknown build level {raw!r}; expected one of "
            + ", ".join(level.value for level in BuildLevel)
        ) from exc


def declared_level(blueprint: Any) -> Optional[BuildLevel]:
    """The level the blueprint carries, or None when it declares none.

    Reads the typed field only; a value the Factory does not define is an
    error, not an undeclared level.
    """
    if isinstance(blueprint, Mapping):
        raw = blueprint.get("build_level")
    else:
        raw = getattr(blueprint, "build_level", None)
    if raw is None or str(raw).strip() == "":
        return None
    return parse_build_level(raw)


def bar_for(blueprint: Any) -> Optional[LevelBar]:
    """The bar of the blueprint's declared level; None when undeclared."""
    level = declared_level(blueprint)
    return BARS[level] if level is not None else None


def ledger_bar(events: Iterable[Any]) -> Optional[dict]:
    """The level a run recorded, read back from its own ledger: the latest
    event payload that carries ``build_level``. None when the run predates
    levels or declared none."""
    found: Optional[dict] = None
    for event in events:
        payload = getattr(event, "payload", None) or {}
        if isinstance(payload, Mapping) and payload.get("build_level"):
            found = {
                "build_level": str(payload.get("build_level")),
                "stop_gate": str(payload.get("stop_gate") or ""),
            }
    return found


def start_expectation(blueprint: Any) -> str:
    """What the build-start message promises, rendered from the level's ONE
    declared value -- its stop gate: where the run stops, and that Finished /
    Download unlocks only when that gate is green."""
    from app.factory.build.level_grade import Level

    bar = bar_for(blueprint)
    if bar is None:
        return (
            "No build level declared: every gate the run reaches is enforced "
            "in full, and Finished / Download unlocks only when the last gate "
            "it runs is green."
        )
    # The grade a green stop gate earns: CODE -> CODE_GREEN, STORE -> STORE_GREEN.
    finish = (
        Level.CODE_GREEN.value if bar.stop_gate == "CODE" else Level.STORE_GREEN.value
    )
    return (
        f"Build level {bar.level.value}: the run stops at the {bar.stop_gate} "
        f"gate. Watch it here — Finished / Download unlocks only when the "
        f"{bar.stop_gate} gate is green ({finish})."
    )


def render_exit_condition(blueprint: Any) -> str:
    """The writer brief's exit condition and the CODE -> PRODUCT -> STORE
    ladder, rendered for the blueprint's declared level."""
    from app.factory.build.level_grade import Level
    from app.factory.build.product_gate import GATE_SCOPES

    ladder = (
        "The ladder (fail-closed; a gate that did not run is NOT a pass):\n"
        f"- CODE -> {Level.CODE_GREEN.value}: {GATE_SCOPES['CODE']}\n"
        f"- PRODUCT -> with STORE, {Level.STORE_GREEN.value}: {GATE_SCOPES['PRODUCT']}\n"
        f"- STORE -> {Level.FOUNDING_CUSTOMER_READY.value} when founding files + "
        f"contracts hold: {GATE_SCOPES['STORE']}"
    )
    bar = bar_for(blueprint)
    if bar is None:
        return (
            "BUILD LEVEL: not declared (a direct, non-Floor run).\n"
            "Every gate this run reaches is enforced in full; nothing is "
            "lowered. Thin SUCCESS is a failure to finish.\n\n" + ladder
        )
    level = bar.level.value
    if not bar.reaches_pilot:
        exit_lines = (
            f"BUILD LEVEL: {level} (chosen by the user).\n"
            f"Exit condition: the run is DONE at {Level.CODE_GREEN.value} -- "
            "the CODE gate passes. PRODUCT and STORE are not run at this level "
            "and the ledger records them as NOT RUN, never as passed."
        )
    else:
        exit_lines = (
            f"BUILD LEVEL: {level} (chosen by the user).\n"
            "Exit condition: the run is DONE only when the PRODUCT gate passes "
            "(pytest -m pilot on the booted product), the STORE gate passes, "
            "and the ledger records pilot_ready=true."
        )
        if bar.thin_success_is_failure:
            exit_lines += (
                f"\n{Level.CODE_GREEN.value} (code-cycle SUCCESS, "
                "pilot_ready=false) is not Finished at this level. Templates-only "
                "output, stub handlers and skipped capabilities are not a "
                "finished product: thin SUCCESS is a failure to finish."
            )
        else:
            exit_lines += (
                "\nAt this level a pass on Factory-template handlers still "
                "counts; the authorship floor is not applied."
            )
        if bar.full_floor:
            exit_lines += (
                "\nThe acceptance floor applies in full: every check, the "
                "production-only security scan included, is enforced."
            )
        else:
            exit_lines += (
                "\nThe acceptance floor's production-only checks (the "
                "security scan) are advisory at this level; every other check "
                "is enforced."
            )
    return exit_lines + "\n\n" + ladder
