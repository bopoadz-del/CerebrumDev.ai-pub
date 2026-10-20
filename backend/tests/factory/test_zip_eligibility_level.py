"""Download gate is build-level aware: a PROTOTYPE build (stops at CODE, never
runs CI/the Store gate) may be downloaded at CODE_GREEN without a CI run id;
every higher level still requires the recorded green CI run (pilot+ path not
loosened). The state and artifact-gate checks apply at every level.

Fail-first: before the fix assert_zip_eligibility required a run id for ALL
levels, so a CODE_GREEN prototype was refused as "not greened by CI".
"""
import pytest

from app.factory.build.export_manifest import (
    ExportManifestError,
    assert_zip_eligibility,
)

GREEN = {"state": "succeeded", "authorship": {"agent_written": 9}}


def test_prototype_code_green_needs_no_ci_run_id():
    status = {**GREEN, "build_level": "prototype"}
    # no run id -> must NOT raise for a prototype
    assert_zip_eligibility(status, run_id="")


def test_pilot_still_requires_ci_run_id():
    status = {**GREEN, "build_level": "pilot"}
    with pytest.raises(ExportManifestError) as exc:
        assert_zip_eligibility(status, run_id="")
    assert "not greened by CI" in str(exc.value)
    # with a run id it passes
    assert_zip_eligibility(status, run_id="run-123")


def test_light_and_production_not_loosened():
    for level in ("light", "production", ""):
        with pytest.raises(ExportManifestError):
            assert_zip_eligibility({**GREEN, "build_level": level}, run_id="")


def test_state_and_artifact_gate_apply_even_to_prototype():
    with pytest.raises(ExportManifestError):  # not succeeded
        assert_zip_eligibility(
            {"state": "failed", "authorship": {"agent_written": 9}, "build_level": "prototype"},
            run_id="",
        )
    with pytest.raises(ExportManifestError):  # no agent-authored artifact
        assert_zip_eligibility(
            {"state": "succeeded", "authorship": {"agent_written": 0}, "build_level": "prototype"},
            run_id="",
        )


def test_caller_can_override_level():
    status = {**GREEN}  # no build_level in status
    # explicit prototype override -> no run id needed
    assert_zip_eligibility(status, run_id="", build_level="prototype")
    # explicit pilot override -> run id required
    with pytest.raises(ExportManifestError):
        assert_zip_eligibility(status, run_id="", build_level="pilot")
