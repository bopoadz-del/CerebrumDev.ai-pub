"""Package, publish and install validated candidate packs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .validator import validate_pack


class PublishRefusedError(RuntimeError):
    pass


def package(pack: Dict[str, Any], report: Dict[str, Any], out_dir: Path) -> Path:
    """Write pack.json + compiler_report.json into out_dir/<domain_id>/.

    Refuses to write a pack that fails validation — packaging a broken pack
    would make the breakage somebody else's problem.
    """
    domain_id = pack["manifest"]["domain_id"]
    ok, reasons = validate_pack(pack)
    if not ok:
        raise PublishRefusedError(
            "refusing to package an invalid pack: " + "; ".join(reasons)
        )
    dest = Path(out_dir) / domain_id
    dest.mkdir(parents=True, exist_ok=True)
    pack_path = dest / "pack.json"
    pack_path.write_text(
        json.dumps(pack, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    (dest / "compiler_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return pack_path


def publish(pack_path: Path, destination_root: Path) -> Path:
    """Copy a packaged candidate pack into the destination's domain_packs dir.

    ``destination_root`` is the Cerebrum-Blocks checkout (store) or any
    domain-pack root. Publishing writes the candidate pack ONLY — it never
    bumps certification, never re-signs, never touches registry blocks.
    """
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    domain_id = pack["manifest"]["domain_id"]
    dest = Path(destination_root) / "domain_packs" / domain_id
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "pack.json"
    target.write_text(pack_path.read_text(encoding="utf-8"), encoding="utf-8")
    return target


def install(pack_path: Path, product_root: Path) -> Path:
    """Install a candidate pack into a generated product's app tree.

    Writes the pack under app/domain_packs/<domain_id>/ plus an install note
    with the kernel wiring snippet. Installation is a copy — the generated
    product's loader decides at runtime whether a candidate pack may answer.
    """
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    domain_id = pack["manifest"]["domain_id"]
    dest = Path(product_root) / "app" / "domain_packs" / domain_id
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "pack.json"
    target.write_text(pack_path.read_text(encoding="utf-8"), encoding="utf-8")
    (dest / "INSTALL_NOTE.md").write_text(
        "# Domain Pack install\n\n"
        f"`{domain_id}` — certification: `{pack['manifest']['certification']}`\n\n"
        "```python\n"
        "from reasoning_kernel.loader import DomainPackLoader\n"
        "loader = DomainPackLoader()\n"
        f"pack = loader.load(\"{domain_id}\")\n"
        "```\n\n"
        "Candidate packs must NOT answer in production mode until promoted "
        "to domain_approved by the store gate.\n",
        encoding="utf-8",
    )
    return target
