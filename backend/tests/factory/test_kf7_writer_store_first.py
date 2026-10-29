"""K-F7 / F-9c: the writer prompt states and the code enforces the Store-first
order of ORDERS_1900Z -- kernel, then Store, then labelled improvisation.

Store trees are synthetic (tmp_path) and named per test, so every assertion
is about structure, never a real pack or kit.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt


def _fr():
    from app.factory.build import formula_resolution

    return formula_resolution


IMPROVISED_LABEL = "improvised, no Store coverage"


class _BP:
    def __init__(self, vertical):
        self.product_id = "p_x"
        self.product_name = "X"
        self.vertical = vertical
        self.summary = "s"
        self.capabilities = []


def _store(root: Path, packs: dict, kits: dict) -> Path:
    for pid, (manifest, fids) in packs.items():
        d = root / "domain_packs" / pid
        d.mkdir(parents=True)
        body = {"manifest": manifest, "formulas": [{"formula_id": f} for f in fids]}
        # packs carry a BOM, as some real Store packs do
        (d / "pack.json").write_text("\ufeff" + json.dumps(body), encoding="utf-8")
    for kid, (serves, iids) in kits.items():
        d = root / "app" / "blocks" / kid
        d.mkdir(parents=True)
        (d / "manifest.yaml").write_text(f"kit: {kid}\nserves_verticals: {json.dumps(serves)}\n", encoding="utf-8")
        inv = "invariants:\n" + "".join(f"  - id: {i}\n    kind: band\n" for i in iids)
        (d / "invariants.yaml").write_text(inv, encoding="utf-8")
    return root


@pytest.mark.parametrize("vertical, expect", [
    ("alpha_ops", ["al.f1", "al.f2", "INV-AL-1"]),
    ("beta", ["be.f1", "INV-BE-1", "INV-BE-2"]),
    ("gamma", []),
])
def test_store_coverage_index_reads_the_vertical_off_the_store(tmp_path, vertical, expect):
    root = _store(tmp_path, {
        "alpha_ops": ({"domain_id": "alpha_ops"}, ["al.f1", "al.f2"]),
        "b_pack": ({"domain_id": "b_pack", "serves_verticals": ["beta"]}, ["be.f1"]),
    }, {
        "akit": (["alpha-ops"], ["INV-AL-1"]),
        "bkit": (["beta"], ["INV-BE-1", "INV-BE-2"]),
    })
    rows = _fr().store_coverage_index(vertical, root)
    assert sorted(r["id"] for r in rows) == sorted(expect)
    kinds = {r["id"]: r["kind"] for r in rows}
    for i in expect:
        assert kinds[i] == ("kit_invariant" if i.startswith("INV") else "pack_formula")


@pytest.mark.parametrize("ident, layer, label", [
    ("k1", "kernel", ""),          # in both: kernel wins
    ("s1", "store", ""),
    ("s2", "store", ""),
    ("new_x", "improvised", IMPROVISED_LABEL),
])
def test_resolve_layer_is_kernel_then_store_then_improvised(ident, layer, label):
    kernel = [{"id": "k1"}, {"id": "k2"}]
    store = [{"id": "k1"}, {"id": "s1"}, {"id": "s2"}]
    got = _fr().resolve_layer(ident, kernel, store)
    assert (got["layer"], got["label"]) == (layer, label)


GOOD = {"id": "imp_a", "label": IMPROVISED_LABEL, "source": "Book, ch. 2"}


@pytest.mark.parametrize("row, cands, expect", [
    (GOOD, [{"definition": "imp_a"}], []),
    ({**GOOD, "id": "imp_b"}, [{"definition": "imp_b"}], []),
    ({"id": "k1"}, [], ["k1: kernel covers this; use the kernel definition"]),
    ({"id": "s1", "label": IMPROVISED_LABEL, "source": "x"}, [{"definition": "s1"}],
     ["s1: the Store covers this; use the Store definition"]),
    ({**GOOD, "label": "made up"}, [{"definition": "imp_a"}],
     [f"imp_a: improvised without the label {IMPROVISED_LABEL!r}"]),
    ({**GOOD, "source": "  "}, [{"definition": "imp_a"}], ["imp_a: improvised without a cited source"]),
    (GOOD, [], ["imp_a: improvised without a new_domain_candidates row"]),
])
def test_writer_formula_errors(row, cands, expect):
    assert _fr().writer_formula_errors([row], [{"id": "k1"}], [{"id": "s1"}], cands) == expect


@pytest.mark.parametrize("kernel, store, sha", [
    ([{"id": "kx_v1", "address": "universal:kx_v1"}], [{"id": "p.f9", "kind": "pack_formula", "source": "domain_packs/p/pack.json"}], "a" * 40),
    ([{"id": "ky_v2", "address": "universal:ky_v2"}], [{"id": "INV-Q", "kind": "kit_invariant", "source": "app/blocks/q/invariants.yaml"}], "b" * 40),
])
def test_prompt_lists_kernel_then_store_then_improvised(kernel, store, sha):
    try:
        text = render_writer_prompt(_BP("v"), brief="b", kernel_index=kernel, store_index=store, store_sha=sha)
    except TypeError as exc:  # base signature: keep the failure an assertion
        raise AssertionError(f"render_writer_prompt takes no formula sources: {exc}")
    for part in ("1. KERNEL", "2. STORE", "3. IMPROVISED", kernel[0]["id"], store[0]["id"]):
        assert part in text, part
    k = text.index(f"- {kernel[0]['id']} @ {kernel[0]['address']}")
    s = text.index(f"- {store[0]['id']} [{store[0]['kind']}] {store[0]['source']}")
    i = text.index("3. IMPROVISED")
    assert text.index("1. KERNEL") < k < text.index("2. STORE") < s < i
    assert f"store.pin {sha}" in text


def test_prompt_states_the_1900z_ruling():
    text = render_writer_prompt(_BP("v"), brief="b")
    assert "the coder writes and finds formulas himself ONLY\n  when nothing is available in our Store" in text
    assert '"improvised, no Store coverage"' in text
    assert "new_domain_candidates.jsonl" in text
    assert "Re-writing a formula the kernel or the Store already has is a failure" in text
    assert int(PROMPT_VERSION.rsplit(".v", 1)[1]) >= 15


def test_caller_feeds_real_kernel_and_store_to_the_prompt(monkeypatch, tmp_path):
    from app.factory.build import roles_handlers as rh

    fr = _fr()
    root = _store(tmp_path, {"zeta": ({"domain_id": "zeta"}, ["ze.f1"])}, {})
    monkeypatch.setattr(fr, "_store_root", lambda _r: root)

    class Ctx:
        blueprint = _BP("zeta")

    got = rh._formula_sources(Ctx())
    assert [r["id"] for r in got["store_index"]] == ["ze.f1"]
    assert got["kernel_index"] and all(r["address"] for r in got["kernel_index"])
