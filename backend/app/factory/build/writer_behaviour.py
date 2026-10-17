"""WRITER behaviour gate: a route may not report success over a failed block.

``gate_workspace_compiles`` is the WRITER's only acceptance check, so the
role that authors every route, model and persistence path is accepted if
``app/`` parses. That is the hole LotDesk shipped through: three routes that
discarded ``handle()``'s result and persisted the request regardless, all of
them syntactically perfect.

The deterministic emitter is not the risk. ``_templated_route_body`` routes
through ``run_capability`` and persists only on ``ActionStatus.SUCCESS``.
The coder path writes its own body, and ``coder._validate_body`` asserts
syntax, sandbox-escape and a non-nested ``return`` -- nothing about the
contract. Whichever path produced the route, this gate judges the artifact.

Two phases, in order, because a one-phase probe passes for the wrong reason:

* **baseline** -- with the blocks working, a payload built from the entity's
  own declared constraints must be accepted. A route that rejects its own
  schema is a defect in itself, and without this phase an invalid payload
  would make phase two "pass" while never reaching a block at all.
* **forced failure** -- with every block call returning an error envelope,
  a route that answers ``ok: True`` or persists is a *per-capability miss*.
  One dishonest capability must not halt the whole WRITER phase (live
  invoice-management, 2026-08-30: four handlers written, then
  ``writer_behaviour`` stopped the run before TESTER/STORE_MANAGER, no zip).
  Isolated schema refusals and isolated F11 unused-block declarations
  are the same class of miss: they must not halt a mixed workspace.
  The gate fails only when *every* capability is dishonest (all refuse
  their own schema, every capability declares blocks it never invokes,
  or every block-reaching capability lies), or when the workspace
  cannot be probed. The Floor banner must name that reason (schema vs
  F11 vs F1) — never map a schema or F11 halt onto
  ``success over a failed block``. ``(F1)`` is a substring of
  ``(F11)``; the host must not treat an F11 finding as F1.

The seam is ``app.dispatch.execute``. Both the kernel path
(``run_capability`` -> ``execute_action`` -> handler) and a coder-written
handler bottom out there, so stubbing it covers both. Handlers bind the name
at import (``from app.dispatch import execute``), so the already-imported
module attributes are patched too.
"""

from __future__ import annotations

from app.factory.build.brief_gates import WRITER_BEHAVIOUR_CHECK

import json
import re
from typing import TYPE_CHECKING, Any, Optional

from app.factory.build.entity_contract import ENTITY_RESOLVER_SLOT, ENTITY_RESOLVER_SRC

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = WRITER_BEHAVIOUR_CHECK

#: Probe / Floor text when no capability accepts a payload from its own model.
SCHEMA_HALT = "no capability accepted its own schema"

#: Probe / Floor text when every block-reaching capability lies (LotDesk).
F1_HALT = "a capability route reported success over a failed block"

#: Probe / Floor text when every capability declares unused BLOCK_IDS (F11).
F11_HALT = "a capability declares block(s) it never invokes"

#: Probe / Floor text when every capability's blocks refuse the coder payload.
CONTRACT_HALT = "every capability wrote a payload its blocks refuse"

#: Probe / Floor text when import, Alembic, or sqlite DDL crashes the probe.
SCHEMA_SQL_HALT = "workspace schema or migration failed"

#: Typed record kinds the probe emits (closed vocabulary, compared by equality).
KIND_SCHEMA = "schema"
KIND_PERSIST = "persist"
KIND_F11 = "f11"
KIND_CONTRACT = "contract"
KIND_ROUNDTRIP = "roundtrip"
KIND_F1 = "f1"

#: The canonical sentence per halt kind, rendered INTO the probe so the probe
#: and the host share one source.
HALT_SENTENCES = {
    KIND_SCHEMA: SCHEMA_HALT,
    "persist": "persist entity missing from migrated schema",
    KIND_F11: F11_HALT,
    KIND_CONTRACT: CONTRACT_HALT,
    KIND_ROUNDTRIP: "no capability could read back a record it stored",
    "migration": SCHEMA_SQL_HALT,
    "import": "workspace does not import",
    "boot": "workspace does not boot",
    "crash": "workspace probe crashed",
    "no_models": "no capabilities to probe (app.models.MODELS is empty)",
}

#: Runs inside the generated workspace. Prints one finding per line to
#: stderr and exits non-zero; a clean run exits 0 and prints nothing.
BEHAVIOUR_PROBE = r'''
import json, os, sys, tempfile

os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="writer-gate-")
os.environ.setdefault("PLATFORM_TOKEN", "dev-local-token")
sys.path.insert(0, os.getcwd())

findings = []          # (kind, text)
schema_misses = []
persist_misses = []
f11_misses = []
roundtrip_misses = []

# The probe speaks in TYPED records, one JSON object per line: the host reads
# each record's kind and never searches the text for words. HALTS (the
# canonical sentence per halt kind) is rendered in from the host module so the
# two sides cannot drift.
HALTS = {}


class _Miss(str):
    """A miss line that carries the capability it is about, so the record
    names it as a field and nothing downstream reads it out of the text."""


def _miss(cap_id, text):
    out = _Miss(text)
    out.cap = cap_id
    return out


def _cap_of(text):
    return getattr(text, "cap", None)


def _record(level, kind, text):
    stream = sys.stdout if level in ("miss", "unjudged") else sys.stderr
    rec = {"gate_record": level, "kind": kind, "text": str(text)}
    if _cap_of(text):
        rec["capability"] = _cap_of(text)
    stream.write(json.dumps(rec) + "\n")


def _halt(kind, entries=()):
    _record("halt", kind, HALTS.get(kind, kind))
    for k, text in entries:
        _record("finding", k, text)
    raise SystemExit(1)


def _exc_text(exc):
    """``Type: message`` for an exception. A SQLAlchemy error wraps the
    database's own error as ``.orig`` and appends the SQL statement to its
    message; the underlying error is the reason, the echoed DDL is not."""
    orig = getattr(exc, "orig", None)
    return "%s: %s" % (type(exc).__name__, orig if orig is not None else exc)


def _db_error_types():
    import sqlite3
    roots = [sqlite3.Error]
    try:
        from sqlalchemy.exc import SQLAlchemyError
        roots.append(SQLAlchemyError)
    except ImportError:
        pass
    return tuple(roots)

# Baked in by _render_probe() from app.factory.build.block_obligations, so
# the probe can name a missing precondition without importing the factory
# (it runs inside the generated workspace, which carries no factory code).
RESOURCE_OBLIGATIONS = {}

try:
    from app.models import MODELS
    from app import store
    from app.main import app
    from fastapi.testclient import TestClient
except Exception as exc:
    _halt("import", [("import", _exc_text(exc))])

if not MODELS:
    _halt("no_models")

# The Factory-written declaration of placeholder connectors. A product built
# before it existed declares none, so every capability is judged.
try:
    from app.placeholders import is_declared_refusal
except Exception:
    def is_declared_refusal(capability_id, status_code, body):
        return False

placeholder_unjudged = []


# The payload every Factory probe posts: TESTER's base sample through the one
# builder (payload_helpers.render_probe_payload defines _payload_for here).
# __PROBE_PAYLOAD__ (rendered in by payload_helpers.render_probe_payload)


ENTITY_RESOLVER = None  # rendered in by _render_probe (entity_contract)


def _entity_of(cap_id, cls):
    """The declared store entity, or None when there is nothing to read back:
    a declared read-only capability, or no declaration at all (product_gate
    names a missing declaration). Never the capability id."""
    entity, declared = _declared_entity(cap_id, cls)
    return entity if (declared and entity) else None


def _rows(entity):
    """Row count, or None if the entity cannot be read.

    Returning 0 on error would make the persistence assertion pass by
    accident whenever the entity name is wrong -- the check would look
    green while never having run.
    """
    if not entity:
        # Nothing declared to read: never judged as a row count.
        return None
    try:
        return len(_list_entity(entity))
    except Exception:
        return None


# The app runs its own migrations at startup, so the probe must enter the
# client context rather than construct it bare -- a bare TestClient skips
# lifespan, leaving a schema-less database and "no such table" for every
# capability. Explicit upgrade_head() first for workspaces whose startup
# does not own the migration. ImportError is the JSON-store fixture path
# (no Alembic). A real migration/SQL failure is a typed halt record — never
# swallowed, never left as unmarked sqlite stderr.
try:
    from app.migrations import upgrade_head
except ImportError:
    upgrade_head = None
if upgrade_head is not None:
    try:
        upgrade_head()
    except Exception as exc:
        _halt("migration", [("migration", _exc_text(exc))])

def _probe_excepthook(typ, exc, tb):
    if issubclass(typ, SystemExit):
        return sys.__excepthook__(typ, exc, tb)
    _record("halt", "crash", HALTS.get("crash", "crash"))
    _record("finding", "crash", _exc_text(exc))
    return sys.__excepthook__(typ, exc, tb)

sys.excepthook = _probe_excepthook

# F11: record which blocks each capability actually reaches while its
# handler runs normally. A declared BLOCK_IDS entry that is never invoked is
# a build error, not a comment -- and the baseline phase is where a handler
# runs to completion, so it is the only phase that can observe the full set.
import app.dispatch as _dispatch

_real_execute = _dispatch.execute
_seen = {"cap": None, "blocks": {}}
contract_misses = []

# CONTRACT PROBE (owner's ruling R1b, 2026-09-01).
#
# F11 says "declared means invoked". This says "invoked means ACCEPTED".
#
# The baseline phase already executes the REAL vendored blocks with the
# payload the coder wrote, so the evidence was passing through this function
# and being thrown away: only the block id was recorded, never the answer. On
# one build six hours after #254 merged, every one of these came back here
# and none was seen:
#
#   analytics  {'error': 'metric and value required'}   <- envelope shape
#   team       'Unknown action: None'                   <- action in payload
#   workflow   'workflow unknown field(s): action'      <- action in payload
#   team       'Team access denied'                     <- precondition
#
# The build passed WRITER, passed its own gate, shipped a 216-file zip that
# booted -- and could not persist one record.
#
# Classified, never guessed: each class is decided by the block's literal
# answer plus the payload the coder actually passed.
def _default_action(block_id):
    """The action dispatch supplies when a handler passes none (the product's
    own rendered map), or None when the block has no default."""
    try:
        from app.block_inputs import default_block_action
        return default_block_action(block_id)
    except Exception:
        return None


def _classify_refusal(block_id, payload, action, answer):
    """Name the mismatch, or return None when the answer is not a refusal."""
    # A refusal is a TYPED answer: an error envelope whose kind is not
    # "unavailable" (a provider that is down is not the coder's contract).
    # Store blocks carry error_kind since Cerebrum-Blocks #137.
    if not isinstance(answer, dict):
        return None
    if answer.get("status") != "error" and answer.get("ok") is not False:
        return None
    if answer.get("error_kind") == "unavailable":
        return None
    text = str(answer.get("error") or "")
    data = payload if isinstance(payload, dict) else {}
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}

    # (a) the action travelled inside the payload instead of as the keyword --
    # decided by the payload's structure, not by the words of the refusal.
    if "action" in data or "action" in inner:
        return (
            "%s: the action travelled inside the payload; app/dispatch.py "
            "routes payload keys into the block's record and reads the "
            "operation only from the action= keyword. Answered %r "
            "(CONTRACT: unknown action)" % (block_id, text[:90])
        )
    if action is None and _default_action(block_id) is None:
        return (
            "%s: called with no action= keyword. Answered %r "
            "(CONTRACT: unknown action)" % (block_id, text[:90])
        )

    # (b) the record is one level too deep for a block that reads it flat.
    # input_keys_read_by_block is harvested from source, not declared --
    # WORKAROUND, removal tracked in CerebrumDev.ai#256 (design:
    # Cerebrum-Blocks#90, block.json requires_inputs).
    if inner:
        try:
            import app.dispatch as _d
            reads = set(
                (_d.BLOCK_CONTRACTS.get(block_id) or {})
                .get("input_keys_read_by_block") or []
            )
        except Exception:
            reads = set()
        buried = sorted(k for k in inner if k in reads and k != "action")
        if buried and "input" not in reads:
            return (
                "%s: envelope shape -- %s sit inside 'input' but %s reads them "
                "at the top level. Answered %r (CONTRACT: envelope shape)"
                % (block_id, ", ".join(buried), block_id, text[:90])
            )

    # (c) an action that needs an id the block itself mints, called without it
    rule = RESOURCE_OBLIGATIONS.get(block_id) or {}
    if action and action in (rule.get("into") or []):
        carry = rule.get("carry")
        if carry and not data.get(carry) and not inner.get(carry):
            return (
                "%s: called %s without %s. %s mints it and must run first, "
                "with the returned %s carried in. Answered %r "
                "(CONTRACT: missing precondition)"
                % (block_id, action, carry, rule.get("ensure"), carry,
                   text[:90])
            )

    return (
        "%s: refused the payload the coder wrote -- answered %r (CONTRACT)"
        % (block_id, text[:110])
    )


def _recording_execute(block_id, *a, **kw):
    cap = _seen["cap"]
    if cap is not None:
        _seen["blocks"].setdefault(cap, set()).add(str(block_id))
    result = _real_execute(block_id, *a, **kw)
    if cap is not None:
        payload = a[0] if a else kw.get("payload")
        act = kw.get("action")
        if act is None and len(a) > 1:
            act = a[1]
        note = _classify_refusal(block_id, payload, act, result)
        if note:
            line = _miss(cap, "%s: %s" % (cap, note))
            if line not in contract_misses:
                contract_misses.append(line)
    return result


_dispatch.execute = _recording_execute
for _name, _mod in list(sys.modules.items()):
    if _name.startswith("app.actions") and hasattr(_mod, "execute"):
        _mod.execute = _recording_execute

AUTH = {"Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN", "dev-local-token")}

try:
    client_cm = TestClient(app)
    client = client_cm.__enter__()
except Exception as exc:
    _halt("boot", [("boot", _exc_text(exc))])
targets = []


# ONE-RECORD ROUND TRIP (owner's ruling R1e, 2026-09-01).
#
#   "Boots and passes its own tests" is no longer enough; the product must
#   remember one thing it was told.
#
# residential-lettings booted, served all its routes, passed its own gate --
# and answered every GET with {"items": [], "total": 0}. Nothing in the
# factory asked the one question a buyer asks first: I gave it a record, is
# it still there?
#
# Scope, so this bites the real defect and nothing else. Only a capability
# whose entity is READABLE is judged: store.list_all does SELECT * FROM
# <entity>, so a generate-only capability with no table raises, _rows
# returns None, and the capability is reported as unjudged rather than
# failed. A capability that persists is judged on both halves -- the store
# grew, AND the GET hands the record back.
_ROUND_TRIP_CHECKED = set()


def _record_matches(record, body):
    """Does this stored/returned record carry a value the POST supplied?"""
    if not isinstance(record, dict):
        return False
    for key, want in (body or {}).items():
        got = record.get(key)
        if got is None:
            continue
        if got == want or str(got) == str(want):
            return True
    return False


def _listed_records(payload):
    """Pull the record list out of whatever shape the list route answers --
    by shape, never by a guessed key: a bare list, or every list of records
    (objects) the answer carries at its top level."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    out = []
    for value in payload.values():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            out.extend(value)
    return out


def _check_round_trip(cap_id, cls, body):
    """POST created it; can it be read back? Judged once per capability."""
    if cap_id in _ROUND_TRIP_CHECKED:
        return
    _ROUND_TRIP_CHECKED.add(cap_id)
    entity = _entity_of(cap_id, cls)
    rows = _rows(entity)
    if rows is None:
        # Not judgeable rather than failed: no table to read.
        return
    if rows < 1:
        roundtrip_misses.append(_miss(
            cap_id,
            "%s: POST reported success and %s holds 0 row(s) -- the product "
            "did not remember what it was told (ROUND-TRIP: nothing stored)"
            % (cap_id, entity),
        ))
        return
    stored = _list_entity(entity)
    if not any(_record_matches(r, body) for r in stored):
        roundtrip_misses.append(_miss(
            cap_id,
            "%s: %s grew to %d row(s) but none carries a value the POST "
            "supplied (ROUND-TRIP: wrong record)" % (cap_id, entity, rows),
        ))
        return
    # ... and the GET the buyer actually makes — authenticated, matching
    # the emitted routes (every CRUD route requires the platform token).
    try:
        got = client.get("/v1/" + cap_id, headers=AUTH)
    except Exception as exc:
        findings.append(
            ("probe_error", "%s: GET raised %s: %s" % (cap_id, type(exc).__name__, exc))
        )
        return
    if got.status_code in (404, 405):
        return  # no list route on this capability; the store half stands
    if got.status_code != 200:
        roundtrip_misses.append(_miss(
            cap_id,
            "%s: stored the record, then GET answered HTTP %s "
            "(ROUND-TRIP: not readable)" % (cap_id, got.status_code),
        ))
        return
    listed = _listed_records(got.json() if got.content else {})
    if not listed:
        roundtrip_misses.append(_miss(
            cap_id,
            "%s: %s holds %d row(s) and GET answered with none -- the exact "
            "residential-lettings answer (ROUND-TRIP: empty list)"
            % (cap_id, entity, rows),
        ))
        return
    if not any(_record_matches(r, body) for r in listed):
        roundtrip_misses.append(_miss(
            cap_id,
            "%s: GET returned %d record(s), none carrying a value the POST "
            "supplied (ROUND-TRIP: wrong record returned)"
            % (cap_id, len(listed)),
        ))

# -- phase 1: baseline -----------------------------------------------------
for cap_id, cls in MODELS.items():
    body = _payload_for(cap_id)
    _seen["cap"] = cap_id
    try:
        resp, _corrected = _post_accepting("/v1/" + cap_id, body, AUTH, cap_id)
    except Exception as exc:
        target = persist_misses if isinstance(exc, _db_error_types()) else schema_misses
        target.append(
            _miss(cap_id, "%s: POST raised %s" % (cap_id, _exc_text(exc)))
        )
        continue
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = None
    if is_declared_refusal(cap_id, resp.status_code, data):
        # Its connector is a DECLARED placeholder: the typed refusal is the
        # right answer. Neither the round trip nor fail-closed under forced
        # block failure is judgeable -- nothing is built to fail -- so it is
        # named with its reason, not probed and not counted as a miss.
        placeholder_unjudged.append(_miss(
            cap_id,
            "%s: not judgeable -- declared placeholder connector(s) need %s"
            % (cap_id, ", ".join(data.get("settings") or [])),
        ))
        continue
    if resp.status_code != 200:
        schema_misses.append(
            # The product's own refusal travels with the miss: a bare status
            # left the writer nothing to converge on (live 9de69276).
            _miss(cap_id, "%s: baseline POST returned HTTP %s: %s%s" % (
                cap_id, resp.status_code, (resp.text or "")[:300],
                (" [the probe already tried the values the route named: "
                 + "; ".join(_corrected) + "]") if _corrected else "",
            ))
        )
        continue
    data = data if isinstance(data, dict) else {}
    if data.get("ok") is not False:
        _check_round_trip(cap_id, cls, body)
    # House convention: ``ok is False`` is the refusal. A route that omits
    # ``ok`` has not refused, so it belongs in phase two rather than being
    # reported here as a schema rejection.
    if data.get("ok") is False:
        # Kernel wrap (#237) and the handler wrap refuse success when a
        # real block fails on the sample payload. That is not a schema
        # miss: a block was reached, so phase two can still judge
        # fail-closed. Treating it as a skip emptied targets on the live
        # construction kit and SystemExit'd before the F1 miss path.
        if _seen["blocks"].get(cap_id):
            targets.append((cap_id, cls))
            continue
        schema_misses.append(_miss(
            cap_id,
            "%s: refused a payload built from its own declared constraints (%s)"
            % (cap_id, str(data.get("error"))[:160]),
        ))
        continue
    targets.append((cap_id, cls))

_seen["cap"] = None

# F11: a declared BLOCK_IDS entry that never gets invoked is a build error,
# not a comment. Checked after the baseline because that is the phase where
# a handler runs to completion -- under forced failure it may short-circuit
# on the first block and never reach the rest.
for _cap_id, _cls in targets:
    _mod = sys.modules.get("app.actions." + _cap_id.replace("-", "_"))
    _declared = [str(b) for b in (getattr(_mod, "BLOCK_IDS", None) or [])]
    if not _declared:
        continue
    _invoked = _seen["blocks"].get(_cap_id, set())
    _never = sorted(b for b in _declared if b not in _invoked)
    if _never:
        f11_misses.append(_miss(
            _cap_id,
            "%s: declares block(s) it never invokes: %s (F11)"
            % (_cap_id, ", ".join(_never)),
        ))

if persist_misses:
    # Isolated schema refusals may continue; a missing persist table is
    # the photographed Veterinary Care Platform class and must halt. Decided
    # by the exception's class (a database error), not its message.
    _halt("persist", [("persist", m) for m in persist_misses])
for _u in placeholder_unjudged:
    _record("unjudged", "placeholder", _u)
if not targets and (schema_misses or not placeholder_unjudged):
    # Every capability failed schema (or never reached a probeable
    # state). Isolated schema misses do not take this path, and neither
    # does a product whose only capabilities are declared placeholders.
    _halt("schema", list(findings) + [("schema", m) for m in schema_misses])

# -- phase 2: every block call fails --------------------------------------
import app.dispatch as _dispatch


_calls = {"n": 0}


def _forced_failure(block_id, payload=None, action=None, params=None, **kw):
    _calls["n"] += 1
    return {
        "status": "error",
        "block": block_id,
        "action": action,
        "error": "writer_behaviour gate: forced block failure",
    }


_dispatch.execute = _forced_failure
# Handlers bind the name at import time, so patch the bound references too.
for _name, _mod in list(sys.modules.items()):
    if _name.startswith("app.actions") and hasattr(_mod, "execute"):
        _mod.execute = _forced_failure

misses = []
honest = 0

for cap_id, cls in targets:
    entity = _entity_of(cap_id, cls)
    before = _rows(entity)
    body = _payload_for(cap_id)
    _calls["n"] = 0
    try:
        resp = client.post("/v1/" + cap_id, json=body, headers=AUTH)
    except Exception as exc:
        findings.append(("probe_error", _miss(cap_id, "%s: POST raised under forced failure: %s" % (cap_id, exc))))
        continue
    data = resp.json() if resp.content else {}
    if _calls["n"] == 0:
        # The capability never reached a block, so there is no block failure
        # for it to propagate. A pure-GENERATE capability is legitimately in
        # this position; asserting fail-closed here would reject it for
        # having no dependency to fail.
        continue
    missed = False
    if resp.status_code == 200 and data.get("ok") is not False:
        misses.append(_miss(
            cap_id,
            "%s: did not fail closed — answered %s while every block call "
            "failed (F1)" % (cap_id, json.dumps(data)[:120]),
        ))
        missed = True
    after = _rows(entity)
    if before is None or after is None:
        findings.append(
            ("unreadable", _miss(cap_id, "%s: cannot read entity %r — persistence was never checked" % (cap_id, entity)))
        )
    elif after > before:
        misses.append(
            _miss(cap_id, "%s: persisted %d row(s) after a failed handler (F1)" % (cap_id, after - before))
        )
        missed = True
    if not missed:
        honest += 1

if findings:
    for _kind, _text in findings:
        _record("finding", _kind, _text)
    raise SystemExit(1)
_f11_caps = set(_cap_of(m) for m in f11_misses)
if f11_misses and all(cid in _f11_caps for cid, _cls in targets):
    # Every probed capability declared unused BLOCK_IDS. Isolated F11
    # (below) continues so a mixed workspace can still ship a zip.
    _halt("f11", [("f11", m) for m in f11_misses])
_rt_caps = set(_cap_of(m) for m in roundtrip_misses)
if roundtrip_misses and all(cid in _rt_caps for cid, _cls in targets):
    # Nothing the product was told survived. That is residential-lettings
    # exactly, and it is the bar "boots and passes its own tests" never
    # reached. Isolated misses (below) record and continue.
    _halt("roundtrip", [("roundtrip", m) for m in roundtrip_misses])
_contract_caps = set(_cap_of(m) for m in contract_misses)
if contract_misses and all(cid in _contract_caps for cid, _cls in targets):
    # Every probed capability wrote a payload its own blocks refuse. That is
    # the residential-lettings shape exactly: a zip that boots and cannot
    # persist. Isolated contract misses (below) continue so a mixed
    # workspace can still ship.
    _halt("contract", [("contract", m) for m in contract_misses])
if misses and honest == 0:
    # Every block-reaching capability lied. Same as LotDesk: the WRITER
    # produced nothing honest to ship. Isolated misses (below) continue.
    for _m in misses:
        _record("finding", "f1", _m)
    raise SystemExit(1)
# Isolated F1, F11, contract and schema refusals: record, do not halt.
for _kind, _misses in (
    ("f1", misses), ("schema", schema_misses), ("f11", f11_misses),
    ("contract", contract_misses), ("roundtrip", roundtrip_misses),
):
    for _m in _misses:
        _record("miss", _kind, _m)
'''


_EXCEPTION_LINE_RE = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*): (.*)$")


def _exception_class(dotted: str) -> Optional[type]:
    """The exception class a traceback line names, resolved -- or None.

    ``sqlalchemy.exc.OperationalError`` imports ``sqlalchemy.exc`` and reads
    the attribute; a bare name is looked up in ``builtins``. Only a real
    ``BaseException`` subclass counts, so prose that happens to contain a
    colon is never mistaken for an exception.
    """
    import builtins
    import importlib

    module_name, _, name = dotted.rpartition(".")
    try:
        holder = importlib.import_module(module_name) if module_name else builtins
    except Exception:  # noqa: BLE001 -- an unimportable name is not a class
        return None
    found = getattr(holder, name, None)
    return found if isinstance(found, type) and issubclass(found, BaseException) else None


def _database_error_types() -> tuple:
    """The roots of every database / migration error the probe can raise:
    the DB-API error base (sqlite3) and SQLAlchemy's error base. Subclasses
    are found by the hierarchy, never by their names."""
    import sqlite3

    roots: list = [sqlite3.Error]
    try:
        from sqlalchemy.exc import SQLAlchemyError

        roots.append(SQLAlchemyError)
    except ImportError:  # pragma: no cover - sqlalchemy is a backend dependency
        pass
    return tuple(roots)


#: The typed record levels the probe emits. One tuple: the gate's reader
#: (probe_records) and the writer's self-check (render_self_check) both read
#: it, so the two can never disagree about what a record is.
RECORD_LEVELS = ("halt", "finding", "miss", "unjudged")


def probe_records(text: str) -> list[dict]:
    """The typed records in probe output: one JSON object per line carrying
    ``gate_record`` (halt / finding / miss / unjudged), ``kind`` and ``text``. Anything
    else on the stream (a library's own print, a traceback) is not a record."""
    out: list[dict] = []
    for line in (text or "").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(rec, dict)
            and rec.get("gate_record") in RECORD_LEVELS
            and isinstance(rec.get("kind"), str)
            and isinstance(rec.get("text"), str)
        ):
            out.append(rec)
    return out


def classify_unmarked_probe_failure(raw_lines: list[str]) -> str:
    """One sentence for a probe that died without a typed record.

    Read from the stderr's structure, never its words: the traceback's own
    exception line, resolved to its class and judged by the class hierarchy
    (a database error is a schema/migration halt). A raw stderr line is never
    the banner -- live veterinary-care, sqlite dumped CREATE TABLE columns and
    the Floor showed ``scheduled_time TEXT,`` as the reason.
    """
    raised = None
    for ln in (x.strip() for x in raw_lines):
        match = _EXCEPTION_LINE_RE.match(ln)
        if match:
            cls = _exception_class(match.group(1))
            if cls is not None:
                raised = (cls, match.group(1), match.group(2))
    if raised is not None:
        cls, name, message = raised
        message = message.split("[SQL:", 1)[0].strip()
        if issubclass(cls, _database_error_types()):
            return f"{SCHEMA_SQL_HALT}: {name}: {message}"[:280]
        return f"{HALT_SENTENCES['crash']}: {name}: {message}"[:280]
    if any(x.strip() for x in raw_lines):
        return f"{HALT_SENTENCES['crash']} with no typed record"
    return "behaviour probe failed with no output"


def record_finding(rec: dict) -> str:
    """One typed probe record as a typed Finding: the capability is the
    record's own field, the shape its kind -- never read from the text."""
    from app.factory.build.findings import Finding

    cap = rec.get("capability")
    return Finding(
        rec["text"],
        gate=GATE_NAME,
        capability_id=cap if isinstance(cap, str) and cap else None,
        finding_shape=rec.get("kind"),
    )


def findings_from_probe_stderr(stderr: str) -> list:
    """Halt and finding records as typed Findings, or one classified reason
    -- never raw lines."""
    recs = [r for r in probe_records(stderr) if r["gate_record"] in ("halt", "finding")]
    if recs:
        # The probe writes its halt banner before the per-capability records;
        # the first finding is what the ledger decision and the Floor show, so
        # the records that carry each capability's own refusal go first and the
        # banner last (live 9d382ae7: the decision read only "no capability
        # accepted its own schema" while the refusal texts sat behind it).
        ordered = sorted(recs, key=lambda r: r["gate_record"] == "halt")
        return [record_finding(r) for r in ordered][-20:]
    return [classify_unmarked_probe_failure((stderr or "").splitlines())]


def banner_from_records(records: list[dict], stderr: str = "") -> str:
    """Floor banner from typed probe records.

    A halt names itself (its canonical sentence). Probe errors with no halt
    are shown as the first one. A run whose only findings are F1 lies is the
    LotDesk banner. Nothing is decided by searching the text.
    """
    halts = [r for r in records if r["gate_record"] == "halt"]
    if halts:
        halt = halts[0]
        detail = next(
            (r["text"] for r in records
             if r["gate_record"] == "finding" and r["kind"] == halt["kind"]),
            "",
        )
        sentence = halt["text"]
        if halt["kind"] in ("migration", "import", "boot", "crash") and detail:
            return f"{sentence}: {detail}"[:280]
        return sentence
    findings = [r for r in records if r["gate_record"] == "finding"]
    if not findings:
        return classify_unmarked_probe_failure((stderr or "").splitlines())
    if all(r["kind"] == KIND_F1 for r in findings):
        return F1_HALT
    return findings[0]["text"]


def _pass_detail(
    f1_misses: list[str], schema_misses: list[str], f11_misses: list[str]
) -> str:
    """Success-path banner: name schema vs F11 vs F1, never collapse them."""
    parts = []

    def _caps(items: list) -> set:
        # The capability field of each typed miss; an untyped miss counts once.
        return {getattr(m, "capability_id", None) or m for m in items}

    if f1_misses:
        n = len(_caps(f1_misses))
        parts.append(
            f"{n} capability(ies) recorded as misses "
            "(success over a failed block)"
        )
    if schema_misses:
        n = len(_caps(schema_misses))
        parts.append(
            f"{n} capability(ies) recorded as misses (refused their own schema)"
        )
    if f11_misses:
        n = len(_caps(f11_misses))
        parts.append(
            f"{n} capability(ies) recorded as misses "
            "(declared blocks they never invoke — F11)"
        )
    if parts:
        return "; ".join(parts) + "; remaining capabilities fail closed"
    return "every capability fails closed when its blocks fail"


def _run_probe_source(ctx: Any, source: str) -> Any:
    """Run a rendered probe from a temp file (payload_helpers.run_probe_source)."""
    from app.factory.build.payload_helpers import run_probe_source

    return run_probe_source(ctx, source)


def _render_probe() -> str:
    """The probe with this factory's resource obligations, halt sentences,
    entity resolver and ONE payload source (payload_helpers) baked in. The
    payload base is TESTER's sampler over the live models, computed when the
    probe runs -- so the rendered bytes depend on the Factory alone."""
    from app.factory.build.block_obligations import resource_obligations
    from app.factory.build.payload_helpers import render_probe_payload

    return render_probe_payload(
        BEHAVIOUR_PROBE.replace(
            "RESOURCE_OBLIGATIONS = {}",
            "RESOURCE_OBLIGATIONS = " + repr(dict(resource_obligations())),
            1,
        ).replace("HALTS = {}", "HALTS = " + repr(dict(HALT_SENTENCES)), 1)
        .replace(ENTITY_RESOLVER_SLOT, ENTITY_RESOLVER_SRC, 1)
    )


#: The writer's self-check: the WRITER gate's own probe, stamped into the
#: workspace so the writer can see what the gate will see BEFORE it declares
#: done. Its own suite cannot -- F1 needs every block call made to fail.
SELF_CHECK_REL = "scripts/factory_checks.py"
SELF_CHECK_COMMAND = f"python {SELF_CHECK_REL}"

_SELF_CHECK_TEMPLATE = '''"""Factory self-check -- the WRITER gate's own behaviour probe.

Factory-owned and re-stamped on every writer pass: editing it changes
nothing, the gate runs its own copy of the same probe. Run it from the
workspace root before declaring done:

    {command}

It forces every block call to fail and records what each capability does.
Each line is one typed record: [halt] or [finding] fails the WRITER gate;
[miss] is a capability that reported success over a failed block (F1),
refused its own schema, or declared blocks it never calls (F11) -- fix
every one. Then it runs the product-gate suites TESTER stamped (once a
TESTER pass has put them on disk): the same files that judge you, rendered
from your own app/models.py. A [suite] line is a failing test -- every field
a handler requires must be declared in that capability's model FIELDS, and
a payload built from those FIELDS must be accepted. Exit 0 only when there
is nothing to fix.
"""

import json
import os
import subprocess
import sys

PROBE = {probe}
RECORD_LEVELS = {levels}
SUITES = {suites}
TIMEOUT_S = 900


def run_suites(root, env):
    """The TESTER suites on disk, every marker; one [suite] line per failure."""
    present = [s for s in SUITES if os.path.isfile(os.path.join(root, s))]
    if not present:
        print("[suite] none on disk yet (first writer pass): TESTER stamps them; "
              "the probe above already checks the same schema contract")
        return []
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider",
         "-m", "pilot or not pilot", *present],
        cwd=root, capture_output=True, text=True, env=env, timeout=TIMEOUT_S,
    )
    failures = [
        line for line in (proc.stdout or "").splitlines()
        if line.startswith("FAILED ") or line.startswith("ERROR ")
    ]
    if proc.returncode not in (0, 5) and not failures:
        failures.append("ERROR pytest exited " + str(proc.returncode) + ": "
                        + (proc.stdout or proc.stderr or "")[-600:])
    for line in failures:
        print("[suite] " + line)
    return failures


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("DATABASE_URL", None)
    # The probe runs from a temp file, as the gate runs it: on the command
    # line it outgrows the Windows argv limit (WinError 206).
    import tempfile
    fd, probe_path = tempfile.mkstemp(prefix="factory-check-probe-", suffix=".py")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(PROBE)
    loader = ("import runpy, sys; sys.path.insert(0, ''); "
              "runpy.run_path(" + repr(probe_path) + ", run_name='__main__')")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", loader],
            cwd=root,
            capture_output=True,
            text=True,
            env=env,
            timeout=TIMEOUT_S,
        )
    finally:
        try:
            os.unlink(probe_path)
        except OSError:
            pass
    records = []
    for line in ((proc.stdout or "") + "\\n" + (proc.stderr or "")).splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("gate_record") in RECORD_LEVELS:
            records.append(rec)
    for rec in records:
        print("[{{}}] {{}}: {{}}".format(rec["gate_record"], rec.get("kind"), rec.get("text")))
    halted = proc.returncode != 0
    to_fix = [r for r in records if r["gate_record"] in ("halt", "finding", "miss")]
    if halted and not records:
        print("[halt] probe: the probe exited " + str(proc.returncode) + " with no typed record")
        print((proc.stderr or "")[-2000:])
    print(
        "WRITER GATE WOULD FAIL" if halted
        else str(len(to_fix)) + " capability record(s) to fix" if to_fix
        else "every capability fails closed when its blocks fail"
    )
    suite_failures = run_suites(root, env)
    if suite_failures:
        print(str(len(suite_failures)) + " product-gate test(s) failing")
    return 1 if (halted or to_fix or suite_failures) else 0


if __name__ == "__main__":
    sys.exit(main())
'''


def render_self_check() -> str:
    """The stamped self-check: the SAME rendered probe the gate runs (its
    payload base read off the live models when it runs), then the
    product-gate suites TESTER stamped (product_suites)."""
    from app.factory.build.product_suites import PRODUCT_SUITES

    return _SELF_CHECK_TEMPLATE.format(
        command=SELF_CHECK_COMMAND,
        probe=repr(_render_probe()),
        levels=repr(RECORD_LEVELS),
        suites=repr(PRODUCT_SUITES),
    )


def emit_self_check(workspace: object) -> None:
    """Stamp the self-check (Factory-owned) before any writer path runs;
    re-stamped by factory_refresh before every TESTER."""
    from app.factory.build.workspace import write_workspace_text

    write_workspace_text(workspace, SELF_CHECK_REL, render_self_check())


def gate_writer_behaviour(ctx: "GateContext") -> "GateResult":
    """WRITER: isolated schema, F11, or F1 is a miss, not a halt.

    The probe still fail-closes when every capability is dishonest
    (all refuse their own schema, every capability declares unused
    blocks, or every block-reaching capability lies -- LotDesk). One
    miss among honest ones is recorded and the phase continues so
    TESTER/STORE_MANAGER can still produce a zip. Every miss is classed by
    the TYPED record the probe emitted, never by the words in its text.
    """
    from app.factory.build.gates import GateResult

    if not (ctx.workspace / "app" / "models.py").is_file():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="writer_no_models",
            detail="app/models.py is missing — nothing to probe",
            findings=["writer produced no models"],
        )

    proc = _run_probe_source(ctx, _render_probe())
    if proc.returncode != 0:
        records = probe_records(proc.stderr or "")
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="writer_behaviour_failed",
            detail=banner_from_records(records, proc.stderr or ""),
            findings=findings_from_probe_stderr(proc.stderr or ""),
        )
    raw_out = proc.stdout or ""
    raw_err = proc.stderr or ""
    records = probe_records(raw_out + "\n" + raw_err)
    miss_records = [r for r in records if r["gate_record"] == "miss"]
    unjudged = [r["text"] for r in records if r["gate_record"] == "unjudged"]

    def _of(kind: str) -> list:
        return [record_finding(r) for r in miss_records if r["kind"] == kind]

    misses = [record_finding(r) for r in miss_records]
    schema_misses = _of(KIND_SCHEMA)
    f11_misses = _of(KIND_F11)
    contract_misses = _of(KIND_CONTRACT)
    roundtrip_misses = _of(KIND_ROUNDTRIP)
    f1_misses = _of(KIND_F1)
    skipped = [
        ln
        for ln in raw_out.splitlines()
        if ln.strip() and ln.strip() != "SKIPPED" and not _is_record_line(ln)
    ]
    detail = _pass_detail(f1_misses, schema_misses, f11_misses)
    if contract_misses:
        detail += (
            f"; {len(contract_misses)} capability(ies) wrote a payload their "
            "blocks refuse (CONTRACT)"
        )
    if roundtrip_misses:
        detail += (
            f"; {len(roundtrip_misses)} capability(ies) could not read back a "
            "record they stored (ROUND-TRIP)"
        )
    if skipped:
        detail += f"; {len(skipped)} capability(ies) skipped — see payload"
    if unjudged:
        detail += (
            f"; {len(unjudged)} capability(ies) not judgeable: declared "
            "placeholder connector(s)"
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=detail,
        findings=list(misses),
        payload={
            "skipped": skipped,
            "misses": misses,
            "schema_misses": schema_misses,
            "f11_misses": f11_misses,
            "f1_misses": f1_misses,
            "contract_misses": contract_misses,
            "roundtrip_misses": roundtrip_misses,
            "unjudged": unjudged,
        },
    )


def _is_record_line(line: str) -> bool:
    return bool(probe_records(line))
