"""FG-1 on the FG-3 replay harness: every grader finding has an owner.

The tree is composed by the Factory's own code (the CLONER's vendoring from
the pinned Store, every registered stamp) plus an invented writer layer, and
graded through ``RoleRunner._writer_grader`` with the grader on. The harness
records which layer wrote each path by digest diff, independently of the
grader's own provenance read, so the two are checked against each other.

FG-3 kept these cases uncollected as the red-on-master proof; FG-1 enables
the ownership cases. Certification of a clean tree (FG-4, FG-2) stays in
``red_grader_replay_certifies.py`` until those fixes land.

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


def _a_forbidden_literal(tree):
    """A literal the grader forbids for this blueprint, read from the grader's
    own derivation of the blueprint's structured values."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build.grader import _as_mapping, derive_forbidden

    forbidden = sorted(derive_forbidden(_as_mapping(load_blueprint(tree.blueprint_path)), "", ""))
    assert forbidden, "the blueprint yields no forbidden literal to plant"
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
    literal = _a_forbidden_literal(tree)
    from app.factory.build.stamp_registry import owned_paths

    vendored, stamped = _a_py(tree, gr.VENDORED), _a_py(tree, gr.STAMPED, set(owned_paths()))
    planted = "app/writer_planted_constant.py"
    _plant(tree, vendored, literal)
    _plant(tree, stamped, literal)
    tree.write_writer_file(planted, f"PLANTED = {literal!r}\n")

    replay = gr.grade(tree, monkeypatch)
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
