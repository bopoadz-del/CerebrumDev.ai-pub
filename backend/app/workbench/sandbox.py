"""Isolated sandbox for Build Mode sessions.

Filesystem confined to the session workspace. No shell outside the workspace.
No Store write credentials. Every command logged. Timeout + resource caps;
runaway sessions die loudly.

Child processes get an address-space BUDGET (parent VmSize + configured MB)
via ``RLIMIT_AS``, not an absolute ceiling — an absolute 1536 MB cap is
already below a torch/embedding parent and kills the child on first
allocation. If parent size cannot be read, no rlimit is set; the child is
still the largest process, so the OOM killer takes it rather than the
Factory worker.

Network is NOT restricted: subprocesses inherit the host's network access.
Real egress control would require a network namespace, an egress proxy, or
firewall rules — none of which this sandbox provides.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.cerebrum_product_kernel.isolation import (
    budget_bytes_from_env,
    subprocess_preexec,
)
from app.workbench.envelope import path_allowed

# Env vars that must NEVER be present inside a sandbox process.
FORBIDDEN_ENV_KEYS = (
    "RENDER_API_KEY",
    "GITHUB_TOKEN",
    "DEPLOY_REPO_URL",
    "CEREBRUM_API_KEY",
    "STORE_WRITE_TOKEN",
    "STORE_PUBLISH_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
)


class SandboxViolation(RuntimeError):
    """Raised when an agent action violates sandbox rules — dies loudly."""


class SandboxTimeout(RuntimeError):
    """Session exceeded timeout or resource cap."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def workbench_root() -> Path:
    root = Path(os.getenv("STORAGE_PATH", "./storage")) / "workbench"
    root.mkdir(parents=True, exist_ok=True)
    return root


def session_timeout_seconds() -> int:
    try:
        return max(5, int(os.getenv("WORKBENCH_SESSION_TIMEOUT_SECONDS", "300")))
    except ValueError:
        return 300


class WorkbenchSandbox:
    """Session-scoped workspace with command logging and confinement checks."""

    def __init__(self, session_id: str, *, root: Optional[Path] = None):
        self.session_id = session_id
        base = root or workbench_root()
        self.workspace = (base / "sessions" / session_id).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.transcript: List[Dict[str, Any]] = []
        self.started_at = time.monotonic()
        self.timeout_seconds = session_timeout_seconds()
        self._command_count = 0
        self.max_commands = int(os.getenv("WORKBENCH_MAX_COMMANDS", "200"))

    def _check_alive(self) -> None:
        elapsed = time.monotonic() - self.started_at
        if elapsed > self.timeout_seconds:
            raise SandboxTimeout(
                f"workbench session {self.session_id} exceeded timeout "
                f"({self.timeout_seconds}s); dying loudly"
            )
        if self._command_count >= self.max_commands:
            raise SandboxTimeout(
                f"workbench session {self.session_id} exceeded command cap "
                f"({self.max_commands}); dying loudly"
            )

    def log(self, event: str, **payload: Any) -> None:
        entry = {"at": _now(), "event": event, **payload}
        self.transcript.append(entry)
        transcript_dir = self.workspace / "transcript"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        path = transcript_dir / "session.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")

    def resolve_in_workspace(self, relative: str, *, write: bool = False) -> Path:
        self._check_alive()
        target = (self.workspace / relative).resolve()
        if not path_allowed(self.workspace, target, write=write):
            self.log(
                "boundary_refused",
                path=str(relative),
                write=write,
                reason="outside_envelope",
            )
            raise SandboxViolation(f"path outside envelope: {relative} (write={write})")
        return target

    def write_text(self, relative: str, content: str) -> Path:
        path = self.resolve_in_workspace(relative, write=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.log("write_text", path=relative, bytes=len(content.encode("utf-8")))
        return path

    def read_text(self, relative: str) -> str:
        path = self.resolve_in_workspace(relative, write=False)
        text = path.read_text(encoding="utf-8")
        self.log("read_text", path=relative, bytes=len(text.encode("utf-8")))
        return text

    def copy_tree(self, src: Path, dest_relative: str, *, readonly_label: bool = False) -> Path:
        """Copy a tree into the workspace (baseline / DNA). Dest must be allowed."""
        dest = self.resolve_in_workspace(dest_relative, write=True if not readonly_label else False)
        # For readonly destinations like product-dna/, allow initial seed write once.
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        self.log("copy_tree", src=str(src), dest=dest_relative)
        return dest

    def seed_readonly(self, src: Path, dest_relative: str) -> Path:
        """Seed a readonly path (product-dna/, baseline/) — initial workspace setup only."""
        self._check_alive()
        dest = (self.workspace / dest_relative).resolve()
        try:
            dest.relative_to(self.workspace)
        except ValueError as exc:
            raise SandboxViolation(f"seed outside workspace: {dest_relative}") from exc
        if dest.exists():
            shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
        self.log("seed_readonly", src=str(src), dest=dest_relative)
        return dest

    def scrubbed_env(self) -> Dict[str, str]:
        """Child process env: no deploy / Store write credentials."""
        env = {k: v for k, v in os.environ.items() if k not in FORBIDDEN_ENV_KEYS}
        # Explicitly clear even if somehow reintroduced
        for key in FORBIDDEN_ENV_KEYS:
            env.pop(key, None)
        env["WORKBENCH_SANDBOX"] = "1"
        env["WORKBENCH_SESSION_ID"] = self.session_id
        env["WORKBENCH_WORKSPACE"] = str(self.workspace)
        # Honest: no Store write path
        env["WORKBENCH_STORE_WRITE"] = "impossible"
        env.pop("STORE_ROOT_WRITE", None)
        return env

    def run_command(
        self,
        argv: Sequence[str],
        *,
        cwd_relative: str = ".",
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run a command confined to the workspace. No shell=True."""
        self._check_alive()
        self._command_count += 1
        if not argv:
            raise SandboxViolation("empty command")
        cwd = self.resolve_in_workspace(cwd_relative, write=False)
        # Refuse absolute paths in argv that escape workspace
        for arg in argv[1:]:
            if arg.startswith("/") or arg.startswith(".."):
                candidate = Path(arg)
                if candidate.is_absolute() or ".." in Path(arg).parts:
                    try:
                        resolved = (cwd / arg).resolve() if not candidate.is_absolute() else candidate.resolve()
                        resolved.relative_to(self.workspace)
                    except (OSError, ValueError):
                        self.log("command_refused", argv=list(argv), reason="path_escape")
                        raise SandboxViolation(f"command path escapes workspace: {arg}")
        t_lim = timeout or min(60, self.timeout_seconds)
        self.log("command_start", argv=list(argv), cwd=cwd_relative)
        run_kwargs: Dict[str, Any] = {
            "cwd": str(cwd),
            "env": self.scrubbed_env(),
            "capture_output": True,
            "text": True,
            "timeout": t_lim,
            "shell": False,
            "check": False,
        }
        # POSIX only: preexec_fn is not available on Windows.
        if os.name == "posix":
            run_kwargs["preexec_fn"] = subprocess_preexec(
                budget_bytes_from_env(
                    "WORKBENCH_CHILD_MEM_MB",
                    "CEREBRUM_CHILD_MEM_MB",
                )
            )
        try:
            proc = subprocess.run(list(argv), **run_kwargs)
        except subprocess.TimeoutExpired as exc:
            self.log("command_timeout", argv=list(argv))
            raise SandboxTimeout(f"command timed out: {argv[0]}") from exc
        result = {
            "argv": list(argv),
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-4000:],
            "stderr_tail": (proc.stderr or "")[-4000:],
        }
        self.log("command_end", **result)
        return result

    def attempt_store_write(self) -> None:
        """Boundary probe: Store write is impossible (no credentials / path)."""
        self.log("store_write_attempt", result="impossible")
        raise SandboxViolation(
            "Store write impossible: no credentials and no write path in sandbox"
        )

    def persist_meta(self) -> Dict[str, Any]:
        meta = {
            "session_id": self.session_id,
            "workspace": str(self.workspace),
            "timeout_seconds": self.timeout_seconds,
            "network_isolation": "none — subprocesses inherit host network access",
            "memory_isolation": (
                "RLIMIT_AS budget = parent VmSize + WORKBENCH_CHILD_MEM_MB "
                "(no limit if parent size unknown; rlimit refusal logs and continues)"
            ),
            "command_count": self._command_count,
            "deploy_credentials": False,
            "store_write": "impossible",
            "trust_tier_ceiling": "staging/community",
        }
        path = self.workspace / "sandbox_meta.json"
        path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return meta
