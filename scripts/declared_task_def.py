"""The backend task's size is declared in the repo (infra/backend_task.json).

deploy-aws.yml hands this script the LIVE task definition (``aws ecs
describe-task-definition`` output) and the declaration. When cpu, memory or a
declared environment value differs, it writes a register-task-definition input
that is the live revision with ONLY those fields replaced -- roles, secrets,
logging, ports, volumes and every other container field are copied verbatim --
and the workflow registers it and rolls the service onto it. When they are
equal it writes nothing and the workflow rolls as before.

Usage:
  declared_task_def.py --live live.json --declared infra/backend_task.json --out new.json
Prints ``changed: ...`` or ``unchanged``; exit 0 either way, 2 on a bad input.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

#: describe-task-definition fields that register-task-definition does not
#: accept (read-only, set by ECS).
READ_ONLY_FIELDS = frozenset(
    {
        "taskDefinitionArn",
        "revision",
        "status",
        "requiresAttributes",
        "compatibilities",
        "registeredAt",
        "registeredBy",
        "deregisteredAt",
    }
)


class DeclarationError(ValueError):
    """The declaration or the live definition cannot be compared."""


def _task_definition(live: Mapping[str, Any]) -> Dict[str, Any]:
    """Accept the raw describe output ({"taskDefinition": ..., "tags": ...})
    or the bare definition."""
    td = live.get("taskDefinition", live)
    if not isinstance(td, dict) or "containerDefinitions" not in td:
        raise DeclarationError("live input is not a task definition")
    return td


def _container(defn: Dict[str, Any], name: str) -> Dict[str, Any]:
    for container in defn.get("containerDefinitions") or []:
        if container.get("name") == name:
            return container
    raise DeclarationError(f"no container named {name!r} in the live task definition")


def _apply_env(container: Dict[str, Any], declared_env: Mapping[str, str]) -> List[str]:
    env: List[Dict[str, str]] = container.setdefault("environment", [])
    by_name = {e.get("name"): e for e in env}
    changed = []
    for key, value in declared_env.items():
        value = str(value)
        entry = by_name.get(key)
        if entry is None:
            env.append({"name": key, "value": value})
            changed.append(key)
        elif entry.get("value") != value:
            entry["value"] = value
            changed.append(key)
    return changed


def override(live: Mapping[str, Any], declared: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The register-task-definition input for ``live`` with the declared
    fields applied, or None when the live revision already matches."""
    td = _task_definition(live)
    name = declared.get("container")
    if not isinstance(name, str) or not name:
        raise DeclarationError("declaration names no container")
    new = {k: copy.deepcopy(v) for k, v in td.items() if k not in READ_ONLY_FIELDS}
    tags = live.get("tags") if isinstance(live, Mapping) else None
    if tags:
        new["tags"] = copy.deepcopy(tags)
    changed: List[str] = []
    for field in ("cpu", "memory"):
        if field in declared:
            want = str(declared[field])
            if str(td.get(field)) != want:
                new[field] = want
                changed.append(field)
    env = declared.get("environment") or {}
    if not isinstance(env, Mapping):
        raise DeclarationError("declared environment must be an object")
    changed += [f"env:{k}" for k in _apply_env(_container(new, name), env)]
    if not changed:
        return None
    new["_changed"] = changed
    return new


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", required=True)
    parser.add_argument("--declared", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        live = json.loads(Path(args.live).read_text(encoding="utf-8"))
        declared = json.loads(Path(args.declared).read_text(encoding="utf-8"))
        new = override(live, declared)
    except (OSError, json.JSONDecodeError, DeclarationError) as exc:
        print(f"declared_task_def: {exc}", file=sys.stderr)
        return 2
    if new is None:
        print("unchanged")
        return 0
    changed = new.pop("_changed")
    Path(args.out).write_text(json.dumps(new, indent=1), encoding="utf-8")
    print("changed: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
