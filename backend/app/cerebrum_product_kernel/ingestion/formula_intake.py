"""Client submission of an L3/L4 overlay definition. PLACEHOLDER, no logic.

SCOPE
-----
``cerebrum_product_kernel/formulas/__init__.py`` already implements the real
extend/override precedence contract and already ships into every generated
product. What does NOT exist is a way for a CLIENT to submit an overlay
definition to their OWN deployed platform at runtime — today the only path
is ``scripts/intake_formulas.py`` equivalent tooling, a developer-run CLI
inside the Factory's own repo (the exact gap identified 2026-09-16: The
Fork's ``scripts/intake_formulas.py`` is the same shape — a dev CLI, not a
client-facing endpoint).

CONTRACT — must match cerebrum_product_kernel/formulas/__init__.py exactly.
An overlay definition dict requires:
    "id"                 str, collides with a base id only if "overrides"
                          names that exact base address
                          ("universal:<id>_v<definition_version>")
    "definition_version"  the version component of the address
    "overrides"           optional str, the exact base address this
                          replaces (omit for a pure "extend")
    "provenance"          required when overriding — where this number/rule
                          came from
    "reason"              required when overriding — why it replaces the
                          base value

An overlay whose id collides with a base id but declares no "overrides" is
an ERROR (PrecedenceError), never a silent replacement. Do not soften that
in this module — the refusal is the point.

WHAT THIS MODULE MUST DECIDE (deferred to whoever implements it, not
decided here — these are open design questions, not oversights):
  1. Where does a submitted overlay PERSIST between submission and
     resolve_definitions() being called? formulas/__init__.py reads from
     ``universal_definitions.json`` plus an overlay file passed by the
     caller — there is currently no per-tenant overlay STORE. This needs
     one (likely per-tenant, alongside the per-tenant Chroma collection in
     retrieval_engine.py — same isolation boundary, do not invent a second
     one).
  2. Does a client submit raw JSON, or does an LLM assist turning a
     described business rule into the ``{id, definition_version, overrides,
     provenance, reason}`` shape? If the latter, that draft step must never
     bypass the same validation resolve_definitions() already enforces —
     LLM-assisted drafting, code-enforced validation, same discipline as
     the Factory's own Architect draft / deterministic-template split.
  3. Audit trail: precedence.py already logs divergence records when an L3
     formula shadows an L1 certified one. A client-submitted overlay should
     produce that same record on first resolution, not a separate log.

Every function below is a stub.
"""

from __future__ import annotations

from typing import Any, Dict


def submit_overlay(tenant_id: str, overlay: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and (eventually) persist one client overlay definition.

    PLACEHOLDER — not implemented. Intended to call
    ``cerebrum_product_kernel.formulas.resolve_definitions`` (or an
    equivalent single-definition validation path) so a malformed or
    silently-colliding overlay is refused with the SAME PrecedenceError the
    kernel already raises — never a bespoke validation path.
    """
    raise NotImplementedError(
        "submit_overlay is a Phase 2 scaffold placeholder — see "
        "cerebrum_product_kernel/ingestion/__init__.py"
    )


def list_overlays(tenant_id: str) -> Dict[str, Any]:
    """PLACEHOLDER — not implemented. See module docstring, open question 1."""
    raise NotImplementedError(
        "list_overlays is a Phase 2 scaffold placeholder — see "
        "cerebrum_product_kernel/ingestion/__init__.py"
    )
