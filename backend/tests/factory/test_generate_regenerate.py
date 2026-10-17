"""Generate + regenerate reproducibility.

What is actually guaranteed here changed once the factory coder started
writing GENERATE capabilities: an LLM does not emit the same bytes twice, so
"same blueprint -> same tree hash" can only ever hold for the part of the
product the coder did not write.

The old single test asserted whole-tree byte equality and therefore passed
only while the coder was doing nothing -- green on CI, which has no LLM key,
and red on any machine with one. That is the worst shape a test can have: it
reported the agent working as a failure. These two split the claim in half so
both halves stay true and both run without an API key.
"""

import os
from pathlib import Path

from app.cerebrum_product_kernel.provenance import hash_tree
from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator


ROOT = Path(__file__).resolve().parents[3]

IGNORED = {".git", "__pycache__", "provenance.json", ".pytest_cache"}


def real_blocks_root():
    """The actual Cerebrum-Blocks checkout, or None when there isn't one.

    Replaces a hardcoded ``/home/ubuntu/repos/Cerebrum-Blocks`` that existed
    on no machine, CI included. A path that silently resolves to nothing is
    worse than no path: the tests passed while exercising something other
    than what their argument claimed.

    A candidate only counts if it carries ``block_registry/`` -- an empty or
    wrong directory must not masquerade as the store.
    """
    env = os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH")
    candidates = [Path(env)] if env else []
    # Sibling checkout next to this repo, the standard local layout.
    candidates.append(ROOT.parent / "Cerebrum-Blocks")
    for candidate in candidates:
        if (candidate / "block_registry").is_dir():
            return candidate
    return None


def _stable_hash(root: Path) -> str:
    # Exclude provenance timestamp file for byte-stability comparison of code tree
    return hash_tree(root, ignore_names=IGNORED)


def _snapshot(root: Path) -> dict:
    """Per-file digests, so a diff names the file instead of one tree hash."""
    import hashlib

    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in IGNORED for part in path.relative_to(root).parts):
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _generator() -> ProductGenerator:
    # blocks_root=None on purpose: these assert reproducibility, which does
    # not depend on block fidelity. Pinning them to the vendor mirror keeps
    # CI and a developer laptop with a real checkout byte-identical, instead
    # of quietly testing two different things.
    bp = load_blueprint(ROOT / "blueprints/examples/basic_product.yaml")
    return ProductGenerator(bp, blocks_root=None, factory_commit="test", blocks_commit="test")


def test_scaffold_is_byte_reproducible_without_the_coder(tmp_path, monkeypatch):
    """Everything the factory itself writes must be deterministic."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    out = tmp_path / "basic"
    gen = _generator()

    r1 = gen.generate(out)
    h1 = _stable_hash(out)
    assert (out / "app" / "main.py").exists()
    assert (out / "factory_plan.json").exists()
    assert r1["product_id"] == "basic-factory-smoke"

    r2 = gen.generate(out)
    assert _stable_hash(out) == h1
    assert r1["inputs_hash"] == r2["inputs_hash"]


def test_only_coder_written_modules_vary_between_builds(tmp_path, monkeypatch):
    """Non-determinism must be confined to what the coder wrote.

    The coder is stubbed rather than called, so this runs in CI with no LLM
    key and still fails if a future change lets LLM output bleed into a
    catalog, manifest or provenance file that is supposed to be stable.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")

    calls = {"n": 0}

    def fake_body(cap, blueprint):
        calls["n"] += 1
        return {"model": "stub-coder", "body": f'    return {{"run": {calls["n"]}}}'}

    monkeypatch.setattr("app.factory.coder.generate_handler_body", fake_body)

    out = tmp_path / "basic"
    gen = _generator()
    gen.generate(out)
    first = _snapshot(out)
    gen.generate(out)
    second = _snapshot(out)

    assert calls["n"] >= 2, "the coder never ran — this test would prove nothing"
    assert set(first) == set(second), "regeneration changed the file set"

    changed = {k for k in first if first[k] != second[k]}
    assert changed, "the stub returns different bodies; something must differ"

    # Every changed file is a coder-written action module, and it says so.
    for rel in changed:
        assert rel.startswith("app/actions/"), f"non-determinism escaped into {rel}"
        assert "strategy=GENERATE" in (out / rel).read_text(encoding="utf-8")


def test_turning_the_coder_on_does_not_perturb_the_scaffold(tmp_path, monkeypatch):
    """Everything outside app/actions/ is identical coder-on and coder-off.

    Both builds happen in one test so the two snapshots can actually be
    compared -- parametrising over the flag would run them in separate
    invocations with nothing to compare against, which asserts nothing.
    """
    monkeypatch.setattr(
        "app.factory.coder.generate_handler_body",
        lambda cap, bp: {"model": "stub-coder", "body": '    return {"stub": True}'},
    )

    full: dict = {}

    def scaffold_of(target: Path) -> dict:
        _generator().generate(target)
        snap = _snapshot(target)
        full[target.name] = snap
        return {k: v for k, v in snap.items() if not k.startswith("app/actions/")}

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    off = scaffold_of(tmp_path / "off")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    on = scaffold_of(tmp_path / "on")

    assert off, "no scaffold files were produced"
    # Proves the comparison below is live rather than equal for the wrong
    # reason: the two builds genuinely differ, just only under app/actions/.
    assert {
        k for k in set(full["off"]) & set(full["on"]) if full["off"][k] != full["on"][k]
    }, "coder-on and coder-off produced identical trees — the flag did nothing"
    assert set(off) == set(on), (
        f"coder-on added/removed scaffold files: {set(off) ^ set(on)}"
    )
    drifted = {k for k in off if off[k] != on[k]}
    assert not drifted, f"a coder run leaked into the scaffold: {sorted(drifted)}"


def test_steward_generate(tmp_path):
    """Generate against the real Store checkout when one is present."""
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    out = tmp_path / "steward"
    gen = ProductGenerator(
        bp, blocks_root=real_blocks_root(), factory_commit="test", blocks_commit="test"
    )
    result = gen.generate(out)
    assert (out / "vendor" / "blocks" / "estate_registry" / "block.json").exists()
    assert (out / "docs" / "edge_profile.json").exists()
    assert "portfolio_rollup" in result["plan"]["dual_registered_blocks"]
    catalog = (out / "app" / "actions" / "__init__.py").read_text()
    assert "estate_registry" in catalog


def test_steward_blocks_come_from_the_store(tmp_path):
    """Pin where the steward blocks actually come from.

    ``estate_registry`` and ``portfolio_rollup`` are absent from the real
    Cerebrum-Blocks repo; ``dual_registry.load_blocks_registry`` merges the
    factory's own ``vendor_blocks_mirror`` into the registry unconditionally,
    so they dual-register against a copy the factory ships to itself. That is
    why pointing blocks_root at the real Store changes nothing here.

    Asserting the provenance keeps the situation visible instead of implied.
    This test is meant to go red the day those blocks land upstream -- that is
    the signal to drop them from the mirror, not a regression.
    """
    from app.factory.dual_registry import load_blocks_registry

    registry = load_blocks_registry(real_blocks_root())
    for block_id in ("estate_registry", "portfolio_rollup"):
        assert block_id in registry, f"{block_id} vanished from both store and mirror"
        assert registry[block_id].source == "cerebrum-blocks", (
            f"{block_id} resolves from {registry[block_id].source} — the Factory "
            "holds no blocks; every block must come from the Store"
        )
