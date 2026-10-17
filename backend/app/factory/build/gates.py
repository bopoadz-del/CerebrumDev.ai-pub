"""Phase gates — the checks a role must pass before the next role starts.

A gate is the only reason a long build converges instead of drifting. Each
role hands over a workspace and a claim about it; the gate is what decides
whether the claim is true, independently of anything the role said about its
own work.

Gates are values, not methods on the roles, for exactly that reason -- a
role cannot supply, weaken or skip the check that judges it. The runner
looks the gate up by phase and runs it against the workspace.

Every gate returns a :class:`GateResult` rather than raising, because a
failure is normal control flow here: it is what sends the WRITER back round
for another pass. Only a gate that cannot run at all raises.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

from app.factory.build.authority import BuildRole
from app.factory.build.brief_gates import WORKSPACE_COMPILES_CHECK, WRITER_CONTRACT_CHECK
from app.factory.build.pilot_durability import gate_pilot_outcome_survives_restart
from app.factory.build.ui_surface import gate_ui_surface
from app.factory.build.vendored_integrity import gate_vendored_integrity
from app.factory.build.writer_behaviour import gate_writer_behaviour

#: Wall-clock ceiling for a single gate subprocess. A gate that hangs would
#: silently consume the whole build budget.
DEFAULT_GATE_TIMEOUT_S = 600.0

#: Factory TESTER runs the code-phase suite only. Store-backed execute-all
#: lives on ``@pytest.mark.pilot`` and is not this gate: a complete platform
#: as designed is a later phase, not a 20–30 minute coder pass.
FACTORY_SUITE_MARKER_EXPR = "not pilot"


@dataclass(frozen=True)
class GateResult:
    ok: bool
    gate: str
    detail: str = ""
    #: F1: named reason token (writer_no_output, suite_red, ...).
    reason: str = ""
    #: Machine-readable specifics the runner records in the ledger and the
    #: next WRITER pass reads as its work list. Typed (build/findings.py):
    #: every item is a Finding -- a human line carrying gate, check_id,
    #: capability_id, file, line and finding_shape.
    findings: List[str] = field(default_factory=list)
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        from app.factory.build.findings import coerce

        payload = self.payload or {}
        object.__setattr__(
            self,
            "findings",
            coerce(
                self.findings,
                gate=self.gate,
                default_check=str(payload.get("check") or self.gate),
                per_index_checks=list(payload.get("finding_checks") or []),
                shape=payload.get("finding_shape") or self.reason,
            ),
        )

    def to_json(self) -> Dict[str, Any]:
        from app.factory.build.findings import TYPED_KEY, typed

        return {
            "ok": self.ok,
            "gate": self.gate,
            "detail": self.detail,
            "reason": self.reason,
            "findings": [str(f) for f in self.findings],
            TYPED_KEY: typed(self.findings),
            "payload": dict(self.payload),
        }


class Gate(Protocol):
    """A check over a finished phase."""

    name: str

    def __call__(self, ctx: "GateContext") -> GateResult: ...


@dataclass(frozen=True)
class GateContext:
    """What a gate is allowed to look at."""

    workspace: Path
    role: BuildRole
    #: Populated by the COLLECTOR: capabilities with no adequate block.
    gaps: tuple = ()
    #: Populated by the CLONER: block ids vendored into the workspace.
    vendored_blocks: tuple = ()
    timeout_s: float = DEFAULT_GATE_TIMEOUT_S
    #: Injected so tests drive the gates without spawning real subprocesses.
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None
    #: pytest ``-m`` expression. Code-phase is ``not pilot``; the Store-green
    #: cycle is ``pilot``.
    suite_marker: str = FACTORY_SUITE_MARKER_EXPR
    #: ``code`` (factory 5/5) or ``pilot`` (Store-green).
    cycle: str = "code"
    #: The USER's brief, as typed. A gate that judges domain assumptions
    #: needs what the customer actually said, not the compiled writer
    #: prompt (70k characters of factory boilerplate and inventory).
    brief: str = ""
    #: STORE_MANAGER decisions recorded for this cycle.
    store_ops: tuple = ()
    #: True when CEREBRUM_API_URL is unset — local clone-register reads only.
    store_unwired: bool = False

    def run(self, argv: List[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        run = self.runner or _real_run
        return run(argv, cwd=cwd or self.workspace, timeout=self.timeout_s)


def _real_run(argv: List[str], *, cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    import os

    # UTF-8, unconditionally. On Windows a gate subprocess otherwise inherits
    # the console codepage, and a vendored block that prints one checkmark
    # ("✓") dies with a charmap UnicodeEncodeError that looks like a
    # block failure -- measured live on the team block.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    # The FACTORY's own database binding must never become the workspace's.
    # DATABASE_URL rode along from Render's auto-injection into the AWS task
    # env, and every gate subprocess inherited it -- so the emitted platform
    # under test picked Postgres while its templated migration code assumed
    # sqlite, and every generation died in the writer-behaviour probe
    # (ObjectNotExecutableError, 2026-09-27). Now that the emitted product
    # honours DATABASE_URL end to end, inheriting it here would be worse
    # than the crash: a product's migrations pointed at the Factory's own
    # database. A build carries no operator DATABASE_URL; a gate that wants
    # one must say so explicitly.
    env.pop("DATABASE_URL", None)
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        # A gate must never inherit an interactive stdin -- a subprocess that
        # blocks on input would stall the build with no diagnosis.
        stdin=subprocess.DEVNULL,
    )


# -- suite failure classes (Floor banner is verdict.detail only) ---------

#: Needles the PRODUCT / TESTER banner must name. Live VetCare Hub
#: sess_5dfb4a3 showed only "suite is red" while pytest had a concrete
#: class — rework then regenerated every handler because findings named
#: no capability.
_SUITE_ASSERTION_CLASSES: tuple[tuple[str, str], ...] = (
    ("KeyError: 'items'", "accept-payload list shape (KeyError: 'items')"),
    ('KeyError: "items"', "accept-payload list shape (KeyError: 'items')"),
    (
        "rejected a payload built from its own schema",
        "schema sample refused",
    ),
    (
        "status must be one of",
        "schema sample refused (status vocabulary)",
    ),
    (
        "Unknown channel:",
        "schema sample refused (notification channel)",
    ),
    (
        "step_0 (event_bus)",
        "schema sample refused (event_bus workflow step)",
    ),
    (
        "step_1 (event_bus)",
        "schema sample refused (event_bus workflow step)",
    ),
    (
        "(event_bus): error",
        "schema sample refused (event_bus workflow step)",
    ),
    (
        "not JSON serializable",
        "e2e handle() result not JSON serializable",
    ),
    (
        "Object of type bytes",
        "e2e handle() result not JSON serializable",
    ),
    (
        "accepted a record but persisted nothing",
        "accept-payload persisted nothing",
    ),
    ("create_persists", "domain acceptance: create_persists"),
    ("list_only_persisted", "domain acceptance: list_only_persisted"),
    ("queue_item_processed", "domain acceptance: queue_item_processed"),
    ("Missing required field", "Missing required field"),
    ("no such table", "no such table"),
    ("No module named", "missing module"),
)


def suite_assertion_classes(findings: List[str], raw: str = "") -> List[str]:
    """Stable labels for the pytest lines a red PRODUCT suite produced."""
    blob = "\n".join(findings) + "\n" + (raw or "")
    seen: List[str] = []
    for needle, label in _SUITE_ASSERTION_CLASSES:
        if needle in blob and label not in seen:
            seen.append(label)
    return seen


def classify_suite_red(findings: List[str], raw: str = "") -> str:
    """PRODUCT / Floor detail must name the assertion class, not only 'red'."""
    classes = suite_assertion_classes(findings, raw)
    failed = next((ln.strip() for ln in findings if ln.startswith("FAILED")), "")
    err = next((ln.strip() for ln in findings if ln.startswith("E ")), "")
    snippet = " ".join(part for part in (failed, err) if part) or next(
        (ln.strip() for ln in findings if ln.strip()), ""
    )
    if snippet and len(snippet) > 240:
        snippet = snippet[:237] + "..."
    if classes and snippet:
        return "suite is red: " + "; ".join(classes[:3]) + " — " + snippet
    if classes:
        return "suite is red: " + "; ".join(classes[:3])
    if snippet:
        return "suite is red: " + snippet
    return "suite is red"


# -- individual gates ----------------------------------------------------


def gate_gaps_enumerated(ctx: GateContext) -> GateResult:
    """COLLECTOR: every capability is either backed or declared a gap.

    The failure this exists for is the silent one -- a collector that plans
    around a missing block by dropping the capability, so the platform ships
    without it and nothing in the artifact says so.
    """
    unresolved = [g for g in ctx.gaps if not str(g).strip()]
    if unresolved:
        return GateResult(
            ok=False,
            gate="gaps_enumerated",
            reason="gaps_unnamed",
            detail="collector reported an unnamed gap",
            findings=[f"gap {i} has no capability id" for i, _ in enumerate(unresolved)],
        )
    return GateResult(
        ok=True,
        gate="gaps_enumerated",
        detail=f"{len(ctx.gaps)} gap(s) declared for the writer",
        payload={"gaps": list(ctx.gaps)},
    )


def gate_blocks_import_offline(ctx: GateContext) -> GateResult:
    """CLONER: every vendored block imports with no store configured.

    This is the gate that makes a delivered platform standalone. It runs
    with CEREBRUM_API_URL deliberately absent, so a block that still reaches
    for the store at import time fails here rather than in the customer's
    environment.
    """
    vendor = ctx.workspace / "vendor" / "blocks"
    if not vendor.is_dir():
        return GateResult(
            ok=False,
            gate="blocks_import_offline",
            reason="vendor_blocks_missing",
            detail="vendor/blocks is missing — nothing was cloned",
            findings=["cloner produced no vendored blocks"],
        )

    missing = [b for b in ctx.vendored_blocks if not (vendor / b / "block.py").is_file()]
    if missing:
        return GateResult(
            ok=False,
            gate="blocks_import_offline",
            reason="registered_block_missing",
            detail=f"{len(missing)} block(s) registered but not on disk",
            findings=[f"vendor/blocks/{b}/block.py missing" for b in missing],
        )

    declared = sorted(_declared_third_party_modules(ctx.workspace))
    assert _IMPORT_PROBE.count(_DECLARED_SLOT) == 1, "probe lost its declared-modules slot"
    probe = _IMPORT_PROBE.replace(
        _DECLARED_SLOT, "_DECLARED_MODULES = " + repr(",".join(declared))
    )
    proc = ctx.run([sys.executable, "-c", probe])
    if proc.returncode != 0:
        return GateResult(
            ok=False,
            gate="blocks_import_offline",
            reason="block_import_offline_failed",
            detail="a vendored block failed to import offline",
            findings=[ln for ln in (proc.stderr or "").splitlines() if ln.strip()][-10:],
        )
    stood_in = sorted(
        ln.split(":", 1)[1].strip()
        for ln in (proc.stdout or "").splitlines()
        if ln.startswith(_STOOD_IN_PREFIX)
    )
    detail = f"{len(ctx.vendored_blocks)} block(s) import with no store configured"
    if stood_in:
        detail += (
            "; declared package(s) not installed on the build host, stood in "
            "for the import check: " + ", ".join(stood_in)
        )
    return GateResult(
        ok=True,
        gate="blocks_import_offline",
        detail=detail,
        payload={"declared_not_on_build_host": stood_in},
    )


_DECLARED_SLOT = '_DECLARED_MODULES = ""'
_STOOD_IN_PREFIX = "STOOD_IN:"


def _declared_third_party_modules(workspace: Path) -> set:
    """Import names the vendored source declares as pip dependencies.

    Derived by the SAME AST scan that writes them into the product's
    requirements.txt (block_obligations.dependency_obligations) -- not a list
    kept here. Anything that scan cannot name a distribution for raises in
    the CLONER long before this gate, so every name returned is one the
    product is guaranteed to declare.
    """
    from app.factory.build.block_obligations import (
        BlockObligationError,
        dependency_obligations_on_disk,
    )

    try:
        return {row["module"] for row in dependency_obligations_on_disk(workspace).values()}
    except BlockObligationError:
        # Undeclarable imports are the CLONER's refusal to make, with its own
        # message. Tolerate nothing here rather than guess.
        return set()


#: Imports every vendored block by file path, with the store env stripped.
#:
#: THE BUILD HOST IS NOT THE PRODUCT. This runs under the Factory's own
#: interpreter, which carries the Factory's dependencies and not the blocks'.
#: Live, in a production-like sweep of all 197 Store blocks:
#:
#:     construction_advisor: ModuleNotFoundError: No module named 'sympy'
#:
#: sympy is a module-level import in that block's runtime, the CLONER already
#: derives it as a dependency obligation, and the product's requirements.txt
#: declares it -- the PRODUCT is correct. The gate failed because the Factory
#: host has no sympy, which says nothing about whether the block needs the
#: Store. Installing every block's dependencies into the Factory is the wrong
#: fix: it hard-wires the Store's package set into this repo's lock.
#:
#: So a package the vendored source DECLARES, when absent here, gets a
#: placeholder module and the import is RETRIED -- the rest of the import
#: still executes, so a real ``No module named 'app'`` hiding behind it is
#: still caught. Only declared names qualify; an undeclared or Store-local
#: module fails exactly as before. Placeholders exist in this probe process
#: only and are reported, never shipped.
_IMPORT_PROBE = """
import importlib.util, os, pathlib, sys, types
for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
    os.environ.pop(var, None)
# Replaced by the gate with the declared set. Left as it is, the probe is
# still valid Python and tolerates nothing -- the strictest reading.
_DECLARED_MODULES = ""
declared = {m for m in _DECLARED_MODULES.split(",") if m}
stood_in = set()


class _Anything:
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Anything()

    def __getattr__(self, name):
        return _Anything()

    def __mro_entries__(self, bases):
        return (object,)

    def __iter__(self):
        return iter(())

    def __getitem__(self, key):
        return _Anything()


class _Placeholder(types.ModuleType):
    __path__ = []

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Anything()


class _DeclaredLoader:
    @staticmethod
    def create_module(spec):
        return _Placeholder(spec.name)

    @staticmethod
    def exec_module(module):
        pass


class _DeclaredFinder:
    # Submodules of a stood-in package (sympy.parsing...) resolve to it too.
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name.split(".")[0] in stood_in:
            return importlib.util.spec_from_loader(name, _DeclaredLoader())
        return None


sys.meta_path.append(_DeclaredFinder)
failed = []
for mod in sorted(pathlib.Path("vendor/blocks").glob("*/block.py")):
    name = "vendored_" + mod.parent.name
    for _attempt in range(len(declared) + 1):
        spec = importlib.util.spec_from_file_location(name, mod)
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            break
        except ModuleNotFoundError as exc:
            top = (exc.name or "").split(".")[0]
            if top in declared and top not in stood_in:
                stood_in.add(top)
                # Half-imported vendored modules would mask the retry.
                for loaded in [m for m in sys.modules if m.startswith("vendor.")]:
                    del sys.modules[loaded]
                continue
            failed.append(mod.parent.name + ": " + type(exc).__name__ + ": " + str(exc))
            break
        except Exception as exc:
            failed.append(mod.parent.name + ": " + type(exc).__name__ + ": " + str(exc))
            break
for top in sorted(stood_in):
    print("STOOD_IN: " + top)
if failed:
    sys.stderr.write("\\n".join(failed))
    raise SystemExit(1)
"""


def gate_workspace_compiles(ctx: GateContext) -> GateResult:
    """WRITER: everything under app/ is at least syntactically real.

    Cheap and non-negotiable. A writer pass that emits a file which cannot
    be parsed must not reach the tester, where the failure would surface as
    a confusing collection error instead of a compile error.
    """
    app_dir = ctx.workspace / "app"
    if not app_dir.is_dir():
        return GateResult(
            ok=False,
            gate=WORKSPACE_COMPILES_CHECK,
            reason="writer_no_app",
            detail="app/ is missing — the writer produced nothing",
            findings=["no app/ directory"],
        )

    proc = ctx.run([sys.executable, "-m", "compileall", "-q", "app"])
    if proc.returncode != 0:
        output = ((proc.stdout or "") + (proc.stderr or "")).splitlines()
        return GateResult(
            ok=False,
            gate=WORKSPACE_COMPILES_CHECK,
            reason="workspace_compile_failed",
            detail="app/ does not compile",
            findings=[ln for ln in output if ln.strip()][-20:],
        )
    return GateResult(ok=True, gate=WORKSPACE_COMPILES_CHECK, detail="app/ compiles")


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: A collection error naming one of these is the build box missing a
#: dependency, not the product being wrong.
_MISSING_DEP = re.compile(r"No module named ['\"]?([A-Za-z0-9_.]+)")
#: ``path/to/file.py:123:`` -- a traceback frame in pytest's long format.
def _frame_locations(text: str) -> List[str]:
    """Source files of a pytest traceback's frames, in order.

    pytest writes each frame's location as ``<path>:<lineno>: ...`` at the
    start of a line (its ReprFileLocation grammar). A line is a frame when
    the part before the first ``:`` is a Python source path and the part
    before the second is a line number -- read by that grammar, not matched.
    """
    out: List[str] = []
    for line in (text or "").splitlines():
        if not line or line[0].isspace():
            continue
        path, sep, rest = line.partition(":")
        lineno, sep2, _ = rest.partition(":")
        if sep and sep2 and lineno.isdigit() and PurePath(path).suffix == ".py":
            out.append(path)
    return out


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


def _nodeid(workspace: Path, classname: str, name: str) -> tuple:
    """(``tests/test_x.py::[Class::]name``, ``tests/test_x.py``) from JUnit."""
    parts = [p for p in (classname or "").split(".") if p]
    for cut in range(len(parts), 0, -1):
        rel = "/".join(parts[:cut]) + ".py"
        if (workspace / rel).is_file():
            inner = parts[cut:] + [name]
            return rel + "::" + "::".join(inner), rel
    rel = "/".join(parts) + ".py" if parts else ""
    return (rel + "::" + name) if rel else name, rel


def failing_tests_from_junit(workspace: Path, junit_path: Path) -> Optional[List[Dict[str, str]]]:
    """Every failed/errored test case in a JUnit report, or None if unreadable."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(str(junit_path)).getroot()
    except (OSError, ET.ParseError):
        return None
    out: List[Dict[str, str]] = []
    for case in root.iter("testcase"):
        problem = case.find("failure")
        kind = "failure"
        if problem is None:
            problem = case.find("error")
            kind = "error"
        if problem is None:
            continue
        nodeid, rel = _nodeid(workspace, case.get("classname") or "", case.get("name") or "")
        message = (problem.get("message") or problem.text or "").strip().splitlines()
        frames = _frame_locations(problem.text or "")
        out.append(
            {
                "nodeid": nodeid,
                "file": rel,
                "name": case.get("name") or "",
                "kind": kind,
                "message": (message[0] if message else "")[:300],
                # The exception CLASS pytest recorded -- typed, not read from
                # the message text.
                "exc_type": (problem.get("type") or "").rsplit(".", 1)[-1],
                # The innermost frame of the traceback: where it actually broke.
                "innermost": frames[-1].replace("\\", "/") if frames else "",
                "text": (problem.text or "")[:4000],
            }
        )
    return out


def _pytest_available() -> bool:
    """The suite runs as ``sys.executable -m pytest``: is pytest importable
    in that interpreter? Asked of the import system, not of pytest's output."""
    import importlib.util

    return importlib.util.find_spec("pytest") is not None


def _verdict_from_junit(
    workspace: Path, returncode: int, junit_path: Path, raw: str, gate_name: str
) -> Optional[GateResult]:
    """Classify a suite run from exit code + JUnit. None = fall back to stdout.

    pytest exit codes: 0 passed, 1 tests failed, 2 interrupted (collection
    errors), 3 internal error, 4 usage error, 5 no tests collected.
    """
    if returncode == 0:
        return None  # the green path below reports the summary line
    if not _pytest_available():
        return GateResult(
            ok=False,
            gate=gate_name,
            reason="environment_fault",
            detail=(
                "the suite could not be RUN: pytest is not installed on the build "
                "host -- an environment fault, not a failing test"
            ),
            findings=["No module named pytest"],
            payload={"returncode": returncode, "infrastructure": True},
        )
    failing = failing_tests_from_junit(workspace, junit_path)
    if failing is None:
        return None
    # The product's own typed record of which test it answered a DECLARED
    # placeholder refusal (app/placeholders.py, beside the JUnit report).
    from app.factory.build.placeholder_connectors import (
        REFUSAL_LOG_SUFFIX,
        read_refusal_log,
    )

    from app.factory.build.findings import CAPABILITY_LOG_SUFFIX, read_capability_log

    refusals = read_refusal_log(str(junit_path) + REFUSAL_LOG_SUFFIX)
    # Which capability each failing test found failing: the emitted tests'
    # own typed record (findings.render_capability_recorder), never a parse
    # of the assertion message.
    cap_log = read_capability_log(str(junit_path) + CAPABILITY_LOG_SUFFIX)
    for row in failing:
        row["placeholder_refusals"] = list(refusals.get(row["nodeid"]) or [])
        caps = list(cap_log.get(row["nodeid"]) or [])
        for cap in row["placeholder_refusals"]:
            if cap not in caps:
                caps.append(cap)
        row["capabilities"] = caps
    if returncode in (3, 4) or (returncode == 2 and not failing):
        return GateResult(
            ok=False,
            gate=gate_name,
            reason="environment_fault",
            detail=(
                f"the suite could not be RUN: pytest exited {returncode} without "
                "running it -- an environment fault, not a failing test"
            ),
            findings=[ln for ln in raw.splitlines() if ln.strip()][-8:] or ["pytest produced no output"],
            payload={"returncode": returncode, "infrastructure": True},
        )
    # A collection error whose only cause is a third-party module missing on
    # the build box is the environment, not the product.
    if failing and all(f["kind"] == "error" for f in failing):
        matches = [_MISSING_DEP.search(f["text"] or f["message"]) for f in failing]
        missing = {m.group(1).split(".")[0] for m in matches if m}
        local = {"app", "tests", "vendor", "scripts"}
        if all(matches) and not (missing & local):
            return GateResult(
                ok=False,
                gate=gate_name,
                reason="environment_fault",
                detail="missing dependency on the build host: " + ", ".join(sorted(missing)),
                findings=[f"ERROR {f['nodeid']} - {f['message']}" for f in failing][:20],
                payload={"returncode": returncode, "infrastructure": True,
                         "missing_modules": sorted(missing)},
            )
    if not failing:
        return None
    from app.factory.build.findings import junit_row_findings

    findings = junit_row_findings(failing, gate=gate_name)
    return GateResult(
        ok=False,
        gate=gate_name,
        reason="suite_red",
        detail=classify_suite_red(findings, raw),
        findings=findings,
        payload={
            "returncode": returncode,
            "failing_tests": [
                {
                    k: f[k]
                    for k in (
                        "nodeid", "file", "name", "kind", "message", "innermost",
                        "placeholder_refusals", "capabilities",
                    )
                }
                for f in failing
            ],
            "assertion_classes": suite_assertion_classes(findings, raw),
        },
    )


def gate_suite_green(ctx: GateContext) -> GateResult:
    """TESTER: the code-phase suite runs and passes.

    This gate judges the coder's 20–30 minute pass: imports, dispatch load,
    model round-trip, routes that answer HTTP 200 JSON, handle() returning a
    mapping. Store-backed ``ok: True`` / nested-error scans are
    ``@pytest.mark.pilot`` and are *not* this gate.

    An empty or missing suite fails. "No tests ran" is the single most
    dangerous green in a generated platform -- it looks identical to success
    in every summary line.
    """
    tests_dir = ctx.workspace / "tests"
    if not tests_dir.is_dir() or not any(tests_dir.rglob("test_*.py")):
        return GateResult(
            ok=False,
            gate="suite_green",
            reason="no_tests_written",
            detail="no tests were written",
            findings=["tester produced no test files"],
        )

    # The suite's import surface is the app package. When the coder's
    # checkout lacks it, every generated test fails collection with
    # "No module named 'app'" and the rework round burns its budget on
    # import errors instead of the real gap. Fail fast and name the gap.
    app_root = ctx.workspace / "app"
    if not app_root.is_dir():
        expected = (
            "app/__init__.py",
            "app/main.py",
            "app/models.py",
            "app/actions",
        )
        missing = [
            rel for rel in expected if not (ctx.workspace / rel).exists()
        ]
        return GateResult(
            ok=False,
            gate="suite_green",
            reason="missing_app_package",
            detail=(
                "the coder's checkout has no app/ package — the suite "
                "cannot import it "
                f"(missing: {', '.join(missing) or 'app/'})"
            ),
            findings=[f"missing founding file: {rel}" for rel in missing]
            or ["app/ package absent"],
            payload={"assertion_classes": ["missing app package"]},
        )

    marker = (ctx.suite_marker or FACTORY_SUITE_MARKER_EXPR).strip() or FACTORY_SUITE_MARKER_EXPR
    gate_name = "pilot_green" if marker == "pilot" else "suite_green"
    # G4: read RESULTS, never colours. Coloured summary lines ("\x1b[31mFAILED")
    # defeated the startswith("FAILED") scrape below, so a genuinely failing
    # test was reported as "the suite could not be RUN ... environment fault".
    # Classification comes from the exit code + a JUnit report; stdout is only
    # a fallback, and even then with escape codes stripped.
    import tempfile

    junit_dir = Path(tempfile.mkdtemp(prefix="suite-junit-"))
    junit_path = junit_dir / "junit.xml"
    proc = ctx.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "-q",
            "--no-header",
            "--color=no",
            "-p",
            "no:cacheprovider",
            f"--junitxml={junit_path}",
            "-m",
            marker,
        ]
    )
    raw = _strip_ansi((proc.stdout or "") + (proc.stderr or ""))
    output = raw.splitlines()
    verdict = _verdict_from_junit(ctx.workspace, proc.returncode, junit_path, raw, gate_name)
    report_written = junit_path.is_file()
    shutil.rmtree(junit_dir, ignore_errors=True)
    if verdict is not None:
        return verdict
    if proc.returncode != 0:
        findings = [
            ln for ln in output if ln.startswith(("FAILED", "ERROR", "E "))
        ][:20]
        # "The suite could not run" is NOT "the suite failed". Production
        # builds failed three rework rounds with detail "suite is red" and
        # ZERO findings because the image had no pytest: a missing test
        # runner masquerading as bad generated code, which sent the agent
        # back to rewrite working handlers. Name the real cause instead.
        # Typed, not phrased: pytest absent from the interpreter, or pytest
        # never wrote its report and named no failing test -- it did not run.
        cannot_run = not _pytest_available() or (not findings and not report_written)
        if cannot_run:
            return GateResult(
                ok=False,
                gate=gate_name,
                reason="suite_could_not_run",
                detail=(
                    "the suite could not be RUN (test runner unavailable or "
                    "collection failed) — this is a build-environment fault, "
                    "not a failing test"
                ),
                findings=[ln for ln in output if ln.strip()][-8:]
                or ["pytest produced no output"],
                payload={"returncode": proc.returncode, "infrastructure": True},
            )
        classified_findings = findings or [ln for ln in output if ln.strip()][-8:]
        return GateResult(
            ok=False,
            gate=gate_name,
            reason="suite_red",
            detail=classify_suite_red(classified_findings, raw),
            # Never report a failure with nothing to act on: fall back to the
            # output tail so a rework round has something concrete.
            findings=classified_findings,
            payload={
                "returncode": proc.returncode,
                "assertion_classes": suite_assertion_classes(
                    classified_findings, raw
                ),
            },
        )
    return GateResult(
        ok=True,
        gate=gate_name,
        detail=output[-1].strip() if output else "suite passed",
    )


def gate_tester_contract(ctx: GateContext) -> GateResult:
    """TESTER: the code-phase suite, or -- on the pilot cycle -- PRODUCT.

    OWNER'S RULING 1, 2026-09-01 (FINDING 3). The code-phase gate runs
    ``pytest -m "not pilot"``, and ``@pytest.mark.pilot`` is the marker on
    the only tests that exercise a business action. So "all phase gates
    passed" meant "everything except the tests that check the product works
    passed", and residential-lettings shipped a booting 216-file zip that
    could not persist one record with every gate green.

    The code-phase gate is UNCHANGED: a 20-30 minute coder pass is not where
    a product is judged. What changes is that the pilot cycle's TESTER phase
    is now the PRODUCT gate -- the pilot-marked suite against the booted
    product, plus a one-record round-trip per capability (R1e) -- rather
    than the same suite runner with a different marker.
    """
    from app.factory.build.brief_gates import SUITE_CHECK, declare_check
    from app.factory.build.product_gate import gate_product

    if (ctx.cycle or "code").strip().lower() == "pilot":
        return gate_product(ctx)
    # The verdict names the brief check it measures, so the runner can tell
    # a failure the brief defined from one the Factory invented.
    return declare_check(gate_suite_green(ctx), SUITE_CHECK)


def gate_store_ops_authorised(ctx: GateContext) -> GateResult:
    """STORE_MANAGER: nothing was published without passing its op gate.

    The authority model lives in app.factory.store_manager; this gate only
    asserts the runner recorded a decision for every op, so an unrecorded
    publish cannot pass as an authorised one.

    Code-phase 5/5 still accepts an empty register (historical: the role
    applied no op). The pilot cycle requires at least one authorised op
    (local ``STORE_READ`` of the clone register counts when the Store URL
    is unset).
    """
    if ctx.cycle != "pilot":
        return GateResult(
            ok=True,
            gate="store_ops_authorised",
            detail="no store ops applied",
        )
    if not ctx.store_ops:
        return GateResult(
            ok=False,
            gate="store_ops_authorised",
            reason="no_store_ops_recorded",
            detail="pilot cycle recorded no store ops",
            findings=["STORE_MANAGER applied no store op"],
        )
    detail = f"applied {len(ctx.store_ops)} store op(s)"
    if ctx.store_unwired:
        detail += "; store unwired (CEREBRUM_API_URL unset) — local clone-register reads only"
    return GateResult(
        ok=True,
        gate="store_ops_authorised",
        detail=detail,
        payload={
            "store_ops": list(ctx.store_ops),
            "store_unwired": ctx.store_unwired,
        },
    )


def gate_cloner_contract(ctx: GateContext) -> GateResult:
    """CLONER: blocks import offline *and* match their published digests.

    Import proves the clone runs; integrity proves it is the clone it claims
    to be. A vendored tree that imports cleanly but no longer matches the
    block's own manifest is a stale mirror or a tampered copy, and neither
    is visible from an import check.
    """
    imported = gate_blocks_import_offline(ctx)
    if not imported.ok:
        return imported
    integrity = gate_vendored_integrity(ctx)
    if not integrity.ok:
        return integrity
    return GateResult(
        ok=True,
        gate="cloner_contract",
        detail=f"{imported.detail}; {integrity.detail}",
        payload=dict(integrity.payload),
    )


def gate_writer_contract(ctx: GateContext) -> GateResult:
    """WRITER: the workspace parses *and* fails closed when a block fails.

    Compilation alone was the whole WRITER gate, so a route that discarded
    its handler's result and persisted anyway passed every phase and reached
    the customer. Syntax first because it is cheap and its failure mode is
    clearer; behaviour second because that is the claim worth checking.

    Before any of that: the artifact gate (0.5). Zero agent-authored
    artifacts -- counted from the workspace files the writer actually
    produced, never from the writer's own status claim -- refuses with
    ``writer_no_output``. A workspace that parses and behaves but was
    written entirely by the deterministic template is the hollow pass the
    gate exists to stop.
    """
    from app.factory.build import factory_owned
    from app.factory.build.authorship import agent_written_handler_ids_in_workspace
    WRITER_NO_OUTPUT = "writer_no_output"

    touched = factory_owned.recorded(ctx.workspace)
    if touched:
        return GateResult(
            ok=False,
            gate=WRITER_CONTRACT_CHECK,
            reason=factory_owned.WRITER_AUTHORED,
            detail=(
                f"{factory_owned.WRITER_AUTHORED}: the writer touched "
                + ", ".join(row["path"] for row in touched)
                + " -- Factory-owned, restored to the Factory's version"
            ),
            findings=factory_owned.rework_findings(touched),
            payload={"touched": touched, "finding_shape": factory_owned.finding_shape(touched)},
        )

    agent_written = agent_written_handler_ids_in_workspace(ctx.workspace)
    if not agent_written:
        return GateResult(
            ok=False,
            gate=WRITER_CONTRACT_CHECK,
            reason="writer_no_output",
            detail=(
                f"{WRITER_NO_OUTPUT}: zero agent-authored artifacts in the "
                "workspace (no coding-agent-stamped handler in "
                "app/actions/*.py); the deterministic template path is not "
                "a governed product"
            ),
            findings=[WRITER_NO_OUTPUT],
            payload={"agent_written": 0},
        )
    compiled = gate_workspace_compiles(ctx)
    if not compiled.ok:
        return compiled
    behaviour = gate_writer_behaviour(ctx)
    if not behaviour.ok:
        return behaviour
    surface = gate_ui_surface(ctx)
    if not surface.ok:
        return surface
    # gate_ui_surface checks the files exist. This one checks they work: the
    # served UI drives more than one capability, every route it calls answers,
    # the formulas ship reachable, an answer carries its authority label, and
    # the pilot serves ONE UI rather than a live console beside dead source.
    from app.factory.build.ui_e2e import gate_ui_end_to_end

    ui_live = gate_ui_end_to_end(ctx)
    if not ui_live.ok:
        return ui_live
    # A platform that computes money may not invent the country it computes
    # for. The writer prompt says so; this is the check behind it, because an
    # instruction the factory does not verify is a suggestion. FinOps
    # (sess_065fc3eac75c4f62) hardcoded UK VAT and GBP for a Dubai business
    # and passed 13/13 -- no gate had ever read a tax rate.
    # The locale is the user's typed answer (Floor -> blueprint.locale ->
    # docs/declared_locale.json); the product's models declare which fields
    # are money. VETO on a literal code or rate; WITHHELD when money exists
    # but no currency was declared -- never a frozen guess.
    from app.factory.build.money_contract import MONEY_ASSUMED, money_verdict

    money = money_verdict(ctx.workspace)
    if money.status == "FAIL":
        return GateResult(
            ok=False,
            gate=WRITER_CONTRACT_CHECK,
            reason=MONEY_ASSUMED,
            detail=f"{MONEY_ASSUMED}: {money.findings[0]}",
            findings=list(money.findings),
            payload={"money_contract": "FAIL", "money_findings": len(money.findings)},
        )
    # The image the product's Dockerfile builds must carry what the app loads
    # at runtime (live: cycle 2 smoke B shipped an image with no vendor/ and
    # a gate that judged the checkout certified it). Emulated -- the live
    # Factory has no Docker -- from the Dockerfile's COPY lines and
    # .dockerignore; the writer hears it here, before any gate dispatch.
    from app.factory.build.image_sufficiency import check as image_check

    image = image_check(ctx.workspace)
    if image.misloaded:
        return GateResult(
            ok=False,
            gate=WRITER_CONTRACT_CHECK,
            reason="locked_block_loaded_elsewhere",
            detail=f"locked_block_loaded_elsewhere: {image.detail}",
            findings=[
                f"{line}: a block listed in blocks.lock.json must load from its locked path or fail "
                "loudly at start-up, never fall back silently"
                for line in image.misloaded
            ],
            payload={"misloaded": list(image.misloaded)},
        )
    if not image.ok:
        return GateResult(
            ok=False,
            gate=WRITER_CONTRACT_CHECK,
            reason="image_missing_runtime_path",
            detail=f"image_missing_runtime_path: {image.detail}",
            findings=[
                f"image missing {path}: the Dockerfile does not put it in the image, but the app loads it at runtime"
                + (f" -- {image.causes[path]}" if image.causes.get(path) else "")
                for path in image.missing
            ] or [image.detail],
            payload={"image_missing": list(image.missing)},
        )
    money_line = (
        f"money_contract WITHHELD({money.reason})"
        if money.status == "WITHHELD"
        else f"money_contract {money.status}"
    )
    return GateResult(
        ok=True,
        gate=WRITER_CONTRACT_CHECK,
        detail=f"{compiled.detail}; {behaviour.detail}; {surface.detail}; {money_line}; {image.detail}",
        findings=list(behaviour.findings),
        payload={
            **dict(behaviour.payload),
            "agent_written": len(agent_written),
            "money_contract": money.status,
            "money_reason": money.reason,
            "image_judged": image.judged,
        },
    )


def gate_provenance_complete(ctx: GateContext) -> GateResult:
    """The delivered product can say which Factory and which Store made it.

    Every export shipped ``factory_commit`` and ``blocks_commit`` as
    "unknown": ``converge`` read them from ``ctx.state`` and nothing ever put
    them there. A buyer's IT team opening the zip found two fields that
    answer "which code made this?" with "no idea", and a build that
    misbehaves cannot be traced to the code that produced it.

    Judged on the artifact, not the process -- this reads the provenance
    document the product actually ships.
    """
    from app.factory.build.build_provenance import missing_provenance

    rel = Path("docs") / "provenance" / "provenance.json"
    path = Path(ctx.workspace) / rel
    if not path.is_file():
        return GateResult(
            ok=False,
            gate="provenance_complete",
            reason="provenance_missing",
            detail=f"the product ships no {rel.as_posix()}",
            findings=[f"write {rel.as_posix()}"],
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return GateResult(
            ok=False,
            gate="provenance_complete",
            reason="provenance_unreadable",
            detail=f"{rel.as_posix()}: {exc}",
        )
    missing = missing_provenance(payload)
    if missing:
        return GateResult(
            ok=False,
            gate="provenance_complete",
            reason="provenance_unknown",
            detail=(
                "the product cannot say what produced it: "
                + ", ".join(f"{f}=unknown" for f in missing)
            ),
            findings=list(missing),
        )
    return GateResult(
        ok=True,
        gate="provenance_complete",
        detail="factory_commit and blocks_commit both resolved",
        payload={f: str(payload.get(f)) for f in ("factory_commit", "blocks_commit")},
    )


def gate_store_manager_contract(ctx: GateContext) -> GateResult:
    """STORE_MANAGER: store ops authorised, and on pilot, data that survives.

    This is the phase whose SUCCESS makes ``pilot_ready`` true, so it is the
    last place a durability claim can be checked before the flag is emitted.
    On the code cycle the durability gate is a no-op.
    """
    authorised = gate_store_ops_authorised(ctx)
    if not authorised.ok:
        return authorised
    durable = gate_pilot_outcome_survives_restart(ctx)
    if not durable.ok:
        return durable
    traceable = gate_provenance_complete(ctx)
    if not traceable.ok:
        return traceable
    payload = dict(authorised.payload)
    payload["provenance"] = dict(traceable.payload)
    if (ctx.cycle or "code").strip().lower() == "pilot":
        from app.factory.build.store_acceptance import gate_store_acceptance

        accept = gate_store_acceptance(ctx)
        payload["acceptance"] = dict(accept.payload)
        if not accept.ok:
            return GateResult(
                ok=False,
                gate="store_manager_contract",
                reason=accept.reason or "store_acceptance_failed",
                detail=accept.detail,
                findings=list(accept.findings),
                payload=payload,
            )
        return GateResult(
            ok=True,
            gate="store_manager_contract",
            detail=f"{authorised.detail}; {durable.detail}; {accept.detail}",
            payload=payload,
        )
    return GateResult(
        ok=True,
        gate="store_manager_contract",
        detail=f"{authorised.detail}; {durable.detail}",
        payload=payload,
    )


GATES: Mapping[BuildRole, Gate] = {
    BuildRole.COLLECTOR: gate_gaps_enumerated,
    BuildRole.CLONER: gate_cloner_contract,
    BuildRole.WRITER: gate_writer_contract,
    BuildRole.TESTER: gate_tester_contract,
    BuildRole.STORE_MANAGER: gate_store_manager_contract,
}


def gate_for(role: BuildRole | str) -> Gate:
    return GATES[BuildRole(role)]
