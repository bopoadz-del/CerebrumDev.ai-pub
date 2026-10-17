"""Clone-and-test standard: every generated pilot is self-verifying.

A client clones the repo, runs scripts/release_gate.py, and watches the
pilot prove itself. These tests lock the TEMPLATE generator to that
contract; it is no longer the production default (see
``test_production_uses_the_runner.py``) but remains the documented revert
path, so its contract still has to hold. The runner artifact's equivalent
promise is asserted in the cutover tests.
"""

from __future__ import annotations

import os
import py_compile

import pytest

from app.factory import product_architect


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    # Pin the engine: generate_product now routes to the role runner by
    # default, which is a background job with a different artifact shape.
    # These assertions are about the template path specifically.
    os.environ["FACTORY_BUILD_ENGINE"] = "template"
    out = tmp_path_factory.mktemp("clone-test-product")
    bp = product_architect.draft_blueprint_from_brief(
        "platform for private estate operations with staff and vehicles",
        use_llm=False,
    )
    try:
        result = product_architect.generate_product(bp, out / bp.product_id)
    finally:
        os.environ.pop("FACTORY_BUILD_ENGINE", None)
    return out / result["product_id"]


def test_gate_artifacts_exist(generated):
    assert (generated / "scripts" / "release_gate.py").is_file()
    assert (generated / "tests" / "test_smoke.py").is_file()
    assert (generated / ".env.example").is_file()


def test_gate_files_are_valid_python(generated):
    py_compile.compile(str(generated / "scripts" / "release_gate.py"), doraise=True)
    py_compile.compile(str(generated / "tests" / "test_smoke.py"), doraise=True)


def test_smoke_suite_checks_the_contract(generated):
    smoke = (generated / "tests" / "test_smoke.py").read_text(encoding="utf-8")
    assert "test_health" in smoke
    assert "test_provenance_present_and_hashed" in smoke
    assert "test_block_snapshot_present" in smoke
    assert "test_action_catalog_covers_plan" in smoke


def test_readme_has_clone_and_test_sections(generated):
    readme = (generated / "README.md").read_text(encoding="utf-8")
    assert "## Quickstart (clone-and-test)" in readme
    assert "## Gates" in readme
    assert "## Architecture" in readme
    assert "## Feature-to-block map" in readme
    assert "## Honesty notes" in readme
    assert "release_gate.py" in readme
    # Ubuntu 24.04 / PEP 668: system pip refuses -- a stranger following
    # `python3 -m pip install -r requirements.txt` cannot clone-and-test.
    assert "python3 -m venv .venv" in readme
    assert ".venv/bin/pip install -r requirements.txt -r requirements-dev.txt" in readme
    assert "python3 -m pip install -r requirements.txt" not in readme


def test_readme_carries_honesty_labels(generated):
    readme = (generated / "README.md").read_text(encoding="utf-8")
    assert "PILOT" in readme
    assert "docs/certification/dual_certification.json" in readme


def test_requirements_include_gate_dependencies(generated):
    reqs = (generated / "requirements.txt").read_text(encoding="utf-8")
    assert "pytest" in reqs
    assert "httpx" in reqs


def test_smoke_suite_references_generated_product_id(generated):
    smoke = (generated / "tests" / "test_smoke.py").read_text(encoding="utf-8")
    import json

    product_id = json.loads((generated / "factory_plan.json").read_text(encoding="utf-8"))["product_id"]
    assert f'PRODUCT_ID = "{product_id}"' in smoke
