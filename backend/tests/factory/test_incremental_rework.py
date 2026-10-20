"""Incremental rework: a WRITER rework round seeds staging from the committed
product so the agent patches in place — passing files stay byte-identical and
the file count does not reset 1->N. Factory-owned paths are excluded from the
seed (prestamp re-renders them). These are unit tests on the seeding helpers;
they monkeypatch the Factory-owned set so they need no store fixture.

Fail-first: before the change the helpers did not exist and a rework round ran
against an EMPTY staging tree (the agent re-authored every file); these assert
the committed product is seeded instead.
"""
import types
from pathlib import Path

from app.factory.build.authority import BuildRole
from app.factory.build.runner import RoleRunner


def _ns(workspace):
    return types.SimpleNamespace(workspace=Path(workspace))


def _seed_ns(workspace):
    """A stub self for _should_seed_rework: provides workspace plus the two
    collaborator methods it calls (the flag staticmethod and the committed
    probe), so the predicate's branch logic is exercised directly."""
    ns = types.SimpleNamespace(workspace=Path(workspace))
    ns._incremental_rework_on = RoleRunner._incremental_rework_on
    ns._writer_committed_before = lambda: RoleRunner._writer_committed_before(ns)
    return ns


def test_flag_default_on_and_off_switch(monkeypatch):
    monkeypatch.delenv("FACTORY_INCREMENTAL_REWORK", raising=False)
    assert RoleRunner._incremental_rework_on() is True  # ON by default (option B)
    for off in ("0", "off", "false", "NO"):
        monkeypatch.setenv("FACTORY_INCREMENTAL_REWORK", off)
        assert RoleRunner._incremental_rework_on() is False
    for on in ("on", "1", "yes", ""):
        monkeypatch.setenv("FACTORY_INCREMENTAL_REWORK", on)
        assert RoleRunner._incremental_rework_on() is True


def test_committed_before_is_false_on_first_pass(tmp_path):
    ns = _ns(tmp_path)
    # no app/ yet -> first pass, seed must not fire
    assert RoleRunner._writer_committed_before(ns) is False
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "__init__.py").write_text("", encoding="utf-8")
    # only the package marker -> still not a prior authored pass
    assert RoleRunner._writer_committed_before(ns) is False
    (actions / "work_orders.py").write_text("def handle(p): return p\n", encoding="utf-8")
    assert RoleRunner._writer_committed_before(ns) is True


def test_committed_before_sees_routes_or_models_without_actions(tmp_path):
    # a product that renders no app/actions handler still resumes: routes.py
    # (or models.py / main.py) is a prior authored pass.
    ns = _ns(tmp_path)
    (tmp_path / "app").mkdir(parents=True)
    assert RoleRunner._writer_committed_before(ns) is False
    (tmp_path / "app" / "routes.py").write_text("router = 1\n", encoding="utf-8")
    assert RoleRunner._writer_committed_before(ns) is True


def test_should_seed_rework_guard(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_INCREMENTAL_REWORK", raising=False)  # default ON
    # a committed prior pass present
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "work_orders.py").write_text("x = 1\n", encoding="utf-8")
    ns = _seed_ns(tmp_path)
    staging = tmp_path / ".staging"
    # rework round, flag on, prior pass -> seed
    assert RoleRunner._should_seed_rework(ns, BuildRole.WRITER, staging, False) is True
    # resuming an interrupted pass -> never seed
    assert RoleRunner._should_seed_rework(ns, BuildRole.WRITER, staging, True) is False
    # not staged -> never seed
    assert RoleRunner._should_seed_rework(ns, BuildRole.WRITER, None, False) is False
    # a non-WRITER role -> never seed
    assert RoleRunner._should_seed_rework(ns, BuildRole.CLONER, staging, False) is False
    # flag disabled -> never seed, even on a rework round
    monkeypatch.setenv("FACTORY_INCREMENTAL_REWORK", "off")
    assert RoleRunner._should_seed_rework(ns, BuildRole.WRITER, staging, False) is False
    monkeypatch.delenv("FACTORY_INCREMENTAL_REWORK", raising=False)
    # first pass (empty destination) -> never seed
    empty = _seed_ns(tmp_path / "empty")
    (tmp_path / "empty").mkdir()
    assert RoleRunner._should_seed_rework(empty, BuildRole.WRITER, staging, False) is False


def test_seed_keeps_passing_files_and_drops_factory_owned(tmp_path, monkeypatch):
    dest = tmp_path / "dest"
    (dest / "app" / "actions").mkdir(parents=True)
    (dest / "app" / "actions" / "work_orders.py").write_text("HANDLER-A\n", encoding="utf-8")
    (dest / "app" / "actions" / "assets.py").write_text("HANDLER-B\n", encoding="utf-8")
    (dest / "tests").mkdir(parents=True)
    (dest / "tests" / "test_work_orders.py").write_text("TEST-A\n", encoding="utf-8")
    (dest / "docs").mkdir(parents=True)
    (dest / "docs" / "openapi.json").write_text("{}", encoding="utf-8")
    # a Factory-owned file present in the committed product
    (dest / "app" / "domain_ops.py").write_text("FACTORY-OWNED\n", encoding="utf-8")
    # vendor is large and not writer-authored -> must NOT be seeded
    (dest / "vendor" / "blocks").mkdir(parents=True)
    (dest / "vendor" / "blocks" / "big.py").write_text("X" * 100, encoding="utf-8")

    monkeypatch.setattr(
        "app.factory.build.factory_owned.factory_owned_paths",
        lambda: ("app/domain_ops.py",),
    )

    staging = tmp_path / "staging"
    staging.mkdir()
    kept = RoleRunner._seed_rework_staging(_ns(dest), staging)

    # passing files are seeded BYTE-IDENTICAL to the committed product (so the
    # writer can patch them in place). Compare to the source bytes directly,
    # not a literal, so the assertion is exact regardless of OS newline mode.
    for rel in (
        "app/actions/work_orders.py",
        "app/actions/assets.py",
        "tests/test_work_orders.py",
        "docs/openapi.json",
    ):
        assert (staging / rel).read_bytes() == (dest / rel).read_bytes()
    # Factory-owned path dropped from the seed so prestamp re-renders it fresh
    assert not (staging / "app" / "domain_ops.py").exists()
    # vendor not seeded (not a writer-authored area)
    assert not (staging / "vendor").exists()
    # file count did not reset to ~1: the product tree is present
    assert kept >= 4
