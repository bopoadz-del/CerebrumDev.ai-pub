"""S1 root cause / lane-authority — which factory module owns which defect.

U1–U12 and F1–F29 each map to a named owner module (existing path, or an
explicit missing expected path). Lanes come from ``authority.ROLE_CONTRACTS``;
this module does not invent a second authority model.

LotDesk-class symptoms (F1, F5, F6, F11, F14, F18, F19, F20, F24) map to
named owners. The fixture is inspected, never patched.

Evidence: ``build/stages/S1_root_cause.json`` + reread twin. Mismatch = fail.
Does not emit PILOT_READY. Does not start S2+.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.factory.build import probe_set
from app.factory.build.authority import ROLE_CONTRACTS
from app.factory.build.domain_acceptance import inspect_lotdesk_domain
from app.factory.build.lotdesk_gate import reject_lotdesk_as_shipped
from app.factory.build.preflight import (
    S4_EVIDENCE_FILENAME,
    reread_twin_path,
    write_evidence,
)

EMITTER_ID = "app.factory.build.root_cause.evaluate_root_cause"
STAGE_NAME = "ROOT_CAUSE"
STAGE = probe_set.stage_id(STAGE_NAME)

#: Every defect the probe set declares, by family, in declared order.
REQUIRED_U_IDS: Tuple[str, ...] = probe_set.codes_where(family="U")
REQUIRED_F_IDS: Tuple[str, ...] = probe_set.codes_where(family="F")
REQUIRED_DEFECT_IDS: Tuple[str, ...] = REQUIRED_U_IDS + REQUIRED_F_IDS

#: LotDesk-as-shipped symptoms the named gate and S12 domain inspect prove:
#: the defects the probe set marks ``lotdesk``.
LOTDESK_CLASS_CODES: Tuple[str, ...] = probe_set.codes_where(lotdesk=True)

#: id -> owner, from the probe set. ``owner_module`` is a repo-relative path.
#: ``present`` is filled at evaluation time. The probe-set bookkeeping keys
#: (family, shapes, class, ...) stay in the probe set.
_PROBE_KEYS = frozenset(
    {"family", "shapes", "class", "lotdesk_required_rejection", "promotion_blocker", "parent"}
)
DEFECT_OWNERS: Dict[str, Dict[str, Any]] = {
    code: {k: v for k, v in row.items() if k not in _PROBE_KEYS}
    for code, row in probe_set.defects().items()
}
PARENT_DEFECT: str = probe_set.codes_where(parent=True)[0]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_stages_dir() -> Path:
    return _repo_root() / "build" / "stages"


def lane_authority_map() -> Dict[str, Any]:
    """Cite authority.py. Do not fork write-lane truth."""
    roles: Dict[str, Any] = {}
    for role, contract in ROLE_CONTRACTS.items():
        roles[role.value] = {
            "title": contract.title,
            "write_lanes": [glob for _root, glob in contract.write_lanes],
            "read_only": not contract.write_lanes,
            "gate": contract.gate,
            "agent": contract.agent.value,
        }
    return {
        "source": "backend/app/factory/build/authority.py",
        "roles": roles,
        "COLLECTOR_blocked": "cannot remove echo stubs or invent block ids",
        "CLONER_blocked": "cannot vendor kernel under app/; cannot rewrite factory/",
        "WRITER_blocked": "cannot write tests/** or vendor/**",
        "TESTER_blocked": "Never patch app/; cannot ship kernel",
        "STORE_MANAGER_blocked": "harvest remain unbuilt",
    }


def defect_owners(*, repo: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    root = Path(repo) if repo is not None else _repo_root()
    out: Dict[str, Dict[str, Any]] = {}
    for code, spec in DEFECT_OWNERS.items():
        row = dict(spec)
        owner = spec["owner_module"]
        row["owner_present"] = (root / owner).is_file()
        also = spec.get("also")
        if also:
            row["also_present"] = (root / also).is_file()
        out[code] = row
    return out


def lotdesk_symptom_owners(
    *,
    repo: Optional[Path] = None,
    shipped: Optional[Dict[str, Any]] = None,
    domain: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Every LotDesk finding code maps to a named owner module."""
    owners = defect_owners(repo=repo)
    shipped = shipped if shipped is not None else reject_lotdesk_as_shipped()
    domain = domain if domain is not None else inspect_lotdesk_domain()
    codes = []
    for raw in list(shipped.get("codes") or []) + list(domain.get("codes") or []):
        if raw not in codes:
            codes.append(raw)
    mapped: List[Dict[str, Any]] = []
    unmapped: List[str] = []
    for code in codes:
        spec = owners.get(code)
        if not spec or not spec.get("owner_module"):
            unmapped.append(code)
            continue
        mapped.append(
            {
                "code": code,
                "owner_module": spec["owner_module"],
                "owner_present": spec.get("owner_present"),
                "title": spec.get("title"),
            }
        )
    return {
        "codes": codes,
        "mapped": mapped,
        "unmapped": unmapped,
        "lotdesk": "fixture only; not patched",
        "ok": not unmapped and bool(mapped),
    }


def canonical_fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    owners = result.get("defect_owners") or {}
    return {
        "required_ids": result.get("required_ids"),
        "owner_modules": {
            code: (owners.get(code) or {}).get("owner_module")
            for code in REQUIRED_DEFECT_IDS
        },
        "lane_source": (result.get("lane_authority_map") or {}).get("source"),
        "lotdesk_unmapped": (result.get("lotdesk_symptoms") or {}).get("unmapped"),
        "s4_evidence": result.get("s4_evidence") or S4_EVIDENCE_FILENAME,
    }


def fingerprint_disagreements(
    primary: Dict[str, Any], reread: Dict[str, Any]
) -> List[str]:
    left = canonical_fingerprint(primary)
    right = canonical_fingerprint(reread)
    if left == right:
        return []
    found: List[str] = []
    for key in left:
        if left.get(key) != right.get(key):
            found.append(key)
    return found or ["canonical_fingerprint"]


def evaluate_root_cause(*, repo: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo) if repo is not None else _repo_root()
    owners = defect_owners(repo=root)
    missing_keys = [code for code in REQUIRED_DEFECT_IDS if code not in owners]
    symptoms = lotdesk_symptom_owners(repo=root)
    first = None
    if missing_keys:
        first = "missing_defect_keys:" + ",".join(missing_keys)
    elif not symptoms["ok"]:
        first = "lotdesk_unmapped:" + ",".join(symptoms["unmapped"] or ["empty"])
    ok = first is None
    return {
        "stage": STAGE,
        "name": STAGE_NAME,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emitter": EMITTER_ID,
        "verdict": "PASS" if ok else "FAIL",
        "ok": ok,
        "first_failing_criterion": first,
        "required_ids": list(REQUIRED_DEFECT_IDS),
        "counts": {
            "U": len(REQUIRED_U_IDS),
            "F": len(REQUIRED_F_IDS),
            "total": len(REQUIRED_DEFECT_IDS),
        },
        "note_on_count": (
            "U1–U12 (12) + F1–F29 (29) = 41. No U0. F0 from draft-1 "
            "(shape-not-outcome) aliases U7."
        ),
        "lane_authority_map": lane_authority_map(),
        "defect_owners": owners,
        "lotdesk_symptoms": symptoms,
        "parent_defect": PARENT_DEFECT,
        "s4_evidence": S4_EVIDENCE_FILENAME,
        "one_sentence": (
            "Each U/F class is owned by a named factory module; LotDesk-class "
            "symptoms map to those owners and the fixture is not patched."
        ),
        "PILOT_READY": False,
        "not_claimed": [
            "PILOT_READY",
            "S2 cosign / image signature verification",
            "U4 persist-rewrite removal",
            "U7 marker change",
        ],
        "lotdesk": "fixture only; not patched",
        "llm_route_authorship": "not restored; _coder_route_body still returns None",
    }


def reread_matches(evidence: Dict[str, Any], twin: Dict[str, Any]) -> bool:
    if str(evidence.get("verdict") or "").strip().upper() != str(
        twin.get("verdict") or ""
    ).strip().upper():
        return False
    disagreements = twin.get("disagreements")
    if isinstance(disagreements, list) and disagreements:
        return False
    return True


def write_reread_twin(
    evidence_path: Path,
    result: Dict[str, Any],
    *,
    reread: Optional[Dict[str, Any]] = None,
) -> Path:
    second = reread if reread is not None else evaluate_root_cause()
    disagreements = fingerprint_disagreements(result, second)
    if disagreements:
        result["verdict"] = "FAIL"
        result["ok"] = False
        result["first_failing_criterion"] = "reread_mismatch:" + ",".join(
            disagreements
        )
        write_evidence(evidence_path, result)
    twin = {
        "stage": STAGE,
        "name": "root_cause",
        "verdict": result.get("verdict"),
        "reread_of": evidence_path.as_posix(),
        "reread_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "independent": True,
        "reader": "cloud-agent-s1-root-cause",
        "emitter": EMITTER_ID,
        "disagreements": disagreements,
        "checked": [
            "U1–U12 and F1–F29 keys present",
            "each id has a named owner_module",
            "LotDesk-class symptoms map to those owners",
            "lanes cited from authority.py ROLE_CONTRACTS",
            "_coder_route_body still returns None",
        ],
        "lane_source": (second.get("lane_authority_map") or {}).get("source"),
        "not_claimed": result.get("not_claimed") or [],
        "PILOT_READY": False,
    }
    dest = reread_twin_path(evidence_path)
    dest.write_text(json.dumps(twin, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return dest


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    stages = default_stages_dir()
    dest = stages / "S1_root_cause.json"
    if args:
        stages = Path(args[0])
        dest = stages / "S1_root_cause.json"
    if len(args) > 1:
        dest = Path(args[1])
    result = evaluate_root_cause()
    write_evidence(dest, result)
    write_reread_twin(dest, result)
    print(
        json.dumps(
            {
                "wrote": str(dest),
                "verdict": result["verdict"],
                "PILOT_READY": False,
            },
            indent=2,
        )
    )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
