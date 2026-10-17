"""A file the Factory rewrites whole is a Factory-owned file -- never a silent overwrite.

Owner rule: the writer never authors a Factory-owned file; a pass that touches
one is rejected with the path named, never silently overwritten afterwards.
Before cycle 8's fix, ``app/money_settings.py`` and ``docs/declared_locale.json``
were re-stamped whole before EVERY writer pass (money_contract.
emit_money_artifacts) but were not on the Factory-owned list: a writer edit
was erased on the next pass without a word, and the writer was never told.

Two derived checks, no list of their own:

* every stamp in the registry that writes whole (OWNED) is on the owned list;
* behaviourally, through the production writer step: a writer that rewrites
  every file it finds that is not on the owned list keeps those bytes on its
  next pass -- unless the path is a SHARED file (only the Factory's marked
  block is the Factory's) or Factory-internal (never shipped).
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build import factory_owned
from app.factory.build.builds_push import FACTORY_INTERNAL_PATHS
from app.factory.build.factory_block import outside
from app.factory.build.stamp_registry import OWNED, shared_paths, stamps

SENTINEL = "# the writer's own bytes\n"


def test_every_whole_file_stamp_target_is_factory_owned():
    owned = set(factory_owned.factory_owned_paths())
    whole = {p for s in stamps() if s.kind == OWNED for p in s.paths}
    assert whole <= owned, sorted(whole - owned)


def test_the_money_settings_the_factory_restamps_are_owned():
    from app.factory.build.money_contract import DECLARED_LOCALE_REL, MONEY_SETTINGS_REL

    owned = set(factory_owned.factory_owned_paths())
    for rel in (MONEY_SETTINGS_REL, DECLARED_LOCALE_REL):
        assert Path(rel).as_posix() in owned, rel


def test_no_writer_edit_is_silently_overwritten_by_a_pre_pass_stamp(tmp_path, monkeypatch):
    from app.factory.build.roles_handlers import run_writer
    from app.factory.build.writer_control import CODEWHALE_WRITER_ENV
    from tests.factory.test_codewhale_writer_dispatch import _ctx, _plant_authored_handler, _receipt

    monkeypatch.setenv(CODEWHALE_WRITER_ENV, "1")
    owned = set(factory_owned.factory_owned_paths())
    shared = set(shared_paths())
    planted: dict = {}
    seen: dict = {}
    passes: list = []

    def worker(_prompt, root, **_kw):
        root = Path(root)
        passes.append(root)
        if len(passes) == 1:
            pass  # the Factory lays out everything it writes around a first pass
        elif len(passes) == 2:
            for path in sorted(root.rglob("*")):
                rel = path.relative_to(root).as_posix()
                if (
                    path.is_file()
                    and rel not in owned
                    and rel not in FACTORY_INTERNAL_PATHS
                    and not rel.startswith(("app/actions/", ".git/"))
                    and "__pycache__" not in rel
                ):
                    path.write_text(SENTINEL, encoding="utf-8")
                    planted[rel] = True
        else:
            seen.update({rel: (root / rel).read_text(encoding="utf-8") for rel in planted if (root / rel).is_file()})
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", worker)
    dest = tmp_path / "build"
    _plant_authored_handler(dest)
    for _ in range(3):
        run_writer(_ctx(tmp_path))

    assert planted and seen, "the writer step never ran three times"
    overwritten = sorted(
        rel
        for rel, text in seen.items()
        if text != SENTINEL and not (rel in shared and outside(text) == SENTINEL)
    )
    assert not overwritten, f"writer bytes silently overwritten before the next pass: {overwritten}"
