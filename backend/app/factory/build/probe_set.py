"""The Factory's probe set -- every probe id, as data, with what it is.

``probe_set.json`` beside this module is the one place a defect code, a stage
id or a network-posture id is written down. Each entry carries what the code
that consumes it actually decides on:

* a defect: its ``family``, owner module, ``shapes`` (the conditions a
  detector names -- "health_constant_ok", "hollow_queue"), ``class`` and
  attributes such as ``lotdesk``, ``lotdesk_required_rejection`` and
  ``promotion_blocker``;
* a stage: its ``name`` (the module's STAGE_NAME), expected module, purpose,
  ``minimum_for_promotion`` and ``provenance`` kind;
* a network posture: ``chosen``, ``network`` (does it egress) and why.

Gates and stages ask by shape, class, name or attribute and get ids back. No
module spells an id: a new probe of an existing class is handled by adding
one record here, never by a code change.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROBE_SET_PATH = Path(__file__).with_name("probe_set.json")


class ProbeSetError(ValueError):
    """The probe set is malformed or a lookup is ambiguous."""


@lru_cache(maxsize=1)
def load() -> Dict[str, Any]:
    data = json.loads(PROBE_SET_PATH.read_text(encoding="utf-8"))
    _validate(data)
    return data


def _validate(data: Dict[str, Any]) -> None:
    seen: Dict[str, str] = {}
    for code, row in (data.get("defects") or {}).items():
        for shape in row.get("shapes") or []:
            if shape in seen:
                raise ProbeSetError(
                    f"shape {shape!r} is declared by both {seen[shape]} and {code}"
                )
            seen[shape] = code
    names = [s.get("name") for s in data.get("stages") or [] if s.get("name")]
    if len(names) != len(set(names)):
        raise ProbeSetError("two stages share a name")
    chosen = [p for p, row in (data.get("network_postures") or {}).items() if row.get("chosen")]
    if len(chosen) != 1:
        raise ProbeSetError(f"exactly one network posture is chosen, found {chosen}")


# -- defects ------------------------------------------------------------------


def defects() -> Dict[str, Dict[str, Any]]:
    """Every defect, in declared order, as a fresh copy."""
    return {code: dict(row) for code, row in load()["defects"].items()}


def code_for(shape: str) -> str:
    """The one defect a detector reports when it observes ``shape``."""
    for code, row in load()["defects"].items():
        if shape in (row.get("shapes") or []):
            return code
    raise ProbeSetError(f"no defect declares the shape {shape!r}")


def codes_where(**attrs: Any) -> Tuple[str, ...]:
    """Defect codes, in declared order, whose attributes equal ``attrs``.

    ``family``/``class``/``lotdesk``... -- ``cls`` stands for ``class``.
    """
    want = {("class" if k == "cls" else k): v for k, v in attrs.items()}
    return tuple(
        code
        for code, row in load()["defects"].items()
        if all(row.get(k) == v for k, v in want.items())
    )


def present_flags(codes: List[str], wanted: Tuple[str, ...]) -> Dict[str, bool]:
    """``{"<code>_present": bool}`` for each wanted code, keys lower-cased."""
    found = set(codes)
    return {f"{code.lower()}_present": code in found for code in wanted}


# -- stages -------------------------------------------------------------------


def stages() -> List[Dict[str, Any]]:
    """Every stage, in pipeline order, as fresh copies."""
    return [dict(row) for row in load()["stages"]]


def stage_ids() -> Tuple[str, ...]:
    return tuple(row["id"] for row in load()["stages"])


def stage_id(name: str) -> str:
    """The stage whose declared name is ``name`` (a module's STAGE_NAME)."""
    for row in load()["stages"]:
        if row.get("name") == name:
            return row["id"]
    raise ProbeSetError(f"no stage is named {name!r}")


def stage_ids_where(**attrs: Any) -> Tuple[str, ...]:
    return tuple(
        row["id"]
        for row in load()["stages"]
        if all(row.get(k) == v for k, v in attrs.items())
    )


def stages_after(stage: str) -> Tuple[str, ...]:
    ids = stage_ids()
    return ids[ids.index(stage) + 1:]


# -- network postures ---------------------------------------------------------


def chosen_posture() -> Tuple[str, Dict[str, Any]]:
    for pid, row in load()["network_postures"].items():
        if row.get("chosen"):
            return pid, dict(row)
    raise ProbeSetError("no network posture is chosen")


def posture(posture_id: str) -> Optional[Dict[str, Any]]:
    row = load()["network_postures"].get(posture_id)
    return dict(row) if row is not None else None


def rejected_postures() -> Dict[str, str]:
    """Rejected alternative -> why it was rejected, in declared order."""
    return {
        pid: row.get("rejected_because", "")
        for pid, row in load()["network_postures"].items()
        if not row.get("chosen")
    }


def posture_egresses(posture_id: str) -> bool:
    """True when the named posture declares network egress."""
    row = load()["network_postures"].get(posture_id)
    return bool(row and row.get("network"))


# -- acceptance outcomes ------------------------------------------------------


def acceptance_outcomes() -> Tuple[Dict[str, str], ...]:
    return tuple(dict(row) for row in load()["acceptance_outcomes"])
