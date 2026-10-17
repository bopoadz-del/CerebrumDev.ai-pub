"""One provider ladder for the Factory's model calls.

A model call walks an ordered list of providers (rungs) read from config:
the configured primary, then the cross-provider fallback leg
(``get_factory_fallback_leg`` -- OpenRouter by default, its own endpoint,
key and model, and its cost guard). A rung that answers ends the walk.

Failover is decided by the TYPED kind of the refusal, never by message text:

* ``payment_refused`` -- HTTP 402 (the account is out of credit)
* ``rate_limited``    -- HTTP 429
* ``unavailable``     -- HTTP 5xx, timeouts, connection failures

Those three move to the next rung. ``bad_request`` (any other 4xx) does not:
the request itself is wrong, and another vendor would be asked the same wrong
thing. When the ladder is exhausted -- or no rung is configured at all --
:class:`ModelUnavailable` carries the last kind, so the Floor can say WHY
("the provider refused payment"), never "not configured" when it is.

Every failover is logged and handed to the active failover sink; inside a
build the architect module points the sink at the build ledger.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

PAYMENT_REFUSED = "payment_refused"
RATE_LIMITED = "rate_limited"
UNAVAILABLE = "unavailable"
BAD_REQUEST = "bad_request"
NOT_CONFIGURED = "not_configured"

#: Kinds that move the call to the next rung.
FAILOVER_KINDS = frozenset({PAYMENT_REFUSED, RATE_LIMITED, UNAVAILABLE})

FailoverSink = Callable[[Dict[str, Any]], None]
_SINK: contextvars.ContextVar[Optional[FailoverSink]] = contextvars.ContextVar(
    "model_ladder_failover_sink", default=None
)


class ModelUnavailable(RuntimeError):
    """No rung answered. ``kind`` is the last rung's typed refusal."""

    def __init__(self, kind: str, message: str, attempts: Sequence[Mapping[str, Any]] = ()):
        super().__init__(message)
        self.kind = kind
        self.attempts = [dict(a) for a in attempts]


@dataclass
class Rung:
    """One provider: its own endpoint, key and model -- never borrowed."""

    provider: str
    base_url: str
    api_key: str
    model: str
    fallback_model: str = ""
    temperature: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


def classify(exc: BaseException) -> Tuple[Optional[str], Optional[int]]:
    """``(kind, status)`` for a provider refusal, or ``(None, None)`` when the
    exception is not the provider refusing (e.g. the model's JSON was bad)."""
    if isinstance(exc, ModelUnavailable):
        return exc.kind, None
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else None
        if status == 402:
            return PAYMENT_REFUSED, status
        if status == 429:
            return RATE_LIMITED, status
        if status is not None and status >= 500:
            return UNAVAILABLE, status
        return BAD_REQUEST, status
    # The watchdog (llm_watchdog.post_with_deadline) raises httpx.ReadTimeout.
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError)):
        return UNAVAILABLE, None
    return None, None


@contextlib.contextmanager
def failover_to(sink: Optional[FailoverSink]) -> Iterator[None]:
    """Route failover events raised inside the block to ``sink``."""
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def _report(event: Dict[str, Any]) -> None:
    logger.warning(
        "model provider failover: %s answered %s (status %s); trying %s",
        event.get("provider"),
        event.get("kind"),
        event.get("status"),
        event.get("next_provider"),
    )
    sink = _SINK.get()
    if sink is None:
        return
    try:
        sink(event)
    except Exception:  # noqa: BLE001 -- a ledger hiccup must not fail the call
        logger.exception("failover sink raised")


def _usable(cfg: Optional[Mapping[str, Any]]) -> bool:
    return bool(
        cfg
        and not cfg.get("error")
        and not cfg.get("mock")
        and cfg.get("api_key")
        and cfg.get("base_url")
    )


def rungs_from_config(primary: Optional[Mapping[str, Any]]) -> List[Rung]:
    """The ordered ladder: the configured primary, then the fallback leg.

    Each rung carries the base_url its own config resolved -- never a default
    borrowed from another vendor. An armed-but-misconfigured fallback leg is
    skipped with its reason logged, not an error.
    """
    from app.core.llm_config import _host_of, get_factory_fallback_leg

    rungs: List[Rung] = []
    if _usable(primary):
        rungs.append(
            Rung(
                provider=str(primary.get("provider") or ""),
                base_url=str(primary["base_url"]),
                api_key=str(primary["api_key"]),
                model=str(primary.get("model") or ""),
                fallback_model=str(primary.get("fallback_model") or ""),
                temperature=primary.get("temperature"),
            )
        )
    leg = get_factory_fallback_leg()
    if leg and leg.get("error"):
        logger.warning("model fallback rung skipped: %s", leg["error"])
    elif leg and _usable(leg):
        if not rungs or _host_of(str(leg["base_url"])) != _host_of(rungs[0].base_url):
            rungs.append(
                Rung(
                    provider=str(leg.get("provider") or ""),
                    base_url=str(leg["base_url"]),
                    api_key=str(leg["api_key"]),
                    model=str(leg.get("model") or ""),
                    temperature=leg.get("temperature"),
                    extra={"is_free": bool(leg.get("is_free"))},
                )
            )
    return rungs


def run_ladder(rungs: Sequence[Rung], attempt: Callable[[Rung], Any]) -> Any:
    """Call ``attempt(rung)`` down the ladder; return the first answer.

    A refusal of a failover kind moves to the next rung (and is reported);
    any other refusal stops the walk as a typed :class:`ModelUnavailable`.
    An exception that is not a provider refusal propagates unchanged.
    """
    if not rungs:
        raise ModelUnavailable(NOT_CONFIGURED, "No LLM provider configured")
    attempts: List[Dict[str, Any]] = []
    for i, rung in enumerate(rungs):
        try:
            return attempt(rung)
        except Exception as exc:  # noqa: BLE001 -- classified below
            kind, status = classify(exc)
            if kind is None:
                raise
            record = {"provider": rung.provider, "model": rung.model, "kind": kind, "status": status}
            attempts.append(record)
            nxt = rungs[i + 1] if i + 1 < len(rungs) else None
            if kind in FAILOVER_KINDS and nxt is not None:
                _report({**record, "next_provider": nxt.provider, "next_model": nxt.model})
                continue
            raise ModelUnavailable(
                kind,
                f"model provider {rung.provider or '?'} refused ({kind}"
                + (f", HTTP {status}" if status else "")
                + ")",
                attempts,
            ) from exc
    raise ModelUnavailable(UNAVAILABLE, "model ladder exhausted", attempts)  # pragma: no cover


#: What the Floor says when a CONFIGURED provider refused, by kind.
REFUSAL_PHRASES = {
    PAYMENT_REFUSED: "the model provider refused payment (the account is out of credit)",
    RATE_LIMITED: "the model provider is rate-limiting requests",
    UNAVAILABLE: "the model provider is unavailable",
    BAD_REQUEST: "the model provider rejected the request",
}


def floor_unavailable_reason(kind: Optional[str]) -> str:
    """Why the Floor chat model did not answer, by typed kind.

    ``not configured`` is said only when no provider is configured at all.
    """
    if kind in REFUSAL_PHRASES:
        return REFUSAL_PHRASES[kind]
    return "the Floor chat model is not configured on this deployment"
