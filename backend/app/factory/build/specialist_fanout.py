"""F-S2c: the Factory runs the writer's specialists as their own sessions.

One writer session used to wear five hats (INFRA, BACKEND, FRONTEND, DEVOPS,
SECURITY) and delegate them to its own sub-agents. With the fan-out on, the
Factory dispatches them itself:

* the writer session keeps BACKEND, in the checkout, under the tenant's
  writer slot -- which the Factory now HOLDS for the whole pass;
* INFRA and FRONTEND each run as a fresh session in a worktree of their own,
  copied from the skeleton the pass started from, each under a CHILD slot of
  that held writer slot (ruling (a): counted against the process cap only,
  bounded by ``writer_specialist_cap()``; no new number);
* their work is merged back into the checkout. A path two sessions changed
  differently is a CONFLICT: recorded by path and by the two writers, and
  never overwritten -- the checkout keeps what it has. A Factory-owned path is
  never merged: the pass's factory_owned snapshot/restore owns those;
* DEVOPS then SECURITY run serially on the merged checkout, each under a
  child slot, because both need a tree that has stopped moving.

Each specialist session sees the brief, an index of the skeleton and its own
scope -- the scope text is the SPECIALISTS block of the writer prompt, read
from the one template (writer_prompt.specialist_scopes).

Nothing here names a product, a capability or a file of one: the mechanism is
the same for every brief.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Collection, Dict, List, Mapping, Optional, Sequence

from app.factory.build.coder_session_status import (
    OwnerStopped,
    current_owner_control,
    owner_control_scope,
)

#: Operator switch. Off by default: the single-writer pass is unchanged until
#: an operator turns the fan-out on (``1``/``true``/``yes``/``on``).
FANOUT_ENV = "FACTORY_WRITER_SPECIALIST_FANOUT"
#: Hats that run beside the writer session, each in its own worktree.
PARALLEL_SPECIALISTS = ("INFRA", "FRONTEND")
#: Hats that run after the merge, on the merged checkout, in this order.
SERIAL_SPECIALISTS = ("DEVOPS", "SECURITY")
#: Most skeleton paths listed in a specialist's prompt; the rest are counted.
SKELETON_INDEX_LIMIT = 400
#: Seconds between two "specialists still working" heartbeats while the
#: writer session has finished and the parallel sessions have not.
JOIN_HEARTBEAT_S = 60.0

CONFLICT = "conflict"

_TRUE = {"1", "true", "yes", "on"}


def fanout_enabled() -> bool:
    """Whether the Factory dispatches the specialists itself."""
    return (os.getenv(FANOUT_ENV) or "").strip().lower() in _TRUE


def dispatched_specialists() -> tuple:
    """The hats the Factory runs as their own sessions, in run order."""
    return (*PARALLEL_SPECIALISTS, *SERIAL_SPECIALISTS)


def tree_manifest(root: Path | str) -> Dict[str, str]:
    """``{relative path: sha256}`` for every file the authored-file count
    counts (codewhale_worker.agent_file_paths: no .git, vendor, caches, CLI
    home, progress logs or dispatch audit files)."""
    from app.factory.build.codewhale_worker import agent_file_paths

    base = Path(root)
    out: Dict[str, str] = {}
    for rel in agent_file_paths(base):
        try:
            out[rel] = hashlib.sha256((base / rel).read_bytes()).hexdigest()
        except OSError:
            continue
    return out


def skeleton_index(manifest: Mapping[str, str], limit: int = SKELETON_INDEX_LIMIT) -> str:
    """The skeleton's paths, one per line, capped at ``limit``."""
    paths = sorted(manifest)
    lines = [f"- {rel}" for rel in paths[:limit]]
    if len(paths) > limit:
        lines.append(f"- ... and {len(paths) - limit} more")
    return "\n".join(lines)


def render_specialist_prompt(
    name: str,
    *,
    brief: str,
    index: str,
    after_merge: bool,
) -> str:
    """One specialist session's prompt: the brief, the skeleton index and its
    own scope (read from the writer prompt's SPECIALISTS block)."""
    from app.factory.build.writer_prompt import _factory_owned_lines, specialist_scopes

    scope = specialist_scopes()[name]
    where = (
        "You run AFTER the other specialists, on the merged checkout: the tree "
        "has stopped moving."
        if after_merge
        else "You run BESIDE the backend session, in a worktree of your own "
        "copied from the skeleton below; the Factory merges your files back. "
        "A file another session also changed is a conflict the Factory records "
        "and does not take from you, so stay inside your scope."
    )
    return (
        f"You are the {name} specialist of the CerebrumDev factory's WRITER.\n"
        f"{where}\n\n"
        f"YOUR SCOPE ({name}):\n{scope}\n\n"
        "Do only this scope. Do not start agents for other work.\n\n"
        "FACTORY-OWNED FILES (read-only; never create, edit or delete them):\n"
        f"{_factory_owned_lines()}\n\n"
        "PROGRESS LOG: after every completed step append one line to "
        f"docs/writer_progress.log: `STEP <n> [{name}]: <summary>`.\n\n"
        "AUTHORSHIP MARKER: every action handler you author sets "
        '`AUTHORED_BY = "codewhale exec"` directly under its imports.\n\n'
        f"SKELETON INDEX (the tree the pass started from):\n{index}\n\n"
        f"BRIEF:\n{(brief or '').strip()}\n"
    )


def _rmtree(path: Path) -> None:
    """Remove a worktree, including files the owned-file lock left read-only."""
    for item in Path(path).rglob("*"):
        try:
            os.chmod(item, stat.S_IMODE(item.lstat().st_mode) | stat.S_IWUSR)
        except OSError:
            continue
    shutil.rmtree(path, ignore_errors=True)


def make_worktree(skeleton: Path | str, parent: Path | str, name: str) -> Path:
    """A fresh copy of the skeleton for one specialist session."""
    from app.factory.build.codewhale_worker import WORKER_CLI_HOME_DIR

    target = Path(parent) / name.lower()
    shutil.copytree(
        skeleton,
        target,
        ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__", WORKER_CLI_HOME_DIR),
    )
    return target


def changed_paths(baseline: Mapping[str, str], root: Path | str) -> Dict[str, Optional[str]]:
    """``{path: new digest, or None when deleted}`` for every path under
    ``root`` that differs from ``baseline``."""
    now = tree_manifest(root)
    out: Dict[str, Optional[str]] = {}
    for rel in sorted(set(baseline) | set(now)):
        if baseline.get(rel) != now.get(rel):
            out[rel] = now.get(rel)
    return out


def merge_back(
    dest: Path | str,
    baseline: Mapping[str, str],
    worktrees: Mapping[str, Path | str],
    *,
    owned: Collection[str],
    writer: str,
) -> Dict[str, Any]:
    """Merge each worktree's changes into ``dest``, in ``worktrees`` order.

    ``dest``'s own changes since ``baseline`` (the ``writer`` session's) are
    claimed first. A path a later session changed to a different result is a
    conflict, recorded by path with both writers and NOT applied. A path in
    ``owned`` is never merged; it is recorded under ``owned``.
    """
    base = Path(dest)
    claimed: Dict[str, tuple] = {
        rel: (writer, digest) for rel, digest in changed_paths(baseline, base).items()
    }
    applied: Dict[str, List[str]] = {}
    conflicts: List[Dict[str, Any]] = []
    owned_rows: List[Dict[str, str]] = []
    owned_set = set(owned)
    for name, tree in worktrees.items():
        applied[name] = []
        for rel, digest in changed_paths(baseline, tree).items():
            if rel in owned_set:
                owned_rows.append({"path": rel, "specialist": name})
                continue
            prior = claimed.get(rel)
            if prior is not None:
                if prior[1] != digest:
                    conflicts.append(
                        {"path": rel, "kept": prior[0], "not_applied": name, "change": CONFLICT}
                    )
                continue
            target = base / rel
            if digest is None:
                if target.is_file():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(tree) / rel, target)
            claimed[rel] = (name, digest)
            applied[name].append(rel)
    return {"applied": applied, "conflicts": conflicts, "owned": owned_rows}


#: launch(name, prompt, checkout, progress) -> receipt. The caller binds the
#: slot (a child of its held writer slot), walls and tenant.
Launch = Callable[[str, str, Path, Optional[Callable[[str, Dict[str, Any]], None]]], Any]


class SpecialistFanout:
    """One writer pass's specialist sessions: start beside the writer, then
    finish (join, merge back, run the serial hats)."""

    def __init__(
        self,
        dest: Path | str,
        *,
        brief: str,
        launch: Launch,
        note: Callable[..., None],
        owned: Collection[str],
        workroot: Optional[Path | str] = None,
        parallel: Sequence[str] = PARALLEL_SPECIALISTS,
        serial: Sequence[str] = SERIAL_SPECIALISTS,
        join_heartbeat_s: float = JOIN_HEARTBEAT_S,
    ) -> None:
        self.dest = Path(dest)
        self.brief = brief
        self.launch = launch
        self.note = note
        self.owned = tuple(owned)
        self.parallel = tuple(parallel)
        self.serial = tuple(serial)
        self.join_heartbeat_s = float(join_heartbeat_s)
        self._workroot = Path(workroot) if workroot is not None else None
        self._tmp: Optional[Path] = None
        self.baseline: Dict[str, str] = {}
        self.worktrees: Dict[str, Path] = {}
        self.results: Dict[str, Dict[str, Any]] = {}
        self._threads: Dict[str, threading.Thread] = {}

    def start(self) -> None:
        """Snapshot the skeleton and start each parallel hat in its worktree."""
        self.baseline = tree_manifest(self.dest)
        index = skeleton_index(self.baseline)
        self._tmp = Path(tempfile.mkdtemp(prefix="specialists-", dir=self._workroot))
        for name in self.parallel:
            tree = make_worktree(self.dest, self._tmp, name)
            self.worktrees[name] = tree
            prompt = render_specialist_prompt(
                name, brief=self.brief, index=index, after_merge=False
            )
            thread = threading.Thread(
                target=self._run, args=(name, prompt, tree, None), daemon=True
            )
            self._threads[name] = thread
            thread.start()

    def _run(
        self,
        name: str,
        prompt: str,
        checkout: Path,
        progress: Optional[Callable[[str, Dict[str, Any]], None]],
    ) -> None:
        """One session. Never raises: its outcome is recorded by name."""
        started = time.monotonic()
        try:
            receipt = self.launch(name, prompt, checkout, progress)
        except OwnerStopped:
            # F-U1a: the session's CLI tree is already killed; the build
            # thread reads the same Stop and ends the build.
            self.results[name] = {
                "status": "stopped",
                "wall_s": round(time.monotonic() - started, 1),
            }
            return
        except Exception as exc:  # noqa: BLE001 -- one hat's failure is recorded, not fatal
            self.results[name] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "wall_s": round(time.monotonic() - started, 1),
            }
            return
        rounds = dict(getattr(receipt, "rounds", None) or {})
        self.results[name] = {
            "status": str(getattr(receipt, "status", "") or "-"),
            "wall_s": round(time.monotonic() - started, 1),
            "peak_rss_mb": rounds.get("peak_rss_mb"),
            "rounds": rounds.get("rounds"),
        }

    def _join(self, progress: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
        """Wait for the parallel sessions, saying so while they run."""
        from app.factory.build.codewhale_worker import FILES_ON_DISK

        last = time.monotonic()
        while any(t.is_alive() for t in self._threads.values()):
            for thread in self._threads.values():
                thread.join(timeout=0.5)
            now = time.monotonic()
            if progress is not None and now - last >= self.join_heartbeat_s:
                last = now
                running = [n for n, t in self._threads.items() if t.is_alive()]
                files = sum(len(changed_paths(self.baseline, self.worktrees[n])) for n in running)
                progress(
                    "specialists still working: " + ", ".join(running),
                    {FILES_ON_DISK: files},
                )

    def finish(
        self, progress: Optional[Callable[[str, Dict[str, Any]], None]] = None
    ) -> Dict[str, Any]:
        """Join the parallel hats, merge their work back, run the serial hats
        on the merged checkout. Returns the pass's specialist record."""
        self._join(progress)
        merged = merge_back(
            self.dest,
            self.baseline,
            {name: self.worktrees[name] for name in self.parallel if name in self.worktrees},
            owned=self.owned,
            writer=_writer_name(),
        )
        index = skeleton_index(tree_manifest(self.dest))
        for name in self.serial:
            prompt = render_specialist_prompt(
                name, brief=self.brief, index=index, after_merge=True
            )
            self._run(name, prompt, self.dest, progress)
        record = {
            "sessions": {name: self.results.get(name, {"status": "not_run"}) for name in (*self.parallel, *self.serial)},
            "merged": {name: len(paths) for name, paths in merged["applied"].items()},
            "conflicts": merged["conflicts"],
            "owned": merged["owned"],
        }
        self.note(
            "specialist sessions: "
            + " ".join(
                f"{name}={row.get('status')}"
                f"/{row.get('wall_s', '-')}s"
                f"/rss={row.get('peak_rss_mb') if row.get('peak_rss_mb') is not None else '-'}"
                for name, row in record["sessions"].items()
            )
            + f" merged={sum(record['merged'].values())}"
            + f" conflicts={len(record['conflicts'])}",
            stage="writer-specialists",
            source="factory",
            specialists=record,
        )
        for row in record["conflicts"]:
            self.note(
                f"specialist merge conflict: {row['path']} -- kept {row['kept']}, "
                f"{row['not_applied']}'s version not applied",
                stage="writer-specialists",
                source="factory",
                conflict=row,
            )
        return record

    def cleanup(self) -> None:
        """Remove the worktrees. Waits for a parallel session still running
        (a failed writer session leaves them to end on their own walls)."""
        for thread in self._threads.values():
            thread.join()
        if self._tmp is not None and self._tmp.exists():
            _rmtree(self._tmp)


def _writer_name() -> str:
    from app.factory.build.writer_prompt import WRITER_SPECIALIST

    return WRITER_SPECIALIST


def start_for_writer(
    stack: Any,
    dest: Path | str,
    *,
    brief: str,
    note: Callable[..., None],
    tenant_store: Any,
    session_id: str,
    product_id: str,
    timeout_s: Optional[float],
    live_time_left: Optional[Callable[[], Optional[float]]],
    slot_wait_left: Callable[[], Optional[float]],
    on_slot_wait: Optional[Callable[[Dict[str, Any]], None]],
    turn_wall_s: Optional[float],
) -> tuple:
    """Hold the tenant's writer slot on ``stack`` for the whole pass and start
    the parallel specialists under child slots of it.

    Returns ``(fanout, held_scope)``: ``held_scope`` is what the writer
    session itself runs under (the slot already held -- never a second one).
    """
    from contextlib import nullcontext

    from app.factory.build.codewhale_worker import (
        run_worker_job_with_stall_retry,
        specialist_child_slot,
        worker_job_slot,
    )
    from app.factory.build.factory_owned import factory_owned_paths

    held = stack.enter_context(
        worker_job_slot(
            tenant_store,
            holder={"session_id": session_id or None, "product_id": product_id or Path(dest).name},
            wait_left=slot_wait_left,
            on_wait=on_slot_wait,
        )
    )

    def held_scope() -> Any:
        return nullcontext(held)

    def child_scope() -> Any:
        return specialist_child_slot(tenant_store, wait_left=slot_wait_left)

    # F-U1a: the specialists run on their own threads; they read the same
    # owner control as the writer that started them.
    control = current_owner_control()

    def launch(name: str, prompt: str, checkout: Path, progress: Any) -> Any:
        extra: Dict[str, Any] = {}
        if turn_wall_s is not None:
            extra["turn_wall_s"] = turn_wall_s
        with owner_control_scope(control):
            return run_worker_job_with_stall_retry(
                prompt,
                checkout,
                tenant_store=tenant_store,
                timeout_s=timeout_s,
                session_id=session_id,
                product_id=product_id,
                progress=progress,
                live_time_left=live_time_left,
                slot_wait_left=slot_wait_left,
                slot_scope=child_scope,
                **extra,
            )

    fanout = SpecialistFanout(
        dest, brief=brief, launch=launch, note=note, owned=factory_owned_paths()
    )
    fanout.start()
    return fanout, held_scope
