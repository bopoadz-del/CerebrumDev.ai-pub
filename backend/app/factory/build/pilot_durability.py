"""Pilot gate: a written record must survive the process that wrote it.

``pilot_ready`` is read off a ``RUN_SUCCEEDED`` event whose cycle is
``pilot``, so it means "the pilot cycle's phases passed". Every one of those
phases runs in a single interpreter: the suite writes and reads back through
the same import, the same module globals and, for an in-process block, the
same object graph. A platform whose persistence never leaves memory passes
all of it.

That is the demo/pilot boundary. A pilot writes a customer's data on Monday
and is expected to still have it on Tuesday, across a redeploy. This gate
asserts the smallest honest version of that: write in one process, read in
another, with nothing shared but the storage path.

The second process is a real ``sys.executable`` child rather than a
reimport, because a reimport keeps module-level caches alive -- which is
exactly the failure mode being tested. LotDesk's queue reported
``enqueued: True`` and then ``pending: 0``, because the block instance
holding the deque was discarded with the call.

Runs only on the pilot cycle. The code cycle is a 20-30 minute coder pass
and is not where durability is decided.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "pilot_outcome_survives_restart"

#: Writes in this process, then re-reads from a fresh interpreter that
#: shares only STORAGE_PATH. Findings to stderr, non-zero exit on failure.
DURABILITY_PROBE = r'''
import json, os, subprocess, sys, tempfile

STORAGE = tempfile.mkdtemp(prefix="pilot-durability-")
os.environ["STORAGE_PATH"] = STORAGE
os.environ.setdefault("PLATFORM_TOKEN", "dev-local-token")
sys.path.insert(0, os.getcwd())

findings = []

try:
    from app.models import MODELS
    from app.main import app
    from fastapi.testclient import TestClient
except Exception as exc:
    sys.stderr.write("workspace does not import: %s: %s\n" % (type(exc).__name__, exc))
    raise SystemExit(1)

try:
    from app.migrations import upgrade_head
    upgrade_head()
except Exception:
    pass


def _ann(cls, name):
    raw = getattr(cls, "__annotations__", {}).get(name, "str")
    return str(raw).replace("Optional[", "").replace("]", "").strip()


def _value(cls, name):
    con = getattr(cls, "CONSTRAINTS", {}).get(name, {})
    allowed = con.get("allowed_values")
    if allowed:
        return allowed[0]
    kind = _ann(cls, name)
    kind_l = kind.lower().replace("datetime.", "").replace(" ", "")
    if kind in ("int", "float") or kind_l in ("int", "float"):
        low, high = con.get("min"), con.get("max")
        if low is not None:
            return low
        if high is not None:
            return high if high < 1 else 1
        return 1
    if kind == "bool" or kind_l == "bool":
        return False
    # Declared type / format only -- never a meaning read from the field name.
    fmt = str(con.get("format") or "").lower().replace("-", "")
    if fmt == "email":
        return "sample@example.com"
    if kind_l in ("datetime", "timestamp") or fmt in ("datetime", "timestamp", "iso8601"):
        return "2026-09-03T10:00:00"
    if kind_l == "date" or fmt == "date":
        return "2026-09-03"
    if kind_l == "time" or fmt == "time":
        return "10:00:00"
    return "sample"


def _payload(cls):
    return {n: _value(cls, n) for n in getattr(cls, "FIELDS", [])}


# Where each capability persists is the product's DECLARATION, read by the
# same resolver as the round-trip probes -- never assumed from the capability
# id. A capability that declares no persisted entity has nothing to outlive
# the process and is not written here.
ENTITY_RESOLVER = None

# __CREATE_CONTRACT__

written = {}

# -- process one: write ----------------------------------------------------
client_cm = TestClient(app)
client = client_cm.__enter__()
for cap_id, cls in MODELS.items():
    entity, declared = _declared_entity(cap_id, cls)
    if not (declared and entity):
        continue
    resp = client.post(
        "/v1/" + cap_id,
        json=_payload(cls),
        headers={"Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN", "dev-local-token")},
    )
    data = resp.json() if resp.content else {}
    if resp.status_code not in _ACCEPT_STATUSES or data.get(_OK_KEY) is False:
        # Not this gate's finding: the writer gate judges acceptance.
        continue
    written[cap_id] = entity

# The tenant the writes actually landed under. The route resolves this from
# the Authorization header (app/tenancy.py), so the read-back MUST use the
# same value -- a hardcoded tenant reads an empty scope and reports a
# durability failure against data that persisted perfectly well.
PROBE_TENANT = ""
try:
    from app import tenancy as _tenancy

    PROBE_TENANT = str(
        _tenancy.resolve_tenant(
            {"Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN", "dev-local-token")}
        ).tenant_id
    )
except Exception:
    PROBE_TENANT = ""

try:
    client_cm.__exit__(None, None, None)
except Exception:
    pass

if not written:
    sys.stderr.write("no capability accepted a write; durability not provable\n")
    raise SystemExit(1)

# -- process two: a genuinely new interpreter ------------------------------
READBACK = (
    "import json, os, sys\n"
    "sys.path.insert(0, os.getcwd())\n"
    "from app import store\n"
    "TENANT = os.environ.get('PILOT_TENANT') or ''\n"
    "out = {}\n"
    "for cap, ent in json.loads(os.environ['PILOT_ENTITIES']).items():\n"
    "    try:\n"
    "        out[cap] = len(store.list_all(ent, tenant_id=TENANT))\n"
    "    except Exception as exc:\n"
    "        out[cap] = 'ERROR: ' + type(exc).__name__ + ': ' + str(exc)\n"
    "sys.stdout.write(json.dumps(out))\n"
)

env = dict(os.environ)
env["STORAGE_PATH"] = STORAGE
env["PILOT_ENTITIES"] = json.dumps(written)
env["PILOT_TENANT"] = PROBE_TENANT
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.run(
    [sys.executable, "-c", READBACK],
    cwd=os.getcwd(),
    capture_output=True,
    text=True,
    env=env,
    timeout=300,
)

if proc.returncode != 0:
    sys.stderr.write(
        "the second process could not read the store: %s\n"
        % ((proc.stderr or "").strip()[-400:])
    )
    raise SystemExit(1)

try:
    counts = json.loads(proc.stdout or "{}")
except ValueError:
    sys.stderr.write("unreadable read-back output: %s\n" % (proc.stdout or "")[:200])
    raise SystemExit(1)

for cap_id, entity in written.items():
    seen = counts.get(cap_id)
    if isinstance(seen, str):
        findings.append("%s: %s cannot be read from a new process (%s)" % (cap_id, entity, seen))
    elif not isinstance(seen, int) or seen < 1:
        findings.append(
            "%s: wrote to %s and a new process sees %r row(s) — persistence "
            "did not outlive the writing process" % (cap_id, entity, seen)
        )

if findings:
    sys.stderr.write("\n".join(findings) + "\n")
    raise SystemExit(1)
'''


def _render_durability_probe() -> str:
    """The probe with the declared-entity resolver and the declared
    status/key contract rendered in (entity_contract, rejection_contract)."""
    from app.factory.build.entity_contract import (
        ENTITY_RESOLVER_SLOT,
        ENTITY_RESOLVER_SRC,
    )
    from app.factory.build.rejection_contract import contract_source

    return DURABILITY_PROBE.replace(ENTITY_RESOLVER_SLOT, ENTITY_RESOLVER_SRC, 1).replace(
        "# __CREATE_CONTRACT__\n", contract_source() + "\n", 1
    )


def gate_pilot_outcome_survives_restart(ctx: "GateContext") -> "GateResult":
    """A record written by one process is readable by the next."""
    from app.factory.build.gates import GateResult

    if str(getattr(ctx, "cycle", "code")).lower() != "pilot":
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="code cycle — durability is decided on the pilot cycle",
        )

    if not (ctx.workspace / "app" / "models.py").is_file():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="pilot_no_models",
            detail="app/models.py is missing — nothing to persist",
            findings=["no models to probe"],
        )

    proc = ctx.run([sys.executable, "-c", _render_durability_probe()])
    if proc.returncode != 0:
        raw = (proc.stderr or "").splitlines()
        # Filter to marked findings: alembic and uvicorn also log to stderr,
        # so a tail of raw lines reported their INFO noise as the gate reason.
        lines = [
            ln.split("GATE-FINDING: ", 1)[1] for ln in raw if "GATE-FINDING: " in ln
        ]
        if not lines:
            lines = [ln for ln in raw if ln.strip()][-8:]
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="durability_failed",
            detail="a written record did not survive the process that wrote it",
            findings=lines[-20:] or ["durability probe failed with no output"],
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail="written records are readable from a separate process",
    )
