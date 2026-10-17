"""Every Factory-owned path exists before a writer pass -- read off the one list.

Live, release cycle 8 (19fc746c):

* smoke B (account 0): "FAILED(WRITER, writer_contract, constraints.txt:
  created by the writer -- this file is Factory-owned ...)". constraints.txt
  was rendered only beside a requirements.txt, which does not exist before
  the first writer pass.
* fintech repeat pick (sess_3436e978c9e543b7): rounds 1/2 and 2/2 were the
  writer creating constraints.txt and TESTER's suites (tests/test_smoke.py,
  tests/test_routes.py, tests/test_domain_acceptance.py, ...), rendered only
  by TESTER, after the writer.

Same class as cycle 7's conftest.py / provenance.json (#717), which named two
files. Nothing here names a file: the test iterates the Factory-owned list.
Only what a build CARRIES rather than renders (the Store gate workflow, from
cerebrum-builds main with the checkpoint) and a suite the blueprint does not
call for are allowed to be absent.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build import factory_owned


def _allowed_absent(blueprint, ctx=None) -> set:
    from app.factory.build.builds_push import STORE_GATE_PATH
    from app.factory.build.placeholder_connectors import CONTRACT_TEST, render_contract_tests

    allowed = {STORE_GATE_PATH}
    if not render_contract_tests(blueprint, {}):
        allowed.add(CONTRACT_TEST)  # the blueprint declares no placeholder connector
    if ctx is not None:
        from app.factory.build.converge import provenance_record

        if provenance_record(ctx) is None:
            # A stand-in context with no blueprint/plan has no provenance inputs.
            allowed.add(factory_owned.PROVENANCE_REL)
    return allowed


def test_a_fresh_tree_has_every_owned_path_after_the_prestamp(tmp_path):
    from tests.factory.test_provenance_is_the_factorys import _ctx as real_ctx

    ctx = real_ctx(tmp_path)
    root = Path(ctx.workspace.destination)
    factory_owned.prestamp(root, ctx.blueprint, ctx)

    missing = sorted(
        rel
        for rel in factory_owned.factory_owned_paths()
        if rel not in _allowed_absent(ctx.blueprint) and not (root / rel).exists()
    )
    assert not missing, f"Factory-owned paths absent when the writer starts: {missing}"


def test_the_codewhale_writer_finds_every_owned_path_rendered(tmp_path, monkeypatch):
    """Through the production writer step: nothing owned is absent when the
    writer runs, so a writer completing its tree has nothing owned to create."""
    from tests.factory.test_writer_never_authors_factory_files import _run_pass

    seen: dict = {}

    def look(root):
        seen.update({rel: (Path(root) / rel).exists() for rel in factory_owned.factory_owned_paths()})

    ctx, dest, _result = _run_pass(tmp_path, monkeypatch, look)
    allowed = _allowed_absent(ctx.blueprint, ctx)
    absent = sorted(rel for rel, there in seen.items() if not there and rel not in allowed)
    assert not absent, absent
    assert factory_owned.recorded(dest) == []
