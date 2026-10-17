"""Forked-child address-space budget for generated products and the Factory host.

WHY THIS LIVES IN THE KERNEL

Generated products receive this package via ``ProductGenerator._write_app``
(``shutil.copytree`` of ``cerebrum_product_kernel``). An isolation helper that
lived only in The_Fork's document indexer would be re-invented — and the
absolute-ceiling form of the bug would ship again. The kernel is the Factory
path that every product already inherits.

WHY THE CEILING IS A BUDGET, NOT AN ABSOLUTE NUMBER

``RLIMIT_AS`` caps VIRTUAL address space. ``fork()`` hands the child a copy of
the parent's entire mapping. With torch / embeddings resident, parent VmSize
is already multiple GB, so an ABSOLUTE ceiling of 1536 MB was breached the
instant the child started. The first allocation raised MemoryError; extraction
swallowed it as a successful empty document; every file >= 1 MB indexed as
ZERO_CHUNK with nothing logged. That shipped (The_Fork, after the isolation
change).

The child's limit is therefore ``parent VmSize + budget``. If the parent's
size cannot be read, no limit is set: isolation still contains a runaway,
because the child is the largest process and the OOM killer takes it rather
than the web worker. Guessing an absolute number is what caused the outage.

PIPE PROTOCOL (exactly one message)

The parent reads ONE payload from the pipe and treats it as the result. A
follow-up that sent ``("nolimit", str(exc))`` then ``("ok", result)`` made the
parent treat rlimit refusal as failure. Refusing to set the limit must log
and CONTINUE — it must not send a status message.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Extra address space the child may consume ABOVE the parent's current VmSize.
# This is a budget, not a ceiling: see child_address_space_limit.
_DEFAULT_BUDGET_MB = 1536

_TIMEOUT_S = float(os.getenv("CEREBRUM_CHILD_TIMEOUT_S", "600"))

_ENABLED = os.getenv("CEREBRUM_CHILD_ISOLATE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}

# Set in the child so a nested isolated call does not fork again.
_IN_CHILD_ENV = "CEREBRUM_CHILD_ISOLATE_NESTED"


def budget_bytes_from_env(*names: str, default_mb: int = _DEFAULT_BUDGET_MB) -> int:
    """Read the first valid ``*_MEM_MB`` env var as a byte budget."""
    for name in names:
        raw = os.getenv(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            return max(1, int(str(raw).strip())) * 1024 * 1024
        except ValueError:
            continue
    return default_mb * 1024 * 1024


def default_budget_bytes() -> int:
    return budget_bytes_from_env("CEREBRUM_CHILD_MEM_MB")


def isolation_available() -> bool:
    """True when a forking child with RLIMIT_AS can actually be used."""
    if not _ENABLED:
        return False
    if os.getenv(_IN_CHILD_ENV):
        return False
    if os.name != "posix":
        return False
    try:
        import multiprocessing
        import resource  # noqa: F401
    except Exception:
        return False
    return "fork" in multiprocessing.get_all_start_methods()


def _parent_virtual_bytes() -> int | None:
    """The parent's CURRENT virtual size, or None if it cannot be read."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmSize:"):
                    return int(line.split()[1]) * 1024  # kB -> bytes
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not read /proc/self/status VmSize: %s", exc)
    return None


def child_address_space_limit(budget_bytes: int) -> int | None:
    """Absolute RLIMIT_AS for the child: parent's virtual size + budget.

    Returns None when the parent's size cannot be read. No limit is then set.
    """
    parent = _parent_virtual_bytes()
    if parent is None:
        return None
    return parent + budget_bytes


def apply_child_address_space_budget(
    budget_bytes: int,
    *,
    log: bool = True,
) -> int | None:
    """Set RLIMIT_AS to parent VmSize + budget. Never raises.

    On ValueError/OSError (including a hard-rlimit clamp that still refuses),
    log a warning and return None so the caller CONTINUES without sending a
    pipe/status message. The child remains the largest process, so the OOM
    killer takes it rather than the web worker.
    """
    try:
        import resource
    except ImportError:
        return None
    limit = child_address_space_limit(budget_bytes)
    if limit is None:
        return None
    try:
        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
        return limit
    except (ValueError, OSError) as exc:
        if log:
            logger.warning(
                "could not set RLIMIT_AS in isolated child (%s); continuing without it",
                exc,
            )
        return None


def subprocess_preexec(budget_bytes: int) -> Callable[[], None]:
    """``preexec_fn`` for ``subprocess.run``: apply the budget, never abort.

    Logging is disabled here: ``preexec_fn`` runs after fork in a threaded
    parent and must not take the logging lock.
    """

    def _preexec() -> None:
        apply_child_address_space_budget(budget_bytes, log=False)

    return _preexec


def _safe_send(conn, payload) -> bool:
    """Best-effort send from the child. False when the pipe is already gone."""
    try:
        conn.send(payload)
        return True
    except Exception as exc:  # noqa: BLE001 - the child must not raise
        logger.debug("child could not send %r: %s", payload[0], exc)
        return False


def _safe_close(conn) -> None:
    """Close the pipe, tolerating a parent that already hung up."""
    try:
        conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("child could not close its pipe: %s", exc)


def _child_work(conn, fn: Callable[..., Any], args: tuple, mem_bytes: int) -> None:
    """Cap the address space, run ``fn``, send exactly one result.

    Rlimit refusal is not a result. Do not send a status message for it.
    """
    apply_child_address_space_budget(mem_bytes)
    os.environ[_IN_CHILD_ENV] = "1"
    try:
        result = fn(*args)
        _safe_send(conn, ("ok", result))
    except MemoryError:
        _safe_send(conn, ("memory", None))
    except BaseException as exc:  # noqa: BLE001 - the child must not hang
        _safe_send(conn, ("error", f"{type(exc).__name__}: {exc}"))


def _child(conn, fn: Callable[..., Any], args: tuple, mem_bytes: int) -> None:
    """Child entry point. Exits with ``os._exit`` so atexit handlers do not run."""
    try:
        _child_work(conn, fn, args, mem_bytes)
    finally:
        _safe_close(conn)
        os._exit(0)


def run_isolated(
    fn: Callable[..., Any],
    args: tuple,
    *,
    fallback: Any,
    label: str = "isolated-work",
    budget_bytes: Optional[int] = None,
    timeout_s: Optional[float] = None,
) -> tuple[Any, dict[str, Any]]:
    """Run ``fn(*args)`` in a memory-budgeted child.

    Returns ``(result, diag)``. ``diag`` is empty on success. Falls back to
    in-process execution when isolation is unavailable.
    """
    if not isolation_available():
        return fn(*args), {}

    import multiprocessing

    mem_bytes = budget_bytes if budget_bytes is not None else default_budget_bytes()
    wait = _TIMEOUT_S if timeout_s is None else timeout_s

    ctx = multiprocessing.get_context("fork")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child,
        args=(child_conn, fn, args, mem_bytes),
        daemon=True,
    )
    proc.start()
    child_conn.close()

    status, payload = "crash", None
    try:
        if parent_conn.poll(wait):
            status, payload = parent_conn.recv()
        else:
            status = "timeout"
    except EOFError:
        status = "crash"
    finally:
        parent_conn.close()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)

    if status == "ok":
        return payload, {}

    budget_mb = mem_bytes // (1024 * 1024)
    reason = {
        "memory": f"{label} exceeded the {budget_mb} MB child budget",
        "timeout": f"{label} exceeded {wait:.0f}s",
        "crash": f"{label} child died (exitcode={proc.exitcode})",
    }.get(status, f"{label} failed: {payload}")

    logger.warning(
        "isolated %s failed: %s — returning fallback, service unaffected",
        label,
        reason,
    )
    return fallback, {"isolate_failed": status, "isolate_failed_detail": reason}
