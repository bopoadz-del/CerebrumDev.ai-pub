"""The WRITER asks the coding agent, and never lies about who wrote the code.

New-shape tests for wiring the coder into the runner's WRITER role. The hazard
is authorship drift: a handler header that credits an LLM for a template, or a
coder failure that disappears so the build looks like the agent worked when it
did not. Both are the "plausible green" family — the artifact looks like
manufactured output either way, and only the provenance distinguishes them.

No LLM key and no network: the coder entry point is monkeypatched, which is
also what lets CI exercise this path at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.runner import Outcome, RoleRunner
from tests.factory.coder_stub_bodies import invoking_handler_body

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


@pytest.fixture(autouse=True)
def _coder_on(monkeypatch):
    """Coder enabled, but every entry point stubbed — no paid calls, ever.

    The WRITER now asks the coder for four artifact classes, not one. Stubbing
    only generate_platform_handler left the model-spec, route-body and README
    calls hitting the live API on any machine with a key: the suite went past
    two minutes and started costing money. Each test below overrides the one
    entry point it is actually about.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_HTTP_ONESHOT", "1")
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_model_spec",
        lambda **kw: {
            "entity": kw["capability_id"].replace("-", "_"),
            "fields": [{"name": "reference", "type": "str", "required": True}],
            "model": "stub-spec",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_route_body",
        lambda **kw: {"body": _STUB_ROUTE, "model": "stub-route"},
    )
    # The README path calls _llm_code_call directly.
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


_STUB_ROUTE = (
    "    result = handle(payload)\n"
    '    return {"ok": True, "capability": CAPABILITY_ID, "result": result}'
)


def _headers(out: Path) -> dict:
    return {
        p.stem: p.read_text(encoding="utf-8")
        for p in (out / "app" / "actions").glob("*.py")
        if p.stem != "__init__"
    }


def _oneshot_payload(model: str = "test-model-1", marker=None):
    body = invoking_handler_body(marker if marker is not None else {"agent": True})
    return {
        "specs": {
            "analytics_surface": {
                "entity": "analytics_surface",
                "fields": [{"name": "reference", "type": "str", "required": True}],
            },
            "dashboard_surface": {
                "entity": "dashboard_surface",
                "fields": [{"name": "reference", "type": "str", "required": True}],
            },
        },
        "handlers": {
            "analytics_surface": body,
            "dashboard_surface": body,
        },
        "model": model,
    }


def test_the_writer_uses_the_coding_agent_when_one_is_configured(
    blueprint, tmp_path, monkeypatch
):
    calls = []

    def fake_oneshot(**kwargs):
        calls.append(kwargs)
        return _oneshot_payload()

    monkeypatch.setattr("app.factory.coder.generate_from_compiled_brief", fake_oneshot)
    monkeypatch.setattr("app.factory.build.coder_session.cli_available", lambda command=None: False)
    per_cap = []
    monkeypatch.setattr(
        "app.factory.coder.generate_platform_handler",
        lambda **kw: per_cap.append(kw) or {"body": "    return {}", "model": "x"},
    )

    out = tmp_path / "build"
    outcome = RoleRunner(blueprint, out).run()
    assert outcome.ok, outcome.to_dict()

    assert len(calls) == 1, "one compiled-brief dispatch, not one shot per capability"
    assert "analytics_surface" in calls[0]["capabilities"]
    assert "dashboard_surface" in calls[0]["capabilities"]
    assert "TARGET" in calls[0]["brief"]
    assert "STEP 0 INVENTORY" in calls[0]["brief"]
    assert per_cap == [], "per-capability handle() shots are retired on the brief path"

    for name, text in _headers(out).items():
        assert "coder LLM (test-model-1)" in text, name
        assert '"agent": True' in text, name


def test_writer_halts_when_cli_missing_without_oneshot(
    blueprint, tmp_path, monkeypatch
):
    """Keyed brief path without FACTORY_CODE_CLI must not fake WRITER progress."""
    from app.factory.build.coder_session import NAMED_BLOCKER_CLI

    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or _oneshot_payload(),
    )
    outcome = RoleRunner(blueprint, tmp_path / "build").run()
    assert outcome.ok is False
    assert NAMED_BLOCKER_CLI in (outcome.detail or "")
    assert oneshot == []
    # Dispatch receipt exists; handlers must not be credited as agent-written.
    receipt = tmp_path / "build" / "docs" / "coder_receipt.json"
    if receipt.is_file():
        data = json.loads(receipt.read_text(encoding="utf-8"))
        assert data.get("blocker") == NAMED_BLOCKER_CLI


def test_writer_records_creds_miss_and_keeps_empty_gap_reuse(
    blueprint, tmp_path, monkeypatch
):
    """Empty-gap REUSE + missing Kimi creds: honest receipt, no oneshot, no CLI claim."""
    from app.factory.build.coder_session import NAMED_BLOCKER_CLI_CREDS

    script = tmp_path / "kimi"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.delenv("FACTORY_BRIEF_HTTP_ONESHOT", raising=False)
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_REQUIRE_CLI", "1")
    monkeypatch.setenv("FACTORY_CODE_CLI", str(script))
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "no-kimi-home"))
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: True,
    )
    oneshot = []
    monkeypatch.setattr(
        "app.factory.coder.generate_from_compiled_brief",
        lambda **kw: oneshot.append(kw) or _oneshot_payload(),
    )
    out = tmp_path / "build"
    outcome = RoleRunner(blueprint, out).run()
    assert oneshot == [], "credentials miss must not enable HTTP oneshot"
    receipt = out / "docs" / "coder_receipt.json"
    assert receipt.is_file()
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert data.get("blocker") == NAMED_BLOCKER_CLI_CREDS
    assert data.get("ok") is False
    assert data.get("honesty_class") == "FACTORY_CODE_CLI_FAILED"
    for name, text in _headers(out).items():
        assert "coder LLM" not in text, name
        assert "FACTORY_CODE_CLI" not in text or "factory-grounded" in text, name
    # Honesty: this is not a ≥2h CLI session and not a pilot_zip claim.
    assert "pilot_zip" not in (outcome.detail or "").lower()


def test_a_coder_failure_with_no_artifacts_refuses_with_writer_no_output(
    blueprint, tmp_path, monkeypatch
):
    """0.5: degraded output that lands zero agent artifacts refuses.

    The old contract shipped the deterministic template and recorded why --
    an honest note on a hollow build. The artifact gate closes that hole:
    a WRITER pass with zero coding-agent artifacts is writer_no_output.
    """
    from app.factory.coder import CoderError

    def failing_oneshot(**kwargs):
        raise CoderError("model refused")

    monkeypatch.setattr("app.factory.build.coder_session.cli_available", lambda command=None: False)
    monkeypatch.setattr("app.factory.coder.generate_from_compiled_brief", failing_oneshot)

    out = tmp_path / "build"
    runner = RoleRunner(blueprint, out)
    outcome = runner.run()

    assert outcome.ok is False
    assert "writer_no_output" in outcome.detail
    # The refusal can land at either layer: the entry-point count (in-memory
    # source labels) or the writer-contract gate (disk ground truth). On the
    # oneshot-failure path the in-memory labels still claim the attempt while
    # the disk carries only template bodies, so the gate is the layer that
    # fires -- both are the named reason, never a silent green.
    assert outcome.outcome in {Outcome.FAILED_GATE, Outcome.FAILED_ROLE_ERROR}

    # The failure is still recorded with its reason -- refusal is not silence.
    failures = runner.state.get("coder_failures", {})
    assert "brief_dispatch" in failures
    assert "model refused" in failures["brief_dispatch"]


def test_the_coder_is_not_called_when_disabled(blueprint, tmp_path, monkeypatch):
    """0.5: coder disabled means the deterministic template, which means
    zero agent-authored artifacts -- and the WRITER refuses with
    writer_no_output. The coder is still not called."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    called = []
    monkeypatch.setattr(
        "app.factory.coder.generate_platform_handler",
        lambda **kw: called.append(kw) or {"body": "    return {}", "model": "x"},
    )

    out = tmp_path / "build"
    outcome = RoleRunner(blueprint, out).run()
    assert outcome.ok is False
    assert "writer_no_output" in outcome.detail
    assert called == []


def test_rework_findings_are_handed_to_the_coder(blueprint, tmp_path, monkeypatch):
    """A second attempt must be told what failed, not guess from scratch."""
    seen = []

    def fake_oneshot(**kwargs):
        seen.append(kwargs.get("brief") or "")
        return _oneshot_payload(model="m", marker={})

    monkeypatch.setattr("app.factory.build.coder_session.cli_available", lambda command=None: False)
    monkeypatch.setattr("app.factory.coder.generate_from_compiled_brief", fake_oneshot)

    from app.factory.build.roles import ROLE_IMPLEMENTATIONS, RoleResult

    def barren_tester(ctx):
        return RoleResult(ok=True, detail="wrote no tests")

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.TESTER] = barren_tester

    from app.factory.build.runner import BuildBudget

    runner = RoleRunner(
        blueprint, tmp_path / "build", roles=roles, budget=BuildBudget(max_rework=1)
    )
    runner.run()

    # First writer pass has no findings; the rework pass carries the gate's
    # in the compiled whole-job brief.
    assert seen, "the coding agent must receive the compiled brief"
    assert "tester produced no test files" not in seen[0]
    rework_briefs = [b for b in seen[1:] if "tester produced no test files" in b]
    assert rework_briefs, "the rework pass must receive the tester's findings in the brief"


def test_coder_output_still_passes_the_validation_gate(monkeypatch):
    """The agent does not get to bypass the static gate on emitted code."""
    import app.factory.coder as coder

    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "sk-not-real")
    # Column-zero statements: _validate_body indents them into the wrapper.
    monkeypatch.setattr(
        coder, "_llm_code_call", lambda messages: ("import os\nreturn {}", "stub-model")
    )

    with pytest.raises(coder.CoderError, match="forbidden construct"):
        coder.generate_platform_handler(
            capability_id="c",
            description="d",
            block_ids=["analytics"],
            product_name="P",
            vertical="v",
        )


def test_valid_coder_output_is_indented_into_the_handler(monkeypatch):
    """The body the WRITER writes must be function-body indented, once."""
    import app.factory.coder as coder

    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("KIMI_API_KEY", "sk-not-real")
    monkeypatch.setattr(
        coder,
        "_llm_code_call",
        lambda messages: (
            'data = execute(BLOCK_IDS[0], payload)\nreturn {"capability": CAPABILITY_ID, "data": data}',
            "stub-model",
        ),
    )

    result = coder.generate_platform_handler(
        capability_id="c",
        description="d",
        block_ids=["analytics"],
        product_name="P",
        vertical="v",
    )
    body = result["body"]
    assert body.startswith("    data = execute(")
    assert not body.startswith("        "), "double-indented body would not compile"
    # And it compiles in the shape roles._handler_module places it in.
    compile(f"def handle(payload):\n{body}\n", "<t>", "exec")
