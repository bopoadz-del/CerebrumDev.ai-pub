#!/usr/bin/env python3
"""Record a live post-deploy smoke audit artifact.

Usage:
    python3 scripts/record_live_audit.py <base_url>

Runs scripts/post_deploy_smoke.py, parses [LIVE]/[DEAD] lines, and writes:
  artifacts/audits/live_audit_<UTC>.json
  artifacts/audits/live_audit_latest.json

Exit codes:
    0 — all checks LIVE, artifact written
    1 — at least one DEAD check
    2 — no checks parsed (script output malformed or empty)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ARTIFACT_DIR = Path("artifacts/audits")
SMOKE_SCRIPT = Path("scripts/post_deploy_smoke.py")
CHECK_RE = re.compile(r"^\[(LIVE|DEAD)\]\s*(.+)$")


def main() -> int:
    base_url = (sys.argv[1] if len(sys.argv) > 1 else "https://api.cerebrum-dev.com").rstrip("/")
    if not SMOKE_SCRIPT.is_file():
        print(f"SMOKE script not found: {SMOKE_SCRIPT}", file=sys.stderr)
        return 2

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), base_url],
        capture_output=True,
        text=True,
        check=False,
    )

    checks = []
    for line in result.stdout.splitlines():
        m = CHECK_RE.match(line.strip())
        if not m:
            continue
        status, rest = m.group(1), m.group(2).strip()
        name = rest.split(" — ", 1)[0].split(" - ", 1)[0].strip()
        checks.append({"name": name, "status": status})

    if not checks:
        print("No [LIVE]/[DEAD] checks parsed from smoke output.", file=sys.stderr)
        print("STDOUT:\n", result.stdout, file=sys.stderr)
        print("STDERR:\n", result.stderr, file=sys.stderr)
        return 2

    ran_at = datetime.now(timezone.utc)
    all_live = all(c["status"] == "LIVE" for c in checks)

    artifact = {
        "schema_version": "live_audit.v1",
        "ran_at": ran_at.isoformat(),
        "base_url": base_url,
        "all_live": all_live,
        "checks": checks,
    }

    timestamp = ran_at.strftime("%Y%m%dT%H%M%SZ")
    dated_path = ARTIFACT_DIR / f"live_audit_{timestamp}.json"
    latest_path = ARTIFACT_DIR / "live_audit_latest.json"

    dated_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    print(f"Wrote {dated_path}")
    print(f"Wrote {latest_path}")
    live = sum(1 for c in checks if c["status"] == "LIVE")
    dead = sum(1 for c in checks if c["status"] == "DEAD")
    print(f"Checks: {live} live, {dead} dead")

    return 0 if all_live else 1


if __name__ == "__main__":
    sys.exit(main())
