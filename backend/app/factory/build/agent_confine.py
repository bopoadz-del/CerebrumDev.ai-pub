"""Scoped environment and OS filesystem rules for one coding-agent child.

The child used to receive ``os.environ.copy()`` and a working directory.
A working directory is not confinement: ``..`` walks to sibling builds, a
symlink is followed, and a file descriptor opened before the spawn stays
usable. This module builds an allowlisted environment and, in the child
before exec, applies a Landlock filesystem ruleset.

Landlock does not revoke descriptors that are already open, so inherited
descriptors above stderr are closed before the ruleset is enforced.
``PR_SET_NO_NEW_PRIVS`` is required for an unprivileged ``restrict_self``.
If the kernel cannot enforce the ruleset, the child exits and the caller
does not continue with an unconfined process.

The ruleset is filesystem-only (network is not handled). It is not a
complete sandbox.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import stat
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

_SYS_CREATE = 444
_SYS_ADD = 445
_SYS_RESTRICT = 446
_CREATE_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_FS_EXECUTE = 1 << 0
_FS_WRITE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_RX = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR
_DEV = _FS_READ_FILE | _FS_WRITE

# ABI 1: bits 0-12. ABI 2 adds REFER. ABI 3 adds TRUNCATE. ABI 5 adds IOCTL_DEV.
_FS_BITS = {1: 13, 2: 14, 3: 15, 4: 15, 5: 16, 6: 16}

_BROAD_RW = frozenset({
    "/",
    "/app",
    "/etc",
    "/home",
    "/opt",
    "/proc",
    "/root",
    "/tmp",
    "/usr",
    "/var",
    "/workspace",
})

_PROCESS_KEYS = frozenset({
    "CODE_CLI_ARGV_LOG",
    "CODEWHALE_FORCE_HTTP1",
    "CURL_CA_BUNDLE",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NODE_EXTRA_CA_CERTS",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TZ",
})

_WRITER_KEYS = frozenset({
    "CODEWHALE_API_KEY",
    "CODWHALE_HOME",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_CODE_MODEL",
    "DEEPSEEK_MAX_CONTEXT_SIZE",
    "DEEPSEEK_THINKING_EFFORT",
    "FACTORY_CLI_PIVOT_SESSION_ID",
    "FACTORY_CODE_CLI",
    "FACTORY_CODE_PROVIDER",
    "FACTORY_SESSION_ID",
    "KIMI_CODE_CLI",
    "KIMI_CODE_NO_AUTO_UPDATE",
    "KIMI_DISABLE_TELEMETRY",
    "KIMI_MODEL_API_KEY",
    "KIMI_MODEL_BASE_URL",
    "KIMI_MODEL_MAX_CONTEXT_SIZE",
    "KIMI_MODEL_NAME",
    "KIMI_MODEL_PROVIDER_TYPE",
    "KIMI_MODEL_THINKING_EFFORT",
})

_DENIED = frozenset({
    "ACCOUNTS_DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "CEREBRUM_CHAT_LLM_API_KEY",
    "CEREBRUM_DEV_API_KEY",
    "CEREBRUM_LLM_API_KEY",
    "CLAUDE_API_KEY",
    "CURSOR_AGENT_API_KEY",
    "CURSOR_API_KEY",
    "DATA_ENCRYPTION_KEY",
    "FACTORY_CURSOR_API_KEY",
    "FACTORY_LLM_FALLBACK_API_KEY",
    "OPENROUTER_API_KEY",
    "RESEND_API_KEY",
    "SENTRY_DSN",
    "SMOKE_GATE_TOKEN",
    "SMTP_PASSWORD",
    "SMTP_USER",
    "STRIPE_API_KEY",
    "STRIPE_PRICE_ID",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
})

_SYSTEM_RX_DIRS = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/ssl")
_SYSTEM_RX_FILES = (
    "/dev/null",
    "/dev/urandom",
    "/dev/zero",
    "/etc/group",
    "/etc/hosts",
    "/etc/ld.so.cache",
    "/etc/nsswitch.conf",
    "/etc/passwd",
    "/etc/resolv.conf",
)
_CONFINEMENT_EXIT = 126
_DEFAULT_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


class ConfinementUnavailable(RuntimeError):
    """The kernel cannot enforce a filesystem ruleset. Do not spawn."""


def _syscall():
    proto = ctypes.CFUNCTYPE(
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_long,
        ctypes.c_long,
        use_errno=True,
    )
    return proto(("syscall", ctypes.CDLL(None, use_errno=True)))


def landlock_abi() -> int:
    """Highest Landlock ABI, or 0 when the syscall is missing or disabled."""
    ctypes.set_errno(0)
    rc = _syscall()(_SYS_CREATE, 0, 0, _CREATE_VERSION, 0)
    if rc < 1:
        return 0
    return int(rc)


def require_landlock() -> int:
    """Raise when this process cannot install a filesystem ruleset."""
    abi = landlock_abi()
    if abi < 1:
        raise ConfinementUnavailable(
            "coding-agent filesystem confinement is unavailable"
        )
    return abi


def _fs_mask(abi: int) -> int:
    bits = _FS_BITS.get(abi, 16 if abi > 6 else 13)
    return (1 << bits) - 1


def _real(path: Path | str) -> str:
    return os.path.realpath(path)


def _is_broad(path: str) -> bool:
    return _real(path) in _BROAD_RW


def narrow_rw_roots(cwd: Path | str, extras: Iterable[Path | str] = ()) -> list[str]:
    """Read-write roots for one build: its cwd, plus a same-parent sibling.

    An ancestor of the cwd (the session directory, ``/``, a home directory)
    is not granted. A path inside the cwd is already covered.
    """
    cwd_r = Path(_real(cwd))
    if _is_broad(str(cwd_r)) or not cwd_r.is_dir():
        raise ConfinementUnavailable(
            "coding-agent filesystem root is missing or too broad"
        )
    chosen = [cwd_r]
    for extra in extras:
        try:
            er = Path(_real(extra))
        except OSError:
            continue
        if not er.is_dir() or _is_broad(str(er)):
            continue
        if er == cwd_r or cwd_r in er.parents or er in cwd_r.parents:
            continue
        if er.parent != cwd_r.parent:
            continue
        if er not in chosen:
            chosen.append(er)
    return [str(p) for p in chosen]


def system_rx_paths(executable: str = "") -> list[str]:
    """Read-execute paths a dynamic binary needs, plus that binary's own prefix.

    ``/usr`` and the loader directories are included. Home, the repo, the
    session directory, and ``/tmp`` are not.
    """
    found: list[str] = []

    def add(path: str) -> None:
        if path and os.path.exists(path):
            real = _real(path)
            if real not in found:
                found.append(real)

    for directory in _SYSTEM_RX_DIRS:
        add(directory)
    for filename in _SYSTEM_RX_FILES:
        add(filename)
    raw = (executable or "").strip()
    if raw:
        resolved = shutil.which(raw) or raw
        parents = (
            os.path.dirname(os.path.abspath(resolved)),
            os.path.dirname(os.path.realpath(resolved)),
        )
        for parent in parents:
            if not parent or _is_broad(parent):
                continue
            add(parent)
            if os.path.basename(parent) != "bin":
                continue
            prefix = os.path.dirname(parent)
            if _is_broad(prefix):
                continue
            # A virtualenv's pyvenv.cfg sits beside bin/. Grant that prefix
            # only, not the directory that contains the virtualenv.
            if os.path.isfile(os.path.join(prefix, "pyvenv.cfg")):
                add(prefix)
            lib = os.path.join(prefix, "lib")
            if os.path.isdir(lib):
                add(lib)
    return found


def scoped_child_env(
    parent: Mapping[str, str],
    extras: Mapping[str, str],
    *,
    home: Optional[Path | str] = None,
    tmp: Optional[Path | str] = None,
    provider_key: Optional[str] = None,
) -> dict[str, str]:
    """Allowlisted child environment. ``parent`` is not copied wholesale."""
    allowed = set(_PROCESS_KEYS) | set(_WRITER_KEYS)
    if provider_key and provider_key not in _DENIED:
        allowed.add(provider_key)
    env: dict[str, str] = {}
    for key in _PROCESS_KEYS:
        value = parent.get(key)
        if value:
            env[key] = value
    env.setdefault("PATH", _DEFAULT_PATH)
    for key, value in extras.items():
        if key in allowed and value is not None and value != "":
            env[key] = value
    if home is not None:
        env["HOME"] = str(home)
    if tmp is not None:
        env["TMPDIR"] = str(tmp)
        env["TMP"] = str(tmp)
    for key in _DENIED:
        env.pop(key, None)
    return env


def _close_inherited_fds() -> None:
    """Close every descriptor above stderr. Landlock ignores ones left open."""
    try:
        names = os.listdir("/proc/self/fd")
    except OSError:
        names = [str(n) for n in range(3, 256)]
    for name in names:
        try:
            fd = int(name)
        except ValueError:
            continue
        if fd > 2:
            try:
                os.close(fd)
            except OSError:
                pass


def _rights_for(path: str) -> int:
    try:
        mode = os.stat(path, follow_symlinks=True).st_mode
    except OSError:
        return _RX
    if stat.S_ISCHR(mode):
        return _DEV
    return _RX


def _no_new_privs() -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    return prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0


def _install(rw_paths: Sequence[str], rx_paths: Sequence[str], handled: int) -> bool:
    """Install the ruleset in this process. False when enforcement did not hold."""
    if not _no_new_privs() and os.geteuid() != 0:
        return False
    _close_inherited_fds()
    call = _syscall()
    mask = handled if handled else _fs_mask(max(landlock_abi(), 1))
    attr = ctypes.create_string_buffer(16)
    ctypes.memmove(attr, ctypes.byref(ctypes.c_uint64(mask)), 8)
    ctypes.set_errno(0)
    ruleset = call(_SYS_CREATE, ctypes.addressof(attr), 16, 0, 0)
    if ruleset < 0:
        return False

    def add(path: str, rights: int) -> bool:
        try:
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            return False
        blob = ctypes.create_string_buffer(12)
        ctypes.memmove(blob, ctypes.byref(ctypes.c_uint64(rights & mask)), 8)
        ctypes.memmove(
            ctypes.addressof(blob) + 8,
            ctypes.byref(ctypes.c_int32(parent)),
            4,
        )
        try:
            rc = call(_SYS_ADD, ruleset, _RULE_PATH_BENEATH, ctypes.addressof(blob), 0)
        finally:
            os.close(parent)
        return rc == 0

    rw_rights = mask
    ok = True
    for path in rw_paths:
        ok = add(path, rw_rights) and ok
    for path in rx_paths:
        rights = _rights_for(path) & mask
        if add(path, rights):
            continue
        # Character devices and single files are best-effort. A missing
        # loader directory means the binary cannot start under the ruleset.
        if not os.path.isdir(path):
            continue
        ok = False
    if not ok or not rw_paths:
        os.close(ruleset)
        return False
    ctypes.set_errno(0)
    rc = call(_SYS_RESTRICT, ruleset, 0, 0, 0)
    os.close(ruleset)
    return rc == 0


def make_preexec(
    rw_paths: Sequence[str],
    rx_paths: Sequence[str],
    *,
    handled: int = 0,
):
    """``preexec_fn`` that enforces the ruleset or exits. Never returns on failure."""
    rw = tuple(rw_paths)
    rx = tuple(rx_paths)
    forced = int(handled)

    def _preexec() -> None:
        if not _install(rw, rx, forced):
            os._exit(_CONFINEMENT_EXIT)

    return _preexec


def confinement_popen_kwargs(
    cwd: Path | str,
    executable: str,
    extras: Iterable[Path | str] = (),
    *,
    handled: int = 0,
) -> dict:
    """Keyword arguments that enforce confinement.

    Raises :class:`ConfinementUnavailable` instead of returning an unconfined spawn.
    """
    require_landlock()
    rw = narrow_rw_roots(cwd, extras)
    rx = system_rx_paths(executable)
    return {
        "close_fds": True,
        "preexec_fn": make_preexec(rw, rx, handled=handled),
    }
