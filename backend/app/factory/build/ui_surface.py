"""WRITER gate: the declared UI surface exists, or is honestly declared absent.

The WRITER mandate says "UI wiring" and its lane includes ``ui/**``, so the
role was permitted to emit a UI and told to. LotDesk shipped zero HTML, JS
or CSS and passed every phase, because no gate looked. The nine vendored
blocks each published a ``ui_schema`` and the factory consumed none of them.

This gate does not judge the UI's quality. It asserts the two things whose
absence made F8 invisible:

* if the blueprint declares UI modules, ``frontend/`` carries a module per
  declared name -- an empty shell satisfying a manifest is the failure mode
  that a file-count check would miss;
* every module is non-trivial, so an emitter cannot satisfy the gate with a
  placeholder file.

A product that legitimately has no UI declares none, and passes. Silence is
the thing being removed, not the choice.
"""

from __future__ import annotations

from app.factory.build.brief_gates import UI_SURFACE_CHECK

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = UI_SURFACE_CHECK

#: A module shorter than this is a placeholder, not a surface.
MIN_MODULE_CHARS = 200


def declared_ui_modules(workspace: Path) -> List[str]:
    """UI module names the emitted product claims, from its own manifest."""
    for rel in (
        Path("docs") / "blueprint" / "product_blueprint.json",
        Path("product-dna") / "architecture.json",
        Path("product-dna") / "generation_manifest.json",
    ):
        path = workspace / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        modules = data.get("ui_modules")
        if isinstance(modules, list):
            return [str(m) for m in modules if str(m).strip()]
    return []


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def gate_ui_surface(ctx: "GateContext") -> "GateResult":
    """Declared UI modules are emitted, and are not placeholders."""
    from app.factory.build.gates import GateResult

    modules = declared_ui_modules(ctx.workspace)
    if not modules:
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="no UI modules declared — nothing claimed, nothing owed",
        )

    ui_root = ctx.workspace / "frontend" / "src" / "modules"
    if not ui_root.is_dir():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="ui_surface_missing",
            detail=f"{len(modules)} UI module(s) declared but frontend/ was not emitted",
            findings=[f"missing frontend/src/modules/{_safe(m)}.tsx" for m in modules],
        )

    findings: List[str] = []
    for module in modules:
        path = ui_root / f"{_safe(module)}.tsx"
        if not path.is_file():
            findings.append(f"declared UI module {module!r} was not emitted")
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(f"{module}: unreadable ({exc})")
            continue
        if len(body.strip()) < MIN_MODULE_CHARS:
            findings.append(
                f"{module}: emitted as a {len(body.strip())}-char placeholder"
            )

    if findings:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="ui_surface_declared_missing",
            detail="the declared UI surface is missing or a placeholder",
            findings=findings,
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=f"{len(modules)} declared UI module(s) emitted",
    )
