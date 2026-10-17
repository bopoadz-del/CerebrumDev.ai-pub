"""Honesty invariant: /health flag fields are EVALUATED bools, one source of truth.

Root cause (2026-07-28): /health reported `resident_engineer_enabled` as the raw
env string via `os.getenv(..., "false")`, while `build_mode_enabled` and
`kimi_workbench_enabled` reported `_truthy()`-evaluated bools. Two sources of
truth — health was the unreliable one, and it let the same env value read as
"enabled" in one place and "disabled" in another.

This pins the contract that would have caught it: for ANY raw env string, each
health flag field is a real bool and equals the flag function's evaluation. A
raw-string leak (the original bug) fails the `is bool` assertion immediately.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.resident_engineer.flags import resident_engineer_enabled
from app.workbench.flags import build_mode_enabled

# (env value, expected truthiness) — mixes the messy real-world casings that a
# raw-string report would mishandle.
_CASES = [
    ("true", True),
    ("True", True),
    ("on", True),
    ("1", True),
    ("yes", True),
    ("  TRUE  ", True),
    ("false", False),
    ("0", False),
    ("no", False),
    ("", False),
    ("garbage", False),
]

_FLAGS = {
    "resident_engineer_enabled": ("RESIDENT_ENGINEER_ENABLED", resident_engineer_enabled),
    "build_mode_enabled": ("BUILD_MODE_ENABLED", build_mode_enabled),
}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize("field,env_var,fn", [(f, e, fn) for f, (e, fn) in _FLAGS.items()])
@pytest.mark.parametrize("value,expected", _CASES)
def test_health_flag_is_evaluated_bool_matching_flag_fn(
    client, monkeypatch, field, env_var, fn, value, expected
):
    monkeypatch.setenv(env_var, value)

    payload = client.get("/health").json()

    # 1. Never a raw string — this alone catches the original leak.
    assert isinstance(payload[field], bool), (
        f"{field} reported {payload[field]!r} (type {type(payload[field]).__name__}); "
        "health must report an evaluated bool, not the raw env string"
    )
    assert fn() == expected, f"flag fn {fn()} disagrees with env={value!r}"
    # 2. Matches the single source of truth (the flag function).
    assert payload[field] == fn() == expected, (
        f"{field}={payload[field]} disagrees with flag fn {fn()} for env={value!r}"
    )
