"""Kit packs must ship in the generated product, not only vendored blocks.

The live winery-hospitality export had app/ + vendor/blocks/ and no kits/.
CLONER (and ProductGenerator) now stock kits/{kit}/ from the Factory shelf.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.roles import RoleContext, run_cloner
from app.factory.build.workspace import RoleWorkspace
from app.factory.kit_pack import kits_for_blocks, stock_kits
from tests.factory.store_paths import store_root


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_shelf_maps_winery_blocks_to_platform_kit():
    grouped = kits_for_blocks(
        ["analytics", "capture", "dashboard", "database", "event_bus"]
    )
    assert grouped == {
        "platform": ["analytics", "capture", "dashboard", "database", "event_bus"]
    }


def test_estate_blocks_map_to_estate_kit():
    grouped = kits_for_blocks(["estate_registry", "database"])
    assert grouped["private_estate_operations"] == ["estate_registry"]
    assert grouped["platform"] == ["database"]


def test_cloner_stocks_kit_packs_next_to_vendored_blocks(tmp_path):
    """CLONER must write kits/platform/manifest.json when it vendors shelf blocks."""
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=store_root(),
        state={"resolved_blocks": ("estate_registry", "estate_maintenance")},
    )
    result = run_cloner(ctx)
    assert result.ok, result.detail

    dest = ws.destination
    assert (dest / "vendor" / "blocks" / "estate_registry" / "block.py").is_file()
    manifest = dest / "kits" / "private_estate_operations" / "manifest.json"
    assert manifest.is_file(), "CLONER did not stock kits/private_estate_operations"
    body = json.loads(manifest.read_text(encoding="utf-8"))
    assert body["id"] == "private_estate_operations"
    assert "estate_registry" in body["product_blocks"]
    assert (
        body["vendored_blocks"]["estate_registry"]
        == "vendor/blocks/estate_registry"
    )

    lock = json.loads((dest / "blocks.lock.json").read_text(encoding="utf-8"))
    assert "kits" in lock
    assert lock["kits"]["private_estate_operations"]["path"] == (
        "kits/private_estate_operations"
    )


def test_generator_copies_kits_and_export_zip_lists_them(tmp_path):
    """The download zip listing must include app, blocks, and kits."""
    from app.factory.blueprint import load_blueprint
    from app.factory.generator import ProductGenerator
    from app.routers.session_product import zip_generated_product

    bp = load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    out = tmp_path / "basic-factory-smoke"
    ProductGenerator(
        bp, blocks_root=store_root(), factory_commit="t", blocks_commit="t"
    ).generate(out)

    assert (out / "app" / "main.py").is_file()
    assert (out / "vendor" / "blocks" / "audit" / "block.py").is_file()
    assert (out / "kits" / "platform" / "manifest.json").is_file()

    zpath = zip_generated_product(out, tmp_path / "basic-factory-smoke-export")
    names = zipfile.ZipFile(zpath).namelist()
    assert any(n.startswith("app/") for n in names), names[:20]
    assert any(n.startswith("vendor/blocks/") for n in names), names[:20]
    assert any(
        n.startswith("kits/") and n.endswith("manifest.json") for n in names
    ), [n for n in names if n.startswith("kits")]
    assert not any("__pycache__" in n for n in names)
    assert not any(".pytest_cache" in n for n in names)


def test_estate_kit_pack_copies_factory_kit_tree(tmp_path):
    """Estate products must ship the Factory private_estate_operations kit, not a stub."""
    records = stock_kits(tmp_path / "product", ["estate_registry"])
    kit_root = tmp_path / "product" / "kits" / "private_estate_operations"
    assert (kit_root / "manifest.json").is_file()
    assert (kit_root / "steward_runtime" / "api.py").is_file()
    assert records["private_estate_operations"]["files_copied"] > 0
    manifest = json.loads((kit_root / "manifest.json").read_text(encoding="utf-8"))
    assert "estate_registry" in manifest["product_blocks"]
