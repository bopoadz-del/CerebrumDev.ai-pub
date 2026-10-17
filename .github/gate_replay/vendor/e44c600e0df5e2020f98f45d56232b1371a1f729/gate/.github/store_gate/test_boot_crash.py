"""A container that built but died at boot is scored, with a typed crash row."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boot_crash  # noqa: E402

HARNESS = (
    'CHECKS = ["docker_health_200", "no_token_401", "audit_clean"]\n'
    "REQUIRED = 3\n"
    'IMAGE_CHECKS = ["docker_health_200"]\n'
)

LIVE_LOG = """\
INFO  [alembic.runtime.migration] Running upgrade  -> 0001
Traceback (most recent call last):
  File "/usr/local/bin/uvicorn", line 8, in <module>
    sys.exit(main())
  File "/usr/local/lib/python3.12/site-packages/uvicorn/main.py", line 412, in main
    run(app)
  File "/app/app/main.py", line 14, in <module>
    install_observability(app)
  File "/app/app/observe.py", line 417, in configure_logging
    file_handler = logging.FileHandler(dest / "request.jsonl")
  File "/usr/local/lib/python3.12/logging/__init__.py", line 1231, in __init__
    StreamHandler.__init__(self, self._open())
PermissionError: [Errno 13] Permission denied: '/app/storage/request.jsonl'
"""


def _harness(tmp_path):
    h = tmp_path / "acceptance.py"
    h.write_text(HARNESS, encoding="utf-8")
    return h


def test_a_boot_crash_is_scored_not_left_unscored(tmp_path):
    log = tmp_path / "container.log"
    log.write_text(LIVE_LOG, encoding="utf-8")
    assert boot_crash.main([str(_harness(tmp_path)), str(log), str(tmp_path)]) == 0
    out = (tmp_path / "acceptance.out").read_text(encoding="utf-8")
    assert "FAIL docker_health_200" in out
    assert "NOT_RUN no_token_401" in out and "NOT_RUN audit_clean" in out
    assert "ACCEPTANCE: 0/3" in out
    evidence = json.loads((tmp_path / "image_build_evidence.json").read_text(encoding="utf-8"))
    assert "PermissionError" in evidence["docker_health_200"]


def test_the_row_is_the_innermost_frame_inside_the_product_tree(tmp_path):
    row = boot_crash.crash_row(LIVE_LOG)
    assert row == {
        "kind": "boot_crash",
        "file": "app/observe.py",
        "line": 417,
        "text": "PermissionError: [Errno 13] Permission denied: '/app/storage/request.jsonl'",
    }


def test_no_traceback_means_no_row_but_still_a_score(tmp_path):
    log = tmp_path / "container.log"
    log.write_text("uvicorn: waiting\nKilled\n", encoding="utf-8")
    assert boot_crash.main([str(_harness(tmp_path)), str(log), str(tmp_path)]) == 0
    rows = json.loads((tmp_path / "image_evidence_rows.json").read_text(encoding="utf-8"))
    assert rows == {}
    assert "FAIL docker_health_200" in (tmp_path / "acceptance.out").read_text(encoding="utf-8")


def test_a_harness_without_image_checks_stays_unscored(tmp_path):
    h = tmp_path / "acceptance.py"
    h.write_text('CHECKS = ["a"]\nREQUIRED = 1\n', encoding="utf-8")
    log = tmp_path / "container.log"
    log.write_text(LIVE_LOG, encoding="utf-8")
    assert boot_crash.main([str(h), str(log), str(tmp_path)]) == boot_crash.NO_IMAGE_CHECKS
    assert not (tmp_path / "acceptance.out").exists()
