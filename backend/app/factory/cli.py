"""CLI: python -m app.factory.cli generate|plan|store ..."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.blocks_lock import BlocksLockError
from app.factory.dual_registry import DualRegistryError
from app.factory.generator import ProductGenerator, git_head
from app.factory.planner import CapabilityPlanner
from app.factory.store_manager import (
    ImprovementClass,
    PromotionDecision,
    StoreOp,
    evaluate_publish_gates,
    health_cycle_steps,
    store_manager_manifest,
)


def _load_backend_env() -> None:
    """Load backend/.env into the environment, without overriding it.

    The API server gets its environment from the platform (Render) or the
    operator's shell; this CLI is run directly, and on the first live build
    the coder silently fell back to templates on every artifact because the
    configured key sat in backend/.env, which nothing loaded. Existing
    environment variables win -- this only fills gaps.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)


def _resolve_blocks_root(cli_value: str | None) -> Path | None:
    if cli_value:
        return Path(cli_value).resolve()
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    return Path(env).resolve() if env else None


def main(argv: list[str] | None = None) -> int:
    _load_backend_env()
    parser = argparse.ArgumentParser(prog="cerebrum-factory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan", help="Plan capabilities from a blueprint")
    p_plan.add_argument("--blueprint", required=True)
    p_plan.add_argument("--blocks-root", default=None)

    p_gen = sub.add_parser("generate", help="Generate a product repository")
    p_gen.add_argument("--blueprint", required=True)
    p_gen.add_argument("--out", required=True)
    p_gen.add_argument("--blocks-root", default=None)
    p_gen.add_argument("--no-clean", action="store_true")

    p_build = sub.add_parser(
        "build",
        help="Build a platform through the role runner (agent-manufactured)",
    )
    p_build.add_argument("--blueprint", required=True)
    p_build.add_argument("--out", required=True)
    p_build.add_argument("--blocks-root", default=None)
    p_build.add_argument(
        "--max-rework",
        type=int,
        default=3,
        help="writer/tester rounds before the run fails on budget",
    )
    p_build.add_argument(
        "--wall-clock",
        type=float,
        default=7200.0,
        help="seconds before the run fails on budget (0 disables)",
    )
    p_build.add_argument(
        "--phase-wall-clock",
        type=float,
        default=1500.0,
        help="seconds each role may spend (0 disables; code-only Floor 25 min, Store-green 90 min)",
    )
    p_build.add_argument(
        "--cycle",
        choices=("code", "pilot"),
        default="code",
        help="code-phase 5/5 (default) or Store-green pytest -m pilot cycle",
    )

    p_lock = sub.add_parser(
        "update-lock",
        help="Regenerate blocks.lock.json from a Cerebrum-Blocks checkout",
    )
    p_lock.add_argument(
        "--blocks-root",
        default=None,
        help="Store checkout to hash (default: CEREBRUM_BLOCKS_ROOT)",
    )
    p_lock.add_argument(
        "--output",
        default=None,
        help="Lock path (default: <factory-repo>/blocks.lock.json)",
    )
    p_lock.add_argument(
        "--update-lock",
        action="store_true",
        help="Accepted for the documented refresh path; this command always writes the lock",
    )

    p_gen.add_argument(
        "--update-lock",
        action="store_true",
        help="Regenerate blocks.lock.json from --blocks-root before generating",
    )
    p_build.add_argument(
        "--update-lock",
        action="store_true",
        help="Regenerate blocks.lock.json from --blocks-root before building",
    )

    p_bump = sub.add_parser(
        "bump-store",
        help="Move the Factory to a new Store commit: pin + re-lock + verify signatures, together",
    )
    p_bump.add_argument("sha", help="40-hex Store commit to pin")
    p_bump.add_argument(
        "--blocks-root",
        default=None,
        help="Store checkout AT that commit (default: CEREBRUM_BLOCKS_ROOT)",
    )

    p_store = sub.add_parser("store", help="Block Store Manager tools")
    store_sub = p_store.add_subparsers(dest="store_cmd", required=True)
    p_registry = store_sub.add_parser(
        "registry",
        help="Read-only registrar: what each platform cloned, and what has drifted",
    )
    p_registry.add_argument(
        "--root",
        required=True,
        help="directory containing built platforms (scanned for build ledgers)",
    )
    p_registry.add_argument(
        "--store-head",
        default=None,
        help="Store commit to compare against; without it every clone is 'unknown'",
    )
    p_registry.add_argument(
        "--stale-only",
        action="store_true",
        help="exit non-zero if any clone is stale",
    )

    store_sub.add_parser("manifest", help="Print Store Manager authority manifest")
    store_sub.add_parser("health-scan", help="Print Store Health Cycle steps")
    p_classify = store_sub.add_parser("classify", help="Classify a product vs Store diff")
    p_classify.add_argument("--block-id", required=True)
    p_classify.add_argument(
        "--class",
        dest="improvement_class",
        required=True,
        choices=[c.value for c in ImprovementClass],
    )
    p_classify.add_argument(
        "--decision",
        required=True,
        choices=[d.value for d in PromotionDecision],
    )
    p_decide = store_sub.add_parser("decide", help="Evaluate publish gates for a Store op")
    p_decide.add_argument(
        "--op",
        required=True,
        choices=[o.value for o in StoreOp],
    )
    p_decide.add_argument("--user-approved", action="store_true")
    p_decide.add_argument(
        "--checklist-json",
        default="{}",
        help="JSON object of checklist booleans",
    )

    args = parser.parse_args(argv)

    if args.cmd == "store":
        return _store_cmd(args)
    if args.cmd == "update-lock":
        return _update_lock_cmd(args)
    if args.cmd == "bump-store":
        return _bump_store_cmd(args)

    bp = load_blueprint(args.blueprint)
    blocks_root = _resolve_blocks_root(getattr(args, "blocks_root", None))

    if getattr(args, "update_lock", False):
        rc = _write_blocks_lock(blocks_root)
        if rc != 0:
            return rc

    if args.cmd == "build":
        return _build_cmd(args, bp, blocks_root)

    try:
        if args.cmd == "plan":
            plan = CapabilityPlanner(blocks_root).plan(bp)
            print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
            return 0
        factory_root = Path(__file__).resolve().parents[3]
        blocks_commit = git_head(blocks_root) if blocks_root else "unknown"
        gen = ProductGenerator(
            bp,
            blocks_root=blocks_root,
            factory_commit=git_head(factory_root),
            blocks_commit=blocks_commit,
        )
        result = gen.generate(args.out, clean=not args.no_clean)
        print(
            json.dumps(
                {
                    "ok": True,
                    "inputs_hash": result["inputs_hash"],
                    "output_dir": result["output_dir"],
                    "resident_engineer": result.get("resident_engineer"),
                },
                indent=2,
            )
        )
        return 0
    except (DualRegistryError, BlocksLockError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


def _write_blocks_lock(blocks_root: Path | None, output: Path | None = None) -> int:
    from app.factory.blocks_lock import (
        BlocksLockError,
        default_lock_path,
        generate_lock,
        write_lock,
    )

    if blocks_root is None:
        print(json.dumps({"ok": False, "error": "update-lock requires --blocks-root or CEREBRUM_BLOCKS_ROOT"}))
        return 2
    try:
        lock = generate_lock(blocks_root)
    except BlocksLockError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    dest = Path(output) if output else default_lock_path()
    write_lock(dest, lock)
    print(
        json.dumps(
            {
                "ok": True,
                "lock": str(dest),
                "store_sha": (lock.get("store") or {}).get("sha"),
                "blocks": len(lock.get("blocks") or {}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def bump_store(sha: str, blocks_root: Path, *, repo_root: Path | None = None) -> dict:
    """Pin the Factory to Store ``sha``: one atomic step, never two.

    Refuses unless ``blocks_root`` is checked out at exactly ``sha``; verifies
    every block signature against the Store's own registry; regenerates
    ``blocks.lock.json`` from that tree; and only then writes ``store.pin``.
    The caller commits both files in one Factory PR.
    """
    import subprocess

    from app.factory.blocks_lock import default_lock_path, generate_lock, write_lock
    from app.factory.build.vendored_integrity import verify_signature
    from app.factory.store_pin import StorePinError, pin_path, write_pin

    sha = (sha or "").strip().lower()
    root = Path(blocks_root)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip().lower()
    if head != sha:
        raise StorePinError(f"Store checkout {root} is at {head or '?'}, not {sha}")
    registry = root / "block_registry"
    unverified = []
    for block_dir in sorted(p for p in registry.iterdir() if (p / "block.json").is_file()):
        verdict = verify_signature(block_dir)
        if verdict["verified"] is not True:
            unverified.append(f"{block_dir.name}: {verdict['reason']}")
    if unverified:
        raise StorePinError(
            f"{len(unverified)} block(s) do not verify at {sha}: " + "; ".join(unverified[:5])
        )
    lock = generate_lock(root)
    if (lock.get("store") or {}).get("sha") != sha:
        raise StorePinError(f"lock names {(lock.get('store') or {}).get('sha')}, not {sha}")
    lock_path = (Path(repo_root) / "blocks.lock.json") if repo_root else default_lock_path()
    write_lock(lock_path, lock)
    pin = write_pin(sha, pin_path(repo_root) if repo_root else None)
    return {
        "ok": True,
        "store_sha": sha,
        "pin": str(pin),
        "lock": str(lock_path),
        "blocks_locked": len(lock.get("blocks") or {}),
        "signatures_verified": sum(1 for p in registry.iterdir() if (p / "block.json").is_file()),
    }


def _bump_store_cmd(args: argparse.Namespace) -> int:
    from app.factory.store_pin import StorePinError

    blocks_root = _resolve_blocks_root(getattr(args, "blocks_root", None))
    if blocks_root is None:
        print(json.dumps({"ok": False, "error": "bump-store requires --blocks-root or CEREBRUM_BLOCKS_ROOT"}))
        return 2
    try:
        result = bump_store(args.sha, blocks_root)
    except (StorePinError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _update_lock_cmd(args: argparse.Namespace) -> int:
    blocks_root = _resolve_blocks_root(getattr(args, "blocks_root", None))
    output = Path(args.output).resolve() if getattr(args, "output", None) else None
    return _write_blocks_lock(blocks_root, output)


def _build_cmd(args: argparse.Namespace, blueprint, blocks_root) -> int:
    """Drive a build through the role runner.

    Separate from `generate`, which is the legacy template path and remains
    what production calls. Exit 0 only on RUN_SUCCEEDED -- a run that spent
    its budget or failed a gate exits non-zero, so a CI or shell caller
    cannot mistake a failed build for a delivered one.
    """
    from app.factory.build.runner import BuildBudget, RoleRunner

    runner = RoleRunner(
        blueprint,
        args.out,
        blocks_root=blocks_root,
        budget=BuildBudget(
            max_rework=args.max_rework,
            wall_clock_s=args.wall_clock,
            phase_wall_clock_s=args.phase_wall_clock,
        ),
        cycle=getattr(args, "cycle", "code"),
    )
    outcome = runner.run()
    from app.factory.build.authorship import coding_agent_artifact_ids

    sources = runner.state.get("artifact_sources", {})
    by_agent = coding_agent_artifact_ids(sources)
    print(
        json.dumps(
            {
                **outcome.to_dict(),
                "artifacts": len(sources),
                "agent_written": len(by_agent),
                "agent_artifacts": by_agent,
                "coder_failures": runner.state.get("coder_failures", {}),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if outcome.ok else 1


def _store_cmd(args: argparse.Namespace) -> int:
    if args.store_cmd == "registry":
        from app.factory.build.registrar import registrar_report

        report = registrar_report(args.root, store_head=args.store_head)
        print(json.dumps(report, indent=2, sort_keys=True))
        # Read-only: exit 0 unless the caller explicitly asked to be told
        # about drift. "unknown" is never treated as a failure -- it means the
        # comparison could not be made, not that anything is wrong.
        if args.stale_only and report["status_counts"].get("stale"):
            return 2
        return 0
    if args.store_cmd == "manifest":
        print(json.dumps(store_manager_manifest(), indent=2, sort_keys=True))
        return 0
    if args.store_cmd == "health-scan":
        print(json.dumps({"steps": health_cycle_steps()}, indent=2))
        return 0
    if args.store_cmd == "classify":
        print(
            json.dumps(
                {
                    "block_id": args.block_id,
                    "classification": args.improvement_class,
                    "decision": args.decision,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.store_cmd == "decide":
        checklist = json.loads(args.checklist_json)
        result = evaluate_publish_gates(
            args.op, checklist, user_approved=args.user_approved
        )
        print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))
        return 0 if result.allowed else 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
