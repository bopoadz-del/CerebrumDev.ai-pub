"""requirements.txt must declare the vendored blocks' packages, resume or not.

Live, a voice-agent platform: 23 vendored blocks, a requirements.txt of seven
packages and none of theirs. The clean Docker image had no numpy,
``vector_search`` could not load, one test went red and the image build failed
-- "acceptance.py in Docker 0/13". Reproduced exactly in a clean virtualenv
(1 failed / 87 passed), and 89 passed once the file declared what the vendored
source imports.

Cause: the CLONER kept the obligations in the runner's in-memory state, which
"dies with the process". The build timed out and resumed, so WRITER rendered
requirements.txt from None.
"""

from __future__ import annotations

import inspect

from app.factory.build import roles_handlers
from app.factory.build.block_inputs import align_spec_to_handler_source, settings_names
from app.factory.build.block_obligations import dependency_obligations_on_disk
from app.factory.build.roles_handlers import _render_requirements


def _vendored(tmp_path, source: str):
    block = tmp_path / "vendor" / "cerebrum" / "blocks"
    block.mkdir(parents=True)
    (block / "thing.py").write_text(source, encoding="utf-8")
    return tmp_path


def test_obligations_are_read_off_the_disk_with_no_state_at_all(tmp_path):
    ws = _vendored(tmp_path, "import numpy\n\n\ndef f():\n    import httpx\n")

    deps = dependency_obligations_on_disk(ws)

    assert deps["numpy"]["reach"] == "module"
    assert deps["httpx"]["reach"] == "lazy"
    rendered = _render_requirements(deps)
    assert "\nnumpy" in rendered and "\nhttpx" in rendered


def test_a_workspace_with_nothing_vendored_obliges_nothing(tmp_path):
    assert dependency_obligations_on_disk(tmp_path) == {}


def test_the_render_site_does_not_trust_the_runners_memory():
    """The defect was one expression: ctx.state.get("vendored_dependencies").
    A resumed build has no such key."""
    source = inspect.getsource(roles_handlers)
    render_site = source[source.index('"requirements.txt",\n        _render_requirements('):][:200]

    assert "ctx.state" not in render_site
    assert "dependency_obligations_on_disk(" in source


# ── settings are not record fields, whatever their case ────────────────────


def test_a_lower_case_name_read_from_the_environment_is_a_setting():
    """Owner: "only the ALL CAPS, u sure?" No. The rule is what the code READS
    FROM THE ENVIRONMENT; upper case is only its usual spelling."""
    source = (
        "import os\n"
        'CREDS = ("zz_client_id", "zz_secret")\n'
        'REQUIRED = ["zz_client_id", "zz_secret", "folder_name"]\n\n\n'
        "def configured():\n"
        "    return all(os.environ.get(key) for key in CREDS)\n\n\n"
        "def handle(payload):\n"
        "    for field in REQUIRED:\n"
        "        if field not in payload:\n"
        '            return {"error": "missing required field: " + field}\n'
    )

    assert settings_names(source) == {"zz_client_id", "zz_secret"}
    # Neither the settings nor the roster declare a record field: only the
    # schema does (owner ruling 2026-10-05).
    aligned, changed = align_spec_to_handler_source({"fields": []}, source)
    assert aligned["fields"] == [] and changed == []


def test_prose_naming_a_setting_does_not_make_it_one():
    source = '"""Set os.environ["zz_in_a_docstring"] to enable."""\nX = 1\n'

    assert settings_names(source) == set()
