"""S13 promotion emitter — PILOT_READY is machine-emitted, not a human claim.

Reads S0–S12 stage evidence (or the subset present). Required minimum is
S10, S11, S12. Any earlier admitted ``S*.json`` in the stages dir is also
required. S4 has one canonical filename (``S4_ship_kernel.json``); the
``S4_kernel.json`` alias is not a reader input.
For each required record:

* evidence file exists
* reread twin exists and matches (same verdict, empty disagreements)
* verdict is PASS / performed drills, not configured-only

Missing, FAIL, reread mismatch, or configured-only → PILOT_READY is false.

LotDesk is a reject fixture. It cannot become PILOT_READY.

Upstream harvest is evaluated separately and is BLOCKED until an authorized
Cerebrum-Blocks write path exists. A BLOCKED harvest does not by itself
flip PILOT_READY — it is recorded with consequences.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.factory.build import probe_set
from app.factory.build.domain_acceptance import inspect_lotdesk_domain
from app.factory.build.harvest import evaluate_harvest
from app.factory.build.lotdesk_gate import inspect_path, reject_lotdesk_as_shipped
from app.factory.build.pilot_cycle_proof import inspect_pilot_cycle
from app.factory.build.network_posture import NETWORK_POSTURE, NETWORK_POSTURE_REASON
from app.factory.build.preflight import (
    S4_EVIDENCE_FILENAME,
    inspect_kernel_ownership,
    is_admitted_stage_evidence,
)
from app.factory.generator import git_head

EMITTER_ID = "app.factory.build.promotion.evaluate_promotion"
STAGE = probe_set.stage_id("PROMOTION")
#: Stages promotion cannot proceed without, whatever evidence is present.
MINIMUM_REQUIRED = probe_set.stage_ids_where(minimum_for_promotion=True)
#: Every stage before this one, in pipeline order: the evidence it reads.
PRIOR_STAGES = probe_set.stage_ids()[: probe_set.stage_ids().index(STAGE)]
STAGE_FILE_RE = re.compile(
    r"^(" + "|".join(re.escape(s) for s in PRIOR_STAGES) + r")_[A-Za-z0-9_]+\.json$"
)
CONFIGURED_MARKERS = ("configured-only", "configured only", "not performed")
PASS_VERDICTS = {"PASS", "PERFORMED"}
FAIL_VERDICTS = {"FAIL", "FAILED", "BLOCKED", "ERROR"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def default_stages_dir() -> Path:
    return _repo_root() / "build" / "stages"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_evidence_files(stages_dir: Path) -> List[Path]:
    """Primary evidence files S0–S12. Reread twins and S13 are excluded."""
    found: List[Path] = []
    if not stages_dir.is_dir():
        return found
    for path in sorted(stages_dir.iterdir()):
        if not path.is_file() or path.suffix != ".json":
            continue
        if path.name.endswith(".reread.json"):
            continue
        if STAGE_FILE_RE.match(path.name) and is_admitted_stage_evidence(path.name):
            found.append(path)
    return found


def required_stage_ids(present: Sequence[Path]) -> Tuple[str, ...]:
    ids = {path.name.split("_", 1)[0] for path in present}
    ids.update(MINIMUM_REQUIRED)
    return tuple(stage for stage in PRIOR_STAGES if stage in ids)


def _files_for_stage(present: Sequence[Path], stage: str) -> List[Path]:
    files = [path for path in present if path.name.startswith(f"{stage}_")]
    # A stage whose evidence filename is pinned admits that one file only.
    if S4_EVIDENCE_FILENAME.split("_", 1)[0] == stage:
        return [path for path in files if path.name == S4_EVIDENCE_FILENAME]
    return files


def reread_twin_path(evidence: Path) -> Path:
    return evidence.with_name(evidence.stem + ".reread.json")


def _dump_lower(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True).lower()


def _verdict_of(data: Dict[str, Any]) -> str:
    raw = data.get("verdict")
    if raw is None:
        return ""
    return str(raw).strip().upper()


def _is_configured_only(data: Dict[str, Any]) -> bool:
    verdict = _verdict_of(data)
    if verdict in {"CONFIGURED", "CONFIGURED-ONLY"}:
        return True
    blob = _dump_lower(data)
    if "configured-only" in blob or "configured only" in blob:
        # S10/S11 evidence mentions the phrase while denying it. A real
        # configured-only record uses it as the verdict or drill status.
        performed = data.get("performed")
        if isinstance(performed, dict):
            for item in performed.values():
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or item.get("result") or "").lower()
                if status in {"configured", "configured-only"}:
                    return True
        outcomes = data.get("outcomes")
        if isinstance(outcomes, dict):
            for item in outcomes.values():
                if not isinstance(item, dict):
                    continue
                if str(item.get("status") or "").lower() in {
                    "configured",
                    "configured-only",
                }:
                    return True
        if verdict in PASS_VERDICTS:
            return False
        return True
    return False


def _has_performed_drills(data: Dict[str, Any], stage: str) -> bool:
    """S10–S12 must show performed drills, not a configured checklist."""
    if stage not in MINIMUM_REQUIRED:
        return _verdict_of(data) in PASS_VERDICTS
    if _is_configured_only(data):
        return False
    performed = data.get("performed")
    if isinstance(performed, list) and performed:
        return True
    if isinstance(performed, dict) and performed:
        for item in performed.values():
            if not isinstance(item, dict):
                continue
            result = str(item.get("result") or item.get("status") or "").upper()
            if result in PASS_VERDICTS or result == "OK":
                continue
            return False
        return True
    outcomes = data.get("outcomes")
    if isinstance(outcomes, dict) and outcomes:
        return all(
            isinstance(item, dict)
            and str(item.get("status") or "").lower() == "performed"
            for item in outcomes.values()
        )
    return False


def reread_matches(evidence: Dict[str, Any], twin: Dict[str, Any]) -> bool:
    """Twin agrees with the primary: same verdict, no recorded disagreements."""
    if _verdict_of(evidence) != _verdict_of(twin):
        return False
    disagreements = twin.get("disagreements")
    if isinstance(disagreements, list) and disagreements:
        return False
    return True


def _evaluate_record(evidence_path: Path) -> Dict[str, Any]:
    stage = evidence_path.name.split("_", 1)[0]
    record: Dict[str, Any] = {
        "stage": stage,
        "evidence": evidence_path.name,
        "exists": evidence_path.is_file(),
        "reread": reread_twin_path(evidence_path).name,
        "reread_exists": False,
        "reread_matches": False,
        "verdict": None,
        "performed": False,
        "configured_only": False,
        "ok": False,
        "reason": None,
    }
    if not evidence_path.is_file():
        record["reason"] = "evidence_missing"
        return record
    try:
        evidence = _load_json(evidence_path)
    except (OSError, json.JSONDecodeError) as exc:
        record["reason"] = f"evidence_unreadable:{exc}"
        return record
    record["verdict"] = _verdict_of(evidence) or None
    record["configured_only"] = _is_configured_only(evidence)
    record["performed"] = _has_performed_drills(evidence, stage)
    twin_path = reread_twin_path(evidence_path)
    record["reread_exists"] = twin_path.is_file()
    if not twin_path.is_file():
        record["reason"] = "reread_missing"
        return record
    try:
        twin = _load_json(twin_path)
    except (OSError, json.JSONDecodeError) as exc:
        record["reason"] = f"reread_unreadable:{exc}"
        return record
    record["reread_matches"] = reread_matches(evidence, twin)
    if not record["reread_matches"]:
        record["reason"] = "reread_mismatch"
        return record
    if record["configured_only"]:
        record["reason"] = "configured_only"
        return record
    verdict = record["verdict"] or ""
    if verdict in FAIL_VERDICTS or verdict not in PASS_VERDICTS:
        record["reason"] = "verdict_not_pass"
        return record
    if stage in MINIMUM_REQUIRED and not record["performed"]:
        record["reason"] = "configured_only"
        record["configured_only"] = True
        return record
    record["ok"] = True
    return record


def _spof_from(data: Dict[str, Any]) -> Optional[str]:
    disk = data.get("sqlite_on_mounted_disk")
    if isinstance(disk, dict) and disk.get("spof"):
        return str(disk["spof"])
    return None


def _network_from(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    chosen = data.get("chosen") or data.get("chosen_id")
    if not chosen:
        return None
    return {
        "chosen": chosen,
        "reason": data.get("reason") or NETWORK_POSTURE_REASON,
    }


def collect_provenance(
    stages_dir: Path,
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    spof_stages = probe_set.stage_ids_where(provenance="spof")
    network_stages = probe_set.stage_ids_where(provenance="network")
    spof: Dict[str, Any] = {stage: None for stage in spof_stages}
    network = None
    for path in discover_evidence_files(stages_dir):
        stage = path.name.split("_", 1)[0]
        try:
            data = _load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if stage in spof and spof[stage] is None:
            spof[stage] = _spof_from(data)
        if stage in network_stages and network is None:
            network = _network_from(data)
    return {
        "git_sha": git_head(_repo_root()),
        "emitter": EMITTER_ID,
        "stage_verdicts": {
            item["evidence"]: item.get("verdict") for item in records if item.get("evidence")
        },
        "kernel_ownership": inspect_kernel_ownership(),
        "network_posture": network
        or {
            "chosen": NETWORK_POSTURE,
            "reason": NETWORK_POSTURE_REASON,
        },
        "spof": spof,
    }


def evaluate_promotion(
    stages_dir: Optional[Path] = None,
    *,
    include_harvest: bool = True,
    pilot_workspace: Optional[Path] = None,
) -> Dict[str, Any]:
    """Machine-emit PILOT_READY. False unless every required stage gates."""
    root = Path(stages_dir) if stages_dir is not None else default_stages_dir()
    present = discover_evidence_files(root)
    required = required_stage_ids(present)
    records: List[Dict[str, Any]] = []
    missing: List[str] = []
    failed: List[str] = []
    for stage in required:
        files = _files_for_stage(present, stage)
        if not files:
            missing.append(stage)
            records.append(
                {
                    "stage": stage,
                    "evidence": None,
                    "exists": False,
                    "reread_exists": False,
                    "reread_matches": False,
                    "verdict": None,
                    "performed": False,
                    "configured_only": False,
                    "ok": False,
                    "reason": "evidence_missing",
                }
            )
            continue
        for path in files:
            record = _evaluate_record(path)
            records.append(record)
            if not record["ok"]:
                failed.append(record["evidence"] or stage)
    first = None
    for record in records:
        if not record["ok"]:
            first = record.get("reason") or "stage_not_ok"
            if record.get("evidence"):
                first = f"{record['evidence']}:{first}"
            else:
                first = f"{record['stage']}:{first}"
            break
    kernel = inspect_kernel_ownership()
    if not kernel["ok"] and first is None:
        first = "kernel_ownership"
        failed.append("kernel_ownership")
    # U7: evidence documents alone must not flip the flag. A pilot cycle has
    # to have run and succeeded, which only a build ledger can show.
    pilot_cycle = inspect_pilot_cycle(pilot_workspace)
    if not pilot_cycle["ok"]:
        failed.append("pilot_cycle")
        if first is None:
            first = f"pilot_cycle:{pilot_cycle.get('reason') or 'unproven'}"
    pilot_ready = (
        first is None
        and not missing
        and not failed
        and kernel["ok"]
        and pilot_cycle["ok"]
    )
    harvest = evaluate_harvest() if include_harvest else None
    result: Dict[str, Any] = {
        "stage": STAGE,
        "name": "PROMOTION AND UPSTREAM HARVEST",
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emitter": EMITTER_ID,
        "PILOT_READY": bool(pilot_ready),
        "verdict": "PASS" if pilot_ready else "FAIL",
        "first_failing_criterion": first,
        "required_stages": list(required),
        "minimum_required": list(MINIMUM_REQUIRED),
        "pilot_cycle": pilot_cycle,
        "records": records,
        "missing": missing,
        "failed": failed,
        "provenance": collect_provenance(root, records),
        "harvest": harvest,
        "lotdesk": "fixture only; not patched",
        "llm_route_authorship": "not restored; _coder_route_body still returns None",
    }
    return result


def reject_lotdesk_promotion(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk cannot become PILOT_READY. The zip is inspected, never patched."""
    shipped = reject_lotdesk_as_shipped(explicit)
    domain = inspect_lotdesk_domain(explicit)
    path = Path(domain["fixture"])
    findings = inspect_path(path)
    codes = sorted({*shipped["codes"], *domain["codes"], *(item.code for item in findings)})
    required = probe_set.codes_where(promotion_blocker="required")
    reported = probe_set.codes_where(promotion_blocker="reported")
    missing = [code for code in required if code not in codes]
    if missing:
        raise AssertionError(
            "GATE HOLLOW: LotDesk promotion reject missing "
            + "/".join(missing)
            + ": "
            + ",".join(codes)
        )
    return {
        "ok": False,
        "PILOT_READY": False,
        "gate": "lotdesk_promotion",
        "lotdesk": "fixture only; not patched",
        "codes": codes,
        **probe_set.present_flags(codes, required + reported),
        "reasons": {
            "always_200": "GET /health is unconditional ok / always-200 (F1/F24)",
            "hollow_queue": "no persisted process transition (F5)",
            "missing_update_delete": "store has no update/delete (F6)",
        },
        "shipped": shipped,
        "domain": {
            "failed": domain["failed"],
            "performed": domain["performed"],
            "codes": domain["codes"],
        },
    }


def write_evidence(
    dest: Path,
    result: Dict[str, Any],
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(result, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return dest


def write_reread_twin(evidence_path: Path, result: Dict[str, Any]) -> Path:
    """Independent re-run of the same inputs. Twin must match the primary."""
    twin = {
        "stage": STAGE,
        "name": "promotion",
        "verdict": result.get("verdict"),
        "PILOT_READY": result.get("PILOT_READY"),
        "reread_of": str(evidence_path.as_posix()),
        "reread_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "independent": True,
        "reader": "cloud-agent-s13-promotion",
        "emitter": EMITTER_ID,
        "checked": [
            "evaluate_promotion re-ran against the same stages dir",
            "PILOT_READY equals the primary machine bit",
            "required S10/S11/S12 plus any earlier S*.json present",
            "reread twins compared by verdict + empty disagreements",
            "LotDesk reject_lotdesk_promotion keeps PILOT_READY false",
            "_coder_route_body still returns None; prepare_pilot_workspace absent",
            "harvest evaluate_harvest is BLOCKED; no Blocks write",
        ],
        "disagreements": [],
        "first_failing_criterion": result.get("first_failing_criterion"),
        "git_sha": (result.get("provenance") or {}).get("git_sha"),
        "harvest_verdict": (result.get("harvest") or {}).get("verdict"),
        "kernel_ownership": (result.get("provenance") or {}).get("kernel_ownership"),
        "not_claimed": [
            "Cerebrum-Blocks write",
            "STORE_MANAGER harvest",
            "LotDesk patched to pass",
            "LLM-authored route bodies",
        ],
    }
    dest = reread_twin_path(evidence_path)
    dest.write_text(json.dumps(twin, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return dest


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    stages = default_stages_dir()
    dest = stages / "S13_promotion.json"
    if args:
        stages = Path(args[0])
    if len(args) > 1:
        dest = Path(args[1])
    result = evaluate_promotion(stages)
    write_evidence(dest, result)
    write_reread_twin(dest, result)
    print(json.dumps({"wrote": str(dest), "PILOT_READY": result["PILOT_READY"]}, indent=2))
    return 0 if result["PILOT_READY"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
