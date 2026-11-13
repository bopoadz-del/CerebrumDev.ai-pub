"""F-S2a: the writer's tool policy for pytest -- a mechanism, not a prompt line.

The writer prompt already says "run only the targeted tests"; live passes
still spent their wall on full-suite runs (F-S1 ``suite_runs=``). A prompt
line is not a mechanism. This module stamps a pytest plugin into the
workspace (``POLICY_DIR_REL``) for the duration of ONE writer pass.

F-S2a-2: the first version loaded the plugin through the writer's env
(``PYTEST_ADDOPTS=-p``, ``PYTHONPATH`` and a root variable). The writer CLI's
shell tool rebuilds its children's environment from a fixed allowlist
(PATH, HOME, locale, proxies ...), so all three were dropped and the plugin
never loaded: pilot 43469565180 read ``suite_runs=20 suite_denied=0``. No env
is relied on now. The Factory's rootdir test bootstrap (``conftest.py``,
Factory-owned, which pytest loads for every run under the workspace) carries
:data:`CONFTEST_LOADER`, which loads the plugin from the stamped file when
it exists. The Factory removes the stamp when the pass ends, so its own gate,
TESTER and after-pass suite runs never carry the policy.

The plugin judges each invocation with the SAME rule F-S1 counts with
(``is_suite_run`` against the self-check's targeted set at that moment,
both rendered from their own source, never a copy that can drift). A run
that is not the targeted set is refused with a usage error that names the
targeted set, and the refusal is appended to ``denials.jsonl`` beside the
plugin; every invocation the plugin judged is appended to ``seen.jsonl``.
The worker reads both after the pass: ``suite_denied=`` and ``policy=`` on
the receipt. A refusal is never silent, and neither is a policy that did not
load (``policy=unbound``: pytest ran and the plugin saw none of it).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Workspace-relative directory the plugin is stamped into for one pass.
#: Factory-internal: removed when the pass ends and never exported.
POLICY_DIR_REL = ".factory/pytest_policy"
#: Module name ``-p`` loads; the file is ``<name>.py`` in the policy dir.
PLUGIN_MODULE = "factory_pytest_policy"
#: The Factory-rendered self-check source the plugin reads its targeted set from.
SELF_CHECK_COPY = "factory_self_check_source.py"
#: One JSON line per refused invocation.
DENIALS_FILE = "denials.jsonl"
#: One JSON line per invocation the plugin judged (refused or not).
SEEN_FILE = "seen.jsonl"
#: The marker line the root bootstrap's loader carries.
LOADER_MARKER = "# factory pytest policy loader (F-S2a-2)"

#: Appended to the Factory's rootdir bootstrap (roles_constants._CONFTEST).
#: A no-op unless the plugin file is stamped, i.e. only during a writer pass.
#: It reads no environment variable: the writer CLI's shell strips them.
CONFTEST_LOADER = (
    "\n\n"
    + LOADER_MARKER
    + """
# The writer's pytest runs are judged by the Factory's policy while a writer
# pass is in progress (the plugin file exists only then). The Factory's own
# gate and TESTER runs happen after the stamp is removed: this is a no-op.
_FACTORY_POLICY_PLUGIN = Path(__file__).resolve().parent.joinpath(
    *{dir_parts!r}, {module!r} + ".py"
)
if _FACTORY_POLICY_PLUGIN.is_file():
    import importlib.util as _factory_policy_util

    _factory_policy_spec = _factory_policy_util.spec_from_file_location(
        {module!r}, _FACTORY_POLICY_PLUGIN
    )
    _factory_policy = _factory_policy_util.module_from_spec(_factory_policy_spec)
    _factory_policy_spec.loader.exec_module(_factory_policy)
    pytest_cmdline_main = _factory_policy.pytest_cmdline_main
""".format(dir_parts=tuple(POLICY_DIR_REL.split("/")), module=PLUGIN_MODULE)
)

_PLUGIN_HEADER = '''"""Factory pytest policy -- Factory-owned, re-stamped on every writer pass.

A pytest run that is not the targeted set for the files this pass changed
is refused: the code-phase gate and TESTER own the suites, and the Factory
runs them itself. Run the targeted set (``python {self_check}`` runs it).
"""

import json
import os
import time
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
SELF_CHECK_REL = {self_check!r}
SELF_CHECK_COPY = {copy!r}
DENIALS_FILE = {denials!r}
SEEN_FILE = {seen!r}
#: The workspace root: this file sits at <root>/{policy_dir}/.
ROOT = str(_HERE.parents[{depth}])
'''

_PLUGIN_BODY = '''

def _targeted(root):
    """The self-check's targeted set now; None when it cannot be computed."""
    try:
        namespace = {
            "__name__": "factory_policy_targeted",
            "__file__": os.path.join(root, SELF_CHECK_REL),
        }
        source = (_HERE / SELF_CHECK_COPY).read_text(encoding="utf-8")
        exec(compile(source, SELF_CHECK_REL, "exec"), namespace)
        changed = namespace["_changed_sources"](root)
        return list(
            namespace["select_targeted_tests"](
                changed, namespace["_test_files"](root)
            )
        )
    except Exception:
        return None


def _selection(config, root):
    """The paths this invocation selects, root-relative; [] = no explicit path."""
    source = getattr(config, "args_source", None)
    if source is not None and source != pytest.Config.ArgsSource.ARGS:
        return []
    base = str(config.invocation_params.dir)
    out = []
    for arg in config.args:
        path, sep, rest = str(arg).partition("::")
        full = os.path.normpath(os.path.join(base, path))
        rel = os.path.relpath(full, root).replace(os.sep, "/")
        out.append(rel + sep + rest)
    return out


def _record(name, entry):
    try:
        with open(_HERE / name, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\\n")
    except OSError:
        pass


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    if config.getoption("version", False) or config.getoption("help", False):
        return None
    root = ROOT
    selection = _selection(config, root)
    targeted = _targeted(root)
    refused = is_suite_run(selection, targeted)
    entry = {
        "t": time.time(),
        "selection": selection,
        "targeted": targeted,
    }
    _record(SEEN_FILE, dict(entry, refused=refused))
    if not refused:
        return None
    _record(DENIALS_FILE, entry)
    named = ", ".join(targeted or []) or "none (no changed source has a test yet)"
    raise pytest.UsageError(
        "factory policy: this pytest run is not the targeted set and is "
        "refused (the code-phase gate and TESTER run the suites). "
        f"Targeted set now: {named}"
    )
'''


def _render_rule() -> str:
    """``_norm_test_path`` and ``is_suite_run`` rendered from their own source."""
    import ast
    import inspect
    import textwrap

    from app.factory.build import codewhale_worker

    class _Unannotate(ast.NodeTransformer):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
            self.generic_visit(node)
            node.returns = None
            for arg in (*node.args.args, *node.args.kwonlyargs):
                arg.annotation = None
            return node

    lines: List[str] = []
    for fn in (codewhale_worker._norm_test_path, codewhale_worker.is_suite_run):
        tree = _Unannotate().visit(ast.parse(textwrap.dedent(inspect.getsource(fn))))
        lines += ["", "", ast.unparse(ast.fix_missing_locations(tree))]
    return "\n".join(lines)


def render_plugin() -> str:
    from app.factory.build.writer_behaviour import SELF_CHECK_REL

    return (
        _PLUGIN_HEADER.format(
            self_check=SELF_CHECK_REL,
            copy=SELF_CHECK_COPY,
            denials=DENIALS_FILE,
            seen=SEEN_FILE,
            policy_dir=POLICY_DIR_REL,
            depth=len(POLICY_DIR_REL.split("/")) - 1,
        )
        + _render_rule()
        + "\n"
        + _PLUGIN_BODY
    )


def policy_dir(root: Path | str) -> Path:
    """Where one pass's plugin and logs live in the workspace."""
    return Path(root).joinpath(*POLICY_DIR_REL.split("/"))


def stamp_policy(root: Path | str) -> Path:
    """Stamp the plugin into the workspace for one writer pass; return its dir.

    Fresh denial and seen logs per pass: the receipt counts this pass only.
    Raises OSError when the directory cannot be written (the worker names it).
    """
    from app.factory.build.writer_behaviour import render_self_check

    target = policy_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{PLUGIN_MODULE}.py").write_text(render_plugin(), encoding="utf-8")
    (target / SELF_CHECK_COPY).write_text(render_self_check(), encoding="utf-8")
    (target / DENIALS_FILE).write_text("", encoding="utf-8")
    (target / SEEN_FILE).write_text("", encoding="utf-8")
    return target


def remove_policy(root: Path | str) -> None:
    """Drop the stamp when the pass ends: the Factory's own runs never carry it."""
    import shutil

    shutil.rmtree(policy_dir(root), ignore_errors=True)


def loader_present(root: Path | str) -> Optional[bool]:
    """Whether the rootdir bootstrap loads the policy; None when it is absent."""
    from app.factory.build.roles_constants import CONFTEST_REL

    try:
        text = (Path(root) / CONFTEST_REL).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return False
    return LOADER_MARKER in text


def _read_log(root: Path | str, name: str) -> Optional[List[Dict[str, Any]]]:
    try:
        text = (policy_dir(root) / name).read_text(encoding="utf-8")
    except OSError:
        return None
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            entry = {"unparsed": line[:200]}
        out.append(entry if isinstance(entry, dict) else {"unparsed": line[:200]})
    return out


def read_denials(root: Path | str) -> Optional[List[Dict[str, Any]]]:
    """This pass's refused invocations; None when the file cannot be read."""
    return _read_log(root, DENIALS_FILE)


def read_seen(root: Path | str) -> Optional[List[Dict[str, Any]]]:
    """Every invocation the plugin judged this pass; None when unreadable."""
    return _read_log(root, SEEN_FILE)


POLICY_BOUND = "bound"
POLICY_UNBOUND = "unbound"
POLICY_IDLE = "idle"


def policy_binding(
    pytest_runs: int, seen: Optional[List[Dict[str, Any]]]
) -> Optional[str]:
    """``bound`` when the plugin judged a run; ``unbound`` when the writer ran
    pytest and the plugin saw none of it; ``idle`` when no pytest ran; None
    (printed ``-``) when the seen log could not be read."""
    if seen is None:
        return None
    if seen:
        return POLICY_BOUND
    return POLICY_UNBOUND if pytest_runs > 0 else POLICY_IDLE


#: Findings kept on the ledger record; the full list stays with the gate.
SUITE_FINDINGS_KEPT = 10


def factory_suite_after_pass(
    root: Path | str,
    *,
    runner: Optional[Any] = None,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """F-S2b: the Factory runs the full suite ONCE after a writer pass.

    The policy (above) refuses the writer's own full-suite runs; this is the
    run the writer no longer makes. It happens after the writer's session has
    ended, so it is outside the model's wall, and it runs the SAME suite
    runner the code-phase gate uses (``gate_suite_green``: JUnit-read
    verdict, the Factory's environment, no policy env) on the tree the writer
    worked in. Telemetry only: the record goes to the ledger, and nothing here
    passes or fails a phase -- the gates are unchanged. A runner that raises
    is recorded as ``error``, never as green.
    """
    import time

    from app.factory.build.authority import BuildRole
    from app.factory.build.gates import DEFAULT_GATE_TIMEOUT_S, GateContext, gate_suite_green

    ctx = GateContext(
        workspace=Path(root),
        role=BuildRole.TESTER,
        runner=runner,
        timeout_s=DEFAULT_GATE_TIMEOUT_S if timeout_s is None else float(timeout_s),
    )
    started = time.monotonic()
    try:
        result = gate_suite_green(ctx)
    except Exception as exc:  # noqa: BLE001 -- recorded, never green
        return {
            "outcome": "error",
            "reason": type(exc).__name__,
            "detail": str(exc)[:300],
            "failures": None,
            "findings": [],
            "elapsed_s": round(time.monotonic() - started, 1),
        }
    findings = [str(f) for f in (result.findings or [])]
    return {
        "outcome": "green" if result.ok else (result.reason or "failed"),
        "reason": result.reason or "",
        "detail": str(result.detail or "")[:300],
        "failures": 0 if result.ok else len(findings),
        "findings": findings[:SUITE_FINDINGS_KEPT],
        "elapsed_s": round(time.monotonic() - started, 1),
    }


def factory_suite_line(record: Dict[str, Any]) -> str:
    """The ledger line: ``suite=`` outcome, ``failures=`` (``-`` = not counted)."""
    failures = record.get("failures")
    return (
        f"factory suite after pass: suite={record.get('outcome', '-')} "
        f"failures={'-' if failures is None else failures} "
        f"elapsed_s={record.get('elapsed_s', '-')}"
        + (f" -- {record['detail']}" if record.get("detail") else "")
    )
