"""F-S2a: the writer's tool policy for pytest -- a mechanism, not a prompt line.

The writer prompt already says "run only the targeted tests"; live passes
still spent their wall on full-suite runs (F-S1 ``suite_runs=``). A prompt
line is not a mechanism. This module stamps a pytest plugin into the
writer's CLI home and loads it into every pytest the writer's shell starts
(``PYTEST_ADDOPTS=-p <plugin>`` on the writer's child environment only --
the Factory's own gate and TESTER runs never carry it).

The plugin judges each invocation with the SAME rule F-S1 counts with
(``is_suite_run`` against the self-check's targeted set at that moment,
both rendered from their own source, never a copy that can drift). A run
that is not the targeted set is refused with a usage error that names the
targeted set, and the refusal is appended to ``denials.jsonl`` beside the
plugin; the worker reads that file after the pass and reports
``suite_denied=`` on the receipt. A refusal is never silent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Directory (under the writer's CLI home) the plugin is stamped into.
POLICY_DIR_NAME = "pytest_policy"
#: Module name ``-p`` loads; the file is ``<name>.py`` in the policy dir.
PLUGIN_MODULE = "factory_pytest_policy"
#: The Factory-rendered self-check source the plugin reads its targeted set from.
SELF_CHECK_COPY = "factory_self_check_source.py"
#: One JSON line per refused invocation.
DENIALS_FILE = "denials.jsonl"
#: The workspace root the targeted set is computed from.
ROOT_ENV = "FACTORY_PYTEST_POLICY_ROOT"

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
ROOT_ENV = {root_env!r}
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


def _record(entry):
    try:
        with open(_HERE / DENIALS_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\\n")
    except OSError:
        pass


@pytest.hookimpl(tryfirst=True)
def pytest_cmdline_main(config):
    root = os.environ.get(ROOT_ENV)
    if not root:
        return None
    if config.getoption("version", False) or config.getoption("help", False):
        return None
    selection = _selection(config, root)
    targeted = _targeted(root)
    if not is_suite_run(selection, targeted):
        return None
    _record(
        {
            "t": time.time(),
            "selection": selection,
            "targeted": targeted,
        }
    )
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
            root_env=ROOT_ENV,
        )
        + _render_rule()
        + "\n"
        + _PLUGIN_BODY
    )


def stamp_policy(cli_home: Path | str, root: Path | str) -> Dict[str, str]:
    """Stamp the plugin for one writer pass; return the child-env entries.

    A fresh denials file per pass: the receipt counts this pass's refusals.
    """
    from app.factory.build.writer_behaviour import render_self_check

    policy_dir = Path(cli_home) / POLICY_DIR_NAME
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / f"{PLUGIN_MODULE}.py").write_text(render_plugin(), encoding="utf-8")
    (policy_dir / SELF_CHECK_COPY).write_text(render_self_check(), encoding="utf-8")
    (policy_dir / DENIALS_FILE).write_text("", encoding="utf-8")
    return {
        "PYTHONPATH": str(policy_dir),
        "PYTEST_ADDOPTS": f"-p {PLUGIN_MODULE}",
        ROOT_ENV: str(Path(root)),
    }


def read_denials(cli_home: Path | str) -> Optional[List[Dict[str, Any]]]:
    """This pass's refused invocations; None when the file cannot be read."""
    path = Path(cli_home) / POLICY_DIR_NAME / DENIALS_FILE
    try:
        text = path.read_text(encoding="utf-8")
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
