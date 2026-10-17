"""The incremental mypy ratchet must list the HTTP/ops modules this PR added."""

from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MYPY_INI = BACKEND / "mypy.ini"
CI = BACKEND.parent / ".github" / "workflows" / "ci.yml"

# New HTTP/ops modules start on the scoped list. Expanding the ratchet means
# appending to mypy.ini files= after that module is clean — not deleting this.
SCOPED_MODULES = (
    "app/core/cors_policy.py",
    "app/core/metrics.py",
    "app/core/session_guard.py",
    "app/core/observability.py",
)


def test_mypy_ini_lists_the_scoped_modules():
    text = MYPY_INI.read_text(encoding="utf-8")
    assert "follow_imports = silent" in text
    for rel in SCOPED_MODULES:
        assert rel in text, f"{rel} missing from mypy.ini files="
        assert (BACKEND / rel).is_file(), f"{rel} does not exist"


def test_ci_has_a_failing_scoped_mypy_job():
    text = CI.read_text(encoding="utf-8")
    assert "backend-types:" in text
    assert "python -m mypy" in text
    assert "mypy.ini" in text
