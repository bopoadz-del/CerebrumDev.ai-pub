"""The coder model-call lifecycle, as a typed field on ledger NOTE payloads.

A NOTE that starts a coder call carries ``model_call=True`` and
``model_call_state=OPEN``; the NOTE that ends it -- the session finished, or
the wall killed it -- carries ``model_call_state=CLOSED``. Readers (the Floor
status, the budget inspector) decide on this field, never by searching a
NOTE's detail text for the sentence an emitter happened to write.
"""

from __future__ import annotations

from typing import Any, Mapping

MODEL_CALL_STATE = "model_call_state"
OPEN = "open"
CLOSED = "closed"


def opens_model_call(payload: Mapping[str, Any] | None) -> bool:
    return bool((payload or {}).get(MODEL_CALL_STATE) == OPEN)


def closes_model_call(payload: Mapping[str, Any] | None) -> bool:
    return bool((payload or {}).get(MODEL_CALL_STATE) == CLOSED)
