"""K-F7 PR B: the writer run wires the formula findings check (roles_handlers).

Kills N1, N2, N7, N8, N12 and X9. Synthetic tmp_path trees only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.factory.build.roles_handlers as roles_handlers  # noqa: F401  (CI-M maps this file)


def _fr():
    import app.factory.build.formula_resolution as formula_resolution

    return formula_resolution


IMPROVISED_LABEL = "improvised, no Store coverage"


class _BP:
    def __init__(self, vertical):
        self.product_id = "p_x"
        self.product_name = "X"
        self.vertical = vertical
        self.summary = "s"
        self.capabilities = []


def _write_defs(root: Path, lines):
    fr = _fr()
    path = root / fr.WRITER_DEFINITIONS_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    cands = root / fr.WRITER_CANDIDATES_REL
    cands.write_text(json.dumps({"definition": "imp_g"}) + "\n", encoding="utf-8")


GOOD_ROW = json.dumps({"id": "imp_g", "label": IMPROVISED_LABEL, "source": "Ref"})


class _Stop(Exception):
    pass



# --- R1 (orchestrator add): the production writer path runs the check -------

@pytest.mark.parametrize("defs, cands, expect", [
    # bad: re-writes a kernel formula, and improvises one unlabelled
    ([{"id": "kz_v1"}, {"id": "imp_q", "source": "Ref"}], [{"definition": "imp_q"}],
     ["kz_v1: kernel covers this; use the kernel definition",
      f"imp_q: improvised without the label {IMPROVISED_LABEL!r}"]),
    # good: improvised, labelled, sourced, with its candidate row
    ([{"id": "imp_q", "label": IMPROVISED_LABEL, "source": "Ref"}], [{"definition": "imp_q"}], []),
])
@pytest.mark.parametrize("elsewhere", [False, True])
def test_writer_run_flags_bad_formulas_it_authored(monkeypatch, tmp_path, defs, cands, expect, elsewhere):
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
    stages = []
    sources = []
    # B11: a workspace whose destination is not the writer's dest; the
    # findings must still be graded and recorded from dest (tmp_path).
    other = tmp_path.parent / (tmp_path.name + "_destination")
    other.mkdir()

    class _WS:
        destination = str(other)

    class Ctx:
        blueprint = _BP("v")
        workspace = _WS() if elsewhere else None
        state = {}

        def note(self, msg, **kw):
            notes.append(msg)
            stages.append((msg, kw.get("stage")))
            sources.append((msg, kw.get("source")))

    with pytest.raises(_Stop):
        rh._run_writer_via_codewhale_worker(Ctx())
    findings = tmp_path / fr.FORMULA_FINDINGS_REL
    assert findings.is_file(), "the writer run recorded no formula findings"
    rec = json.loads(findings.read_text(encoding="utf-8"))
    assert rec == {"errors": expect}
    assert [n for n in notes if n.startswith("writer formula: ")] == [f"writer formula: {e}" for e in expect]
    # X9: every formula note is tagged with the literal stage name
    assert [st for m, st in stages if m.startswith("writer formula: ")] == ["formula-resolution"] * len(expect)
    # B9: every formula note is tagged as coming from the Factory
    assert [src for m, src in sources if m.startswith("writer formula: ")] == ["factory"] * len(expect)
    # B11: nothing was graded into the workspace destination
    assert not (other / fr.FORMULA_FINDINGS_REL).exists()



@pytest.mark.parametrize("store, expect", [
    ([{"id": "imp_g"}], ["imp_g: the Store covers this; use the Store definition"]),
    ([{"id": "other"}], []),
])
def test_n7_the_flag_grades_against_the_store_it_was_given(tmp_path, store, expect):
    from app.factory.build import roles_handlers as rh

    _write_defs(tmp_path, [GOOD_ROW])
    notes = []

    class Ctx:
        def note(self, msg, **kw):
            notes.append(msg)

    got = rh._flag_writer_formulas(Ctx(), tmp_path, {"kernel_index": [], "store_index": store})
    assert got == expect
    assert notes == [f"writer formula: {e}" for e in expect]


# --- B12 (G2 #100 29de1fb): callers pass list indexes; a None or missing
# index is a caller bug and must raise, never be audited against nothing.

@pytest.mark.parametrize("sources", [
    {"kernel_index": None, "store_index": []},
    {"kernel_index": [], "store_index": None},
    {"store_index": []},
    {"kernel_index": []},
])
def test_b12_a_none_or_missing_index_raises(tmp_path, sources):
    from app.factory.build import roles_handlers as rh

    _write_defs(tmp_path, [GOOD_ROW])

    class Ctx:
        def note(self, msg, **kw):
            pass

    try:
        rh._flag_writer_formulas(Ctx(), tmp_path, sources)
        raised = None
    except (TypeError, KeyError) as exc:
        raised = type(exc).__name__
    assert raised is not None, f"a None/missing index was silently accepted: {sources}"
