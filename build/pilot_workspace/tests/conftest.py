"""Test bootstrap for the generated platform.

Puts the platform root on sys.path and points persistence at a scratch
directory, so running the suite never touches a real data file.

STORAGE_PATH is FORCED, not defaulted. The build environment legitimately
carries its own STORAGE_PATH (the factory backend sets one), and a
``setdefault`` here made every tester round share one database file: a
table created by round N rejected round N+1's columns, and the rework loop
burned its budget chasing schema errors no round had actually caused.

Outbound network is BLOCKED, not merely unconfigured. Stripping the store
env only proves the platform does not call the store; a handler that posts
to an arbitrary public URL still passed, and one did -- "sent" a webhook to
the open internet from a platform whose whole claim is running offline.
Loopback stays open so TestClient-style local servers keep working.
P1: this blocker is unchanged. Do not add local-inference or cloud hosts.
"""

import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="platform-test-")

# Schema is versioned. connect() does not CREATE TABLE. Apply head so
# model/route tests have tables; a missing revision fails the suite.
# ImportError is only for isolation probes that exec this file without app/.
try:
    from app.migrations import upgrade_head  # noqa: E402

    upgrade_head()
except ImportError:
    pass

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_real_connect = socket.socket.connect


def _offline_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if isinstance(host, (bytes, bytearray)):
        host = host.decode("utf-8", "replace")
    if str(host) not in _LOCAL_HOSTS:
        raise OSError(
            f"offline suite: outbound connection to {host!r} refused -- this "
            "platform must run with no network"
        )
    return _real_connect(self, address)


socket.socket.connect = _offline_connect


def pytest_configure(config):
    """Register the factory vs pilot split. TESTER's lane is tests/** so
    this cannot live in a repo-root pytest.ini."""
    config.addinivalue_line(
        "markers",
        "pilot: Store-backed execute-all; excluded from the factory code-phase gate",
    )
