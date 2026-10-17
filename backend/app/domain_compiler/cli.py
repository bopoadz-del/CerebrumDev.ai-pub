"""Domain Intelligence Compiler CLI.

Usage (from backend/):
    python -m app.domain_compiler.cli scout   --repo C:/path/to/donor --commit HASH --out out.json
    python -m app.domain_compiler.cli compile --report out.json --domain-id ID --name NAME --out packdir
    python -m app.domain_compiler.cli validate --pack packdir/ID/pack.json
    python -m app.domain_compiler.cli publish  --pack packdir/ID/pack.json --dest C:/path/to/Cerebrum-Blocks
    python -m app.domain_compiler.cli install  --pack packdir/ID/pack.json --product C:/path/to/product
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .generator import CandidatePackGenerator
from .publisher import install, package, publish
from .scout import DonorScout, ScoutReport
from .validator import validate_pack


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def cmd_scout(args: argparse.Namespace) -> None:
    scout = DonorScout(Path(args.repo), commit=args.commit)
    report = scout.scout(paths=args.paths.split(",") if args.paths else None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        f"scouted {report.repo}@{report.commit or '?'}: "
        f"{len(report.discoveries)} discoveries -> {out}"
    )
    for kind, count in report.to_dict()["totals"].items():
        print(f"  {kind}: {count}")


def cmd_compile(args: argparse.Namespace) -> None:
    raw = json.loads(Path(args.report).read_text(encoding="utf-8"))
    report = ScoutReport(
        repo=raw["repo"], commit=raw["commit"]
    )
    from .scout import Discovery

    report.discoveries = [
        Discovery(
            kind=d["kind"],
            symbol=d["symbol"],
            path=d["path"],
            line=d["line"],
            donor_commit=d["donor_commit"],
            test_evidence=d["test_evidence"],
            notes=d.get("notes", ""),
        )
        for d in raw["discoveries"]
    ]
    generator = CandidatePackGenerator(
        domain_id=args.domain_id,
        name=args.name,
        donor_repo=raw["repo"],
        donor_commit=raw["commit"],
        description=args.description or "",
        version=args.version,
    )
    pack = generator.compile(report)
    compiler_report = generator.compiler_report(report)
    pack_path = package(pack, compiler_report, Path(args.out))
    print(f"compiled candidate pack -> {pack_path}")
    print(f"digest: {generator.digest(pack)}")
    print(
        f"duplicates: {len(compiler_report['duplicates'])} | "
        f"contradictions: {len(compiler_report['contradictions'])}"
    )


def cmd_validate(args: argparse.Namespace) -> None:
    pack = json.loads(Path(args.pack).read_text(encoding="utf-8"))
    ok, reasons = validate_pack(pack)
    if ok:
        print(f"PASS: {args.pack} validates against the kernel schemas")
    else:
        _fail("; ".join(reasons))


def cmd_publish(args: argparse.Namespace) -> None:
    target = publish(Path(args.pack), Path(args.dest))
    print(f"published candidate pack -> {target}")


def cmd_install(args: argparse.Namespace) -> None:
    target = install(Path(args.pack), Path(args.product))
    print(f"installed candidate pack -> {target}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="domain-compiler")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scout", help="scout a donor repository")
    p.add_argument("--repo", required=True)
    p.add_argument("--commit", default="")
    p.add_argument("--paths", default=None, help="comma-separated dirs to scan")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_scout)

    p = sub.add_parser("compile", help="compile a scout report into a candidate pack")
    p.add_argument("--report", required=True)
    p.add_argument("--domain-id", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--version", default="0.1.0")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_compile)

    p = sub.add_parser("validate", help="validate a pack against the kernel schemas")
    p.add_argument("--pack", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("publish", help="publish a pack into a domain-pack root")
    p.add_argument("--pack", required=True)
    p.add_argument("--dest", required=True)
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("install", help="install a pack into a generated product")
    p.add_argument("--pack", required=True)
    p.add_argument("--product", required=True)
    p.set_defaults(func=cmd_install)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
