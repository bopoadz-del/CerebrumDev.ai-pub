"""Named gate: reject LotDesk-as-shipped at recorded F-codes.

LotDesk (``artifacts/lotdesk_pilot_ready.zip``, sha256 pinned in S0) is a
failed-build fixture, not a patient. This gate inspects that zip — or a
committed copy under ``tests/factory/fixtures/`` so CI can see it — and
FAILS with named F-codes. It never patches the fixture.

Linux-provable defects only. Windows/cp1252 (F26) is out of scope here.
"""

from __future__ import annotations

import hashlib
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from app.factory.build import probe_set

LOTDESK_SHA256 = "8eec994d37f7068155152d1af486f705ff28d9674c587c0e4aa48217993f1554"
# The committed fixture is LotDesk-as-shipped (S0, above) with its private kit
# members (kits/private_*) removed; every other member is byte-identical, and
# every gate reading the fixture returns the same findings on both.
LOTDESK_PUBLIC_SHA256 = "d06f67ccff4cf8f53cc53e2a8f1108f8c05ad0800ae44e0de3e3e703d5350d10"
GATE_NAME = "lotdesk_as_shipped"

# Defects this Linux host can prove from the fixture tree/zip: the probe set
# marks them ``lotdesk_required_rejection``.
REQUIRED_REJECTION_CODES = probe_set.codes_where(lotdesk_required_rejection=True)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def fixture_candidates() -> List[Path]:
    root = _repo_root()
    return [
        root / "backend" / "tests" / "factory" / "fixtures" / "lotdesk_pilot_ready.zip",
        root / "artifacts" / "lotdesk_pilot_ready.zip",
        root / "artifacts" / "platform_5of5",
    ]


def resolve_lotdesk_fixture(explicit: Optional[Path] = None) -> Path:
    if explicit is not None:
        path = Path(explicit)
        if not path.exists():
            raise FileNotFoundError(f"LotDesk fixture not found: {path}")
        return path
    for candidate in fixture_candidates():
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "LotDesk fixture missing. Expected "
        + " or ".join(str(p) for p in fixture_candidates())
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_map_from_zip(zip_path: Path) -> Dict[str, str]:
    files: Dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name.endswith(".py") or name.endswith("Dockerfile") or name.endswith(".txt"):
                try:
                    files[name] = zf.read(info).decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001 — fixture scan must continue
                    continue
    return files


def _read_map_from_tree(root: Path) -> Dict[str, str]:
    files: Dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {".py", ".txt"} and path.name != "Dockerfile":
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            files[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return files


def _strip_prefix(name: str) -> str:
    parts = name.replace("\\", "/").split("/")
    for marker in ("app/", "vendor/", "scripts/", "tests/", "docs/"):
        if marker.rstrip("/") in parts:
            idx = parts.index(marker.rstrip("/"))
            return "/".join(parts[idx:])
    if parts and parts[-1] == "Dockerfile" and "vendor" not in parts:
        return "Dockerfile"
    return name.replace("\\", "/")


def inspect_files(files: Dict[str, str]) -> List[Finding]:
    """Return named findings. Empty means the tree is not LotDesk-as-shipped."""
    findings: List[Finding] = []
    normalised = {_strip_prefix(name): text for name, text in files.items()}

    dispatch = normalised.get("app/dispatch.py", "")
    if "_default_block_field" in dispatch or "_ALWAYS_FILL" in dispatch:
        findings.append(
            Finding(
                probe_set.code_for("fabricated_store_inputs"),
                "app/dispatch.py",
                "_DISPATCH_RUNTIME fabricates Store inputs "
                "(_default_block_field / _ALWAYS_FILL)",
            )
        )

    dockerfile = normalised.get("Dockerfile", "")
    if dockerfile:
        if "release_gate" not in dockerfile:
            findings.append(
                Finding(
                    probe_set.code_for("image_without_release_gate"),
                    "Dockerfile",
                    "product image does not run scripts/release_gate.py; "
                    "a red suite can still be a deployable image",
                )
            )
        if "python:" in dockerfile and "@sha256:" not in dockerfile:
            findings.append(
                Finding(
                    probe_set.code_for("image_not_digest_pinned"),
                    "Dockerfile",
                    "FROM is not digest-pinned",
                )
            )

    for rel, text in normalised.items():
        if rel.endswith("/Dockerfile") and ":latest" in text:
            findings.append(
                Finding(probe_set.code_for("block_image_latest"), rel, "block image uses :latest"),
            )
            break

    estate = normalised.get("vendor/blocks/estate_registry/block.py", "")
    if estate and 'status": "ok"' in estate and "payload" in estate:
        findings.append(
            Finding(
                probe_set.code_for("echo_block"),
                "vendor/blocks/estate_registry/block.py",
                "estate_registry echoes the caller payload as status=ok",
            )
        )

    main = normalised.get("app/main.py", "")
    if 'return {"status": "ok"}' in main and "def health" in main:
        findings.append(
            Finding(probe_set.code_for("health_constant_ok"), "app/main.py", "GET /health is a constant ok"),
        )
        findings.append(
            Finding(
                probe_set.code_for("health_unconditional_ok"),
                "app/main.py",
                "GET /health is unconditional ok / always-200 (LotDesk-class)",
            ),
        )

    if not any(name.startswith("ui/") or name.startswith("frontend/") for name in normalised):
        # Presence of any ui/frontend file in the scan set. Tree/zip reads
        # only .py/.txt/Dockerfile, so a real frontend would still show
        # frontend/*.py or we treat absence of those as F14.
        # A frontend/ directory anywhere in the scanned paths, by path segment.
        if not any("frontend" in Path(n).parts for n in normalised) and not any(
            n.startswith("frontend/") for n in files
        ):
            findings.append(
                Finding(probe_set.code_for("no_ui_surface"), "ui/", "no ui/ or frontend/ shipped"),
            )

    return findings


def inspect_path(path: Path) -> List[Finding]:
    target = Path(path)
    if target.is_dir():
        return inspect_files(_read_map_from_tree(target))
    if zipfile.is_zipfile(target):
        return inspect_files(_read_map_from_zip(target))
    raise ValueError(f"not a product tree or zip: {target}")


def reject_lotdesk_as_shipped(explicit: Optional[Path] = None) -> Dict[str, object]:
    """Inspect the fixture and return a FAIL envelope with named F-codes.

    ``ok`` is always False when the fixture is LotDesk-as-shipped. A hollow
    gate (zero findings, or missing F18/F19) is a factory defect.
    """
    path = resolve_lotdesk_fixture(explicit)
    identity = {
        "path": str(path),
        "kind": "zip" if path.is_file() else "tree",
    }
    if path.is_file():
        digest = sha256_file(path)
        identity["sha256"] = digest
        identity["sha256_matches_s0"] = digest == LOTDESK_SHA256
        identity["sha256_matches_committed"] = digest == LOTDESK_PUBLIC_SHA256
    findings = inspect_path(path)
    codes = [item.code for item in findings]
    missing_required = [code for code in REQUIRED_REJECTION_CODES if code not in codes]
    return {
        "ok": False,
        "gate": GATE_NAME,
        "fixture": identity,
        "findings": [asdict(item) for item in findings],
        "codes": codes,
        "required_codes_present": not missing_required,
        "missing_required_codes": missing_required,
        "lotdesk": "fixture only; not patched",
    }


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    explicit = Path(args[0]) if args else None
    try:
        result = reject_lotdesk_as_shipped(explicit)
    except FileNotFoundError as exc:
        print(f"GATE INCOMPLETE: {exc}")
        return 2
    codes = result["codes"]
    print(f"gate={GATE_NAME} fixture={result['fixture']}")
    for item in result["findings"]:
        print(f"  {item['code']}: {item['path']}: {item['detail']}")
    if result["ok"]:
        print("GATE HOLLOW: LotDesk-as-shipped was accepted")
        return 1
    if not result["required_codes_present"]:
        print(
            "GATE INCOMPLETE: fixture was not rejected for "
            + ", ".join(result["missing_required_codes"])
        )
        return 1
    print(f"REJECTED LotDesk-as-shipped with {codes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
