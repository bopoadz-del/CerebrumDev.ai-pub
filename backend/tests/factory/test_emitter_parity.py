"""S6: RoleRunner and ProductGenerator must share the 14-class contract.

Converge, not a third silent-drop path. RoleRunner invokes ProductGenerator
class emitters; it must not call generate() (that rmtree's the tree).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.converge import (
    DECLARED_GENERATOR_EXTRAS,
    DECLARED_RUNNER_EXTRAS,
    FOURTEEN_ARTIFACT_CLASSES,
    missing_classes,
)
from app.factory.build.runner import RoleRunner
from app.factory.generator import ProductGenerator

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_fourteen_class_contract_is_the_documented_list():
    assert FOURTEEN_ARTIFACT_CLASSES == (
        "app/main.py",
        "app/actions",
        "app/agents/manifests",
        "app/workflows",
        "app/cerebrum_product_kernel",
        "app/connectors",
        "product-dna",
        "docs/blueprint",
        "docs/provenance",
        "docs/certification",
        "frontend",
        "vendor/blocks",
        "kits",
        "scripts/release_gate.py",
    )
    assert DECLARED_GENERATOR_EXTRAS
    assert DECLARED_RUNNER_EXTRAS


def test_role_runner_does_not_call_product_generator_generate(
    tmp_path, monkeypatch, stub_coder
):
    def _boom(*_a, **_k):
        raise AssertionError(
            "ProductGenerator.generate() rmtree's the destination; "
            "converge must invoke class emitters only"
        )

    monkeypatch.setattr(ProductGenerator, "generate", _boom)
    result = RoleRunner(load_blueprint(SMOKE), tmp_path / "runner").run()
    assert result.ok, result.to_dict()


def test_role_runner_and_product_generator_share_fourteen_classes(
    tmp_path, stub_coder
):
    bp = load_blueprint(SMOKE)
    runner_out = tmp_path / "runner"
    runner = RoleRunner(bp, runner_out)
    result = runner.run()
    assert result.ok, result.to_dict()

    gen_out = tmp_path / "generator"
    ProductGenerator(
        bp,
        plan=runner.plan,
        blocks_root=None,
        factory_commit="test",
        blocks_commit="test",
    ).generate(gen_out)

    missing_runner = missing_classes(runner_out)
    missing_gen = missing_classes(gen_out)
    assert missing_runner == (), (
        f"RoleRunner dropped 14-class paths: {missing_runner}"
    )
    assert missing_gen == (), (
        f"ProductGenerator dropped 14-class paths: {missing_gen}"
    )

    # Frontend is the ProductGenerator UI stub, not an invented chat block.
    assert (runner_out / "frontend" / "src" / "App.tsx").is_file()
    assert (gen_out / "frontend" / "src" / "App.tsx").is_file()
    assert not any(
        "chat" in path.name.lower()
        for path in (runner_out / "frontend").rglob("*")
    )

    # DNA checksum surface exists on both; extras are declared, not required.
    assert (runner_out / "product-dna" / "checksum_manifest.json").is_file()
    assert (gen_out / "product-dna" / "checksum_manifest.json").is_file()
    assert (gen_out / "factory_plan.json").is_file()
    assert not (runner_out / "factory_plan.json").exists()
