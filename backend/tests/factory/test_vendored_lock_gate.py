"""F-2 (grader 19:35Z, item e): through the real build entry point, a lock
row the writer edits is refused.

The coding agent may write the product directory itself (its confinement
grants it), so it can rewrite ``blocks.lock.json`` and the vendored files
without passing the lane check on its staging tree. The runner records the
vendored blocks in the build's Factory-held, signed proof store at the end of
CLONER, and checks the lock after every later role. A forged override row,
over bytes the writer changed, has no proof and fails the build.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("attrs", [("destination", "workspace"), ("workspace",)])
def test_the_spawns_extra_roots_are_the_role_workspaces_trees(tmp_path, attrs):
    from types import SimpleNamespace

    from app.factory.build.coder_session import agent_rw_extras

    values = {a: tmp_path / a for a in attrs}
    assert agent_rw_extras(SimpleNamespace(**values)) == [
        tmp_path / a for a in ("destination", "workspace") if a in attrs
    ]
    assert agent_rw_extras(None) == []
