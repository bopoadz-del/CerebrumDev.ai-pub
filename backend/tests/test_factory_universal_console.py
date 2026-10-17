"""Every export ships a working UI — the standard universal console.

New-shape tests for the 2026-08-02 field finding that generated platforms
were API-only: `ui_modules` emitted orphan .tsx files with no page, no
build, no route — a user who opened the export saw nothing at all. The
universal console is one self-contained static page served at ``/``,
data-driven from the export's own APIs so the identical asset works on
every platform; capability-specific UI is the coder's job at regeneration,
a missing UI is not.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _template_engine(monkeypatch):
    """The universal console is a TEMPLATE-path artifact.

    Production now builds through the role runner, which does not emit it
    (registered in KNOWN_INCOMPLETE 1b as not-yet-ported). These tests keep
    guarding the template contract they were written for, which is still the
    documented revert path.
    """
    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "template")

import os
import re
from pathlib import Path

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

from app.factory import coder as coder_mod  # noqa: E402
from app.factory.product_architect import (  # noqa: E402
    draft_blueprint_from_brief,
    generate_product,
)

STANDARD = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "factory"
    / "standards"
    / "universal_console.html"
)

BRIEF = "Build me a logistics platform with dashboard, database and audit."


def _generate(tmp_path, monkeypatch):
    monkeypatch.setenv(coder_mod.CODER_ENABLED_ENV, "0")
    bp = draft_blueprint_from_brief(BRIEF, use_llm=False)
    out = tmp_path / "export"
    generate_product(bp, out)
    return out


def test_standard_console_asset_exists_and_is_self_contained():
    assert STANDARD.is_file(), "factory standards lost universal_console.html"
    html = STANDARD.read_text(encoding="utf-8")
    # Exports may run air-gapped: the console must not reach any external
    # host — no CDN scripts, no remote styles, no fonts.
    external = re.findall(r'(?:src|href)\s*=\s*["\']https?://', html)
    assert not external, f"console references external resources: {external}"
    for endpoint in ("/health", "/v1/capabilities", "/v1/actions/", "/v1/workflows", "/v1/agents"):
        assert endpoint in html, f"console does not use {endpoint}"


def test_export_ships_the_console_and_serves_it(tmp_path, monkeypatch):
    out = _generate(tmp_path, monkeypatch)

    shipped = out / "app" / "static" / "console.html"
    assert shipped.is_file(), "export has no console asset — API-only skeleton again"
    assert shipped.read_text(encoding="utf-8") == STANDARD.read_text(encoding="utf-8")

    main_py = (out / "app" / "main.py").read_text(encoding="utf-8")
    assert '@app.get("/", include_in_schema=False)' in main_py, (
        "generated main.py does not serve the console at /"
    )

    smoke = (out / "tests" / "test_smoke.py").read_text(encoding="utf-8")
    assert "test_console_ui_is_served_at_root" in smoke, (
        "the export's own smoke suite no longer proves the UI is served"
    )


def test_readme_tells_the_user_the_console_exists(tmp_path, monkeypatch):
    out = _generate(tmp_path, monkeypatch)
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "universal console" in readme.lower()
