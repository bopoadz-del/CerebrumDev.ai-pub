"""A product image that does not build is scored, not reported as a gate fault."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import score_failed_build as sfb  # noqa: E402

HARNESS = '''
import os
CHECKS = ['no_token_401', 'zeta_image_ok', 'omega_route']
ADVISORY = []
IMAGE_CHECKS = ['zeta_image_ok']
REQUIRED = 3
def main():
    raise SystemExit("never run by the scorer")
'''

LOG = (
    "#16 [8/9] RUN python3 scripts/release_gate.py\n"
    "#16 0.301 VERDICT: CANNOT RUN\n"
    "\x1b[31mERROR: failed to build\x1b[0m: exit code: 2\n"
)


def _write(tmp_path: Path, body: str = HARNESS) -> Path:
    path = tmp_path / "acceptance.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_failed_image_build_fails_the_declared_image_checks_and_starves_the_rest(tmp_path):
    harness = _write(tmp_path)
    log = tmp_path / "build.log"
    log.write_text(LOG, encoding="utf-8")

    assert sfb.main([str(harness), str(log), "1", str(tmp_path)]) == 0

    out = (tmp_path / "acceptance.out").read_text(encoding="utf-8").splitlines()
    assert out[0].startswith("NOT_RUN no_token_401")
    assert out[1].startswith("FAIL zeta_image_ok")
    assert "Dockerfile" in out[1] and "exit 1" in out[1]
    assert out[2].startswith("NOT_RUN omega_route")
    assert out[-1] == "ACCEPTANCE: 0/3"


def test_the_build_log_is_evidence_never_the_detail(tmp_path):
    # The Factory derives ownership from the detail; a log naming a Factory
    # script the product's Dockerfile chose to run must not reach it.
    harness = _write(tmp_path)
    log = tmp_path / "build.log"
    log.write_text(LOG, encoding="utf-8")
    sfb.main([str(harness), str(log), "1", str(tmp_path)])

    out = (tmp_path / "acceptance.out").read_text(encoding="utf-8")
    assert "release_gate" not in out
    evidence = json.loads((tmp_path / "image_build_evidence.json").read_text(encoding="utf-8"))
    assert set(evidence) == {"zeta_image_ok"}
    assert "release_gate.py" in evidence["zeta_image_ok"]
    assert "\x1b[" not in evidence["zeta_image_ok"]


def test_the_harness_is_read_never_run(tmp_path):
    harness = _write(tmp_path, HARNESS.replace("import os", "raise RuntimeError('imported')"))
    log = tmp_path / "build.log"
    log.write_text(LOG, encoding="utf-8")
    assert sfb.main([str(harness), str(log), "2", str(tmp_path)]) == 0


def test_a_harness_without_image_checks_stays_unscored(tmp_path):
    harness = _write(tmp_path, HARNESS.replace("IMAGE_CHECKS = ['zeta_image_ok']\n", ""))
    log = tmp_path / "build.log"
    log.write_text(LOG, encoding="utf-8")
    assert sfb.main([str(harness), str(log), "1", str(tmp_path)]) == sfb.NO_IMAGE_CHECKS
    assert not (tmp_path / "acceptance.out").exists()


def test_the_gate_workflow_scores_a_failed_build_and_skips_docker_steps():
    import yaml

    wf = Path(__file__).resolve().parent.parent / "workflows" / "store-gate.yml"
    steps = yaml.safe_load(wf.read_text(encoding="utf-8"))["jobs"]["store-gate"]["steps"]
    by_name = {s.get("name"): s for s in steps}
    build = by_name["Build artifact image"]
    assert "IMAGE_BUILT" in build["run"] and "if" not in build
    scorer = [s for s in steps if "score_failed_build.py" in str(s.get("run"))]
    assert len(scorer) == 1 and scorer[0]["if"] == "env.IMAGE_BUILT == '0'"
    for name in (
        "Boot the image on Postgres (measures postgres_boot_200)",
        "Run scripts/acceptance.py inside the image",
    ):
        assert by_name[name]["if"] == "env.IMAGE_BUILT == '1'"
    parse = by_name["Parse k/N and write result"]["run"]
    assert "NOT_RUN" in parse and "image_build_evidence.json" in parse


def test_a_missing_harness_stays_unscored(tmp_path):
    assert sfb.main([str(tmp_path / "nope.py"), str(tmp_path / "x.log"), "1", str(tmp_path)]) == sfb.NO_IMAGE_CHECKS
