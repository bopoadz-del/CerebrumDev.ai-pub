"""A killed phase must not leave a workspace that looks finished.

New-shape tests for the defect a killed live build exposed. The process died
during a WRITER rework round and left:

    models.py, store.py   from the killed pass   (table `defect`)
    routes.py, main.py    from an earlier pass   (store.save("field_defect"))

The platform booted and then answered
``sqlite3.OperationalError: no such table: field_defect``. The agent picks
different entity names on each call, so two partial passes do not compose.

Resume made it worse rather than catching it: WRITER's last *terminal* event
was GATE_PASSED from the previous round, so completed_roles() called it done
and resume_point() returned TESTER — which would test a torn app/. The
existing resume test only ever kills *between* phases, so it could not see
this.

Two guarantees are asserted here:
  * the destination is never touched until a pass completes, so a hard kill
    leaves the previous complete attempt;
  * a role whose last event is PHASE_STARTED is running, not complete.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import RoleRunner
from app.factory.build.workspace import RoleWorkspace

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


class _Killed(BaseException):
    """Stands in for a hard kill: not caught by the runner."""


# -- the ledger no longer calls an interrupted role complete ---------------


def test_a_role_that_started_and_never_finished_is_not_complete(tmp_path):
    led = BuildLedger(tmp_path / "l.jsonl")
    led.start_run(product_id="p", inputs_hash="h")

    led.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    led.append(EventKind.GATE_PASSED, role=BuildRole.WRITER)
    assert BuildRole.WRITER in led.completed_roles()

    # A rework sends it back round, and the process dies mid-pass.
    led.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)

    assert BuildRole.WRITER not in led.completed_roles(), (
        "a stale GATE_PASSED from an earlier attempt must not mask an "
        "interrupted pass"
    )
    assert led.interrupted_role() is BuildRole.WRITER


def test_resume_returns_the_interrupted_role_not_the_next_one(tmp_path):
    led = BuildLedger(tmp_path / "l.jsonl")
    led.start_run(product_id="p", inputs_hash="h")
    for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
        led.append(EventKind.PHASE_STARTED, role=role)
        led.append(EventKind.GATE_PASSED, role=role)
    led.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    led.append(EventKind.GATE_PASSED, role=BuildRole.WRITER)
    led.append(EventKind.PHASE_STARTED, role=BuildRole.TESTER)
    led.append(EventKind.GATE_FAILED, role=BuildRole.TESTER)
    led.append(EventKind.REWORK, role=BuildRole.WRITER)
    led.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)  # killed here

    assert led.resume_point() is BuildRole.WRITER
    assert led.terminal_event() is None


def test_a_completed_run_reports_no_interrupted_role(tmp_path):
    led = BuildLedger(tmp_path / "l.jsonl")
    for role in BuildRole:
        led.append(EventKind.PHASE_STARTED, role=role)
        led.append(EventKind.GATE_PASSED, role=role)
    assert led.interrupted_role() is None
    assert led.resume_point() is None


# -- the destination is untouched until a pass completes -------------------


def test_a_staged_workspace_writes_nothing_until_commit(tmp_path):
    dest = tmp_path / "product"
    dest.mkdir()
    ws = RoleWorkspace(
        BuildRole.WRITER, dest, staging=tmp_path / "stage"
    )
    ws.write_text("app/models.py", "STAGED = True\n")

    assert not (dest / "app" / "models.py").exists(), "staging leaked to destination"
    ws.commit()
    assert (dest / "app" / "models.py").read_text(encoding="utf-8") == "STAGED = True\n"


def test_a_staged_writer_can_still_read_what_earlier_roles_produced(tmp_path):
    """The WRITER reads the CLONER's vendored blocks from the destination."""
    dest = tmp_path / "product"
    (dest / "vendor" / "blocks" / "web").mkdir(parents=True)
    (dest / "vendor" / "blocks" / "web" / "block.json").write_text("{}", encoding="utf-8")

    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=tmp_path / "stage")
    assert ws.exists("vendor/blocks/web/block.json")
    assert ws.read_text("vendor/blocks/web/block.json") == "{}"


def test_a_kill_mid_writer_leaves_the_previous_pass_intact(
    blueprint, tmp_path, stub_coder
):
    """The whole point: no splice of two attempts on disk.

    A first build completes. A second run is then killed part-way through a
    WRITER pass. Every app/ file must still be from the completed build --
    same content, one consistent attempt.
    """
    out = tmp_path / "build"
    assert RoleRunner(blueprint, out).run().ok
    before = {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in (out / "app").rglob("*.py")
    }
    assert before, "the first build produced no app/"

    def dying_writer(ctx):
        # Write a plausible-looking partial pass, then die mid-way.
        ctx.workspace.write_text("app/models.py", "# torn\n")
        ctx.workspace.write_text("app/store.py", "# torn\n")
        raise _Killed("process killed mid-write")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = dying_writer

    # Force the WRITER to re-run by clearing its verdict from the ledger's view.
    led = BuildLedger(out / "build_ledger.jsonl")
    led.append(EventKind.REWORK, role=BuildRole.WRITER, detail="forced")
    led.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER)
    led.append(EventKind.GATE_FAILED, role=BuildRole.WRITER, detail="forced")

    with pytest.raises(_Killed):
        RoleRunner(blueprint, out, roles=roles).run()

    after = {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in (out / "app").rglob("*.py")
    }
    assert after == before, "a killed WRITER pass leaked into the destination"
    assert "# torn" not in (out / "app" / "models.py").read_text(encoding="utf-8")


def test_the_committed_build_is_self_consistent(blueprint, tmp_path, stub_coder):
    """Entity names in store.py and routes.py must come from one pass."""
    import re

    out = tmp_path / "build"
    assert RoleRunner(blueprint, out).run().ok

    mig_src = (out / "alembic" / "versions" / "0001_baseline.py").read_text(
        encoding="utf-8"
    )
    routes_src = (out / "app" / "routes.py").read_text(encoding="utf-8")
    from app.factory.build.data_lifecycle import migration_table_names

    tables = migration_table_names(mig_src)
    referenced = set(re.findall(r'store\.(?:save|list_all)\("(\w+)"', routes_src))

    assert referenced, "routes reference no tables"
    missing = referenced - tables
    assert not missing, f"routes reference tables the store never creates: {missing}"


def test_staging_directories_are_not_shipped(blueprint, tmp_path, stub_coder):
    out = tmp_path / "build"
    assert RoleRunner(blueprint, out).run().ok
    leftovers = [p.name for p in out.parent.iterdir() if ".staging-" in p.name]
    assert not leftovers, f"staging directory left behind: {leftovers}"
