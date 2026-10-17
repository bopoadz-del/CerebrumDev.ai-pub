"""A connector with no credentials is a DECLARED STUB, never a 422.

Live: ``google_drive: HTTP 422: {"detail":"Missing required field:
GOOGLE_CLIENT_ID"}``. Credentials are ENV configuration supplied by the
DevOps team at deploy; the build must answer "unavailable, declared", not
refuse. Nothing here names a real setting or block.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.block_obligations import ensure_record_envelope
from app.factory.build.roles_constants import _DISPATCH_RUNTIME
from app.factory.build.store_acceptance import render_github_ci

_TOKEN = "ZZ_FACTORY_TEST_CONNECTOR_TOKEN"


# ── F1a: credentials never reach a request schema ─────────────────────────


def test_a_credential_field_is_dropped_from_every_spec():
    spec = {
        "entity": "drive_link",
        "fields": [
            {"name": _TOKEN, "required": True},
            {"name": "folder_name", "required": True},
        ],
    }

    out, _added = ensure_record_envelope(spec)
    names = [f["name"] for f in out["fields"]]

    assert _TOKEN not in names
    assert "folder_name" in names


def test_a_spec_with_no_credential_is_left_alone():
    spec = {"entity": "e", "fields": [{"name": "reference"}, {"name": "status"}]}

    out, _ = ensure_record_envelope(spec)

    assert [f["name"] for f in out["fields"]] == ["reference", "status"]


# ── F1b: the dispatch feed degrades to a declared stub ────────────────────


def _platform(tmp_path: Path, runtime: str) -> Path:
    block = tmp_path / "vendor" / "blocks" / "connector"
    block.mkdir(parents=True)
    (block / "block.py").write_text(runtime, encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "dispatch.py").write_text(_DISPATCH_RUNTIME, encoding="utf-8")
    return tmp_path


def _call(root: Path, env: dict | None = None) -> dict:
    probe = textwrap.dedent(
        f"""
        import json, sys
        sys.path.insert(0, {str(root)!r})
        from app.dispatch import execute
        print(json.dumps(execute("connector", {{}}, action="run")))
        """
    )
    clean = {k: v for k, v in os.environ.items() if k != _TOKEN}
    clean.update(env or {})
    proc = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, env=clean
    )
    assert proc.returncode == 0, proc.stderr
    import json

    return json.loads(proc.stdout.strip().splitlines()[-1])


_CONNECTOR = f'''
import os


def run(**kwargs):
    if not os.getenv("{_TOKEN}"):
        raise RuntimeError("no credentials: set {_TOKEN} to enable")
    return {{"status": "success", "result": {{"live": True}}}}
'''


def test_missing_credentials_answer_as_a_declared_stub(tmp_path):
    out = _call(_platform(tmp_path, _CONNECTOR))

    assert out["ok"] is True
    assert out["blocks_unavailable"] == ["connector"]
    assert out["result"] == {"stub": True}
    assert _TOKEN in out["reason"]


def test_with_credentials_the_real_block_runs(tmp_path):
    out = _call(_platform(tmp_path, _CONNECTOR), env={_TOKEN: "configured"})

    assert out.get("result") == {"live": True}
    assert "blocks_unavailable" not in out


def test_a_real_failure_is_never_dressed_up_as_a_stub(tmp_path):
    """The guard: only a refusal naming an UNSET setting the vendored source
    reads is a stub. Anything else is still an error."""
    runtime = _CONNECTOR.replace(
        "raise RuntimeError", "raise ValueError('bad input');RuntimeError"
    )

    out = _call(_platform(tmp_path, runtime))

    assert out["ok"] is False
    assert "blocks_unavailable" not in out


def test_naming_a_setting_that_is_set_is_still_an_error(tmp_path):
    runtime = _CONNECTOR.replace(
        f'if not os.getenv("{_TOKEN}"):', "if True:"
    )

    out = _call(_platform(tmp_path, runtime), env={_TOKEN: "configured"})

    assert out["ok"] is False


# ── F2: boot-clean guard in the product CI ────────────────────────────────


def test_product_ci_imports_the_app_with_an_empty_environment():
    import yaml

    doc = yaml.safe_load(render_github_ci())
    runs = [str(s.get("run")) for j in doc["jobs"].values() for s in j.get("steps", [])]

    assert any("env -i" in r and 'import app.main' in r for r in runs), runs


def test_f3_judges_only_a_top_level_declared_stub():
    """Live: TESTER red on "project_knowledge_grounding: declared stub must be
    ok:true and name blocks_unavailable". A capability whose result merely
    CONTAINS a stubbed block (nested in its block list) was held to the
    declared-stub shape. Only a response that declares itself a stub at the
    top level is judged; nested stubs and ok:false stay allowed here."""
    import inspect

    from app.factory.build import roles_handlers

    src = inspect.getsource(roles_handlers)
    assert "elif 'blocks_unavailable' in body:" in src
    assert "str(body.get('result'))" not in src
