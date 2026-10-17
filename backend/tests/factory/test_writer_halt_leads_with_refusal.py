"""A WRITER-gate halt leads with the capability's own refusal, not the banner.

Live 2026-10-07 (9d382ae7, co-op repro): the round-1 decision recorded only
"no capability accepted its own schema". The probe had emitted each
capability's refusal text (#683) but after the halt banner, and the ledger
decision and Floor show the first finding.
"""

from __future__ import annotations

import json

from app.factory.build.writer_behaviour import SCHEMA_HALT, findings_from_probe_stderr

REFUSAL = "lantern_board: baseline POST returned HTTP 422: wick_count must be >= 1"


def test_the_first_finding_is_the_capabilitys_refusal_and_the_banner_comes_last():
    stderr = "\n".join(
        json.dumps(rec)
        for rec in (
            {"gate_record": "halt", "kind": "schema", "text": SCHEMA_HALT},
            {"gate_record": "finding", "kind": "schema", "text": REFUSAL,
             "capability": "lantern_board"},
        )
    )
    findings = findings_from_probe_stderr(stderr)
    assert str(findings[0]) == REFUSAL
    assert findings[0].capability_id == "lantern_board"
    assert str(findings[-1]) == SCHEMA_HALT
