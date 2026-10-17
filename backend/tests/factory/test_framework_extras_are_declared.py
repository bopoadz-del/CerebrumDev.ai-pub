"""A framework feature the coder used is declared, or it fails at first request.

Live: a voice platform's Twilio webhooks are form-encoded by definition. Its
routes called ``await request.form()``, nothing imported a package by name, and
``python-multipart`` was never declared -- so in the image every webhook
answered ``422 webhook body is not readable``. The inbound half of the platform
was dead, and the only reason anyone found out is that one test happened to post
a form.

The declaration has to come from the FEATURE, because the import scan sees a
clean tree: FastAPI declares starlette and pydantic, and nothing else names the
packages behind form parsing, templates, session cookies or ``EmailStr``.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.factory_refresh import merged_requirements
from app.factory.build.framework_extras import (
    distributions,
    framework_extras,
    framework_lines,
)
from app.factory.build.roles_handlers import _render_requirements


def _tree(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    return root


# ── the feature is what triggers the declaration ──────────────────────────


def test_a_route_that_parses_a_form_declares_the_parser(tmp_path):
    root = _tree(
        tmp_path / "ws",
        {
            "app/routers/voice.py": (
                "async def webhook(request):\n"
                "    form = await request.form()\n"
                "    return dict(form)\n"
            ),
        },
    )

    assert "python-multipart>=0.0.9" in framework_extras(root)
    assert "python-multipart" in distributions(root)


def test_an_upload_endpoint_declares_the_parser(tmp_path):
    root = _tree(tmp_path / "ws", {"app/routes.py": "def up(f: UploadFile):\n    return f\n"})

    assert "python-multipart>=0.0.9" in framework_extras(root)


def test_a_tree_that_parses_no_form_does_not_declare_the_parser(tmp_path):
    """Declared because it is used, not because every product might use it."""
    root = _tree(tmp_path / "ws", {"app/routes.py": "def read(payload: dict):\n    return payload\n"})

    assert framework_extras(root) == {}
    assert framework_lines(root) == []


def test_the_suite_counts_as_source(tmp_path):
    """The release gate runs the suite inside the image, so the suite's needs
    are the image's needs: a helper that only tests reach still declares."""
    root = _tree(
        tmp_path / "ws",
        {
            "app/routes.py": "def read(payload: dict):\n    return payload\n",
            "tests/test_edge.py": (
                "def build(f: UploadFile):\n    return f.filename\n"
            ),
        },
    )

    assert "python-multipart>=0.0.9" in framework_extras(root)


def test_templates_sessions_and_email_each_pull_their_own_package(tmp_path):
    root = _tree(
        tmp_path / "ws",
        {
            "app/web.py": (
                "from fastapi.templating import Jinja2Templates\n"
                "from starlette.middleware.sessions import SessionMiddleware\n"
            ),
            "app/models.py": "from pydantic import BaseModel, EmailStr\n",
        },
    )

    declared = set(distributions(root))

    assert {"jinja2", "itsdangerous", "email-validator"} <= declared


# ── it reaches requirements.txt, at build time and on a resume ─────────────


def test_the_rendered_requirements_carry_it(tmp_path):
    root = _tree(
        tmp_path / "ws",
        {"app/routers/voice.py": "async def w(request):\n    return await request.form()\n"},
    )

    text = _render_requirements({}, root=root)

    assert "python-multipart" in text
    # ... and the reason, so the next reader does not delete it as unused.
    assert "form parsing" in text


def test_without_a_tree_the_floor_is_unchanged(tmp_path):
    """No tree, no scan: the callers that render a floor stay byte-identical."""
    assert _render_requirements(None) == _render_requirements({})
    assert "python-multipart" not in _render_requirements({})


def test_a_resumed_build_gains_the_package_it_was_missing(tmp_path):
    """The branch that failed live: the tree parses forms, requirements.txt
    does not say so, and the resume must fix it without dropping a line."""
    root = _tree(
        tmp_path / "ws",
        {
            "requirements.txt": "fastapi>=0.141\nstarlette>=1.6\nzz-writer-added==1.2.3\n",
            "app/routers/voice.py": (
                "async def webhook(request):\n    return await request.form()\n"
            ),
        },
    )

    merged = merged_requirements(root)

    assert "python-multipart" in merged
    assert "zz-writer-added==1.2.3" in merged
    assert merged.count("fastapi") == 1


def test_a_second_resume_adds_nothing(tmp_path):
    root = _tree(
        tmp_path / "ws",
        {
            "requirements.txt": "fastapi>=0.141\npython-multipart>=0.0.9\n",
            "app/routers/voice.py": "async def w(request):\n    return await request.form()\n",
        },
    )

    assert merged_requirements(root).count("python-multipart") == 1
