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


# --- R1: one test per G2 survivor (synthetic rows / trees only) -------------

IMP = {"label": IMPROVISED_LABEL, "source": "Ref, p. 1"}


@pytest.mark.parametrize("row, expect", [
    ({**IMP}, ["definition row without an id"]),
    ({**IMP, "id": "   "}, ["definition row without an id"]),
    ({**IMP, "id": "imp_n"}, []),
])
def test_s1_row_without_an_id_is_reported(row, expect):
    assert _fr().writer_formula_errors([row], [], [], [{"definition": "imp_n"}]) == expect


@pytest.mark.parametrize("prov, expect", [
    ({"reference": "Handbook, s. 4"}, []),
    ({"reference": "  "}, ["imp_p: improvised without a cited source"]),
])
def test_s2_provenance_reference_counts_as_the_source(prov, expect):
    row = {"id": "imp_p", "label": IMPROVISED_LABEL, "provenance": prov}
    assert _fr().writer_formula_errors([row], [], [], [{"definition": "imp_p"}]) == expect


@pytest.mark.parametrize("cands, expect", [
    ([{"id": "imp_c"}], []),
    ([{"id": "imp_other"}], ["imp_c: improvised without a new_domain_candidates row"]),
])
def test_s3_candidate_row_may_name_it_by_id(cands, expect):
    row = {"id": "imp_c", **IMP}
    assert _fr().writer_formula_errors([row], [], [], cands) == expect


@pytest.mark.parametrize("key, expect", [
    ("imp_k", []),
    ("k9", ["k9: kernel covers this; use the kernel definition"]),
])
def test_s4_row_may_carry_its_id_as_key(key, expect):
    row = {"key": key, **IMP}
    assert _fr().writer_formula_errors([row], [{"id": "k9"}], [], [{"definition": "imp_k"}]) == expect


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


# --- R1 (orchestrator add): the production writer path runs the check -------

@pytest.mark.parametrize("defs, cands, expect", [
    # bad: re-writes a kernel formula, and improvises one unlabelled
    ([{"id": "kz_v1"}, {"id": "imp_q", "source": "Ref"}], [{"definition": "imp_q"}],
     ["kz_v1: kernel covers this; use the kernel definition",
      f"imp_q: improvised without the label {IMPROVISED_LABEL!r}"]),
    # good: improvised, labelled, sourced, with its candidate row
    ([{"id": "imp_q", "label": IMPROVISED_LABEL, "source": "Ref"}], [{"definition": "imp_q"}], []),
])
def test_writer_run_flags_bad_formulas_it_authored(monkeypatch, tmp_path, defs, cands, expect):
    from app.factory.build import roles_handlers as rh
    from app.factory.build import image_sufficiency as img
    from app.factory.build import codewhale_worker as cw
    from app.factory.build import factory_owned
    from app.factory.build import data_lifecycle

    fr = _fr()

    class Receipt:
        def to_dict(self):
            return {}

    def fake_job(prompt, dest, **kw):
        d = Path(dest)
        for rel, rows in ((fr.WRITER_DEFINITIONS_REL, defs), (fr.WRITER_CANDIDATES_REL, cands)):
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            (d / rel).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return Receipt()

    def stop(_ws):
        raise _Stop

    monkeypatch.setattr(rh, "_formula_sources", lambda ctx: {
        "kernel_index": [{"id": "kz_v1", "address": "u:kz_v1"}], "store_index": [], "store_sha": None})
    monkeypatch.setattr(rh, "persist_workspace_root", lambda ws: tmp_path)
    monkeypatch.setattr(rh, "_compiled_writer_brief", lambda ctx: "b")
    monkeypatch.setattr(img, "runtime_paths", lambda tree: [])
    monkeypatch.setattr(img, "locked_entries", lambda tree: {})
    monkeypatch.setattr(cw, "writer_specialist_cap", lambda: 1)
    monkeypatch.setattr(cw, "run_worker_job", fake_job)
    monkeypatch.setattr(factory_owned, "prestamp", lambda *a, **k: [])
    monkeypatch.setattr(data_lifecycle, "backfill_platform_substrate", stop)
    for name in ("_writer_worker_timeout_s", "_writer_worker_live_time_left", "_writer_slot_wait_left"):
        monkeypatch.setattr(rh, name, lambda ctx: None)

    notes = []

    class Ctx:
        blueprint = _BP("v")
        workspace = None
        state = {}

        def note(self, msg, **kw):
            notes.append(msg)

    with pytest.raises(_Stop):
        rh._run_writer_via_codewhale_worker(Ctx())
    findings = tmp_path / fr.FORMULA_FINDINGS_REL
    assert findings.is_file(), "the writer run recorded no formula findings"
    rec = json.loads(findings.read_text(encoding="utf-8"))
    assert rec == {"errors": expect}
    assert [n for n in notes if n.startswith("writer formula: ")] == [f"writer formula: {e}" for e in expect]
