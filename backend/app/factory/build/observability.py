"""Operations substrate every platform ships: metrics, errors, limits, proof.

A pilot goes to a DevOps team. Structured logs alone leave them unable to
answer "is it up, is it slow, is it being hammered" without adding code to
someone else's product, and a backup nobody has restored is a file rather
than a backup.

None of this is domain-specific. It counts requests and seconds, dumps and
restores whatever the platform stores on, and runs the same 200 requests
against /health whatever the platform is for.

The Factory writes these when the agent has not. Mounting is one line in
app/main.py, which the agent owns, and the floor asks for it by name.
"""

from __future__ import annotations

_OBSERVABILITY = '''"""Metrics, error reporting and rate limiting.

Written by the factory. Mount it once in app/main.py:

    from app.observability import mount_observability
    mount_observability(app)

GET /metrics then answers request counts and latency (the floor's
metrics_served), SENTRY_DSN is honoured when set, and auth routes are rate
limited.
"""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse

#: Requests seen and seconds spent, per method+path. In-process on purpose:
#: a pilot runs one container, and a metrics backend is the operator's call.
_COUNTS: Dict[Tuple[str, str], int] = defaultdict(int)
_SECONDS: Dict[Tuple[str, str], float] = defaultdict(float)
_STATUS: Dict[int, int] = defaultdict(int)

#: Auth attempts per client, for the rate limit.
_ATTEMPTS: Dict[str, Deque[float]] = defaultdict(deque)

RATE_LIMIT_WINDOW_S = float(os.environ.get("AUTH_RATE_WINDOW_S", "60"))
RATE_LIMIT_MAX = int(os.environ.get("AUTH_RATE_MAX", "20"))
AUTH_PREFIXES = ("/v1/auth", "/v1/login", "/v1/token", "/auth", "/login")


def _is_auth_route(path):
    low = path.lower()
    return any(low.startswith(prefix) for prefix in AUTH_PREFIXES)


def _rate_limited(client):
    """True when this client has spent its auth attempts for the window."""
    now = time.monotonic()
    seen = _ATTEMPTS[client]
    while seen and now - seen[0] > RATE_LIMIT_WINDOW_S:
        seen.popleft()
    if len(seen) >= RATE_LIMIT_MAX:
        return True
    seen.append(now)
    return False


def _init_sentry():
    """Honour SENTRY_DSN when both the variable and the SDK are present."""
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return "unset"
    try:
        import sentry_sdk
    except ImportError:
        return "dsn set but sentry-sdk is not installed"
    sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0)
    return "enabled"


def mount_observability(app: Any) -> None:
    """Add the middleware and the /metrics route. Call once, at startup."""
    sentry_state = _init_sentry()

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        path = request.url.path
        if _is_auth_route(path):
            client = request.client.host if request.client else "unknown"
            if _rate_limited(client):
                return JSONResponse(
                    {"ok": False, "error": "too many attempts"}, status_code=429
                )
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        key = (request.method, path)
        _COUNTS[key] += 1
        _SECONDS[key] += elapsed
        _STATUS[response.status_code] += 1
        return response

    @app.get("/metrics")
    def metrics():
        lines = [
            "# HELP http_requests_total Requests handled since boot.",
            "# TYPE http_requests_total counter",
        ]
        for (method, path), count in sorted(_COUNTS.items()):
            lines.append(
                "http_requests_total{method=%r,path=%r} %d" % (method, path, count)
            )
        lines.append(
            "# HELP http_request_duration_seconds_total Seconds spent handling."
        )
        lines.append("# TYPE http_request_duration_seconds_total counter")
        for (method, path), seconds in sorted(_SECONDS.items()):
            lines.append(
                "http_request_duration_seconds_total{method=%r,path=%r} %.6f"
                % (method, path, seconds)
            )
        for status, count in sorted(_STATUS.items()):
            lines.append("http_responses_total{status=%d} %d" % (status, count))
        lines.append("# sentry: " + sentry_state)
        return PlainTextResponse(chr(10).join(lines) + chr(10))
'''


_BACKUP_SH = """#!/usr/bin/env bash
# Take a backup of whatever this platform stores on.
#
#   DATABASE_URL set   -> pg_dump
#   DATABASE_URL unset -> sqlite3 .backup of STORAGE_PATH/platform.db
#
# Written by the factory. A backup nobody has restored is a file, not a
# backup: tests/test_backup_restore.py restores what this produces.
set -euo pipefail

OUT="${1:-./backups}"
mkdir -p "$OUT"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

if [ -n "${DATABASE_URL:-}" ]; then
  DEST="$OUT/platform-$STAMP.sql"
  pg_dump "$DATABASE_URL" > "$DEST"
else
  SRC="${STORAGE_PATH:-./data}/platform.db"
  DEST="$OUT/platform-$STAMP.db"
  if [ ! -f "$SRC" ]; then
    echo "no database at $SRC" >&2
    exit 1
  fi
  sqlite3 "$SRC" ".backup '$DEST'"
fi

echo "$DEST"
"""


_BACKUP_TEST = '''"""The backup restores, with the rows still in it.

Written by the factory. The floor (backup_restore_roundtrip) refuses a
backup that has never been restored: dump, wipe, restore, and the planted
row must still be readable.
"""

import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup.sh"


def _posix_shell():
    """A bash that can actually run a POSIX script, or None.

    On Windows ``bash`` usually resolves to the WSL launcher. With no distro
    installed it answers "Windows Subsystem for Linux has no installed
    distributions." -- in UTF-16, which then arrives as mojibake in whatever
    captured it. Proving the shell works before using it is cheaper than
    decoding that twice.
    """
    found = shutil.which("bash")
    if not found:
        return None
    try:
        probe = subprocess.run(
            [found, "-c", "exit 0"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # The shell RAN when it exits 0 -- the launcher with no distro does not.
    return found if probe.returncode == 0 else None


@pytest.mark.skipif(
    bool(os.environ.get("DATABASE_URL")),
    reason="sqlite roundtrip; the Postgres leg runs in the gate against a server",
)
def test_a_backup_restores_with_its_rows(tmp_path):
    storage = tmp_path / "data"
    storage.mkdir()
    db = storage / "platform.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE planted (id TEXT PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO planted VALUES ('a1', 'survives the restore')")

    assert SCRIPT.is_file(), "scripts/backup.sh is missing"
    bash = _posix_shell()
    if bash is None:
        pytest.skip("no POSIX shell here; backup.sh is a shell script")
    env = dict(os.environ)
    env["STORAGE_PATH"] = str(storage)
    env["DATABASE_URL"] = ""
    if shutil.which("sqlite3") is None:
        pytest.skip("the sqlite3 CLI is not on this runner")
    out = subprocess.run(
        [bash, str(SCRIPT), str(tmp_path / "backups")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
    )
    assert out.returncode == 0, out.stdout + out.stderr
    printed = out.stdout.strip().splitlines()
    assert printed, "backup.sh printed no path"
    dump = Path(printed[-1])
    assert dump.is_file(), "backup.sh printed a path that is not a file"

    db.unlink()
    shutil.copy(dump, db)

    with sqlite3.connect(db) as conn:
        rows = list(conn.execute("SELECT note FROM planted WHERE id = 'a1'"))
    assert rows and rows[0][0] == "survives the restore", (
        "the restored database lost the planted row"
    )
'''


_BENCH = '''#!/usr/bin/env python3
"""200 concurrent requests; p95 must come in under 500ms.

Written by the factory. Measured, not estimated: the floor (bench_p95) reads
the number this prints. It exercises /health, which every platform has, so
it measures the platform rather than any one capability.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

REQUESTS = int(os.environ.get("BENCH_REQUESTS", "200"))
BUDGET_MS = float(os.environ.get("BENCH_P95_BUDGET_MS", "500"))


def main() -> int:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)

    def once(_):
        started = time.perf_counter()
        client.get("/health")
        return (time.perf_counter() - started) * 1000.0

    with ThreadPoolExecutor(max_workers=16) as pool:
        timings = sorted(pool.map(once, range(REQUESTS)))

    p95 = timings[max(0, int(len(timings) * 0.95) - 1)]
    print(
        "requests=%d p50=%.1fms p95=%.1fms budget=%.0fms"
        % (len(timings), statistics.median(timings), p95, BUDGET_MS)
    )
    print("STORE_BENCH_P95_MS=%.1f" % p95)
    if p95 >= BUDGET_MS:
        print("p95 over budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def render_observability() -> str:
    return _OBSERVABILITY


def render_backup_script() -> str:
    return _BACKUP_SH


def render_backup_restore_test() -> str:
    return _BACKUP_TEST


def render_bench_script() -> str:
    return _BENCH

