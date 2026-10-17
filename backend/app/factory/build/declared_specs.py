"""The product's DECLARED schema, read off its own ``app/models.py``.

One source for "a payload built from the capability's own schema". The
WRITER gate's probe builds its payload from the live ``MODELS`` (FIELDS +
CONSTRAINTS); TESTER's emitted suites (test_smoke, test_routes, domain
acceptance, negative floor, placeholder contract) built theirs from
``state["model_specs"]`` -- a spec captured once and refreshed from the
product only when it was empty. A writer that declared a field in a rework
round therefore changed nothing TESTER sampled, and the same test failed
again (live bf1e0e5c, sess_1fbea5094c2a4a57: WRITER gate green on the live
models, TESTER refused "a payload built from its own schema: tank_id is
required", rework, SAME_FAILURE_TWICE).

TESTER now reads the shipped models on every pass and merges them over the
state spec: the product's FIELDS decide WHICH fields exist (the declared
schema is the only source of fields), the state spec keeps the contracts it
mined onto them (vocabulary, bounds, envelope). Nothing here invents a field
or reads a name.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

#: ``_declared_specs(MODELS)``: the spec per capability, read off the live
#: models. Runs inside the PRODUCT -- in :data:`SPECS_DUMP` here, and rendered
#: into every Factory probe (payload_helpers.render_probe_payload), so the
#: probes and TESTER read the declared schema with the same code.
SPECS_FROM_MODELS = """
def _declared_specs(models):
    import datetime as _dt, typing
    kinds = {int: "int", float: "float", bool: "bool",
             _dt.datetime: "datetime", _dt.date: "date", _dt.time: "time"}
    def _kind(hint):
        # Optional[X] declares X: the sample follows the declared type.
        args = [a for a in (getattr(hint, "__args__", None) or ()) if a is not type(None)]
        if getattr(hint, "__origin__", None) is typing.Union and len(args) == 1:
            hint = args[0]
        return kinds.get(hint, "str")
    out = {}
    for cap, cls in models.items():
        try:
            hints = typing.get_type_hints(cls)
        except Exception:
            hints = {}
        c = dict(getattr(cls, "CONSTRAINTS", {}) or {})
        out[cap] = {"entity": getattr(cls, "ENTITY", cap),
                    "fields": [{"name": n, "type": _kind(hints.get(n, str)), **c.get(n, {})}
                               for n in getattr(cls, "FIELDS", [])]}
    return out
"""

#: Run inside the PRODUCT (its package is also called ``app``), so in a
#: subprocess with cwd at the product root.
SPECS_DUMP = (
    "import json, sys\n"
    'sys.path.insert(0, ".")\n'
    "from app.models import MODELS\n"
    + SPECS_FROM_MODELS
    + "print(json.dumps(_declared_specs(MODELS)))\n"
)


def specs_from_product_models(workspace: Path) -> Dict[str, Any]:
    """``model_specs`` read back off the product's ``app/models.py``.

    Empty on any failure (no models yet, import error): callers then keep
    the spec they had, exactly as before.
    """
    import subprocess
    import sys

    root = Path(workspace)
    if not (root / "app" / "models.py").is_file():
        return {}
    try:
        proc = subprocess.run(
            [sys.executable, "-c", SPECS_DUMP],
            cwd=str(root), capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            return {}
        lines = (proc.stdout or "").strip().splitlines()
        out = json.loads(lines[-1]) if lines else {}
        return out if isinstance(out, dict) else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def product_root(candidates: Iterable[Optional[Path]]) -> Optional[Path]:
    """The first candidate root that holds ``app/models.py``."""
    for root in candidates:
        if root is not None and (Path(root) / "app" / "models.py").is_file():
            return Path(root)
    return None


def merge_declared_specs(
    state_specs: Dict[str, Any], product_specs: Dict[str, Any]
) -> Dict[str, Any]:
    """The product's declared fields, carrying the contracts already mined.

    Per capability the product decides which fields exist; a field the state
    spec also knows keeps its mined contract, overlaid by what the product
    itself declares (its type and CONSTRAINTS win). Capabilities the product
    does not model keep their state spec. No product models: state unchanged.
    """
    if not product_specs:
        return dict(state_specs)
    merged: Dict[str, Any] = dict(state_specs)
    for cap, product in product_specs.items():
        if not isinstance(product, dict):
            continue
        state = state_specs.get(cap)
        state = dict(state) if isinstance(state, dict) else {}
        known = {
            str(f.get("name")): f
            for f in state.get("fields") or []
            if isinstance(f, dict) and f.get("name")
        }
        fields = []
        for field in product.get("fields") or []:
            if not isinstance(field, dict) or not field.get("name"):
                continue
            fields.append({**known.get(str(field["name"]), {}), **field})
        out = dict(state)
        out["fields"] = fields
        if product.get("entity") and not out.get("entity"):
            out["entity"] = product["entity"]
        merged[cap] = out
    return merged
