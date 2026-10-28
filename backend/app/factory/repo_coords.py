"""Repository coordinates for the Factory's Store, Builds and self repos.

Owner/repo coordinates are configuration, never hard-coded in the product
tree. They are read from the environment with NO default, resolved lazily at
use-time (never at import or as a function default, so the app still imports
and boots without them), and a missing value fails closed with a clear error.
CI and tests set fixture values; a deployment sets the real coordinates on the
task environment.
"""
from __future__ import annotations

import os


class RepoCoordError(RuntimeError):
    """A required repository coordinate is not configured."""


def _require(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RepoCoordError(
            f"{name} is not set; the Factory needs the <owner>/<repo> coordinate "
            f"in the environment (no default)."
        )
    if "/" not in value:
        raise RepoCoordError(
            f"{name}={value!r} is not a <owner>/<repo> coordinate (missing '/')."
        )
    return value


def store_repo() -> str:
    """``<owner>/<repo>`` of the block store."""
    return _require("FACTORY_STORE_REPO")


def builds_repo() -> str:
    """``<owner>/<repo>`` of the certified-builds store."""
    return _require("FACTORY_BUILDS_REPO")


def factory_repo() -> str:
    """``<owner>/<repo>`` of the Factory itself."""
    return _require("FACTORY_SELF_REPO")


def _owner(coord: str) -> str:
    return coord.partition("/")[0]


def _name(coord: str) -> str:
    return coord.partition("/")[2]


def store_owner() -> str:
    return _owner(store_repo())


def store_name() -> str:
    return _name(store_repo())


def builds_owner() -> str:
    return _owner(builds_repo())


def builds_name() -> str:
    return _name(builds_repo())


def store_git_url() -> str:
    """HTTPS clone URL of the block store."""
    return f"https://github.com/{store_repo()}.git"
