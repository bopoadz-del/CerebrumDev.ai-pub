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


@pytest.mark.parametrize("vertical, reads", [("", 0), ("  ", 0), ("omega", 1)])
def test_s5_blank_vertical_never_reads_the_store(monkeypatch, tmp_path, vertical, reads):
    fr = _fr()
    calls = []
    monkeypatch.setattr(fr, "reasoning_kits", lambda root: calls.append(root) or {})
    root = _store(tmp_path, {"omega": ({"domain_id": "omega"}, ["om.f1"])}, {})
    rows = fr.store_coverage_index(vertical, root)
    assert len(calls) == reads
    assert [r["id"] for r in rows] == (["om.f1"] if reads else [])


@pytest.mark.parametrize("fids, expect", [
    (["z.f", "m.f", "a.f"], ["INV-B", "a.f", "m.f", "z.f"]),
    (["c.f", "b.f"], ["INV-B", "b.f", "c.f"]),
])
def test_s6_index_is_returned_sorted_by_id(tmp_path, fids, expect):
    root = _store(tmp_path, {"sigma": ({"domain_id": "sigma"}, fids)}, {"skit": (["sigma"], ["INV-B"])})
    assert [r["id"] for r in _fr().store_coverage_index("sigma", root)] == expect


@pytest.mark.parametrize("fids, expect", [
    (["", "  ", "t.f1"], ["t.f1"]),
    (["t.f1", "t.f2"], ["t.f1", "t.f2"]),
])
def test_s7_blank_formula_ids_are_dropped(tmp_path, fids, expect):
    root = _store(tmp_path, {"tau": ({"domain_id": "tau"}, fids)}, {})
    assert [r["id"] for r in _fr().store_coverage_index("tau", root)] == expect


class _Stop(Exception):
    pass


@pytest.mark.parametrize("sources", [
    {"kernel_index": [{"id": "kq", "address": "u:kq"}], "store_index": [], "store_sha": "c" * 40},
    {"kernel_index": [], "store_index": [{"id": "sq", "kind": "pack_formula", "source": "p"}], "store_sha": None},
])
def test_s8_writer_run_passes_formula_sources_to_the_prompt(monkeypatch, tmp_path, sources):
    from app.factory.build import roles_handlers as rh
    from app.factory.build import writer_prompt as wp
    from app.factory.build import image_sufficiency as img
    from app.factory.build import codewhale_worker as cw

    seen = {}

    def fake_render(blueprint, **kw):
        seen.update(kw)
        raise _Stop

    monkeypatch.setattr(wp, "render_writer_prompt", fake_render)
    monkeypatch.setattr(rh, "_formula_sources", lambda ctx: dict(sources))
    monkeypatch.setattr(rh, "persist_workspace_root", lambda ws: tmp_path)
    monkeypatch.setattr(rh, "_compiled_writer_brief", lambda ctx: "b")
    monkeypatch.setattr(img, "runtime_paths", lambda tree: [])
    monkeypatch.setattr(img, "locked_entries", lambda tree: {})
    monkeypatch.setattr(cw, "writer_specialist_cap", lambda: 1)

    class Ctx:
        blueprint = _BP("v")
        workspace = None
        state = {}

    with pytest.raises(_Stop):
        rh._run_writer_via_codewhale_worker(Ctx())
    for k, v in sources.items():
        assert seen.get(k) == v, k


# --- F-9c split: one test per G2 survivor of 84bf10d (own file) -------------

@pytest.mark.parametrize("sha, want, absent", [
    (None, " (store.pin unreadable)", "at store.pin"),
    ("d" * 40, "at store.pin " + "d" * 40, "store.pin unreadable"),
])
def test_a17_an_unreadable_pin_is_said_not_implied(sha, want, absent):
    text = _fr().render_resolution_section([{"id": "k", "address": "u:k"}], [], sha)
    store_line = [ln for ln in text.splitlines() if ln.startswith("2. STORE")][0]
    assert want in store_line and absent not in store_line


@pytest.mark.parametrize("kernel, first", [
    ([], "   (none)"),
    ([{"id": "kw", "address": "u:kw"}], "   - kw @ u:kw"),
])
def test_a20_an_empty_kernel_says_none(kernel, first):
    lines = _fr().render_resolution_section(kernel, [], "e" * 40).splitlines()
    assert lines[lines.index("1. KERNEL definitions (cerebrum_product_kernel, by address):") + 1] == first


@pytest.mark.parametrize("sha", ["f" * 40, None])
def test_b2_the_store_sha_comes_from_the_pin(monkeypatch, tmp_path, sha):
    from app.factory.build import roles_handlers as rh
    from app.factory import store_pin

    monkeypatch.setattr(_fr(), "_store_root", lambda _r: tmp_path)
    monkeypatch.setattr(store_pin, "pinned_sha_or_none", lambda: sha)

    class Ctx:
        blueprint = _BP("v")

    assert rh._formula_sources(Ctx())["store_sha"] == sha


@pytest.mark.parametrize("const", ["WRITER_DEFINITIONS_REL", "WRITER_CANDIDATES_REL"])
def test_n9_the_prompt_names_where_formulas_go(const):
    text = render_writer_prompt(_BP("v"), brief="b")
    assert f"in {getattr(_fr(), const)}" in text, const
