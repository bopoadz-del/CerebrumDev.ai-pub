"""The emitted suite must not forbid the agent from adding a migration.

A generated platform is extendable by construction: the coding agent
authors schema for the capabilities it writes. The emitted data-lifecycle
suite used to assert ``upgrade_head() == "0002_lifecycle_audit"`` -- an
absolute head pin -- so the first agent-authored revision turned TESTER
red with ``assert '0003_...' == '0002_lifecycle_audit'`` even though the
product was correct. The suite now upgrades to V2 explicitly, which is
what that test was ever about: the v1 -> v2 transition over populated
data.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.data_lifecycle import REVISION_0002, render_product_tests
from app.factory.build.runner import RoleRunner
from tests.factory.conftest import stub_coder_patches

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

AGENT_REVISION = "0003_agent_capability"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path_factory.mktemp("agentmig") / "build"
    with stub_coder_patches():
        outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    return out


def test_emitted_lifecycle_suite_does_not_pin_the_absolute_head():
    """The source of the pin, asserted directly.

    Guards the regression at the emitter so it cannot come back in a
    form the slower end-to-end test below would take minutes to catch.
    """
    src = render_product_tests(
        {
            "booking": {
                "entity": "booking",
                "fields": [{"name": "reference", "type": "string"}],
            }
        }
    )
    assert "upgrade_head() == REV_V2" not in src
    assert "upgrade_to(REV_V2) == REV_V2" in src


def test_agent_authored_migration_keeps_the_emitted_suite_green(built):
    """Stack a revision on top of V2, exactly as the coding agent does."""
    versions = built / "alembic" / "versions"
    assert (versions / f"{REVISION_0002}.py").is_file()

    (versions / f"{AGENT_REVISION}.py").write_text(
        '"""Capability schema the coding agent authored."""\n\n'
        "import sqlalchemy as sa\n"
        "from alembic import op\n\n"
        f'revision = "{AGENT_REVISION}"\n'
        f'down_revision = "{REVISION_0002}"\n'
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade() -> None:\n"
        '    op.create_table(\n'
        '        "corpus_documents",\n'
        '        sa.Column("id", sa.Integer, primary_key=True),\n'
        '        sa.Column("body", sa.Text, nullable=False),\n'
        "    )\n\n\n"
        "def downgrade() -> None:\n"
        '    op.drop_table("corpus_documents")\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_data_lifecycle.py", "-q", "--no-header"],
        cwd=built,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "the emitted suite went red because the agent added a migration:\n"
        + proc.stdout
        + proc.stderr
    )
