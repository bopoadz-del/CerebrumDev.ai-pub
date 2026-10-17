"""A re-entered build gets today's Factory files, not the ones it was born with.

Live: a hotel platform built before the release-gate fix kept a
``scripts/release_gate.py`` that demands ``docs/build_provenance.json`` -- a
file the Factory withholds from every product -- so its Docker image failed to
build and the gate read 0/13, resume after resume, however green the product
was. A Factory fix has to reach the branches that were built before it.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.engine_switch import render_db_module
from app.factory.build.factory_refresh import merged_requirements, refresh_factory_files
from app.factory.build.roles_handlers import _render_release_gate, _render_requirements

STALE_GATE = '''"""Release gate for Probe."""
import sys


def main() -> int:
    manifest = "docs/build_provenance.json"
    print("docs/build_provenance.json: MISSING")
    return 1
'''


def _build(root: Path, requirements: str = "fastapi>=0.110\n") -> Path:
    for rel, body in {
        "scripts/release_gate.py": STALE_GATE,
        "scripts/acceptance.py": "# stale acceptance\n",
        ".github/workflows/ci.yml": "name: stale\n",
        "requirements.txt": requirements,
        "app/actions/thing.py": "CAPABILITY_ID = 'thing'\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


def test_the_stale_release_gate_is_replaced(tmp_path):
    root = _build(tmp_path / "ws")

    changed = refresh_factory_files(root, "Probe")

    assert "scripts/release_gate.py" in changed
    gate = (root / "scripts" / "release_gate.py").read_text(encoding="utf-8")
    assert gate == _render_release_gate("Probe")
    assert "build_provenance.json: MISSING" not in gate


def test_coder_files_are_never_touched(tmp_path):
    root = _build(tmp_path / "ws")
    (root / "app" / "models.py").write_text("MODELS = {'a': 1}\n", encoding="utf-8")
    (root / "app" / "routes.py").write_text("# the coder's routes\n", encoding="utf-8")

    changed = refresh_factory_files(root, "Probe")

    assert not [rel for rel in changed if rel.startswith("app/")]
    assert (root / "app" / "routes.py").read_text(encoding="utf-8") == "# the coder's routes\n"
    assert (root / "app" / "models.py").read_text(encoding="utf-8") == "MODELS = {'a': 1}\n"


def test_a_file_the_build_never_had_is_not_created(tmp_path):
    root = tmp_path / "ws"
    (root / "app").mkdir(parents=True)

    assert refresh_factory_files(root, "Probe") == []
    assert not (root / "scripts" / "release_gate.py").exists()


def test_refreshing_twice_changes_nothing_the_second_time(tmp_path):
    root = _build(tmp_path / "ws")

    refresh_factory_files(root, "Probe")

    assert refresh_factory_files(root, "Probe") == []


def test_requirements_are_merged_never_replaced(tmp_path):
    """The build's own lines stay: a driver the writer added is not dropped."""
    root = _build(tmp_path / "ws", requirements="fastapi>=0.110\nzz-writer-added==1.2.3\n")
    vendor = root / "vendor" / "cerebrum" / "blocks"
    vendor.mkdir(parents=True)
    (vendor / "thing.py").write_text("import numpy\n", encoding="utf-8")

    merged = merged_requirements(root)

    assert "zz-writer-added==1.2.3" in merged
    assert "numpy" in merged
    assert merged.count("fastapi") == 1


# ── the Postgres switch the floor measures ─────────────────────────────────


def test_the_platform_declares_the_driver_its_own_db_module_needs():
    """postgres_boot_200 boots the product on Postgres. app/db.py hands
    DATABASE_URL to SQLAlchemy, so the driver must be declared or the
    variable is accepted and cannot be dialled."""
    assert "psycopg[binary]" in _render_requirements({})


def test_an_operator_style_url_is_pinned_to_the_declared_driver():
    """SQLAlchemy reads a bare postgres:// scheme as psycopg2, which this
    platform does not ship."""
    source = render_db_module()
    namespace: dict = {}
    exec(compile(source, "app/db.py", "exec"), namespace)  # noqa: S102 - the emitted module
    import os

    for given in ("postgres://u:p@h:5432/db", "postgresql://u:p@h:5432/db"):
        os.environ["DATABASE_URL"] = given
        try:
            assert namespace["database_url"]().startswith("postgresql+psycopg://")
        finally:
            os.environ.pop("DATABASE_URL", None)


def test_a_url_that_is_not_postgres_is_left_alone():
    import os

    namespace: dict = {}
    exec(compile(render_db_module(), "app/db.py", "exec"), namespace)  # noqa: S102
    os.environ["DATABASE_URL"] = "mysql://u:p@h/db"
    try:
        assert namespace["database_url"]() == "mysql://u:p@h/db"
    finally:
        os.environ.pop("DATABASE_URL", None)
