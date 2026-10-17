"""Literal tables, regexes, and generated-file templates for the build roles.

Extracted from ``roles.py``. String bodies are copied verbatim so emitted
handlers, models, and tester bootstraps do not change.
"""

from __future__ import annotations

import re

from app.factory.build.deploy_time_settings import (
    SNIPPET as _DEPLOY_TIME_SETTINGS_SNIPPET,
)

#: Any reference to the Store's runtime packages, in code or import strings.
_STORE_RUNTIME_RE = re.compile(r"\bapp\.(blocks|core)\b")
#: A module-level reference to the Store's ``app`` package that is NOT
#: blocks/core -- it executes at import time, so the block cannot load
#: offline at all. Column 0 on purpose: an indented (function-local) import
#: only breaks the one feature that runs it, and is recorded instead.
_STORE_FOREIGN_TOP_RE = re.compile(
    r"^(?:from\s+app(?:\.(?!blocks\b|core\b)[\w.]+)?\s+import|import\s+app\b)",
    re.MULTILINE,
)
#: The same reference inside a function body (indented).
_STORE_FOREIGN_LAZY_RE = re.compile(
    r"^\s+from\s+(app(?:\.(?!blocks\b|core\b)[\w.]+)?)\s+import",
    re.MULTILINE,
)
#: Names importable from ``app.blocks`` that are registry API, not block
#: classes. The generated registry provides them (or core does).
_REGISTRY_API_NAMES = frozenset(
    {
        "get_block",
        "get_all_blocks",
        "get_block_capabilities",
        "BLOCK_REGISTRY",
        "BlockCapabilities",
        "UniversalBlock",
        "UniversalContainer",
        "TypedBlock",
    }
)
#: One entry of the Store registry's literal defs:
#: ``"name": ("app.blocks.module", "ClassName")``. Single or double quotes.
_BLOCK_DEF_RE = re.compile(
    r'''["'](?P<name>[\w-]+)["']\s*:\s*\(\s*["']app\.blocks\.(?P<mod>[\w.]+)["']\s*,\s*["'](?P<cls>\w+)["']\s*\)'''
)
#: ``get_block("formula_executor")`` inside a kit-shelf shim.
_GET_BLOCK_RE = re.compile(r"""get_block\(\s*['"]([\w-]+)['"]""")
#: ``class FormulaExecutorV2(`` in a Store block module.
_BLOCK_CLASS_RE = re.compile(r"^class\s+(\w+)\s*\(", re.MULTILINE)

_INSTANTIATE_HELPER = '''
import os as _os
import sqlite3 as _sqlite3
from pathlib import Path as _HalPath


class _OfflineHal:
    """In-process HAL so Store DatabaseBlock can cursor() without a live store.

    Passing None made the tasting-room suite fail with
    ``'NoneType' object has no attribute 'cursor'`` after construct succeeded.
    """

    def __init__(self, db_path):
        self._db_path = db_path
        self._conn = None
        self.config = {}

    def _connect(self):
        if self._conn is None:
            parent = _HalPath(self._db_path).parent
            parent.mkdir(parents=True, exist_ok=True)
            self._conn = _sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = _sqlite3.Row
        return self._conn

    @property
    def connection(self):
        return self._connect()

    def get_connection(self):
        return self._connect()

    def cursor(self):
        return self._connect().cursor()

    def execute(self, *args, **kwargs):
        return self._connect().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._connect().executemany(*args, **kwargs)

    def commit(self):
        self._connect().commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _offline_hal():
    root = _HalPath(_os.environ.get("STORAGE_PATH") or ".").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return _OfflineHal(str(root / "store_blocks.sqlite"))


def _ensure_store_block_ready(instance):
    """DatabaseBlock only opens SQLite in _legacy_initialize.

    Construct-with-HAL is not enough: process() uses self._connection,
    which stays None until initialize runs. LotDesk pilot then died on
    Insert failed: 'NoneType' object has no attribute 'cursor'.
    """
    conn = getattr(instance, "_connection", None)
    init = getattr(instance, "_legacy_initialize", None)
    if conn is None and callable(init):
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(init())
            return instance
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, init()).result()
    return instance


def _instantiate_store_block(block_cls):
    """Store classes take (hal_block, config); kit shims called block_cls()."""
    hal = _offline_hal()
    attempts = []
    for call in (
        lambda: block_cls(hal, {}),
        lambda: block_cls(hal_block=hal, config={}),
        lambda: block_cls(hal=hal, config={}),
        lambda: block_cls(),
    ):
        try:
            return _ensure_store_block_ready(call())
        except TypeError as exc:
            attempts.append(exc)
    raise attempts[-1]


def _create_block_instance(block_or_name, *args, **kwargs):
    """Store host DI. Generated platforms have no app.dependencies host.

    Live: workflow step_0 (database) raised
    DatabaseBlock.__init__() missing 2 required positional arguments:
    'hal_block' and 'config' after the Store-host import failed and the
    fallback constructed DatabaseBlock() with no HAL.
    """
    target = block_or_name
    if isinstance(target, str):
        try:
            from vendor.cerebrum.blocks import get_block as _gb
        except ImportError:
            from app.blocks import get_block as _gb
        target = _gb(target)
    if isinstance(target, type):
        return _instantiate_store_block(target)
    return _ensure_store_block_ready(target)
'''

_DISPATCH_RUNTIME = '''"""Local block dispatch. No network, no store, no callback.

The old generated platforms answered a capability by POSTing to the operator's
block store. This imports the block that was vendored into this repository at
build time and calls it in-process, which is what makes the platform runnable
offline and independent of the factory's uptime.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "blocks"
_CACHE: Dict[str, Any] = {}


class BlockNotVendored(RuntimeError):
    """Asked for a block this platform does not carry."""


def load_block(block_id: str):
    if block_id in _CACHE:
        return _CACHE[block_id]
    path = _VENDOR / block_id / "block.py"
    if not path.is_file():
        raise BlockNotVendored(
            f"{block_id} is not vendored in this platform (looked in {path})"
        )
    spec = importlib.util.spec_from_file_location(f"vendored_{block_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _CACHE[block_id] = module
    return module


BLOCK_CONTRACTS: Dict[str, Any] = {}


class DispatchContractError(ValueError):
    """Caller payload failed the harvested contract. Do not invent fields."""


#: Every envelope this dispatcher had to normalise, as
#: ``(block_id, lifted_keys)``. A silent normalisation is still a defect in
#: the generated handler; the WRITER contract probe reads this list so the
#: mismatch is NAMED rather than absorbed.
_LIFTED: list = []


def _required_fields(block_id: str) -> list:
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    required = list(contract.get("input_required_fields") or [])
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name and item.get("required") and name not in required:
            required.append(name)
    return [field for field in required if field != "action"]


def _known_fields(block_id: str) -> set:
    """Closed field set from the harvested contract. Empty = no allow-list.

    Three sources, and the third is the one that was missing:

    * ``declared_inputs`` -- the block's CONFIG parameters, from block.json
      (backend, connection_string, theme, ...).
    * ``input_required_fields`` -- what the block refuses to run without.
    * ``input_keys_read_by_block`` -- the keys the block's own code actually
      reads off its payload at run time (table, values, where, file_path,
      user_id, ...). WORKAROUND, harvested from source; see
      CerebrumDev.ai#256 and Cerebrum-Blocks#90.

    Leaving the third one out is what made every generated platform inert.
    ``database`` declares only {input, backend, connection_string}, so a
    correct call -- ``{"table": "crew_logs", "values": {...}}`` -- had both
    keys counted as stray, folded into ``input`` by _adapt_input below, and
    handed to the block double-wrapped. ``_insert`` then read ``table=None``
    and ``values={}`` and emitted ``INSERT INTO None () VALUES ()``:

        database: Insert failed: near ")": syntax error

    One defect, four faces, measured on the booted zip of session
    Live: ``document_engine`` answered "No input files provided"
    holding a file_path, ``notification`` answered "block or tool name
    required for MCP channel" holding a block name, and ``team`` answered
    "Team access denied" holding a user_id. The platform built, shipped,
    booted and served all seventeen routes -- and could not persist a row.

    The fold itself stays: a key the block never reads is still a domain
    record and still belongs in ``input`` (the warehouse `audit` case the
    fold was written for). Only keys the block reads stay flat.
    """
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    names: set = set()
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name and name != "action":
            names.add(name)
    for field in contract.get("input_required_fields") or []:
        if field != "action":
            names.add(field)
    for field in contract.get("input_keys_read_by_block") or []:
        if field and field != "action":
            names.add(str(field))
    return names


def _keys_read(block_id: str) -> set:
    """Only the keys the block's CODE reads off its payload at run time.

    Distinct from ``_known_fields``, which also carries block.json's declared
    CONFIG params. The distinction is what tells a declared ``input`` slot
    from one the block actually reads: measured on the vendored blocks,
    ``analytics``, ``database``, ``team`` and ``storage`` contain ZERO
    ``.get("input")`` -- they read their keys flat -- while ``workflow``
    reads one. Both facts are needed below.

    WORKAROUND: ``input_keys_read_by_block`` is regex-harvested from the
    vendored source because no manifest declares it yet. When block.json
    carries ``requires_inputs`` (Cerebrum-Blocks#90), that declaration
    replaces this read -- removal tracked in CerebrumDev.ai#256.
    """
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    return {
        str(f) for f in (contract.get("input_keys_read_by_block") or []) if f
    }


def _envelope_mismatch(block_id: str, data: Dict[str, Any]) -> tuple:
    """Is the caller's record one level too deep for this block?

    Returns ``(buried, colliding)`` -- the keys the block reads that sit
    inside ``data["input"]`` instead of at the top level, and any of those
    that already exist at the top level with a different value.

    THE INCIDENT (post-#254).
    ``unit_registry_and_vacancy_tracking`` called::

        execute("analytics", {"input": {"metric": "monthly_rent_gbp",
                                        "value": 1450.0, ...}},
                action="track_event")

    and got ``{'error': 'metric and value required'}``. Verified literally in
    the block source: ``AnalyticsBlock._track_event`` reads
    ``data.get("metric")`` and ``data.get("value")`` off the payload it is
    handed, and ``analytics``' block.json declares ``input`` only as a config
    slot -- the code never reads it. Both fields were present in the spec, so
    a plan-time audit that looks for NAMES passes; the block still saw
    neither. That is #254's class one level deeper, and the same file wrapped
    three different shapes with no rule.
    """
    reads = _keys_read(block_id)
    if not reads or "input" in reads:
        # The block genuinely reads an ``input`` key (``workflow`` does).
        # Its wrapper is the contract, not a mistake.
        return (), ()
    inner = data.get("input")
    if not isinstance(inner, dict):
        return (), ()
    buried = sorted(k for k in inner if k in reads and k != "action")
    colliding = sorted(
        k for k in buried if k in data and data[k] != inner[k]
    )
    return tuple(buried), tuple(colliding)


def _adapt_input(block_id: str, payload: Any, action: str | None) -> Dict[str, Any]:
    """Pass the caller payload through. Never invent Store fields (F18).

    A MISMATCHED ENVELOPE IS NEVER PASSED THROUGH SILENTLY. Either the
    record is normalised to the shape the block reads, or the call fails
    with the mismatch named -- it does not reach the block wearing a shape
    the block cannot read (owner's ruling R1d, 2026-09-01).
    """
    data = dict(payload) if isinstance(payload, dict) else {"value": payload}
    known = _known_fields(block_id)

    buried, colliding = _envelope_mismatch(block_id, data)
    if colliding:
        # Two different values for the same field, one nested and one flat.
        # Normalising would have to choose, and choosing is inventing.
        verb = "appears" if len(colliding) == 1 else "appear"
        raise DispatchContractError(
            f"{block_id} envelope mismatch: {', '.join(colliding)} {verb} both "
            f"at the top level and inside 'input' with different values; "
            f"{block_id} reads them at the top level. Pass the record flat."
        )
    if buried:
        inner = dict(data.get("input") or {})
        for key in buried:
            data[key] = inner.pop(key)
        if inner:
            data["input"] = inner
        else:
            data.pop("input", None)
        _LIFTED.append((block_id, tuple(buried)))
    # Store blocks are action-dispatched: the domain record travels in the
    # block's declared ``input`` slot, with block params beside it. A handler
    # that passes the record flat gets every domain key refused as unknown --
    # measured on the warehouse `audit` capability, which declares
    # reference/status/quantity in its own model and had all three rejected.
    #
    # This is adaptation, not F18 fabrication. No value is invented and no
    # field is conjured to satisfy a validator: the caller's own record is
    # placed in the field the block declares for exactly it. If the caller
    # already supplied ``input``, nothing is moved.
    if known and "input" in known and "input" not in data:
        stray = {k: v for k, v in data.items() if k not in known}
        if stray:
            data = {k: v for k, v in data.items() if k in known}
            data["input"] = stray
    missing = [
        field for field in _required_fields(block_id)
        if field not in data or data[field] in (None, "")
    ]
    if missing:
        raise DispatchContractError(
            f"{block_id} missing required field(s): {', '.join(missing)}"
        )
    if known:
        unknown = sorted(str(key) for key in data if key not in known)
        if unknown:
            raise DispatchContractError(
                f"{block_id} unknown field(s): {', '.join(unknown)}"
            )
    return data


#: A block that answers with one of these has NOT succeeded. "partial" is the
#: word both the workflow and notification blocks use for "some of it failed"
#: -- workflow sets it on every step that raises, times out, names an unknown
#: block, or returns an error. Reading only "error" let a pipeline whose only
#: step failed come back as success.
_FAILED_STATUSES = {"error", "failed", "partial"}


def _failed_steps(result: Dict[str, Any]) -> list:
    """Sub-step failures the top-level status may not carry.

    Live, from a booted build: a capability returned
    ``ok: true`` wrapped around

        {"status": "partial", "results": [{"step_id": "step_0",
          "block": "database", "status": "failed",
          "error": "DatabaseBlock.__init__() missing 2 required positional
          arguments: 'hal_block' and 'config'"}]}

    The capability reported success. The row was never written.
    """
    steps = result.get("results")
    if not isinstance(steps, list):
        steps = result.get("steps")
    if not isinstance(steps, list):
        return []
    return [
        step for step in steps
        if isinstance(step, dict)
        and str(step.get("status") or "").lower() in {"error", "failed"}
    ]


def _error_envelope(block_id: str, action: str | None, error: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "block": block_id,
        "action": action,
        "error": error,
        "ok": False,
    }


_ENV_READS: Any = None


def _is_environ(node: Any) -> bool:
    import ast

    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and node.value.id == "os"
    return isinstance(node, ast.Name) and node.id == "environ"


def _vendored_env_reads() -> set:
    """Every setting name the vendored source reads from the environment.

    Read off the vendored bytes, once: no manifest declares required env, so
    the source is the only honest record of what a block needs configured.
    """
    global _ENV_READS
    if _ENV_READS is not None:
        return _ENV_READS
    import ast

    names = set()
    for path in _VENDOR.parent.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError, OSError):
            continue
        for node in ast.walk(tree):
            key = None
            if isinstance(node, ast.Subscript) and _is_environ(node.value):
                key = node.slice
            elif isinstance(node, ast.Call) and node.args:
                fn = node.func
                if isinstance(fn, ast.Attribute) and (
                    (fn.attr in ("get", "pop") and _is_environ(fn.value))
                    or (
                        fn.attr == "getenv"
                        and isinstance(fn.value, ast.Name)
                        and fn.value.id == "os"
                    )
                ):
                    key = node.args[0]
                elif isinstance(fn, ast.Name) and fn.id == "getenv":
                    key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                names.add(key.value)
    _ENV_READS = names
    return names


def unavailable_reason(block_id: str, error: Any) -> str | None:
    """Why a block failure is really "credentials not configured", or None.

    A connector with no credentials is a DECLARED STUB, not a failure: the
    DevOps team supplies them at deploy. Recognised when the block's own
    refusal names a setting the vendored source reads from the environment
    AND that setting is unset here. No block and no setting is named in this
    file -- both come from the vendored source and the block's own words.
    """
    import os
    import re

    text = str(error or "")
    if not text:
        return None
    missing = sorted(
        name
        for name in _vendored_env_reads()
        if not str(os.environ.get(name) or "").strip()
        and re.search("(?<![A-Za-z0-9_])" + re.escape(name) + "(?![A-Za-z0-9_])", text)
    )
    if not missing:
        return None
    return "credentials not configured: " + ", ".join(missing)


def _declared_stub(block_id: str, action: str | None, reason: str) -> Dict[str, Any]:
    """SUCCESS envelope for a connector whose credentials are not configured."""
    return {
        "status": "success",
        "ok": True,
        "block": block_id,
        "action": action,
        "stub": True,
        "result": {"stub": True},
        "blocks_unavailable": [block_id],
        "reason": reason,
    }


def _force_utf8_stdio() -> None:
    """F13: a checkmark/emoji print must not become a charmap crash.

    Windows cp1252 consoles encode print() with the console codepage. A
    vendored block that prints one checkmark used to raise UnicodeEncodeError;
    execute() then swallowed that into a generic error envelope that looked
    like a domain refusal. Force UTF-8 on this process before the block runs.
    """
    import os
    import sys

    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            continue


_force_utf8_stdio()


def execute(
    block_id: str,
    payload: Dict[str, Any],
    action: str | None = None,
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run a vendored block locally and return its result envelope.

    Real Store blocks are action-dispatched: the domain data travels as
    ``input`` and the operation name as ``action`` (each block declares a
    default in its block.json). A call with no action reaches blocks that
    answer "Unknown action" -- pass the one the capability needs.

    Missing required fields and unknown fields/params become an error
    envelope. A block refusal (status=error) is returned as-is — never
    rewritten to ok.
    """
    module = load_block(block_id)
    run = getattr(module, "run", None)
    if run is None:
        raise BlockNotVendored(f"{block_id} exposes no run() entry point")
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    declared = set()
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            declared.add(name)
    if (
        params
        and (contract.get("declared_inputs") or contract.get("input_required_fields"))
    ):
        extra = sorted(
            str(key) for key in params if key not in declared and key != "action"
        )
        if extra:
            return _error_envelope(
                block_id, action, f"unknown param(s): {', '.join(extra)}"
            )
    kwargs = dict(params or {})
    if action is not None:
        kwargs["action"] = action
    try:
        adapted = _adapt_input(block_id, payload, action)
    except DispatchContractError as exc:
        return _error_envelope(block_id, action, str(exc))
    # A block-level failure comes back as data, not as an exception. The
    # Store's shim raises RuntimeError on an error envelope, which destroys
    # the diagnosis: a handler (and a failing test) sees "Input validation
    # failed" with no block name and no field list. Structural failures --
    # a block that is not vendored -- still raise above.
    _force_utf8_stdio()
    try:
        result = run(input=adapted, **kwargs)
    except BlockNotVendored:
        raise
    except UnicodeEncodeError as exc:
        return _error_envelope(
            block_id,
            action,
            f"UnicodeEncodeError on block stdout (encoding, not domain): {exc}",
        )
    except Exception as exc:
        reason = unavailable_reason(block_id, exc)
        if reason:
            return _declared_stub(block_id, action, reason)
        return _error_envelope(
            block_id, action, f"{type(exc).__name__}: {exc}"
        )
    if isinstance(result, dict):
        status = str(result.get("status") or "").lower()
        failed = _failed_steps(result)
        if status in _FAILED_STATUSES or result.get("ok") is False or failed:
            reason = unavailable_reason(
                block_id, result.get("error") or result.get("message") or ""
            )
            if reason:
                return _declared_stub(block_id, action, reason)
            refused = dict(result)
            refused["status"] = status if status in _FAILED_STATUSES else "error"
            refused.setdefault("block", block_id)
            refused["ok"] = False
            if failed and not refused.get("error"):
                refused["error"] = "; ".join(
                    "%s (%s): %s" % (
                        step.get("step_id") or "step",
                        step.get("block") or "?",
                        str(step.get("error") or step.get("status"))[:160],
                    )
                    for step in failed
                )
            return refused
    return result


def verify_locked_blocks() -> None:
    """Start-up check: every block blocks.lock.json lists loads from this
    platform's vendored tree, or the platform refuses to start. A locked block
    that cannot be loaded is never replaced by anything else, silently."""
    import json as _json

    lock = _VENDOR.parents[1] / "blocks.lock.json"
    try:
        data = _json.loads(lock.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    blocks = data.get("blocks") if isinstance(data, dict) else None
    if isinstance(blocks, dict):
        ids = list(blocks)
    else:
        ids = [str(b.get("id") or "") for b in blocks or [] if isinstance(b, dict)]
    failed = []
    for block_id in [i for i in ids if i]:
        try:
            load_block(block_id)
        except Exception as exc:  # noqa: BLE001 -- every failure is named below
            failed.append("%s (%s: %s)" % (block_id, type(exc).__name__, exc))
    if failed:
        raise BlockNotVendored(
            "locked block(s) cannot be loaded; refusing to start: " + "; ".join(failed)
        )


verify_locked_blocks()
'''

_PY_DEFAULTS = {
    "str": '""',
    "int": "0",
    "float": "0.0",
    "bool": "False",
    "datetime": "'2026-09-03T10:00:00'",
    "date": "'2026-09-03'",
    "time": "'10:00:00'",
    "uuid": "'00000000-0000-4000-8000-000000000001'",
    "email": "'guest@example.com'",
}

_SAMPLE_VALUES = {
    "str": "sample",
    "int": 1,
    "float": 1.5,
    "bool": True,
    "datetime": "2026-09-03T10:00:00",
    "date": "2026-09-03",
    "time": "10:00:00",
    "uuid": "00000000-0000-4000-8000-000000000001",
    "email": "guest@example.com",
}

_CONFTEST_TEMPLATE = '''"""Test bootstrap for the generated platform.

Puts the platform root on sys.path and points persistence at a scratch
directory, so running the suite never touches a real data file.

STORAGE_PATH is FORCED, not defaulted. The build environment legitimately
carries its own STORAGE_PATH (the factory backend sets one), and a
``setdefault`` here made every tester round share one database file: a
table created by round N rejected round N+1's columns, and the rework loop
burned its budget chasing schema errors no round had actually caused.

Outbound network is BLOCKED, not merely unconfigured. Stripping the store
env only proves the platform does not call the store; a handler that posts
to an arbitrary public URL still passed, and one did -- "sent" a webhook to
the open internet from a platform whose whole claim is running offline.
Loopback stays open so TestClient-style local servers keep working.
P1: this blocker is unchanged. Do not add local-inference or cloud hosts.
"""

import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="platform-test-")

# Test-context tokens. RUNTIME has no baked token: production reads
# PLATFORM_TOKEN from the deploy environment (a per-package random value)
# and fails closed without one. This bootstrap is the ONE place the
# well-known dev values may exist, and it provisions the second tenant
# through TENANT_TOKENS -- the legitimate mechanism whose absence once
# forced a writer to hardwire a token into app/tenancy.py (a production
# backdoor the floor's no_token_literal check now refuses).
os.environ.setdefault("PLATFORM_TOKEN", "dev-local-token")
os.environ.setdefault("PLATFORM_TOKEN_B", "dev-local-token-b")
os.environ.setdefault("TENANT_TOKENS", "dev-local-token-b:tenant-b")
# F4: the principal set approval fields are validated against. Free text
# ("i am the director trust me") passed a P1 safety escalation on a
# certified product; an approver must name a principal the platform knows.
os.environ.setdefault("APPROVED_PRINCIPALS", "director-jane")

# Settings the operator supplies at deploy time are not a build failure. This
# must run BEFORE the first import of ``app`` below: a product that reads a
# required credential at import would otherwise KeyError inside the factory's
# own tests. Shared, word for word, with scripts/acceptance.py.
#<<DEPLOY_TIME_SETTINGS>>
_stand_in_for_deploy_time_settings(Path(__file__).resolve().parents[1])

# Schema is versioned. connect() does not CREATE TABLE. Apply head so
# model/route tests have tables; a missing revision fails the suite.
# ImportError is only for isolation probes that exec this file without app/.
try:
    from app.migrations import upgrade_head  # noqa: E402

    upgrade_head()
except ImportError:
    pass

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_real_connect = socket.socket.connect


def _offline_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("utf-8", "replace")
    if str(host) not in _LOCAL_HOSTS:
        raise OSError(
            f"offline suite: outbound connection to {host!r} refused -- this "
            "platform must run with no network"
        )
    return _real_connect(self, address)


socket.socket.connect = _offline_connect


import pytest as _pytest  # noqa: E402


@_pytest.fixture
def client():
    """A TestClient on the platform, shipped WITH the suite.

    Any test that asks for ``client`` finds it here, so "fixture 'client' not
    found" cannot happen whatever module the test lives in. A test module that
    defines or imports its own ``client`` still wins -- pytest resolves the
    nearest one first.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def pytest_configure(config):
    """Register the factory vs pilot split. TESTER's lane is tests/** so
    this cannot live in a repo-root pytest.ini."""
    config.addinivalue_line(
        "markers",
        "pilot: Store-backed execute-all; excluded from the factory code-phase gate",
    )
    # Declared placeholder refusals the product answers are written beside
    # the JUnit report, keyed by the running test (app/placeholders.py), so
    # the Factory reads which test was answered the typed refusal.
    xml = getattr(config.option, "xmlpath", None)
    if xml:
        os.environ[#<<REFUSAL_LOG_ENV>>] = str(xml) + #<<REFUSAL_LOG_SUFFIX>>
        # Which capability each Factory-emitted test found failing, keyed by
        # the running test -- the gate's typed source for capability_id.
        os.environ[#<<CAPABILITY_LOG_ENV>>] = str(xml) + #<<CAPABILITY_LOG_SUFFIX>>
'''

# The deploy-time-settings snippet is substituted in, not pasted into the
# literal above: it carries quotes of its own, and it is the SAME text that
# goes into scripts/acceptance.py -- one source, two consumers.
_DEPLOY_TIME_SLOT = "#<<DEPLOY_TIME_SETTINGS>>"
assert _CONFTEST_TEMPLATE.count(_DEPLOY_TIME_SLOT) == 1
_CONFTEST = _CONFTEST_TEMPLATE.replace(
    _DEPLOY_TIME_SLOT, _DEPLOY_TIME_SETTINGS_SNIPPET.strip("\n")
)


def _with_refusal_log(text: str) -> str:
    from app.factory.build.placeholder_connectors import (
        REFUSAL_LOG_ENV,
        REFUSAL_LOG_SUFFIX,
    )

    from app.factory.build.findings import CAPABILITY_LOG_ENV, CAPABILITY_LOG_SUFFIX

    return (
        text.replace("#<<REFUSAL_LOG_ENV>>", repr(REFUSAL_LOG_ENV))
        .replace("#<<REFUSAL_LOG_SUFFIX>>", repr(REFUSAL_LOG_SUFFIX))
        .replace("#<<CAPABILITY_LOG_ENV>>", repr(CAPABILITY_LOG_ENV))
        .replace("#<<CAPABILITY_LOG_SUFFIX>>", repr(CAPABILITY_LOG_SUFFIX))
    )


_CONFTEST = _with_refusal_log(_CONFTEST)

#: Where TESTER writes the Factory's test bootstrap: the rootdir conftest,
#: which pytest loads BEFORE ``tests/conftest.py``. Never ``tests/conftest.py``
#: -- that is the product's own file (the names its tests import live there),
#: and writing over it failed every writer test that imports from it at
#: collection, round after round (live 2026-10-08, rotation cycle 1). Both
#: conftests load; both hook sets run; the product's explicit settings win
#: over this bootstrap's ``setdefault`` values.
CONFTEST_REL = "conftest.py"
