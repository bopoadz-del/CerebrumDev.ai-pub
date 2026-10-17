"""PRODUCT gate: does the thing that shipped actually work? (ruling 1, 2026-09-01)

FINDING 3, in the owner's words:

    the factory gate must not be claimable as "all phase gates passed" while
    the only tests that check a business action are excluded. Add a third
    phase gate, PRODUCT: runs post-boot, executes the pilot-marked tests
    against the booted product AND the R1e one-record round-trip per
    capability. Code-phase gate stays as is; the verdict line becomes three
    gates, each named with its scope. A product that passes code-phase but
    fails PRODUCT is reported exactly so.

The defect this closes is not a bug in any one check; it is a claim. The
code-phase suite runs ``pytest -m "not pilot"`` -- and ``@pytest.mark.pilot``
is precisely the marker on the tests that exercise a business action against
a booted product. So "all phase gates passed" was, literally, "everything
except the tests that check the product works passed". residential-lettings
built, shipped a 216-file zip, booted, served all seventeen routes, and
could not persist one record -- with every gate green.

THE TWO HALVES, and why both are needed:

* the pilot-marked SUITE is what the TESTER wrote about this product's own
  behaviour. It is the product's own account of itself.
* the one-record ROUND-TRIP (R1e) is the factory's account, identical for
  every product and impossible to write around: POST creates, GET returns
  it. A suite can be green and shallow; a round-trip cannot.

SCOPE, stated so a reader knows what a PASS here does and does not mean.
This gate boots the product in-process (``TestClient``, so the lifespan runs
its migrations and R1c preconditions) and asks each capability to remember
one record. It is not a deployment, not a load test, and not a judgement of
whether the answers are correct -- only that the product is a product.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List

from app.factory.build.entity_contract import ENTITY_RESOLVER_SLOT, ENTITY_RESOLVER_SRC

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "product_green"

#: Scope sentences for the three-gate verdict line. Each says what the gate
#: LOOKED AT, so no one has to infer it from a gate name again.
GATE_SCOPES = {
    "CODE": 'the code-phase suite (pytest -m "not pilot") — imports, routes, handlers',
    "PRODUCT": (
        "post-boot: the pilot-marked tests against the booted product, and a "
        "one-record round-trip per capability (POST creates, GET returns it)"
    ),
    "STORE": (
        "scripts/acceptance.py (≥12 measured checks) inside the Store-built "
        "Docker image — k/k required; restart-survival of the booted store; "
        "authorship floor is not acceptance"
    ),
}


from app.factory.build.rejection_contract import contract_source  # noqa: E402

#: Boots the product and asks every capability to remember one record.
#:
#: Runs inside the GENERATED workspace, which carries no factory code, so it
#: is a source string rather than an import. Findings are marked so the
#: gate never mistakes alembic's or uvicorn's stderr for its own reason.
_ROUND_TRIP_SOURCE = r'''
import os, sys, tempfile

# Isolate STORAGE_PATH the same way writer_behaviour does. A leftover
# ./data/platform.db already stamped at 0001_baseline (CLI alembic, or a
# prior TestClient) makes upgrade_head() a no-op after the factory
# rewrites 0001 — live Veterinary Care Platform then POSTed
# OperationalError: no such table: audit / dashboard / veterinary_care_core.
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="product-gate-")
os.environ.setdefault("PLATFORM_TOKEN", "dev-local-token")
sys.path.insert(0, os.getcwd())

try:
    from app.models import MODELS
    from app import store
    from app.main import app
    from fastapi.testclient import TestClient
except Exception as exc:
    sys.stderr.write(
        "GATE-FINDING: workspace does not import: %s: %s\n"
        % (type(exc).__name__, exc)
    )
    raise SystemExit(1)

if not MODELS:
    sys.stderr.write(
        "GATE-FINDING: no capabilities to round-trip (app.models.MODELS is empty)\n"
    )
    raise SystemExit(1)


# The payload every Factory probe posts: TESTER's base sample through the one
# builder (payload_helpers.render_probe_payload defines _payload_for here).
# __PROBE_PAYLOAD__ (rendered in by payload_helpers.render_probe_payload)


AUTH = {"Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN", "dev-local-token")}

# The Factory-written declaration of placeholder connectors. A product built
# before it existed declares none, so every capability is judged.
try:
    from app.placeholders import is_declared_refusal
except Exception:
    def is_declared_refusal(capability_id, status_code, body):
        return False


ENTITY_RESOLVER = None  # rendered in at definition (entity_contract)

def _rows(entity):
    try:
        return len(_list_entity(entity))
    except Exception:
        return None


def _record_matches(record, body):
    if not isinstance(record, dict):
        return False
    for key, want in (body or {}).items():
        got = record.get(key)
        if got is None:
            continue
        if got == want or str(got) == str(want):
            return True
    return False


# __CREATE_CONTRACT__


# The lifespan is the point: it runs the migrations and, since R1c, the
# platform preconditions. A bare TestClient() skips it, and every capability
# would then fail on a schema-less database for a reason that has nothing to
# do with the product.
client_cm = TestClient(app)
client = client_cm.__enter__()

misses = []
passed = []
unjudged = []

for cap_id, cls in MODELS.items():
    body = _payload_for(cap_id)
    try:
        resp, _corrected = _post_accepting("/v1/" + cap_id, body, AUTH, cap_id)
    except Exception as exc:
        misses.append("%s: POST raised %s: %s" % (cap_id, type(exc).__name__, exc))
        continue
    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = None
    if is_declared_refusal(cap_id, resp.status_code, data):
        # Its connector is a DECLARED placeholder: the typed refusal is the
        # right answer, and there is no record to remember. Named with its
        # reason, never counted as a pass or a miss.
        unjudged.append(
            "%s (declared placeholder connector(s) %s not configured)"
            % (cap_id, ", ".join(data.get("settings") or []))
        )
        continue
    if resp.status_code not in _ACCEPT_STATUSES:
        misses.append("%s: POST answered HTTP %s" % (cap_id, resp.status_code))
        continue
    data = data if isinstance(data, dict) else {}
    if isinstance(data, dict) and data.get(_OK_KEY) is False:
        misses.append(
            "%s: POST refused its own sample payload: %s"
            % (cap_id, str(data.get(_ERROR_KEY) or data)[:120])
        )
        continue

    entity, declared = _declared_entity(cap_id, cls)
    if not declared:
        # Where it persists is the product's declaration (the ENTITY its
        # handler module carries, which the route saves to). Never guessed
        # from the capability id.
        misses.append(
            "%s: declares no store entity -- neither app/routes.py ROUTE_ENTITIES "
            "nor app/actions/%s.py ENTITY says where its route saves (ENTITY = "
            "None for a capability that persists nothing)"
            % (cap_id, str(cap_id).replace("-", "_"))
        )
        continue
    if not entity:
        # Declared read-only/aggregate: nothing to remember. Named rather
        # than counted as a pass, so a product made entirely of these cannot
        # be reported as round-tripping.
        unjudged.append("%s (declares no persisted entity)" % cap_id)
        continue
    rows = _rows(entity)
    if rows is None:
        misses.append(
            "%s: declares ENTITY %r but the store cannot read it back"
            % (cap_id, entity)
        )
        continue
    if rows < 1:
        misses.append(
            "%s: POST reported success and %s holds 0 row(s) -- the product "
            "did not remember what it was told" % (cap_id, entity)
        )
        continue
    if not any(_record_matches(r, body) for r in _list_entity(entity)):
        misses.append(
            "%s: %s grew to %d row(s) but none carries a value the POST "
            "supplied" % (cap_id, entity, rows)
        )
        continue

    try:
        # The same token the POST used: the list route resolves the same
        # tenant, and a token-guarded GET without one never reads anything.
        got = client.get("/v1/" + cap_id, headers=AUTH)
    except Exception as exc:
        misses.append("%s: GET raised %s: %s" % (cap_id, type(exc).__name__, exc))
        continue
    if got.status_code in (404, 405):
        # No list route. The store half stands and is reported as such.
        passed.append("%s (stored; no list route to read it back)" % cap_id)
        continue
    if got.status_code not in _ACCEPT_STATUSES:
        misses.append(
            "%s: stored the record, then GET answered HTTP %s"
            % (cap_id, got.status_code)
        )
        continue
    listed = _listed(got.json() if got.content else {})
    if not listed:
        misses.append(
            "%s: %s holds %d row(s) and GET answered with none"
            % (cap_id, entity, rows)
        )
        continue
    if not any(_record_matches(r, body) for r in listed):
        misses.append(
            "%s: GET returned %d record(s), none carrying a value the POST "
            "supplied" % (cap_id, len(listed))
        )
        continue
    passed.append(cap_id)

for m in misses:
    sys.stdout.write("GATE-MISS: %s\n" % m)
for u in unjudged:
    sys.stdout.write("GATE-UNJUDGED: %s\n" % u)
sys.stdout.write(
    "PRODUCT-SUMMARY: %d round-tripped, %d failed, %d unjudged, %d capabilities\n"
    % (len(passed), len(misses), len(unjudged), len(MODELS))
)
raise SystemExit(1 if misses else 0)
'''
#: The probe source before its payload is rendered in (render_round_trip_probe).
ROUND_TRIP_PROBE_TEMPLATE = _ROUND_TRIP_SOURCE.replace(
    ENTITY_RESOLVER_SLOT, ENTITY_RESOLVER_SRC, 1
).replace(
    # The declared status/key contract and the shape-based list reader, from
    # the one module every Factory check reads (rejection_contract).
    "# __CREATE_CONTRACT__\n", contract_source() + "\n", 1
)


def render_round_trip_probe() -> str:
    """The round-trip probe with the ONE payload source every Factory probe
    posts (payload_helpers): TESTER's sampler over the live models, through
    the builder."""
    from app.factory.build.payload_helpers import render_probe_payload

    return render_probe_payload(ROUND_TRIP_PROBE_TEMPLATE)


def __getattr__(name: str):
    # ROUND_TRIP_PROBE renders TESTER's sampler from roles_handlers, which
    # imports this module: render on first use, not at import.
    if name == "ROUND_TRIP_PROBE":
        probe = render_round_trip_probe()
        globals()["ROUND_TRIP_PROBE"] = probe
        return probe
    raise AttributeError(name)


def _marked(lines: List[str], prefix: str) -> List[str]:
    return [ln.split(prefix, 1)[1].strip() for ln in lines if prefix in ln]


def gate_round_trip(ctx: "GateContext") -> "GateResult":
    """Every capability remembers one record it was given.

    Fails when ANY capability fails to round-trip, and also when NOTHING was
    judgeable: a gate that judged nothing must not report a pass, which is
    the same class of defect as the excluded pilot tests.
    """
    from app.factory.build.gates import GateResult

    if not (ctx.workspace / "app" / "models.py").is_file():
        return GateResult(
            ok=False,
            gate="product_round_trip",
            reason="product_no_models",
            detail="app/models.py is missing — there is no product to boot",
            findings=["no models to round-trip"],
        )

    from app.factory.build.payload_helpers import run_probe_source

    proc = run_probe_source(ctx, render_round_trip_probe())
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
    findings = _marked(out, "GATE-FINDING: ")
    misses = _marked(out, "GATE-MISS: ")
    unjudged = _marked(out, "GATE-UNJUDGED: ")
    summary = next(
        (ln.split("PRODUCT-SUMMARY: ", 1)[1].strip()
         for ln in out if "PRODUCT-SUMMARY: " in ln),
        "",
    )

    if findings:
        return GateResult(
            ok=False,
            gate="product_round_trip",
            reason="product_boot_failed",
            detail="the product did not boot: " + findings[0],
            findings=findings[:20],
        )
    if misses:
        return GateResult(
            ok=False,
            gate="product_round_trip",
            reason="round_trip_misses",
            detail=(
                "%d capability(ies) did not remember a record they were given"
                % len(misses)
            ),
            findings=misses[:20],
            payload={"summary": summary, "unjudged": unjudged},
        )
    if proc.returncode != 0:
        return GateResult(
            ok=False,
            gate="product_round_trip",
            reason="round_trip_probe_failed",
            detail="round-trip probe exited %s with no finding" % proc.returncode,
            findings=[ln for ln in out if ln.strip()][-8:] or ["no output"],
        )
    if summary.startswith("0 round-tripped"):
        return GateResult(
            ok=False,
            gate="product_round_trip",
            reason="round_trip_unjudged",
            detail=(
                "no capability was judgeable — the round-trip check ran and "
                "decided nothing, which is not a pass"
            ),
            findings=unjudged[:20] or ["every capability was unjudged"],
            payload={"summary": summary},
        )
    return GateResult(
        ok=True,
        gate="product_round_trip",
        detail=summary or "every capability round-tripped a record",
        payload={"summary": summary, "unjudged": unjudged},
    )


def gate_product(ctx: "GateContext") -> "GateResult":
    """PRODUCT: the pilot-marked suite AND the one-record round-trip.

    Both halves must pass, and a failure names WHICH half — "PRODUCT failed"
    with no scope is the shape of report this gate exists to replace.
    """
    from app.factory.build.brief_gates import PRODUCT_GATE_CHECK
    from app.factory.build.gates import GateResult, gate_suite_green
    from app.factory.build.persist_accept import PRODUCT_ROUND_TRIP_CHECK
    from dataclasses import replace

    # Each half names the brief check it measures (brief_gates), so the
    # runner can tell a brief-defined failure from a factory-invented one.
    suite = gate_suite_green(replace(ctx, suite_marker="pilot"))
    if not suite.ok:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason=suite.reason or "pilot_suite_red",
            detail="PRODUCT (pilot-marked suite): " + suite.detail,
            findings=list(suite.findings),
            payload={
                "half": "pilot_suite",
                **dict(suite.payload),
                "check": PRODUCT_GATE_CHECK,
            },
        )

    trip = gate_round_trip(ctx)
    if not trip.ok:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason=trip.reason or "round_trip_failed",
            detail="PRODUCT (one-record round-trip): " + trip.detail,
            findings=list(trip.findings),
            payload={
                "half": "round_trip",
                **dict(trip.payload),
                "check": PRODUCT_ROUND_TRIP_CHECK,
            },
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail="PRODUCT: %s; round-trip: %s" % (suite.detail, trip.detail),
        payload={"pilot_suite": suite.detail, **dict(trip.payload)},
    )
