"""The three contract gates must be shown to say NO.

Their parts are tested elsewhere. The composites were not, and a composite
that forgets to propagate a failing part is exactly the defect the roster
change was made to prevent: gate_writer_contract exists because compilation
alone passed a route that discarded its handler's result.

scripts/audit_gates.py fails CI without these, so the absence cannot recur
quietly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.gates import (
    GateContext,
    gate_cloner_contract,
    gate_store_manager_contract,
    gate_writer_contract,
)


def _runner(argv, *, cwd, timeout):
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )


def _ctx(workspace: Path, role: BuildRole, **kw) -> GateContext:
    return GateContext(workspace=workspace, role=role, runner=_runner, **kw)


# -- STORE_MANAGER --------------------------------------------------------


def test_store_manager_contract_refuses_a_pilot_with_no_store_ops(tmp_path):
    """A pilot cycle that recorded no store op has not proven authorisation."""
    result = gate_store_manager_contract(
        _ctx(tmp_path, BuildRole.STORE_MANAGER, cycle="pilot", store_ops=())
    )
    assert result.ok is False
    assert "no store ops" in result.detail


def test_store_manager_contract_propagates_the_failing_part(tmp_path):
    """The composite must return the part's verdict, not its own summary."""
    result = gate_store_manager_contract(
        _ctx(tmp_path, BuildRole.STORE_MANAGER, cycle="pilot", store_ops=())
    )
    assert result.gate == "store_ops_authorised", (
        "the composite reported its own name for a failure raised by a part, "
        "which hides which check actually refused"
    )


# -- CLONER ---------------------------------------------------------------


def test_cloner_contract_refuses_a_block_that_is_not_vendored(tmp_path):
    """Import proves the clone runs. A named block that is absent cannot."""
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    result = gate_cloner_contract(
        _ctx(tmp_path, BuildRole.CLONER, vendored_blocks=("no_such_block",))
    )
    assert result.ok is False


# -- WRITER ---------------------------------------------------------------


def test_writer_contract_refuses_a_workspace_that_does_not_parse(tmp_path):
    """Syntax first, because its failure mode is the clearest."""
    app = tmp_path / "app"
    (app / "actions").mkdir(parents=True, exist_ok=True)
    (app / "actions" / "widget_intake.py").write_text(
        '"""Handler for capability widget_intake.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n',
        encoding="utf-8",
    )
    (app / "routes.py").write_text("def broken(:\n", encoding="utf-8")
    result = gate_writer_contract(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is False
    assert "writer_no_output" not in result.detail


def test_writer_contract_does_not_stop_at_compilation(tmp_path):
    """The reason this gate replaced gate_workspace_compiles.

    A workspace that parses cleanly must still not pass on syntax alone --
    if it does, the behaviour and UI-surface halves are unreachable and the
    composite is gate_workspace_compiles under a new name.
    """
    app = tmp_path / "app"
    (app / "actions").mkdir(parents=True, exist_ok=True)
    (app / "actions" / "widget_intake.py").write_text(
        '"""Handler for capability widget_intake.\n\n'
        'Written by the factory WRITER role (coder LLM). Blocks are invoked '
        'through\n'
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        'AUTHORED_BY = "coder LLM"\n',
        encoding="utf-8",
    )
    (app / "routes.py").write_text("x = 1\n", encoding="utf-8")
    result = gate_writer_contract(_ctx(tmp_path, BuildRole.WRITER))
    assert result.ok is False, (
        "a workspace containing nothing but a valid assignment passed the "
        "WRITER contract; only the compile half can be running"
    )
    assert "writer_no_output" not in result.detail
    assert result.gate != "workspace_compiles" or "compile" not in result.detail.lower()
