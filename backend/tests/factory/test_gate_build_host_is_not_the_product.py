"""The CLONER gate runs on the Factory host, which is not the product.

Found by a production-like sweep of all 197 Store blocks (an import hook that
refused every package absent from the Factory's own production lock):

    construction_advisor: ModuleNotFoundError: No module named 'sympy'

sympy is a module-level import in that block's runtime. The CLONER already
derives it as a dependency obligation and the product's requirements.txt
declares it, so the PRODUCT is correct -- the gate failed because the Factory
host has no sympy, which says nothing about whether the block needs the Store.

Installing every block's dependencies into the Factory is the wrong fix: it
hard-wires the Store's package set into this repo's lock. So the probe stands
a placeholder in for a DECLARED package and keeps importing. These tests are
mostly about what that must NOT let through.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.gates import (
    GateContext,
    _declared_third_party_modules,
    gate_blocks_import_offline,
)

# A package name no environment will ever have installed, so these tests mean
# the same thing on a laptop, in CI and in production.
ABSENT = "zz_factory_gate_absent_pkg"


def _workspace(tmp_path: Path, block_py: str, runtime_py: str = "") -> Path:
    block = tmp_path / "vendor" / "blocks" / "thing"
    block.mkdir(parents=True)
    (block / "block.py").write_text(block_py, encoding="utf-8")
    if runtime_py:
        rt = tmp_path / "vendor" / "cerebrum"
        rt.mkdir(parents=True)
        (tmp_path / "vendor" / "__init__.py").write_text("", encoding="utf-8")
        (rt / "__init__.py").write_text("", encoding="utf-8")
        (rt / "runtime.py").write_text(runtime_py, encoding="utf-8")
    return tmp_path


def _gate(ws: Path):
    return gate_blocks_import_offline(
        GateContext(workspace=ws, role=BuildRole.CLONER, vendored_blocks=("thing",))
    )


def _declare(monkeypatch, *modules: str) -> None:
    """Teach the obligations table a distribution for the absent package, the
    way block_obligations.DISTRIBUTIONS does for every real one."""
    from app.factory.build import block_obligations

    patched = dict(block_obligations.DISTRIBUTIONS)
    patched.update({m: m.replace("_", "-") for m in modules})
    monkeypatch.setattr(block_obligations, "DISTRIBUTIONS", patched)


def test_a_declared_package_missing_from_the_build_host_does_not_fail_the_gate(
    tmp_path, monkeypatch
):
    _declare(monkeypatch, ABSENT)
    ws = _workspace(
        tmp_path,
        f"import {ABSENT}\nfrom {ABSENT}.sub import thing\n\n\ndef run(**k):\n    return {{}}\n",
    )

    result = _gate(ws)

    assert result.ok, result.findings
    assert result.payload["declared_not_on_build_host"] == [ABSENT]
    # said out loud, not swallowed: a reviewer sees it was stood in for
    assert ABSENT in result.detail


def test_a_store_dependency_hiding_behind_a_declared_package_is_still_caught(
    tmp_path, monkeypatch
):
    """The reason the probe RETRIES instead of just forgiving the first
    error: the import after the missing package must still execute. ``app``
    is the Store's package; needing it is exactly what this gate exists for."""
    _declare(monkeypatch, ABSENT)
    ws = _workspace(
        tmp_path,
        f"import {ABSENT}\nfrom app.blocks import get_block\n\n\ndef run(**k):\n    return {{}}\n",
    )

    result = _gate(ws)

    assert not result.ok
    assert result.reason == "block_import_offline_failed"
    assert any("No module named 'app'" in f for f in result.findings), result.findings


def test_an_undeclared_missing_package_still_fails(tmp_path):
    """Nothing declared it, so nothing will install it: the product would
    crash at import. Only DECLARED names are stood in for."""
    ws = _workspace(tmp_path, f"import {ABSENT}\n\n\ndef run(**k):\n    return {{}}\n")

    # the obligations scan cannot name it, so nothing is tolerated
    assert _declared_third_party_modules(ws) == set()
    result = _gate(ws)

    assert not result.ok
    assert any(ABSENT in f for f in result.findings), result.findings


def test_a_real_error_in_the_block_is_not_forgiven(tmp_path, monkeypatch):
    _declare(monkeypatch, ABSENT)
    ws = _workspace(tmp_path, f"import {ABSENT}\nraise ValueError('broken block')\n")

    result = _gate(ws)

    assert not result.ok
    assert any("broken block" in f for f in result.findings), result.findings


def test_a_block_with_everything_installed_reports_nothing_stood_in(tmp_path):
    ws = _workspace(tmp_path, "import json\n\n\ndef run(**k):\n    return {}\n")

    result = _gate(ws)

    assert result.ok
    assert result.payload["declared_not_on_build_host"] == []
    assert "stood in" not in result.detail


def test_the_declared_set_is_derived_from_the_vendored_source_not_kept_here(
    tmp_path, monkeypatch
):
    """Owner: "dont hard wire anything". The gate holds no package list; it
    asks the same AST scan that writes the product's requirements.txt."""
    _declare(monkeypatch, ABSENT)
    ws = _workspace(
        tmp_path,
        "def run(**k):\n    return {}\n",
        runtime_py=f"def lazy():\n    import {ABSENT}\n    return {ABSENT}\n",
    )

    assert _declared_third_party_modules(ws) == {ABSENT}
