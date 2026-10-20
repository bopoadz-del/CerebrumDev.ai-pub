"""Item 2 foundation: the cross-session stage-resume key covers the brief, the
Factory git sha and the Store pin -- not just the blueprint. A stage may be
resumed only when ALL of these are unchanged; any one changing redoes the
stage. blueprint_hash stays blueprint-only (the in-session ratchet relies on
that), so full_input_hash must be DISTINCT from it and must move when the
brief / git sha / Store pin move.

Fail-first: before the change full_input_hash did not exist and resume keyed
on blueprint_hash alone (a new Factory deploy or a moved Store pin would not
invalidate a cached stage).
"""
from app.factory.build.runner import blueprint_hash, full_input_hash


class _BP:
    """Minimal blueprint stub exposing model_dump like a pydantic model."""

    def __init__(self, d):
        self._d = d

    def model_dump(self, mode=None, exclude_defaults=False):
        return dict(self._d)


BP = _BP({"capabilities": ["a", "b"], "country": "QA"})
LOCK = {"schema": "factory.blocks.lock.v1", "store": {"sha": "6320266e"}, "blocks": {"x": "h1"}}


def test_stable_when_all_inputs_unchanged():
    h1 = full_input_hash(BP, brief="build a fleet platform", factory_git_sha="ed5f4bc", blocks_lock=LOCK)
    h2 = full_input_hash(BP, brief="build a fleet platform", factory_git_sha="ed5f4bc", blocks_lock=LOCK)
    assert h1 == h2


def test_moves_when_brief_changes():
    base = full_input_hash(BP, brief="one", factory_git_sha="sha1", blocks_lock=LOCK)
    other = full_input_hash(BP, brief="two", factory_git_sha="sha1", blocks_lock=LOCK)
    assert base != other


def test_moves_when_factory_sha_changes():
    base = full_input_hash(BP, brief="one", factory_git_sha="sha1", blocks_lock=LOCK)
    deployed = full_input_hash(BP, brief="one", factory_git_sha="sha2", blocks_lock=LOCK)
    assert base != deployed


def test_moves_when_store_pin_changes():
    base = full_input_hash(BP, brief="one", factory_git_sha="sha1", blocks_lock=LOCK)
    moved_lock = {**LOCK, "store": {"sha": "deadbeef"}}
    moved = full_input_hash(BP, brief="one", factory_git_sha="sha1", blocks_lock=moved_lock)
    assert base != moved


def test_moves_when_blueprint_changes():
    base = full_input_hash(BP, brief="one", factory_git_sha="sha1", blocks_lock=LOCK)
    bp2 = _BP({"capabilities": ["a", "b", "c"], "country": "QA"})
    assert full_input_hash(bp2, brief="one", factory_git_sha="sha1", blocks_lock=LOCK) != base


def test_distinct_from_blueprint_hash():
    # proves the extra inputs ARE covered: the full key is not just the
    # blueprint key (blueprint_hash would miss a brief / sha / pin change).
    assert full_input_hash(BP, brief="one", factory_git_sha="sha1", blocks_lock=LOCK) != blueprint_hash(BP)


def test_empty_inputs_do_not_crash():
    # a run with no brief / sha / lock still produces a stable key
    h = full_input_hash(BP)
    assert isinstance(h, str) and len(h) == 64
    assert h == full_input_hash(BP, brief="", factory_git_sha="", blocks_lock=None)
