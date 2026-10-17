"""Schema-accept contract for C-BRIEF + WRITER ``writer_behaviour``.

The live VetCare Floor halt (sess_91553364089d4970) was:

    WRITER gate 'writer_behaviour' failed: no capability accepted its own schema.

That sentence is the baseline phase of ``writer_behaviour``: the harness
POSTs ``/v1/{capability_id}`` with a payload built from that capability's
own ``FIELDS`` + ``CONSTRAINTS``. Every capability refused before a block
was reached.

This module is the one brief-facing contract. The compiler fills BUILD and
ACCEPTANCE from it. The coding-agent system brief cites it. An LLM never
writes these rules. Sampling literals must stay aligned with the probe in
``writer_behaviour.BEHAVIOUR_PROBE`` (``_value`` / ``_payload``).
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from app.factory.build.block_obligations import ENVELOPE_STATUS_VALUES
from app.factory.build.writer_behaviour import GATE_NAME, SCHEMA_HALT

SCHEMA_ACCEPT_GATE = GATE_NAME
SCHEMA_ACCEPT_HALT = SCHEMA_HALT
SCHEMA_ACCEPT_CHECK = "writer_behaviour"

#: The envelope status vocabulary's first value (it is declared).
ENVELOPE_STATUS_SAMPLE = ENVELOPE_STATUS_VALUES[0]  # open

#: A safe channel value for a channel field that DECLARES no vocabulary
#: (used by workflow guidance; the probe never reads meaning from a name).
CHANNEL_SAMPLE = "email"

#: Probe ``_value`` for a generic str field with no other heuristic.
GENERIC_STR_SAMPLE = "sample"

#: Probe temporal samples (must not be the word ``sample``).
DATETIME_SAMPLE = "2026-09-03T10:00:00"
DATE_SAMPLE = "2026-09-03"
TIME_SAMPLE = "10:00:00"
EMAIL_SAMPLE = "guest@example.com"  # the one builder's declared-email sample

#: Minimum envelope every capability carries after ``ensure_record_envelope``.
ENVELOPE_ACCEPT_SAMPLE: Dict[str, Any] = {
    "reference": GENERIC_STR_SAMPLE,
    "status": ENVELOPE_STATUS_SAMPLE,
}


def schema_accept_rules_text() -> str:
    """BUILD cut: what the WRITER gate will POST and what accept means."""
    vocab = " | ".join(ENVELOPE_STATUS_VALUES)
    sample_json = (
        '{"reference": "'
        + GENERIC_STR_SAMPLE
        + '", "status": "'
        + ENVELOPE_STATUS_SAMPLE
        + '"}'
    )
    return "\n".join(
        [
            f"WRITER gate {SCHEMA_ACCEPT_GATE} (baseline, before PRODUCT):",
            "The harness POSTs /v1/{capability_id} with a payload built from",
            "that capability's own FIELDS + CONSTRAINTS. A route or handle()",
            "that refuses that payload before execute() fails the gate with:",
            f"  {SCHEMA_ACCEPT_HALT}",
            "Accept means HTTP 200 and not ok:false before a block is reached.",
            "If you need a field, type, or vocabulary, declare it on the spec",
            "so the sample includes it (allowed_values[0], bounds, format).",
            "Do not invent a second, stricter contract the spec cannot express.",
            "Do not require block-specific keys (topic, sql/table, file paths,",
            "team_id, channel, steps) from the caller — construct those inputs.",
            "",
            "Sampling rules (must match writer_behaviour probe _value) -- read",
            "from what the field DECLARES, never from its name:",
            "- CONSTRAINTS.allowed_values[0] when declared (the envelope status",
            f"  declares {vocab}, so it samples as {ENVELOPE_STATUS_SAMPLE})",
            f"- type datetime / format datetime|timestamp|iso8601 → {DATETIME_SAMPLE}",
            f"- type date / format date → {DATE_SAMPLE}",
            f"- type time / format time → {TIME_SAMPLE}",
            f"- format email → {EMAIL_SAMPLE}",
            "- int/float → min if set else 1 (min=0 samples as 0 in the probe)",
            "- bool → false",
            f"- otherwise the word {GENERIC_STR_SAMPLE}",
            "",
            "Every capability's model already carries this envelope; the gate",
            "POSTs it plus samples for any extra FIELDS you declare:",
            sample_json,
        ]
    )


def schema_accept_acceptance_line() -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    from app.factory.build.writer_behaviour import SELF_CHECK_COMMAND

    return (
        f"- every capability accepts a POST built from its own FIELDS/"
        f"CONSTRAINTS ({SCHEMA_ACCEPT_GATE} baseline): every field a handler "
        "or route requires is declared in that capability's model FIELDS -- "
        "the WRITER gate and TESTER's product suites (test_smoke, test_routes, "
        "domain acceptance) all build their payload from those FIELDS; run "
        f"`{SELF_CHECK_COMMAND}` before declaring done  "
        f"[check:{SCHEMA_ACCEPT_CHECK}]"
    )


def schema_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        f"WRITER gate {SCHEMA_ACCEPT_GATE} POSTs a payload built from each "
        "capability's own FIELDS + CONSTRAINTS (allowed_values[0], declared "
        "type/format -- never the field's name -- generic str="
        f"{GENERIC_STR_SAMPLE!r}). A field that needs a vocabulary or format "
        "must declare it. Every handler must accept "
        "that payload. Declaring a stricter contract than the spec produces "
        f"{SCHEMA_ACCEPT_HALT!r}."
    )


def probe_sample_value(
    name: str,
    *,
    annotation: str = "str",
    constraints: Optional[Mapping[str, Any]] = None,
) -> Any:
    """The value every Factory probe posts for *name* -- read from the ONE
    builder (roles_handlers._sample_value, the sampler TESTER and the probes
    share through payload_helpers.base_samples). This module keeps no rules
    of its own; ``test_schema_accept_contract`` pins the identity.
    """
    from app.factory.build.roles_handlers import _sample_value

    field: Dict[str, Any] = {"name": name, "type": str(annotation or "str")}
    field.update({k: v for k, v in dict(constraints or {}).items() if v is not None})
    return _sample_value(field)


def probe_sample_payload(
    fields: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Probe payload for a spec's field list (name / type / constraints)."""
    out: Dict[str, Any] = {}
    for field in fields or ():
        if not isinstance(field, Mapping):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        constraints = {
            k: field[k]
            for k in ("allowed_values", "min", "max", "format")
            if field.get(k) is not None
        }
        out[name] = probe_sample_value(
            name,
            annotation=str(field.get("type") or "str"),
            constraints=constraints,
        )
    return out
