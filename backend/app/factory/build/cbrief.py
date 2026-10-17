"""Deterministic C-BRIEF composition — the compiler's front door.

``compose_cbrief`` defaults the plan and delegates to ``brief_compiler``.
It lives HERE, not in ``cli_pivot``, so retiring the Cursor seam cannot
take the C-BRIEF compiler with it: CLONER freezes the brief that the
CodeWhale (DeepSeek) WRITER reads back, and losing it means the agent
authors the platform blind.

Why its own module rather than ``brief_compiler``: this function needs
``plan_blueprint`` from ``app.factory.product_architect``, which drags in
``app.core.llm_config``, the generator, the planner and yaml. Folding it
into ``brief_compiler`` would push all of that onto every module that
imports the compiler. The dependency direction is cli_pivot -> cbrief,
never the reverse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from app.factory.build.brief_compiler import CompiledBrief, compile_brief
from app.factory.product_architect import plan_blueprint


def compose_cbrief(
    blueprint: Any,
    *,
    plan: Any = None,
    blocks_root: Optional[Path] = None,
    store_ids: Optional[Sequence[str]] = None,
    budget_s: Optional[float] = None,
) -> CompiledBrief:
    """Deterministic C-BRIEF. LLM never writes this text."""
    resolved_plan = plan if plan is not None else plan_blueprint(
        blueprint, blocks_root=blocks_root
    )
    return compile_brief(
        blueprint,
        resolved_plan,
        blocks_root=blocks_root,
        store_ids=store_ids,
        budget_s=budget_s,
    )
