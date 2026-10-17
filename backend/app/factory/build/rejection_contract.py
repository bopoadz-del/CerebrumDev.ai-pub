"""The machine-readable shape of a validation rejection.

A Factory-rendered product refuses an invalid payload in two places: the
route guard ``app.auth.reject_invalid_payload`` (HTTP 422) and the handler
constraint guard (HTTP 200, ``ok: false``). Both used to say what was wrong
only in prose ("status must be one of: open, closed"), and the emitted route
test learned the accepted values by running a vocabulary regex over the raw
response text. Over a JSON body that regex matched inside keys -- live
2026-10-05: ``"allowed_scopes":`` yielded the value ``_scopes":``, which the
test wrote into an unrelated field and oscillated on until
SAME_FAILURE_TWICE.

Now the rejection carries its own structure, rendered from these constants
by BOTH emitters, so the product and the test agree by construction and
nothing parses prose:

* 422 path -- response headers ``REJECTED_FIELD_HEADER`` (the field),
  ``REJECTION_REASON_HEADER`` (one of ``REASONS``) and, for a closed
  vocabulary, ``ALLOWED_VALUES_HEADER`` (a JSON list).
* ``ok: false`` path -- the same three facts as body keys.

The prose message stays for humans; no machine reads it.
"""

from __future__ import annotations

REJECTED_FIELD_HEADER = "X-Rejected-Field"
REJECTION_REASON_HEADER = "X-Rejection-Reason"
ALLOWED_VALUES_HEADER = "X-Allowed-Values"

REJECTED_FIELD_KEY = "rejected_field"
REJECTION_REASON_KEY = "rejection_reason"
ALLOWED_VALUES_KEY = "allowed_values"

MISSING_REQUIRED = "missing_required"
NOT_ALLOWED = "not_allowed"
REASONS = (MISSING_REQUIRED, NOT_ALLOWED)


# --- The create response a Factory-rendered capability route answers ---------
# One definition, read by the route renderer (roles_handlers) and by every
# Factory check that creates a record through a route and reads it back (the
# Store gate's cross_tenant_404). A check that indexed a key the contract never
# promised -- or took HTTP 200 as success while the body said ``ok: false`` --
# read a refused create as "no stored id" and sent the writer to fix tenancy
# (live 2026-10-07, 9de69276).
OK_KEY = "ok"
ERROR_KEY = "error"
STORED_RECORD_KEY = "stored"
RECORD_ID_KEY = "id"


# --- The HTTP status contract every Factory check reads ----------------------
# Each value is stated to the writer by the floor line or brief line named
# beside it, and every check that judges a status reads it from here, so a
# check can never demand a status the brief never declared (the class that
# kept stopping builds: a literal written into one checker, never into the
# brief). tests/factory/test_declared_contract_sweep.py holds the floor lines
# and the emitted suites to these values.
#
# brief: "Accept means HTTP 200, not ok:false" (persist_accept, schema_accept)
ACCEPT_STATUSES = (200,)
# floor no_token_401
AUTH_REFUSAL_STATUS = 401
# floor missing_field_422 / enum_422
VALIDATION_REFUSAL_STATUS = 422
# floor cross_tenant_404
CROSS_TENANT_READ_STATUS = 404
# floor negative_floor: what "rejected" / "blocked" means on the wire. Stated
# in that floor line by refusal_statement(), never only here.
REFUSAL_STATUSES = (400, 403, 404, 409, 422)


def refusal_statement() -> str:
    """The negative_floor floor line's definition of a refusal, rendered from
    REFUSAL_STATUSES so the brief and every check say the same thing."""
    codes = "/".join(str(code) for code in REFUSAL_STATUSES)
    return (
        "A refusal is an HTTP %s answer, or HTTP %s with %r: false and %r "
        "saying why; never a 500." % (codes, ACCEPT_STATUSES[0], OK_KEY, ERROR_KEY)
    )


# --- How a list route's answer is read ---------------------------------------
# By SHAPE, never by a guessed key: a bare JSON list, or every top-level list
# of objects the answer carries. The brief never declared a list key, so a
# reader that tried a word list of keys asserted a shape nobody promised. One
# source, rendered into every reader (the PRODUCT round-trip probe and the
# emitted route suites); writer_behaviour's probe reads by the same rule.
LISTED_RECORDS_SRC = '''
def _listed(payload):
    """The records a list route answered -- by shape, never a guessed key."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict) or payload.get(%r) is False:
        return []
    out = []
    for value in payload.values():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            out.extend(value)
    return out
''' % (OK_KEY,)


def contract_source() -> str:
    """Module-level source a rendered probe or suite includes: the declared
    status/key constants and the list reader, all from this one module."""
    return "\n".join(
        [
            "_ACCEPT_STATUSES = %r" % (tuple(ACCEPT_STATUSES),),
            "_OK_KEY = %r" % (OK_KEY,),
            "_ERROR_KEY = %r" % (ERROR_KEY,),
            "_RECORD_ID_KEY = %r" % (RECORD_ID_KEY,),
            "_NOT_FOUND = %r" % (CROSS_TENANT_READ_STATUS,),
            LISTED_RECORDS_SRC,
        ]
    )
