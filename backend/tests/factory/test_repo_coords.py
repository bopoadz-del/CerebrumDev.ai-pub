"""repo_coords reads the Store/Builds/Factory coordinates from the environment
with NO default, failing closed when a value is missing. Two inputs: unset ->
RepoCoordError; set -> the configured coordinate resolves (and the owner/name/
url helpers derive from it, never a hard-coded literal).
"""
from __future__ import annotations

import pytest

from app.factory import repo_coords

_VARS = ("FACTORY_STORE_REPO", "FACTORY_BUILDS_REPO", "FACTORY_SELF_REPO")


def _clear(monkeypatch) -> None:
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_each_resolver_fails_closed_when_unset(monkeypatch):
    _clear(monkeypatch)
    for fn in (repo_coords.store_repo, repo_coords.builds_repo, repo_coords.factory_repo):
        with pytest.raises(repo_coords.RepoCoordError):
            fn()


def test_a_blank_value_also_fails_closed(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FACTORY_STORE_REPO", "   ")
    with pytest.raises(repo_coords.RepoCoordError):
        repo_coords.store_repo()


def test_resolvers_return_two_distinct_configured_coordinates(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("FACTORY_STORE_REPO", "org-a/blocks")
    monkeypatch.setenv("FACTORY_BUILDS_REPO", "org-b/builds")
    monkeypatch.setenv("FACTORY_SELF_REPO", "org-c/factory")

    assert repo_coords.store_repo() == "org-a/blocks"
    assert repo_coords.builds_repo() == "org-b/builds"
    assert repo_coords.factory_repo() == "org-c/factory"

    # The split/url helpers derive from the configured value, so a repoint
    # flows through without any literal in the product tree.
    assert repo_coords.store_owner() == "org-a"
    assert repo_coords.store_name() == "blocks"
    assert repo_coords.builds_owner() == "org-b"
    assert repo_coords.builds_name() == "builds"
    assert repo_coords.store_git_url() == "https://github.com/org-a/blocks.git"
