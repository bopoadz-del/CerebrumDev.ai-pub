"""TESTER never rewrites the product's own ``tests/conftest.py``.

Live 2026-10-08 (98d3a356, rotation cycle 1, the co-op anchor): the writer's
``tests/conftest.py`` defined the names its tests import
(``from conftest import CAPABILITIES, PRIMARY_TENANT, ...``). TESTER then wrote
the Factory's test bootstrap OVER that file, the writer's test module failed
to import at collection, the writer re-added the names in rework, TESTER wrote
over them again -- "FAILED tests.test_placeholder_contract - collection
failure ... SAME_FAILURE_TWICE". A Factory stamp re-rendered a
product-authored file, which the stamp rule forbids (stamp_registry), and no
rework could ever fix it.

The rule: the Factory's test bootstrap lives at its own path (the rootdir
``conftest.py``, loaded by pytest before ``tests/conftest.py``); the product's
``tests/conftest.py`` keeps every byte; both run, and the product's explicit
settings win over the bootstrap's defaults.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.roles import RoleContext
from app.factory.build.workspace import RoleWorkspace

from .test_block_action_contract import _workspace_with_contract

WRITER_CONFTEST = textwrap.dedent(
    '''
    """The product's own test bootstrap -- names its tests import."""
    import os

    PRIMARY_TENANT = "tenant-primary"
    os.environ["PLATFORM_TOKEN"] = "product-test-token"


    def sample_payload():
        return {"name": "x"}
    '''
).lstrip()


def _run_tester_over_a_writer_conftest(tmp_path: Path):
    from app.factory.build.roles import run_tester

    _workspace_with_contract(tmp_path)
    build = tmp_path / "build"
    (build / "tests").mkdir(parents=True, exist_ok=True)
    (build / "tests" / "conftest.py").write_text(WRITER_CONFTEST, encoding="utf-8")

    class _Cap:
        capability_id = "crew_assignment"
        block_ids = ("team",)

    class _Plan:
        capabilities = (_Cap(),)

    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=RoleWorkspace(BuildRole.TESTER, build),
        blueprint=None,
        plan=_Plan(),
        state={
            "vendored_blocks": ("team",),
            "model_specs": {
                "crew_assignment": {
                    "entity": "crew_assignment",
                    "fields": [{"name": "crew", "type": "str", "required": True}],
                }
            },
        },
    )
    result = run_tester(ctx)
    assert result.ok, result.detail
    return build


def test_the_writers_tests_conftest_keeps_every_byte(tmp_path):
    build = _run_tester_over_a_writer_conftest(tmp_path)

    assert (build / "tests" / "conftest.py").read_text(encoding="utf-8") == WRITER_CONFTEST


def test_the_factory_bootstrap_is_still_written_at_its_own_path(tmp_path):
    from app.factory.build.roles_constants import CONFTEST_REL

    build = _run_tester_over_a_writer_conftest(tmp_path)

    assert CONFTEST_REL != "tests/conftest.py"
    bootstrap = (build / CONFTEST_REL).read_text(encoding="utf-8")
    assert "pilot:" in bootstrap
    assert "pytest_configure" in bootstrap


def test_both_bootstraps_load_and_the_products_names_import(tmp_path):
    """Collection is the live failure: a product test importing from its own
    conftest must collect and run beside the Factory bootstrap, with the
    product's explicit setting winning."""
    from app.factory.build.roles_constants import _CONFTEST, CONFTEST_REL

    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / CONFTEST_REL).write_text(_CONFTEST, encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(WRITER_CONFTEST, encoding="utf-8")
    (tmp_path / "tests" / "test_product.py").write_text(
        textwrap.dedent(
            """
            import os

            from conftest import PRIMARY_TENANT, sample_payload


            def test_names_from_the_products_conftest():
                assert PRIMARY_TENANT == "tenant-primary"
                assert sample_payload() == {"name": "x"}
                assert os.environ["PLATFORM_TOKEN"] == "product-test-token"
            """
        ),
        encoding="utf-8",
    )
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider", "--color=no"],
        cwd=tmp_path, capture_output=True, text=True, timeout=180,
    )
    assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]
