"""S0 preflight — fingerprint factory + RoleRunner emission before a build.

This is an inventory and identity gate, not a later-stage closer. S3
(the build's own Domain Pack) is ``build/domain_pack.py``. S2 residual: cosign
is not performed. Kernel ownership failure does fail S0: ``execute_action``
must be callable and ``_coder_route_body`` must return None.

Evidence: ``build/stages/S0_preflight.json`` + reread twin. A fingerprint
mismatch between the two is FAIL. Does not emit PILOT_READY.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.cerebrum_product_kernel.contract.runtime import execute_action
from app.factory.build import probe_set
from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES
from app.factory.build.roles import _coder_route_body
from app.factory.build_jobs import BUILD_ENGINE_ENV, RUNNER, build_engine
from app.factory.delivery_standard import DOMAIN_PACK_FIELDS, STANDARD_SHA256
from app.factory.generator import git_head
from app.factory.paths import factory_repo_root

EMITTER_ID = "app.factory.build.preflight.evaluate_preflight"
STAGE_NAME = "PREFLIGHT"
STAGE = probe_set.stage_id(STAGE_NAME)

# One S4 evidence filename. Closed #203 also wrote ``S4_kernel.json``; that
# alias is not a reader input. Do not commit either JSON from a feature PR.
S4_EVIDENCE_FILENAME = "S4_ship_kernel.json"
S4_EVIDENCE_ALIASES = frozenset({"S4_kernel.json"})

#: Factory + RoleRunner emitter sources hashed before a build. Paths are
#: repo-relative POSIX. A missing path is recorded, not invented.
FACTORY_SOURCE_PATHS: Tuple[str, ...] = (
    "backend/app/factory/delivery_standard.py",
    "backend/app/factory/standards/product_delivery_standard.md",
    "backend/app/factory/build/roles.py",
    "backend/app/factory/build/gates.py",
    "backend/app/factory/build/authority.py",
    "backend/app/factory/build/runner.py",
    "backend/app/factory/build/pilot.py",
    "backend/app/factory/build/converge.py",
    "backend/app/factory/coder.py",
    "backend/app/factory/generator.py",
    "backend/app/factory/product_architect.py",
    "backend/app/factory/build_jobs.py",
    "backend/app/cerebrum_product_kernel/contract/runtime.py",
    "backend/app/cerebrum_product_kernel/__init__.py",
    ".github/workflows/ci.yml",
    "backend/app/factory/build/preflight.py",
    "backend/app/factory/build/root_cause.py",
    "backend/app/factory/build/domain_pack.py",
)

#: Honest inventory of the stage table, from the probe set. Cite existing
#: modules; do not duplicate them. ``expected`` is the path the table named
#: (or the module that landed later -- S11/S12 were listed "no module" but
#: exist). The probe set's own bookkeeping (name, promotion role) stays there.
_INVENTORY_KEYS = ("expected", "purpose", "gaps", "note")
STAGE_MODULE_INVENTORY: Tuple[Dict[str, Any], ...] = tuple(
    {
        "stage": row["id"],
        **{
            key: (tuple(row[key]) if isinstance(row[key], list) else row[key])
            for key in _INVENTORY_KEYS
            if key in row
        },
    }
    for row in probe_set.stages()
)


def _repo_root() -> Path:
    """Workdir S0 fingerprints. Local checkout is the git root; the
    production image is ``/app`` (see ``factory_repo_root``).

    ``parents[4]`` is correct only when this file lives at
    ``backend/app/factory/build/preflight.py``. The live image copies
    ``backend/app`` → ``/app/app``, so that walk lands on ``/`` and every
    inventory path is ``factory_source_missing``.
    """
    return factory_repo_root()


def default_stages_dir() -> Path:
    return _repo_root() / "build" / "stages"


def reread_twin_path(evidence: Path) -> Path:
    return evidence.with_name(evidence.stem + ".reread.json")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_kernel_ownership() -> Dict[str, Any]:
    """Kernel owns HTTP. LLM route authorship stays off."""
    coder_none = _coder_route_body(None, None, None) is None
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    runner_src = Path(runner_mod.__file__).read_text(encoding="utf-8")
    # Identifiers the runner module binds or references, from its syntax tree.
    _tree = ast.parse(runner_src)
    runner_names = (
        {n.id for n in ast.walk(_tree) if isinstance(n, ast.Name)}
        | {n.attr for n in ast.walk(_tree) if isinstance(n, ast.Attribute)}
        | {a.asname or a.name for n in ast.walk(_tree)
           if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        | {n.name for n in ast.walk(_tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    )
    return {
        "execute_action": execute_action.__module__ + ".execute_action",
        "execute_action_callable": callable(execute_action),
        "_coder_route_body_is_None": coder_none,
        "prepare_pilot_workspace": "absent",
        "prepare_pilot_workspace_in_runner": "prepare_pilot_workspace" in runner_names,
        "prepare_pilot_workspace_in_pilot": hasattr(pilot_mod, "prepare_pilot_workspace"),
        "ok": coder_none
        and callable(execute_action)
        and "prepare_pilot_workspace" not in runner_names
        and not hasattr(pilot_mod, "prepare_pilot_workspace"),
    }


def fingerprint_path(repo: Path, rel: str) -> Dict[str, Any]:
    path = repo / rel
    record: Dict[str, Any] = {"path": rel, "present": path.is_file()}
    if not path.is_file():
        record["sha256"] = None
        record["bytes"] = 0
        return record
    data = path.read_bytes()
    record["sha256"] = sha256_bytes(data)
    record["bytes"] = len(data)
    return record


def fingerprint_factory(repo: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(repo) if repo is not None else _repo_root()
    return [fingerprint_path(root, rel) for rel in FACTORY_SOURCE_PATHS]


def fingerprint_role_runner_emission() -> Dict[str, Any]:
    """Identity of the RoleRunner emitter — not a product-tree hash.

    A product build is what this gate runs *before*. Emission identity is
    the default engine, the 14-class contract, and the emitter modules.
    """
    engine = build_engine()
    return {
        "id": "app.factory.build.runner.RoleRunner",
        "default_engine": engine,
        "engine_env": BUILD_ENGINE_ENV,
        "runner_is_default": engine == RUNNER,
        "template_revert": "FACTORY_BUILD_ENGINE=template",
        "fourteen_artifact_classes": list(FOURTEEN_ARTIFACT_CLASSES),
        "kernel_class_in_contract": "app/cerebrum_product_kernel"
        in FOURTEEN_ARTIFACT_CLASSES,
        "generator_class": "app.factory.generator.ProductGenerator",
        "converge_module": "backend/app/factory/build/converge.py",
    }


def is_admitted_stage_evidence(name: str) -> bool:
    """Whether a primary S0–S12 file is a reader input.

    S4 has one canonical name. ``S4_kernel.json`` is a rejected alias.
    Other stages may still carry more than one distinct record (S5, S7).
    """
    if Path(name).suffix != ".json" or Path(name).suffixes[-2:] == [".reread", ".json"]:
        return False
    if name in S4_EVIDENCE_ALIASES:
        return False
    if name.startswith("S4_") and name != S4_EVIDENCE_FILENAME:
        return False
    return True


def list_existing_stage_files(stages_dir: Optional[Path] = None) -> List[str]:
    root = Path(stages_dir) if stages_dir is not None else default_stages_dir()
    if not root.is_dir():
        return []
    names = []
    for path in sorted(root.iterdir()):
        if path.is_file() and path.suffix == ".json":
            names.append(path.name)
    return names


def inventory_stage_modules(repo: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(repo) if repo is not None else _repo_root()
    rows: List[Dict[str, Any]] = []
    for item in STAGE_MODULE_INVENTORY:
        expected = item["expected"]
        present = (root / expected).is_file()
        row = dict(item)
        row["present"] = present
        rows.append(row)
    return rows


def missing_modules(inventory: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Modules the stage table named that are not on disk."""
    return [row for row in inventory if not row.get("present")]


def canonical_fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    """Stable subset compared across a reread. Timestamps excluded."""
    return {
        "git_sha": result.get("git_sha"),
        "emitter": result.get("emitter"),
        "kernel_ownership": result.get("kernel_ownership"),
        "emitter_identity": result.get("emitter_identity"),
        "factory_source": result.get("factory_source"),
        "existing_stage_files": result.get("existing_stage_files"),
        "missing_modules": [
            {"stage": row.get("stage"), "expected": row.get("expected")}
            for row in (result.get("missing_modules") or [])
        ],
        "delivery_standard_pin": (result.get("delivery_standard") or {}).get("pin"),
        "domain_pack_field_count": (result.get("delivery_standard") or {}).get(
            "domain_pack_field_count"
        ),
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


def evaluate_preflight(
    *,
    repo: Optional[Path] = None,
    stages_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(repo) if repo is not None else _repo_root()
    stages = Path(stages_dir) if stages_dir is not None else default_stages_dir()
    kernel = inspect_kernel_ownership()
    factory_source = fingerprint_factory(root)
    missing_source = [row["path"] for row in factory_source if not row["present"]]
    inventory = inventory_stage_modules(root)
    missing = missing_modules(inventory)
    engine = fingerprint_role_runner_emission()
    git_sha = git_head(root)
    first = None
    if not kernel["ok"]:
        first = "kernel_ownership"
    elif missing_source:
        first = "factory_source_missing:" + ",".join(missing_source)
    elif not git_sha or git_sha == "unknown":
        first = "git_sha_unknown"
    elif not engine["runner_is_default"]:
        first = "role_runner_not_default_engine"
    ok = first is None
    return {
        "stage": STAGE,
        "name": STAGE_NAME,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emitter": EMITTER_ID,
        "verdict": "PASS" if ok else "FAIL",
        "ok": ok,
        "first_failing_criterion": first,
        "git_sha": git_sha,
        "kernel_ownership": kernel,
        "emitter_identity": engine,
        "delivery_standard": {
            "pin": STANDARD_SHA256,
            "domain_pack_field_count": len(DOMAIN_PACK_FIELDS),
        },
        "factory_source": factory_source,
        "existing_stage_files": list_existing_stage_files(stages),
        "s4_evidence": S4_EVIDENCE_FILENAME,
        "stage_module_inventory": inventory,
        "missing_modules": missing,
        "pass_criteria": {
            "factory_source_fingerprinted": not missing_source,
            "git_sha_recorded": bool(git_sha) and git_sha != "unknown",
            "emitter_identity_recorded": bool(engine.get("id")),
            "kernel_execute_action_callable": bool(kernel["execute_action_callable"]),
            "kernel_coder_route_body_is_None": bool(
                kernel["_coder_route_body_is_None"]
            ),
            "existing_stage_files_listed": True,
            "missing_modules_named": True,
        },
        "PILOT_READY": False,
        "not_claimed": [
            "PILOT_READY",
            "S2 cosign / image signature verification",
            "S4 U4 _ensure_route_persists_payload removal",
            "S5 U7 FACTORY_SUITE_MARKER_EXPR",
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


def write_evidence(dest: Path, result: Dict[str, Any]) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(result, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return dest


def write_reread_twin(
    evidence_path: Path,
    result: Dict[str, Any],
    *,
    reread: Optional[Dict[str, Any]] = None,
) -> Path:
    """Independent re-run. Twin disagreements fail the stage."""
    second = reread if reread is not None else evaluate_preflight()
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
        "name": "preflight",
        "verdict": result.get("verdict"),
        "reread_of": evidence_path.as_posix(),
        "reread_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "independent": True,
        "reader": "cloud-agent-s0-preflight",
        "emitter": EMITTER_ID,
        "git_sha": second.get("git_sha"),
        "kernel_ownership": second.get("kernel_ownership"),
        "disagreements": disagreements,
        "checked": [
            "factory sources re-hashed",
            "RoleRunner default engine re-read",
            "execute_action callable; _coder_route_body is None",
            "existing build/stages files listed",
            "missing stage-table modules named",
        ],
        "not_claimed": result.get("not_claimed") or [],
        "PILOT_READY": False,
    }
    dest = reread_twin_path(evidence_path)
    dest.write_text(json.dumps(twin, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return dest


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    stages = default_stages_dir()
    dest = stages / "S0_preflight.json"
    if args:
        stages = Path(args[0])
        dest = stages / "S0_preflight.json"
    if len(args) > 1:
        dest = Path(args[1])
    result = evaluate_preflight(stages_dir=stages)
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
