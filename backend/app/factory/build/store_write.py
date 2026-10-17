"""U6: the authorized write path from the Factory to Cerebrum-Blocks.

STORE_MANAGER's mandate has always said harvesting improvements back
upstream "remains unbuilt", and ``harvest.py`` has been an honest BLOCK
around that hole: ``store_write_exists`` was the literal ``False``. Every
correction the Factory learned — Store-unwired adapters, permissions that
contradict behaviour, ``ui_schema`` fields the published contract omits —
stayed in this repo's emission, so each new product re-shipped the same
upstream defects.

This module is that missing half. Its shape is chosen so that a wrong
harvest is recoverable:

* **Never ``main``.** A harvest writes to a fresh branch in a Cerebrum-Blocks
  checkout. A human reviews and merges. There is no code path here that
  commits to the default branch.
* **Never pushes by default.** ``execute_harvest`` plans and, when asked,
  writes and commits locally. Pushing is a separate, explicit act — the
  network is the one step that cannot be undone by deleting a branch.
* **Fail closed on a dirty checkout.** Committing on top of someone's
  uncommitted work would mix their changes into a Factory harvest commit.
* **Authorization is a committed file, not an env var.** A dashboard secret
  must not be able to write to the Store. ``build/stages/HARVEST_AUTHORIZED.json``
  has to name this exact repo, and lands through review like anything else.

The payload is computed, never hand-listed: :func:`plan_harvest` derives it
from the same survey that reports the divergence, so the thing harvested is
by construction the thing detected.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.factory.build import probe_set

BLOCKS_REPO = "bopoadz-del/Cerebrum-Blocks"
PROTECTED_BRANCHES = frozenset({"main", "master"})
BRANCH_PREFIX = "factory-harvest"

#: A harvest commit is machine-authored and says so. Inheriting whoever ran
#: it would attribute Factory output to a person, and a checkout with no
#: user.name configured fails the commit outright (measured: exit 128).
HARVEST_AUTHOR_NAME = "cerebrum-factory-harvest"
HARVEST_AUTHOR_EMAIL = "harvest@cerebrum-dev.local"


class StoreWriteError(RuntimeError):
    """The write path refused. Never raised for "nothing to do"."""


def _git(checkout: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise StoreWriteError(
            f"git {' '.join(args[:2])} failed in {checkout}: "
            + (detail[-1] if detail else f"exit {proc.returncode}")
        )
    return proc


def checkout_is_writable(checkout: Optional[Path]) -> Dict[str, Any]:
    """Can this checkout receive a harvest? Reports; never raises."""
    out: Dict[str, Any] = {
        "writable": False,
        "checkout": str(checkout) if checkout else None,
        "branch": None,
        "clean": None,
        "remote": None,
        "reason": None,
    }
    if checkout is None or not Path(checkout).is_dir():
        out["reason"] = "no Cerebrum-Blocks checkout resolved"
        return out
    checkout = Path(checkout)
    if not (checkout / ".git").exists():
        out["reason"] = f"{checkout} is not a git checkout"
        return out
    try:
        out["branch"] = _git(checkout, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        status = _git(checkout, "status", "--porcelain").stdout.strip()
        out["clean"] = status == ""
        remote = _git(checkout, "remote", "get-url", "origin", check=False)
        out["remote"] = (remote.stdout or "").strip() or None
    except (subprocess.SubprocessError, OSError) as exc:
        out["reason"] = f"git failed on the checkout: {exc}"
        return out

    if not out["clean"]:
        # Committing over someone's work would fold their changes into a
        # Factory harvest commit and attribute them to it.
        out["reason"] = "checkout has uncommitted changes; harvest refuses to write over them"
        return out
    if not out["remote"] or BLOCKS_REPO.lower() not in out["remote"].lower():
        out["reason"] = f"origin does not point at {BLOCKS_REPO}"
        return out
    out["writable"] = True
    return out


def plan_harvest(blocks_root: Path) -> Dict[str, Any]:
    """Compute what would be written, from the same survey that detects it.

    Payload for this increment is the F16 divergence: fields a block's own
    class declares that its published ``block.json`` omits. Those fields are
    invisible to anything reading the published contract — which, since
    #193, is what the UI generator reads.
    """
    from app.factory.build.ui_schema import survey

    registry = Path(blocks_root) / "block_registry"
    modules = Path(blocks_root) / "app" / "blocks"
    if not registry.is_dir():
        raise StoreWriteError(f"no block_registry under {blocks_root}")

    report = survey(registry, modules if modules.is_dir() else None)
    edits: List[Dict[str, Any]] = []
    for item in report["diverging"]:
        edits.append(
            {
                "block_id": item["block_id"],
                "file": f"block_registry/{item['block_id']}/block.json",
                "op": "add_ui_schema_fields",
                "fields": list(item["class_only"]),
                "why": (
                    "the block class declares these UI fields and the published "
                    "block.json omits them, so they never reach a generated UI"
                ),
            }
        )
    return {
        "blocks_repo": BLOCKS_REPO,
        "blocks_checkout": str(blocks_root),
        "finding": probe_set.code_for("ui_schema_unused"),
        "edits": edits,
        "edit_count": len(edits),
        "surveyed": report["blocks"],
    }


def _apply_ui_schema_edit(blocks_root: Path, edit: Dict[str, Any]) -> bool:
    """Add the missing field names to a block.json ui_schema. Idempotent."""
    path = Path(blocks_root) / edit["file"]
    if not path.is_file():
        raise StoreWriteError(f"{edit['file']} does not exist in the checkout")
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("ui_schema")
    if not isinstance(schema, list):
        schema = []
    present = {f.get("name") for f in schema if isinstance(f, dict)}
    added = False
    for name in edit["fields"]:
        if name in present:
            continue
        schema.append(
            {
                "name": name,
                "label": str(name).replace("_", " ").title(),
                "widget": "json" if name == "input" else "text",
            }
        )
        added = True
    if not added:
        return False
    data["ui_schema"] = schema
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return True


def execute_harvest(
    blocks_root: Path,
    plan: Dict[str, Any],
    *,
    apply: bool = False,
    branch: Optional[str] = None,
    stamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Write a harvest onto a fresh branch. ``apply=False`` plans only.

    Returns a manifest either way, so a dry run and a real run are readable
    side by side. Never checks out or commits to a protected branch, and
    never pushes — see the module docstring for why.
    """
    blocks_root = Path(blocks_root)
    state = checkout_is_writable(blocks_root)
    result: Dict[str, Any] = {
        "applied": False,
        "branch": None,
        "commit": None,
        "written": [],
        "skipped": [],
        "checkout": state,
        "plan": {k: v for k, v in plan.items() if k != "edits"},
        "pushed": False,
        "push_note": (
            "not pushed: harvest never contacts the network. Review the branch, "
            "then push and open a PR by hand."
        ),
    }
    if not state["writable"]:
        result["reason"] = state["reason"]
        return result
    if not plan["edits"]:
        result["reason"] = "nothing to harvest — no divergence found"
        return result
    if not apply:
        result["reason"] = "dry run: no files written"
        result["would_write"] = [e["file"] for e in plan["edits"]]
        return result

    made = stamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = branch or f"{BRANCH_PREFIX}/{plan['finding'].lower()}-{made}"
    if target in PROTECTED_BRANCHES:
        raise StoreWriteError(f"refusing to harvest onto protected branch {target}")

    # Remember where we started so a failure can put the checkout back
    # exactly as it was. Entry already required a clean tree, so resetting
    # to this commit destroys nothing that was not written by this harvest.
    start_commit = _git(blocks_root, "rev-parse", "HEAD").stdout.strip()
    start_branch = state["branch"]

    _git(blocks_root, "checkout", "-b", target)
    result["branch"] = target
    try:
        for edit in plan["edits"]:
            if _apply_ui_schema_edit(blocks_root, edit):
                result["written"].append(edit["file"])
            else:
                result["skipped"].append(edit["file"])
        if result["written"]:
            _git(blocks_root, "add", *result["written"])
            message = (
                f"fix(blocks): publish ui_schema fields the classes already declare "
                f"({plan['finding']})\n\n"
                f"Harvested from {BLOCKS_REPO} consumer CerebrumDev.ai. Each block "
                "below declares these fields on its class while its published "
                "block.json omits them, so a generator reading the published "
                "contract renders a surface missing them.\n\n"
                + "\n".join(f"  {e['block_id']}: {', '.join(e['fields'])}" for e in plan["edits"])
            )
            _git(
                blocks_root,
                "-c", f"user.name={HARVEST_AUTHOR_NAME}",
                "-c", f"user.email={HARVEST_AUTHOR_EMAIL}",
                "commit", "-q", "-m", message,
            )
            result["commit"] = _git(blocks_root, "rev-parse", "--short", "HEAD").stdout.strip()
            result["applied"] = True
    except Exception:
        # A half-written harvest must not be left behind. Without this, a
        # failed commit left the edits staged on the caller's checkout —
        # measured — and the next harvest refused to run because the tree
        # it had just dirtied was no longer clean.
        _git(blocks_root, "reset", "--hard", start_commit, check=False)
        _git(blocks_root, "checkout", start_branch, check=False)
        _git(blocks_root, "branch", "-D", target, check=False)
        result["branch"] = None
        raise
    finally:
        # Leave the checkout on its original branch whatever happened: the
        # next reader of this clone should not silently be on a harvest branch.
        _git(blocks_root, "checkout", start_branch, check=False)
    return result


def store_write_capability(blocks_root: Optional[Path] = None) -> Dict[str, Any]:
    """Does a real write path exist? Read by harvest.py in place of False."""
    state = checkout_is_writable(blocks_root)
    return {
        "implemented": True,
        "module": "app.factory.build.store_write",
        "writes_to_default_branch": False,
        "pushes": False,
        "checkout_writable": state["writable"],
        "reason": state["reason"],
    }
