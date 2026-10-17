"""Phase 0.5 acceptance: zero agent-authored artifacts fails closed.

The artifact gate: a writer that produces nothing can never pass the WRITER
contract, hand off to N3, meet the authorship floor, or grade Store-green.
Every refusal carries the named reason ``writer_no_output``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.authorship import full_pilot_authorship_from
from app.factory.build.gates import GateContext, GateResult, gate_writer_contract
from app.factory.build.level_grade import Level, grade_workspace


def _stamped_handler(actions: Path, cid: str, source: str = "coder LLM") -> None:
    actions.mkdir(parents=True, exist_ok=True)
    (actions / f"{cid}.py").write_text(
        f'"""Handler for capability {cid}.\n\n'
        f"Written by the factory WRITER role ({source}). Blocks are invoked "
        "through\n"
        'the local dispatch runtime -- this module makes no network call.\n'
        '"""\n'
        f'AUTHORED_BY = "{source}"\n',
        encoding="utf-8",
    )


# -- T0.5.1: the WRITER contract gate refuses zero artifacts -----------------


def test_zero_agent_artifacts_refuses_the_writer_contract(tmp_path):
    """Templated path, artifacts==0 -> RED with writer_no_output."""
    result = gate_writer_contract(
        GateContext(workspace=tmp_path, role=BuildRole.WRITER)
    )
    assert result.ok is False
    assert "writer_no_output" in result.detail
    assert "writer_no_output" in result.findings


def test_zero_check_fires_before_compilation(tmp_path):
    """A hollow workspace fails for no-output, not for its syntax."""
    app = tmp_path / "app"
    app.mkdir()
    (app / "routes.py").write_text("def broken(:\n", encoding="utf-8")
    result = gate_writer_contract(
        GateContext(workspace=tmp_path, role=BuildRole.WRITER)
    )
    assert result.ok is False
    assert "writer_no_output" in result.detail


def test_agent_stamp_skips_the_zero_check(tmp_path):
    """Control: with an agent-stamped handler the refusal is not blanket."""
    app = tmp_path / "app"
    _stamped_handler(app / "actions", "widget_intake")
    (app / "routes.py").write_text("def broken(:\n", encoding="utf-8")
    result = gate_writer_contract(
        GateContext(workspace=tmp_path, role=BuildRole.WRITER)
    )
    assert result.ok is False
    assert "writer_no_output" not in result.detail


def test_templated_stamp_does_not_count_as_agent_output(tmp_path):
    """Factory templates are not agent artifacts and must not open the gate."""
    app = tmp_path / "app"
    _stamped_handler(
        app / "actions", "widget_intake", source="deterministic contract template"
    )
    result = gate_writer_contract(
        GateContext(workspace=tmp_path, role=BuildRole.WRITER)
    )
    assert result.ok is False
    assert "writer_no_output" in result.detail


# -- T0.5.4: unmeasured is below floor; measured-and-meets stays green --------


def test_unmeasured_authorship_is_below_floor():
    floor = full_pilot_authorship_from({})
    assert floor.measured is False
    assert floor.below_floor is True


def test_measured_and_meets_floor_stays_green():
    """Safety: builds that are measured and meet the floor stay above it."""
    floor = full_pilot_authorship_from({"authorship": {"agent_written": 6}})
    assert floor.measured is True
    assert floor.meets_floor is True
    assert floor.below_floor is False


# -- T0.5.5: zero artifacts and Store-green can never co-render ---------------


def test_zero_artifacts_cannot_grade_store_green(tmp_path):
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        },
    )
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert any("authorship is below" in b for b in grade["blockers"])


# -- R4 mutation: the check must be a live instrument -------------------------


def test_zero_artifact_check_is_a_live_instrument(monkeypatch, tmp_path):
    """R4 mutation: removing the check's inputs flips the gate green.

    This is the control experiment for the zero-check, not a literal
    source deletion: the counter is patched to claim a phantom agent
    artifact and the compile / behaviour / surface halves are patched to
    pass. A hollow workspace then goes green -- proving the zero-check is
    exactly what reds it, since nothing else about the gate can.
    """
    from app.factory.build import gates as gates_mod

    def _ok(ctx):
        return GateResult(ok=True, gate="patched")

    monkeypatch.setattr(gates_mod, "gate_workspace_compiles", _ok)
    monkeypatch.setattr(gates_mod, "gate_writer_behaviour", _ok)
    monkeypatch.setattr(gates_mod, "gate_ui_surface", _ok)
    monkeypatch.setattr(
        "app.factory.build.authorship.agent_written_handler_ids_in_workspace",
        lambda workspace: ["phantom_agent_handler"],
    )
    result = gate_writer_contract(
        GateContext(workspace=tmp_path, role=BuildRole.WRITER)
    )
    assert result.ok is True


# -- P0: the standalone probe suite runs against the real modules -------------


def test_mutation_probes_script_passes():
    """scripts/mutation_probes.py exits 0 only when every P0 probe holds."""
    backend = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    proc = subprocess.run(
        [sys.executable, str(backend / "scripts" / "mutation_probes.py")],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
