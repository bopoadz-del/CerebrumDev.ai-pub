"""U7: PILOT_READY requires a pilot cycle that actually ran.

``evaluate_promotion`` computed PILOT_READY from stage evidence files and
kernel ownership alone. Nothing tied it to a build. Writing three JSON files
under ``build/stages`` was therefore sufficient to flip the flag, with no
pilot cycle having executed anywhere -- which is precisely the claim the
spec forbids: *pilot_ready may only be set by a gate that observed a
business outcome, and no human may assert it*.

The evidence a pilot cycle leaves behind is its ledger. ``BuildLedger``
records ``RUN_SUCCEEDED`` with ``cycle == "pilot"`` only after all five role
gates pass on the pilot marker, which includes the STORE_MANAGER durability
gate. That event is the thing worth requiring, because it cannot be produced
by writing a document.

This module locates such a ledger and reports what it found. It never
synthesises one: an absent ledger is reported as unproven, not assumed.

The ledger alone still leaves a gap: it proves *a* pilot cycle succeeded in
*some* workspace, and nothing stopped a caller pointing at a different one,
or at a tree edited after the cycle finished. So the workspace's stamped
``docs/package_identity.json`` is re-verified against a freshly computed
``artifact_digest``. A mismatch means the tree changed after the pilot cycle
stamped it, and the ledger no longer describes what is there.

``build_ledger.jsonl`` is in ``RESIDUE_NAMES`` and excluded from the digest,
so the ledger can grow without invalidating identity -- verified both ways:
editing ``app/routes.py`` breaks the match, appending to the ledger does not.

Scope note: this binds the ledger to the bytes in the workspace it came
from. It does not by itself prove that workspace was built from the factory
revision under promotion; the identity document records the engine and
digest, and tying those to a source revision is a further step.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Env var naming the workspace whose pilot cycle backs this promotion.
WORKSPACE_ENV = "FACTORY_PILOT_WORKSPACE"

LEDGER_NAME = "build_ledger.jsonl"


def _candidate_ledgers(explicit: Optional[Path]) -> List[Path]:
    out: List[Path] = []
    if explicit is not None:
        p = Path(explicit)
        out.append(p / LEDGER_NAME if p.is_dir() else p)
    env = os.getenv(WORKSPACE_ENV)
    if env:
        p = Path(env)
        out.append(p / LEDGER_NAME if p.is_dir() else p)
    return [p for p in out if p.name == LEDGER_NAME]


def _succeeded_pilot_events(ledger_path: Path) -> List[Dict[str, Any]]:
    """RUN_SUCCEEDED events whose cycle is pilot."""
    found: List[Dict[str, Any]] = []
    try:
        text = ledger_path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if str(event.get("kind")) != "RUN_SUCCEEDED":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if payload.get("cycle") == "pilot" or payload.get("pilot_ready") is True:
            found.append(event)
    return found


def _verify_identity(workspace: Path) -> Dict[str, Any]:
    """Does the workspace still hold the bytes its pilot cycle stamped?"""
    from app.factory.build.package import IDENTITY_REL, artifact_digest

    out: Dict[str, Any] = {
        "checked": True,
        "matches": False,
        "digest": None,
        "stamped": None,
        "reason": None,
    }
    stamp = workspace / IDENTITY_REL
    if not stamp.is_file():
        out["reason"] = (
            f"workspace has no {IDENTITY_REL.as_posix()} — the pilot cycle that "
            "wrote this ledger did not stamp the artifact, so the ledger cannot "
            "be tied to these bytes"
        )
        return out
    try:
        doc = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["reason"] = f"{IDENTITY_REL.as_posix()} is unreadable: {exc}"
        return out
    stamped = str(doc.get("digest") or "")
    actual = artifact_digest(workspace)
    out["stamped"] = stamped[:16] or None
    out["digest"] = actual[:16]
    if not stamped:
        out["reason"] = f"{IDENTITY_REL.as_posix()} records no digest"
        return out
    if stamped != actual:
        out["reason"] = (
            "workspace does not match the identity its pilot cycle stamped "
            f"(stamped {stamped[:12]}…, now {actual[:12]}…) — the tree changed "
            "after the cycle finished, so the ledger no longer describes it"
        )
        return out
    out["matches"] = True
    return out


def inspect_pilot_cycle(workspace: Optional[Path] = None) -> Dict[str, Any]:
    """Did a pilot cycle actually succeed? Reports, never assumes."""
    result: Dict[str, Any] = {
        "ok": False,
        "reason": None,
        "ledger": None,
        "succeeded_pilot_runs": 0,
        "workspace_env": WORKSPACE_ENV,
        "identity": {"checked": False, "matches": None, "digest": None},
        "note": (
            "binds the pilot ledger to the bytes of the workspace it came from; "
            "does not by itself tie that workspace to a factory revision"
        ),
    }
    candidates = _candidate_ledgers(workspace)
    if not candidates:
        result["reason"] = (
            f"no pilot workspace given — set {WORKSPACE_ENV} to the build "
            "workspace whose pilot cycle backs this promotion"
        )
        return result

    for path in candidates:
        if not path.is_file():
            continue
        result["ledger"] = str(path)
        events = _succeeded_pilot_events(path)
        result["succeeded_pilot_runs"] = len(events)
        if not events:
            result["reason"] = "ledger has no RUN_SUCCEEDED event with cycle=pilot"
            return result
        identity = _verify_identity(path.parent)
        result["identity"] = identity
        if not identity["matches"]:
            result["reason"] = identity["reason"]
            return result
        result["ok"] = True
        return result

    result["reason"] = f"ledger not found at {candidates[0]}"
    return result
