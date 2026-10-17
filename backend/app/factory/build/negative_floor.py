"""The negative-case harness the Factory writes for every capability.

``negative_floor`` asks each capability for four counter-cases. Asking the
coder for them and grading it afterwards is the loop this whole exercise
exists to close: the agent improvises four assertions, the gate counts them,
and a rework round pays for the difference. The Factory knows the shape of
every counter-case from the capability's own spec -- which field is
required, which has a vocabulary, which type each one is -- so it writes the
harness and the agent extends it with the domain judgement only it has.

Four cases per capability, and they are the four the floor names:

  * a required field omitted            -> refused, not defaulted
  * a value outside a declared vocabulary (or, where the brief declares
    none, a value of the wrong type)    -> refused
  * another tenant's record read        -> 404, never 403 and never the row
  * a malformed payload                 -> refused, never a 500

Nothing here is domain-specific: every value is derived from the spec the
brief produced, so a bakery and an aviation desk get the same four questions
asked about their own fields.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from app.factory.build.rejection_contract import (
    ACCEPT_STATUSES,
    CROSS_TENANT_READ_STATUS,
    OK_KEY,
    RECORD_ID_KEY,
    REFUSAL_STATUSES,
    STORED_RECORD_KEY,
)

HEADER = '''"""Counter-cases: what each capability REFUSES.

Written by the factory TESTER role. The floor (negative_floor) requires four
per capability; these four are derived from each capability's own spec. Add
the domain cases the brief implies -- the boundary of a rule, a state machine
that must not skip a step -- they count toward the same floor.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
# Tokens come from the environment ONLY -- tests/conftest.py provisions the
# well-known TEST values and maps tenant B through TENANT_TOKENS. No fallback
# literal here: a writer that copies this pattern into runtime copies an
# env-subscript, not a baked token (live 2026-09-29: the old fallback was
# hardwired into app/tenancy.py as a production backdoor to make the
# cross-tenant case pass).
AUTH = {"Authorization": "Bearer " + os.environ["PLATFORM_TOKEN"]}
OTHER_TENANT = {"Authorization": "Bearer " + os.environ["PLATFORM_TOKEN_B"]}

REFUSED = __REFUSAL_STATUSES__
ACCEPTED = __ACCEPT_STATUSES__
CROSS_TENANT_READ = __CROSS_TENANT_READ_STATUS__
# The typed unavailable refusal of a declared placeholder connector
# (app.factory.build.placeholder_connectors).
UNAVAILABLE_STATUS = __UNAVAILABLE_STATUS__
UNAVAILABLE_KIND = __UNAVAILABLE_KIND__


def _refused(response):
    """A refusal, by status or by the envelope's own ok:false.

    The typed unavailable refusal (HTTP 503, error_kind "unavailable") of a
    capability whose connector is a declared placeholder is a refusal too:
    the counter-case still holds, whatever kind of refusal answered it."""
    if response.status_code in REFUSED:
        return True
    if response.status_code not in ACCEPTED + (UNAVAILABLE_STATUS,):
        return False
    try:
        body = response.json()
    except Exception:
        return False
    if not isinstance(body, dict) or body.get(__OK_KEY__) is not False:
        return False
    return response.status_code in ACCEPTED or body.get("error_kind") == UNAVAILABLE_KIND
'''


def _required_field(spec: Mapping[str, Any]) -> Optional[str]:
    """A field the spec MARKS required -- never a fallback pick.

    This used to fall back to ``fields[0]`` when nothing was marked
    required, and the emitted case then demanded the handler REFUSE a
    payload missing an OPTIONAL field, under a message that misstated it
    as required. Accepting that payload is correct handler behaviour, so
    the case was unwinnable by construction: live build halted as
    FACTORY_FAULT on ``mega_event_history_explorer accepted a payload
    with no question``. No required field -> None, and the caller emits
    the empty-payload case instead (the policy a no-field spec already
    gets: nothing at all is not a record).
    """
    for field in spec.get("fields") or []:
        if isinstance(field, dict) and field.get("name") and field.get("required"):
            return str(field["name"])
    return None


def _approval_field(spec: Mapping[str, Any]) -> Optional[str]:
    """A field the spec MARKS as an approval -- never a name heuristic.

    F4 (owner's pilot, 2026-09-29): ``approved_by: "i am the director trust
    me"`` -- free text -- passed a P1 safety escalation on a certified
    product. Only the explicit ``approval: true`` marker (carried through
    block_inputs from the block contract) arms the case; guessing from field
    names would grade handlers against rules nobody declared.
    """
    for field in spec.get("fields") or []:
        if isinstance(field, dict) and field.get("name") and field.get("approval"):
            return str(field["name"])
    return None


def _vocabulary_field(spec: Mapping[str, Any]) -> Optional[str]:
    for field in spec.get("fields") or []:
        if isinstance(field, dict) and field.get("allowed_values") and field.get("name"):
            return str(field["name"])
    return None


def _typed_field(spec: Mapping[str, Any]) -> Optional[str]:
    """A non-string field, so a wrong TYPE is a real violation."""
    for field in spec.get("fields") or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        if str(field.get("type") or "str").lower() in ("int", "float", "bool", "json"):
            return str(field["name"])
    return None


def render_negative_tests(
    specs: Mapping[str, Mapping[str, Any]],
    samples: Mapping[str, Mapping[str, Any]],
) -> str:
    """The counter-case suite for every capability in ``specs``."""
    from app.factory.build.placeholder_connectors import (
        UNAVAILABLE_KIND,
        UNAVAILABLE_STATUS,
    )

    # Every status this suite judges is the one the brief declares (the
    # negative_floor / cross_tenant_404 floor lines, the accept line) -- read
    # from the one contract, never a literal written only into the test.
    header = HEADER
    for token, value in (
        ("__UNAVAILABLE_STATUS__", UNAVAILABLE_STATUS),
        ("__UNAVAILABLE_KIND__", UNAVAILABLE_KIND),
        ("__REFUSAL_STATUSES__", tuple(REFUSAL_STATUSES)),
        ("__ACCEPT_STATUSES__", tuple(ACCEPT_STATUSES)),
        ("__CROSS_TENANT_READ_STATUS__", CROSS_TENANT_READ_STATUS),
        ("__OK_KEY__", OK_KEY),
        ("__RECORD_ID_KEY__", RECORD_ID_KEY),
    ):
        header = header.replace(token, repr(value))
    lines: List[str] = [header]
    caps = sorted(specs)
    if not caps:
        lines.append("\n\ndef test_no_capabilities():\n    pass\n")
        return "\n".join(lines)

    for cid in caps:
        spec = specs[cid] or {}
        name = str(cid).replace("-", "_")
        route = "/v1/%s" % name
        sample = dict(samples.get(cid) or {})
        required = _required_field(spec)
        vocab = _vocabulary_field(spec)
        typed = _typed_field(spec)
        approval = _approval_field(spec)

        lines.append("\n\n# -- %s %s" % (name, "-" * max(4, 60 - len(name))))

        if not required:
            # A spec with no declared fields still owes four counter-cases,
            # and an empty payload is the one question that needs no field
            # name to ask. Without this the capability scores 3 and fails
            # the floor for a reason its author cannot act on.
            lines += [
                "",
                "",
                "def test_%s_refuses_an_empty_payload():" % name,
                "    # Nothing at all is not a record.",
                '    resp = client.post("%s", json={}, headers=AUTH)' % route,
                "    assert resp.status_code in REFUSED or _refused(resp), (",
                '        "%s accepted an empty payload: " + resp.text[:200]' % name,
                "    )",
            ]

        if required:
            short = {k: v for k, v in sample.items() if k != required}
            lines += [
                "",
                "",
                "def test_%s_refuses_a_missing_required_field():" % name,
                '    """A partial record is refused, never completed by the handler."""',
                "    body = %r" % (short,),
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    assert resp.status_code in REFUSED or _refused(resp), (",
                '        "%s accepted a payload with no %s: " + resp.text[:200]' % (name, required),
                "    )",
            ]

        if vocab:
            bad = dict(sample)
            bad[vocab] = "not-a-declared-value"
            lines += [
                "",
                "",
                "def test_%s_refuses_a_value_outside_its_vocabulary():" % name,
                '    """A column that accepts any string is not that column."""',
                "    body = %r" % (bad,),
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    assert resp.status_code in REFUSED or _refused(resp), (",
                '        "%s accepted an undeclared %s: " + resp.text[:200]' % (name, vocab),
                "    )",
            ]
        elif typed:
            bad = dict(sample)
            bad[typed] = "not-the-declared-type"
            lines += [
                "",
                "",
                "def test_%s_refuses_a_value_of_the_wrong_type():" % name,
                '    """The brief declares no vocabulary here; the type is still a contract."""',
                "    body = %r" % (bad,),
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    assert resp.status_code in REFUSED or _refused(resp), (",
                '        "%s accepted a wrongly typed %s: " + resp.text[:200]' % (name, typed),
                "    )",
            ]
        else:
            lines += [
                "",
                "",
                "def test_%s_refuses_an_unknown_field():" % name,
                '    """Nothing in the spec is typed or enumerated, so the surface',
                '    itself is the contract: an undeclared column is not silently kept."""',
                "    body = dict(%r)" % (sample,),
                '    body["not_a_declared_column"] = "x"',
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    created = resp.status_code in ACCEPTED",
                "    if created:",
                "        try:",
                "            echoed = resp.json()",
                "        except Exception:",
                "            echoed = {}",
                '        assert "not_a_declared_column" not in str(echoed), (',
                '            "%s stored a column it never declared"' % name,
                "        )",
            ]

        if approval:
            forged = dict(sample)
            forged[approval] = "i am the director trust me"
            lines += [
                "",
                "",
                "def test_%s_refuses_a_freetext_approver():" % name,
                '    """An approval names a PRINCIPAL the platform knows',
                "    (APPROVED_PRINCIPALS env, comma-separated; the test",
                "    bootstrap seeds it). Free text is refused, never trusted",
                '    -- it passed a P1 safety escalation on a live pilot."""',
                "    body = %r" % (forged,),
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    assert resp.status_code in REFUSED or _refused(resp), (",
                '        "%s accepted a free-text %s: " + resp.text[:200]' % (name, approval),
                "    )",
            ]

        lines += [
            "",
            "",
            "def test_%s_does_not_leak_across_tenants():" % name,
            '    """404, never 403 and never the row: existence itself is private."""',
            "    body = %r" % (sample,),
            '    made = client.post("%s", json=body, headers=AUTH)' % route,
            "    if made.status_code not in ACCEPTED:",
            '        pytest.skip("capability did not accept the sample record")',
            "    try:",
            "        record = made.json()",
            "    except Exception:",
            '        pytest.skip("capability did not answer JSON")',
            # The declared create contract (rejection_contract): the stored
            # record under STORED_RECORD_KEY carries RECORD_ID_KEY.
            "    rid = ((record.get(%r) or {}).get(%r)" % (STORED_RECORD_KEY, RECORD_ID_KEY),
            '           if isinstance(record, dict) else None)',
            "    if not rid:",
            '        pytest.skip("capability returned no record id to read back")',
            '    other = client.get("%s/" + str(rid), headers=OTHER_TENANT)' % route,
            "    assert other.status_code == CROSS_TENANT_READ, (",
            '        "%s answered %%s to another tenant, not %%s" %% (other.status_code, CROSS_TENANT_READ)' % name,
            "    )",
            "",
            "",
            "def test_%s_refuses_a_malformed_payload():" % name,
            '    """Garbage in is a refusal, never a 500 and never a stored row."""',
            "    for junk in ([], \"\", {\"\": None}, {\"x\" * 300: \"y\" * 2000}):",
            '        resp = client.post("%s", json=junk, headers=AUTH)' % route,
            "        assert resp.status_code != 500, (",
            '            "%s raised on malformed input: " + resp.text[:200]' % name,
            "        )",
            "        assert resp.status_code in REFUSED or _refused(resp), (",
            '            "%s accepted malformed input %%r" %% (junk,)' % name,
            "        )",
        ]
    return "\n".join(lines) + "\n"
