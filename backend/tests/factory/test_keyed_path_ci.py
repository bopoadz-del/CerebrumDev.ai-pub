"""U2: CI must exercise the keyed production path with a stubbed LLM wire.

Production enables the coder unless FACTORY_CODER_ENABLED=0. The default
pytest job used to run only ENV=test, so the template path was the only
path CI proved. This module is the keyed-path fixture: coder on, stub
keys present, every LLM entry point stubbed (no paid calls).

The dedicated CI step sets FACTORY_CODER_ENABLED=1 and a stub KIMI key
in the *process* environment — the same shape production uses when keyed.
Tests here also monkeypatch those values so the default factory suite
still covers the path.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.runner import RoleRunner
from tests.factory.coder_stub_bodies import invoking_handler_body

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(autouse=True)
def _keyed_stub(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_HTTP_ONESHOT", "1")
    if not os.environ.get("KIMI_API_KEY") and not os.environ.get("CEREBRUM_LLM_API_KEY"):
        monkeypatch.setenv("KIMI_API_KEY", "ci-stub-not-a-real-key")
    monkeypatch.setattr(
        "app.factory.coder.generate_model_spec",
        lambda **kw: {
            "entity": kw["capability_id"].replace("-", "_"),
            "fields": [{"name": "reference", "type": "str", "required": True}],
            "model": "stub-spec",
        },
    )
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: {
            "specs": {
                cid: {
                    "entity": cid.replace("-", "_"),
                    "fields": [{"name": "reference", "type": "str", "required": True}],
                }
                for cid in (kw.get("capabilities") or [])
            },
            "handlers": {
                cid: invoking_handler_body({"agent": True})
                for cid in (kw.get("capabilities") or [])
            },
            "model": "ci-keyed-stub",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_route_body",
        lambda **kw: {
            "body": (
                "    result = handle(payload)\n"
                '    return {"ok": True, "capability": CAPABILITY_ID, "result": result}'
            ),
            "model": "stub-route",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder._llm_code_call",
        lambda messages: ("# stub readme\n", "stub-model"),
    )
    monkeypatch.setattr(
        "app.factory.coder.review_capability_bindings",
        lambda **kw: {
            "reviews": [
                {
                    "capability_id": c.get("id"),
                    "block_ids": c.get("block_ids") or [],
                    "verdict": "endorse",
                    "reason": "stub",
                }
                for c in kw.get("capabilities") or []
            ],
            "model": "stub-collector",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder.propose_domain_test_cases",
        lambda **kw: {"cases": [], "model": "stub-tester"},
    )


def test_ci_yml_declares_the_keyed_path_job():
    """The workflow file is the fixture. If this assertion dies, U2 reopened."""
    text = CI_YML.read_text(encoding="utf-8")
    assert "FACTORY_CODER_ENABLED" in text
    assert "test_keyed_path_ci.py" in text
    assert "test_writer_coder_wiring.py" in text
    assert "KIMI_API_KEY" in text
    assert "ci-stub-not-a-real-key" in text


def test_keyed_process_env_matches_production_when_ci_sets_it():
    """Dedicated CI step sets KEYED_PATH_CI=1; skip in the default suite."""
    if os.environ.get("KEYED_PATH_CI") != "1":
        pytest.skip(reason="not the keyed-path CI job")
    assert os.environ.get("FACTORY_CODER_ENABLED") == "1"
    assert os.environ.get("KIMI_API_KEY") or os.environ.get("CEREBRUM_LLM_API_KEY")


def test_writer_takes_the_coder_entry_point_when_keyed(tmp_path, monkeypatch):
    calls = []

    def fake_oneshot(**kwargs):
        calls.append(kwargs)
        caps = list(kwargs.get("capabilities") or [])
        return {
            "specs": {
                cid: {
                    "entity": cid.replace("-", "_"),
                    "fields": [{"name": "reference", "type": "str", "required": True}],
                }
                for cid in caps
            },
            "handlers": {
                cid: invoking_handler_body({"agent": True})
                for cid in caps
            },
            "model": "ci-keyed-stub",
        }

    monkeypatch.setattr("app.factory.coder.generate_from_compiled_brief", fake_oneshot)
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    assert calls, "keyed path must dispatch the compiled brief once"
    assert len(calls) == 1
    headers = [
        p.read_text(encoding="utf-8")
        for p in (out / "app" / "actions").glob("*.py")
        if p.stem != "__init__"
    ]
    assert headers
    assert any("coder LLM (ci-keyed-stub)" in text for text in headers)
