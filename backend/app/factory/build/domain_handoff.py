"""C-BRIEF staging for the WRITER.

Freeze the compiled C-BRIEF at ``docs/coder_brief.md`` after CLONER so the
WRITER -- which runs on CodeWhale (DeepSeek) via ``codewhale exec`` --
reads the block contracts, REUSE inventory and gap list instead of
authoring blind. This module makes no network calls and publishes to no
external agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

BRIEF_REL = Path("docs") / "coder_brief.md"


def ensure_coder_brief(
    output_dir: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
    blocks_root: Optional[Path] = None,
) -> Path:
    """Ensure ``docs/coder_brief.md`` exists (compile if missing).

    The frozen C-BRIEF is what the CodeWhale (DeepSeek) WRITER reads back as
    its COMPILED C-BRIEF section, so an empty one means the agent authors
    the platform blind.
    """
    root = Path(output_dir)
    dest = root / BRIEF_REL
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    if blueprint is None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            "# coder_brief\n\n"
            "(brief unavailable — no blueprint to compile; open the Floor "
            "session)\n",
            encoding="utf-8",
        )
        return dest
    from app.factory.build.cbrief import compose_cbrief

    compiled = compose_cbrief(blueprint, plan=plan, blocks_root=blocks_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(compiled.text, encoding="utf-8")
    return dest
