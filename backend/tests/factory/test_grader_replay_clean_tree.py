"""FG-1 on the FG-3 replay harness: every grader finding has an owner.

The tree is composed by the Factory's own code (the CLONER's vendoring from
the pinned Store, every registered stamp) plus an invented writer layer, and
graded through ``RoleRunner._writer_grader`` with the grader on. The harness
records which layer wrote each path by digest diff, independently of the
grader's own provenance read, so the two are checked against each other.

FG-3 kept these cases uncollected as the red-on-master proof; FG-1 enables
the ownership cases. Certification of a clean tree is held by
``test_grader_replay_certifies.py``.

Nothing here names a product, capability, block, stamped file or literal:
every path and literal is read from the composed tree or its grade record.
"""

from __future__ import annotations

import pytest

from tests.factory import grader_replay as gr


@pytest.fixture(scope="module")
def composed(tmp_path_factory):
    from tests.factory.store_paths import store_root

    store = store_root()
    cache = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FACTORY_CODER_ENABLED", "0")
        for case in gr.CASES:
            cache[case] = gr.compose_case(case, tmp_path_factory.mktemp(f"compose-{case}"), store)
    return cache


@pytest.fixture()
def tree(composed, tmp_path, request, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    return composed[request.param].copy_to(tmp_path / "graded")


def _literal_rows(replay):
    hard = replay.grade.get("hardwiring") or {}
    return [f for f in hard.get("findings") or [] if f.get("kind") == "forbidden_literal"]


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_no_finding_billed_to_the_writer_names_a_factory_written_path(tree, monkeypatch):
    from app.factory.build.factory_receipt import WRITER
    from app.factory.build.product_grader.grade_record import finding_is_writers

    replay = gr.grade(tree, monkeypatch)
    rows = _literal_rows(replay)
    assert all(row.get("owner") for row in rows), rows
    billed = {row["file"]: tree.layer_of(row["file"]) for row in rows if finding_is_writers(row)}
    assert {f: layer for f, layer in billed.items() if layer != gr.WRITER} == {}, rows
    # Each owner the grader recorded agrees with the harness's own layer record.
    for row in rows:
        is_writer_layer = tree.layer_of(row["file"]) == gr.WRITER
        assert (row["owner"] == WRITER) == is_writer_layer, row
    # And no row of the writer's work list names a Factory-written path.
    factory_paths = {rel for rel, layer in tree.layers.items() if layer in gr.FACTORY_LAYERS}
    writer_items = tuple(getattr(replay.verdict, "writer_items", ()) or ())
    assert [i for i in writer_items if any(p in i for p in factory_paths)] == [], writer_items


def _plant(tree, rel, literal):
    path = tree.root / rel
    planted = f"\n_GRADER_REPLAY_PLANTED = {literal!r}\n"
    path.write_text(path.read_text(encoding="utf-8") + planted, encoding="utf-8")


def client_brief(tree, *extra):
    """An invented user brief: the blueprint's summary plus a client identity
    minted at test time, and any ``extra`` sentences (FG-4: brief literals
    come from what the user supplied, never from the blueprint's own
    identity)."""
    import uuid

    from app.factory.blueprint import load_blueprint

    summary = str(load_blueprint(tree.blueprint_path).summary or "")
    return "\n".join((summary, f"Bill the work to acct_{uuid.uuid4().hex[:10]}.", *extra))


def _a_forbidden_literal(tree, supplied):
    """A literal the grader forbids for this brief, read from the grader's
    own derivation."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build.grader import _as_mapping, derive_forbidden

    forbidden = sorted(derive_forbidden(_as_mapping(load_blueprint(tree.blueprint_path)), supplied, ""))
    assert forbidden, "the brief yields no forbidden literal to plant"
    return forbidden[0]


def _a_py(tree, layer, within=None):
    """The largest module the layer wrote (a real block or stamp body, not a
    package marker). ``within`` restricts it to paths a stamp OWNS: a GAP
    file the writer changes is the writer's by contract."""
    paths = [rel for rel in tree.paths(layer) if rel.endswith(".py") and (within is None or rel in within)]
    assert paths, f"the composed tree has no {layer} module"
    return max(paths, key=lambda rel: ((tree.root / rel).stat().st_size, rel))


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_one_literal_is_billed_by_who_wrote_the_file(tree, monkeypatch):
    """Two inputs, one literal: planted in a vendored and in a stamped module
    it is owed by the Factory and recorded; planted in a writer-authored
    module it is a writer finding handed back. The bar is unchanged."""
    supplied = client_brief(tree)
    literal = _a_forbidden_literal(tree, supplied)
    from app.factory.build.stamp_registry import owned_paths

    vendored, stamped = _a_py(tree, gr.VENDORED), _a_py(tree, gr.STAMPED, set(owned_paths()))
    planted = "app/writer_planted_constant.py"
    _plant(tree, vendored, literal)
    _plant(tree, stamped, literal)
    tree.write_writer_file(planted, f"PLANTED = {literal!r}\n")

    replay = gr.grade(tree, monkeypatch, supplied=supplied)
    rows = {row["file"]: row for row in _literal_rows(replay)}
    for rel in (vendored, stamped, planted):
        assert rel in rows and literal in rows[rel]["literals"], sorted(rows)
    assert not replay.certified
    assert (replay.grade.get("hardwiring") or {}).get("clean") is False
    verdict = replay.verdict
    assert any(planted in item for item in verdict.writer_items), verdict.writer_items
    assert not any(planted in item for item in verdict.factory_owed), verdict.factory_owed
    for rel in (vendored, stamped):
        assert any(rel in item for item in verdict.factory_owed), verdict.factory_owed
        assert not any(rel in item for item in verdict.writer_items), verdict.writer_items
    tasks = " ".join(replay.grade.get("next_tasks") or [])
    assert planted in tasks and vendored not in tasks and stamped not in tasks, tasks


# -- a customer brief RECORD through the grade path ----------------------------


def _record_brief(tree, **extra):
    """The same invented brief as a ``customer_brief.v1`` record with one
    stored file, the shape the Floor dispatches, plus any other fields."""
    import json

    from app.factory.build.customer_brief import emit_brief

    record = json.loads(emit_brief(client_brief(tree), ["rent_roll.pdf"]))
    return json.dumps({**record, **extra})


RECORD_WORDS = ("ask", "documents", "uploads", "file_types", "pdf")


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_brief_record_makes_no_literal_of_its_own_field_names(tree, monkeypatch):
    """Graded with the brief as a record, no finding names a word of the
    record's own format, in any layer of the tree."""
    replay = gr.grade(tree, monkeypatch, supplied=_record_brief(tree))
    named = {literal for row in _literal_rows(replay) for literal in row.get("literals") or []}
    assert named.isdisjoint(RECORD_WORDS), sorted(named)


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
@pytest.mark.parametrize(
    "extra, said",
    [
        ({}, ()),
        ({"client": "Orchard Row Estates"}, ()),
        # a one-word value after an ask that holds an inch mark: the clause
        # check has no fallback for one word, so only the literal check finds it
        ({"client": "Nimbus"}, ('Lay 4" pipe per unit.',)),
    ],
)
def test_a_literal_from_a_brief_record_is_still_a_writer_finding(tree, monkeypatch, extra, said):
    """The negative: what the customer did supply (in the ask, or as the
    value of another field) and the writer copied is found and handed back."""
    import json

    from app.factory.build.customer_brief import emit_brief

    supplied = _record_brief(tree, **extra)
    if said:
        supplied = json.dumps({**json.loads(emit_brief(client_brief(tree, *said), ["rent_roll.pdf"])), **extra})
    literal = extra.get("client") or _a_forbidden_literal(tree, supplied)
    planted = "app/writer_planted_constant.py"
    tree.write_writer_file(planted, f"PLANTED = {literal!r}\n")

    replay = gr.grade(tree, monkeypatch, supplied=supplied)
    rows = {row["file"]: row for row in _literal_rows(replay)}
    assert planted in rows and literal in rows[planted]["literals"], sorted(rows)
    assert not replay.certified
    assert any(planted in item for item in replay.verdict.writer_items), replay.verdict.writer_items
