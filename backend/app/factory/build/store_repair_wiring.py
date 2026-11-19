"""F-3c (b): the Store repairer and installer, constructed for a real build.

Until this module, ``RoleRunner.store_repairer`` and ``store_installer`` were
set by tests only, so the repair-install-regrade path (K-2, K-3, F-2, F-3) was
off on every real build. :func:`arm` builds both for one runner, behind ONE
setting, :data:`SETTING_ENV`, which is off unless it is set to an on-value.

With the setting off, :func:`arm` reads nothing else and touches nothing: the
runner keeps both as None, which is today's path (``_store_repair_settles``
returns at once). With it on but no exam-proof key in this process, it stays
off as well: without the key no exam can be proven, so no repair could ever be
accepted and running the exam would only spend the build's wall.

The parts the repairer needs are this build's own:

* the writer is the headless coding worker, run on the staged copy of the
  block only (never the vendored copy), bounded by the build's remaining wall;
* the invoker calls the staged block's ``run`` in a separate Python process
  with an empty environment, so code a repair wrote never runs inside the
  Factory process that holds the proof key;
* the rescan is the grader's own hardwiring scan (baked literals and brief
  leaks), run on the staged copy with the inputs the writer was given;
* the proofs and the lock are the runner's (``exam_proofs`` and the
  product's ``blocks.lock.json``), read when a repair runs.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

#: The one setting that switches the Store repair on for real builds.
SETTING_ENV = "FACTORY_STORE_REPAIR"
#: The values that switch it on (case-insensitive); anything else is off.
ON_VALUES = frozenset({"1", "true", "yes", "on"})
#: Why the repair stays off when the setting is not on.
OFF = "store repair off: FACTORY_STORE_REPAIR is not on"
#: Why the repair stays off when the setting is on but no key was taken.
NO_KEY = "store repair off: no exam-proof key in this process (FACTORY_EXAM_PROOF_KEY)"
#: Seconds one exam call to a staged block may take before it counts as refused.
INVOKE_TIMEOUT_S = 60.0
#: Marks the line the child prints its JSON result on.
RESULT_MARK = "@@store-repair-result@@"

#: Runs inside the child: load ``block.py`` from the staged directory, call
#: its ``run`` with the call read from stdin, print the result after the mark.
_CHILD = (
    "import asyncio, importlib.util, inspect, json, sys\n"
    "staged = sys.argv[1]\n"
    "sys.path.insert(0, staged)\n"
    "spec = importlib.util.spec_from_file_location('staged_block', staged + '/block.py')\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "out = module.run(**json.loads(sys.stdin.read() or '{}'))\n"
    "if inspect.isawaitable(out):\n"
    "    out = asyncio.run(out)\n"
    "sys.stdout.write('\\n' + sys.argv[2] + json.dumps(out, default=repr) + '\\n')\n"
)


def setting_on(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True only when :data:`SETTING_ENV` is set to one of :data:`ON_VALUES`."""
    env = os.environ if environ is None else environ
    return str(env.get(SETTING_ENV) or "").strip().lower() in ON_VALUES


def _child_env() -> Dict[str, str]:
    """The staged block's whole environment: no Factory secret reaches it."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    for name in ("SYSTEMROOT", "PATH"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def invoke_staged(
    staging: Path, *, timeout_s: float = INVOKE_TIMEOUT_S
) -> Callable[[Mapping[str, Any]], Any]:
    """The exam's ``invoker``: one call of the staged block's ``run`` per exam
    case, each in its own isolated child process. A call that crashes, times
    out or prints no result raises, which the exam counts as a refusal."""
    staged = str(Path(staging).resolve())

    def invoke(call: Mapping[str, Any]) -> Any:
        proc = subprocess.run(
            [sys.executable, "-I", "-c", _CHILD, staged, RESULT_MARK],
            input=json.dumps(dict(call), default=repr),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            cwd=staged,
            env=_child_env(),
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"staged block exited {proc.returncode}: {proc.stderr.strip()[-300:]}"
            )
        for line in reversed(proc.stdout.splitlines()):
            if line.startswith(RESULT_MARK):
                return json.loads(line[len(RESULT_MARK) :])
        raise RuntimeError("staged block printed no result")

    return invoke


def hardwiring_rescan(
    blueprint: Any, *, supplied: str, brief: str, prompt: str
) -> Callable[[Path], List[Dict[str, Any]]]:
    """The exam's ``rescan``: the grader's baked-literal and brief-leak scan
    (the check that found the Store defect), on the staged tree, with the
    inputs the writer was given. Each finding's ``file`` is relative to the
    staged directory."""
    from app.factory.build.grader import _as_mapping, _factory_part, derive_forbidden
    from app.factory.build.product_grader import scan_generated
    from app.factory.build.product_grader.grade_record import hardwiring_section

    factory_text = _factory_part(prompt, brief, supplied)
    forbidden = derive_forbidden(_as_mapping(blueprint), supplied, factory_text)

    def rescan(staging: Path) -> List[Dict[str, Any]]:
        hits = scan_generated.forbidden_hits(staging, forbidden)
        leaks = scan_generated.prompt_leak(staging, supplied, factory_text, prompt)
        return list(hardwiring_section({}, hits, leaks)["findings"])

    return rescan


def repair_prompt(handback: Mapping[str, Any]) -> str:
    """What the coding worker is told: repair the copy in its directory to the
    declared contract. The handback carries the contract, the defects and
    their failing inputs, never a sealed case or an expected outcome."""
    return (
        "You are repairing one Store block. The current directory is a copy of "
        "the block; edit only files in it. Keep every file and every exported "
        "symbol. Fix each defect listed below so the block does what its "
        "declared contract says: accept the inputs the contract accepts, refuse "
        "the ones it refuses, and return the declared outputs. Do not put any "
        "literal from a failing input into the code.\n\n"
        + json.dumps(dict(handback), indent=2, sort_keys=True, default=str)
    )


def _worker_own(rel: str) -> bool:
    """Is ``rel`` (posix, relative to the job's directory) a file or folder
    the coding worker itself writes there at dispatch: its brief, its argv
    audit, its progress logs, its CLI home? None of them is the block's."""
    from app.factory.build.codewhale_worker import DISPATCH_AUDIT_FILES, WORKER_CLI_HOME_DIR

    folder, _, name = rel.rpartition("/")
    if rel == WORKER_CLI_HOME_DIR or rel in DISPATCH_AUDIT_FILES:
        return True
    return folder == "docs" and name.startswith("writer_progress")


def _entries(root: Path) -> frozenset:
    return frozenset(p.relative_to(root).as_posix() for p in root.rglob("*"))


def strip_worker_files(staging: Path, before: Iterable[str]) -> List[str]:
    """Remove from ``staging`` what the worker wrote there for itself (see
    :func:`_worker_own`) and was not in the block before the job, so the
    repair brief never reaches the exam, the installed block or the
    proposal. A folder the worker made for them only goes too. Returns
    what was removed."""
    had = frozenset(before)
    removed: List[str] = []
    for rel in sorted(r for r in _entries(staging) - had if _worker_own(r)):
        path = staging / rel
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(rel)
    for rel in sorted({r.rpartition("/")[0] for r in removed} - had - {""}):
        folder = staging / rel
        if not any(folder.iterdir()):
            folder.rmdir()
            removed.append(rel)
    return removed


def worker_writer(runner: Any) -> Callable[[str, Path, Dict[str, Any]], Any]:
    """The exam's ``writer``: one headless coding-worker job on the staged
    copy, under this build's tenant and session, bounded by the build's wall
    still left. No wall left: the repair is not attempted (it fails, named).

    The job's directory is the staged copy, and its write confinement is that
    directory alone (``codewhale_worker.run_worker_job`` spawns the CLI with
    ``agent_confine.confinement_popen_kwargs(cwd, cli)``, whose read-write
    root is ``narrow_rw_roots(cwd)``). The worker's own dispatch files are
    removed from the staged copy when the job ends, however it ends."""

    def write(block_id: str, staging: Path, handback: Dict[str, Any]) -> Any:
        from app.factory.build.codewhale_worker import run_worker_job, worker_timeout_s

        left = runner._grantable_wall_left()
        if left is not None and left <= 0:
            raise RuntimeError(f"no build wall left to repair {block_id}")
        timeout = worker_timeout_s() if left is None else min(worker_timeout_s(), left)
        before = _entries(staging)
        try:
            return run_worker_job(
                repair_prompt(handback),
                staging,
                tenant_store=runner.state.get("tenant_store"),
                timeout_s=timeout,
                session_id=str(runner.state.get("session_id") or "") or None,
                product_id=runner.workspace.name,
            )
        finally:
            strip_worker_files(staging, before)

    return write


def _rescan_for(runner: Any) -> Callable[[Path], Iterable[Mapping[str, Any]]]:
    from app.factory.build.writer_prompt import dispatched_writer_inputs

    told = dispatched_writer_inputs(runner.state)
    return hardwiring_rescan(
        runner.blueprint, supplied=told["supplied"], brief=told["brief"], prompt=told["prompt"]
    )


def build_parts(runner: Any) -> Tuple[Callable[..., Any], Callable[..., Any]]:
    """``(repairer, installer)`` bound to ``runner``. Both read the runner's
    proofs, lock and dispatch record when a repair runs, not now."""
    from app.factory.build.store_proposal import install_repair
    from app.factory.build.store_repair_exam import make_repairer

    def repairer(block_id: str, defects: Tuple[Any, ...]) -> Any:
        repair = make_repairer(
            runner.workspace,
            writer=worker_writer(runner),
            invoker=invoke_staged,
            rescan=_rescan_for(runner),
            proofs=runner.exam_proofs(),
        )
        return repair(block_id, defects)

    def installer(result: Any, defects: Tuple[Any, ...]) -> Optional[str]:
        lock = runner._product_lock()
        if lock is None:
            return "blocks.lock.json is not a JSON object"
        return install_repair(
            runner.workspace, lock, result, proofs=runner.exam_proofs(), defects=defects
        )

    return repairer, installer


def arm(runner: Any, environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Switch the Store repair on for ``runner`` when :data:`SETTING_ENV` is on
    and this process holds the exam-proof key. None when armed; otherwise
    why it stays off, with ``runner`` untouched."""
    if not setting_on(environ):
        return OFF
    from app.factory.build.store_override import take_proof_key

    if take_proof_key() is None:
        logger.warning("%s", NO_KEY)
        return NO_KEY
    runner.store_repairer, runner.store_installer = build_parts(runner)
    logger.info("store repair armed for %s", runner.workspace.name)
    return None
