"""LLM coder for GENERATE-strategy capabilities.

The generator composes REUSE capabilities from prebuilt blocks without any
LLM. GENERATE capabilities are the opposite promise — logic no block
provides — and until now they shipped as honest ``dependency_required``
stubs. This module writes the missing handler bodies with the factory LLM
(same provider config as the Product Architect).

Failure policy — decided after the 2026-08-02 field test where dead LLM
credit made every path silently degrade:

* The coder RAISES ``CoderError`` on any LLM failure. It never fabricates.
* The generator catches it, ships the honest stub instead, and records the
  failure in the generation result so the chat/UI can say "N capabilities
  could not be coder-written (reason)" — degraded output is acceptable,
  invisible degradation is not.
* ``FACTORY_CODER_ENABLED=0`` skips the LLM entirely (stubs, recorded as
  "coder disabled").

Emitted code is stamped into the export with provenance (model, capability)
and wrapped by the kernel handler in try/except — a broken generated body
answers ``execution_error``, it does not crash the product.
"""

from __future__ import annotations

import ast
import contextlib
import contextvars
import json
import logging
import os
import threading
import time as _wall_time
from typing import Any, Dict, List, Optional, Sequence

import httpx

from .blueprint import CapabilitySpec, ProductBlueprint
from .build.authority import kernel_seat_brief
from .build.block_inputs import sanitize_python_identifier
from .build.writer_brief import writer_system_brief
from .code_cli import (  # noqa: F401 — re-export the public FACTORY_CODE_CLI seam
    CODE_CLI_ENV,
    CODE_PROVIDER_ENV,
    DEEPSEEK_API_KEY_ENV,
    DEFAULT_DEEPSEEK_CLI,
    DEFAULT_DEEPSEEK_MODEL,
    LEGACY_CODE_CLI_ENV,
    ClaudePrintPromptEmpty,
    KimiPromptEmpty,
    claude_print_argv,
    claude_print_log_argv,
    claude_print_prompt,
    code_cli_command,
    kimi_prompt_argv,
    kimi_prompt_log_argv,
    kimi_prompt_text,
    deepseek_api_key,
    deepseek_cli_environ,
    deepseek_coder_selected,
    deepseek_code_model,
    factory_code_provider,
    is_claude_code_cli,
    is_kimi_code_cli,
)
from .llm_watchdog import (
    attempt_wall_s,
    call_timeout_s,
    is_timeout_error,
    post_with_deadline,
)

#: Shared wall for one calling-NOTE (fallback legs + validation retry).
#: A kwarg on ``_llm_code_call`` would break the many test stubs that
#: only accept ``messages``.
_attempt_deadline: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "factory_coder_attempt_deadline", default=None
)

#: Ceiling on completions in flight against the provider at one time.
#:
#: The WRITER declares its fan-out here before opening a pool, so the number
#: of simultaneous requests is bounded by the client and not only by whoever
#: happens to be calling. This is a gate on *concurrency*, deliberately not on
#: retry: a 429 is retried only on a zero-cost leg (see ``_attempt`` below,
#: gated on ``leg["is_free"]``), and the way to keep that policy affordable is
#: not to create the burst in the first place. ``None`` -- the default -- is
#: unbounded, which is the same thing as serial for a single-threaded caller.
_inflight: Optional[threading.BoundedSemaphore] = None
_inflight_lock = threading.Lock()


def set_inflight_limit(limit: Optional[int]) -> None:
    """Bound concurrent completions. ``None`` removes the bound."""
    global _inflight
    with _inflight_lock:
        _inflight = (
            threading.BoundedSemaphore(limit) if limit and limit > 1 else None
        )


@contextlib.contextmanager
def _inflight_slot():
    """Hold a slot for one completion, if a limit is set."""
    sem = _inflight
    if sem is None:
        yield
        return
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


logger = logging.getLogger("cerebrumdev.factory.coder")

CODER_ENABLED_ENV = "FACTORY_CODER_ENABLED"

#: Provider-agnostic name for the agentic coding CLI. ``KIMI_CODE_CLI`` stays
#: honoured so existing deployments keep working unchanged; point
#: FACTORY_CODE_CLI at Kimi Code (DeepSeek V4 Pro OpenAI-compat, or
#: historical Moonshot). Claude Code is not the DeepSeek vehicle.
#: The seam is "run this command, read its result". Selection + DeepSeek
#: subprocess env live in ``app.factory.code_cli`` (re-exported above).


class CoderError(RuntimeError):
    """The coder LLM could not produce a handler. Never fabricate instead."""


class CoderTimeout(CoderError):
    """The coder LLM exceeded the wall-clock watchdog. Never hang the WRITER."""


def coder_enabled() -> bool:
    return os.getenv(CODER_ENABLED_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


CODER_BUDGET_ENV = "FACTORY_CODER_BUDGET_S"
#: Template-generator loop across ALL GENERATE capabilities. Default is
#: stage 1 (~30 min), not a silent 2h burn. Leftover 300s still remaps
#: up so it cannot stub remaining caps after five minutes.
DEFAULT_CODER_BUDGET_S = 1800.0
#: Last-resort ceiling. Never the default; honour an explicit leftover.
CODER_BUDGET_CEILING_S = 7200.0
#: Live leftover ``FACTORY_CODER_BUDGET_S=300`` (and the same "hold the
#: worker" band) must not keep stubbing a multi-capability pilot.
#: ``0`` still disables. Sub-minute values stay honoured for tests.
LEGACY_CODER_BUDGET_MIN_S = 60.0
LEGACY_CODER_BUDGET_MAX_S = 600.0


def coder_budget_s() -> float:
    """Total wall-clock the coder may spend across one generation (seconds).

    The template generator still runs the GENERATE loop in-request.
    Production Floor uses the role runner (staged 30 min → inspect → 45 min).
    Leftover 300s remaps to stage 1, not a silent 2h. Explicit 7200 is
    honoured (do not slash an in-flight leftover). ``0`` disables it.
    """
    raw = os.getenv(CODER_BUDGET_ENV, "").strip()
    if not raw:
        return DEFAULT_CODER_BUDGET_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CODER_BUDGET_S
    if value == 0:
        return 0.0
    if LEGACY_CODER_BUDGET_MIN_S <= value <= LEGACY_CODER_BUDGET_MAX_S:
        return DEFAULT_CODER_BUDGET_S
    return max(0.0, value)


_SYSTEM = """You write ONE Python function body for a generated business platform.

Contract:
- You are given a capability description and the platform blueprint summary.
- Emit ONLY Python code for the body of:
    async def generated_logic(context: dict, arguments: dict) -> dict
- The body must return a JSON-serialisable dict describing the outcome of the
  requested operation. Use arguments.get("action") to dispatch operations the
  capability implies (list/create/update style), and keep state in the
  module-level ``_STATE`` dict (an in-memory store the wrapper provides).
- Standard library only. No imports of third-party packages. No network, no
  filesystem, no subprocess, no eval/exec.
- No markdown fences, no surrounding function definition, no commentary —
  raw indented statements only (they will be placed inside the function).
- Validate inputs and return {"ok": False, "error": "..."} dicts for bad
  requests rather than raising.
"""

# Dangerous stdlib modules the generated body may not import (directly or via
# ``from X import ...`` / ``import X.Y`` / ``import X as Z``). Safe stdlib
# (math, json, datetime, re, ...) is allowed.
_FORBIDDEN_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "importlib", "ctypes",
    "pickle", "marshal", "builtins", "multiprocessing", "threading", "pty",
    "posix", "nt", "resource", "mmap", "fcntl", "signal", "code", "codeop",
    "gc", "inspect", "platform", "pathlib", "tempfile", "glob", "urllib",
    "http", "ftplib", "smtplib", "asyncio",
})
# Builtins that read/write the host or escape the sandbox.
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "open", "__import__", "compile", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "input", "breakpoint",
    "__builtins__", "memoryview", "exit", "quit",
})
# Dunder attributes used for classic sandbox escapes
# (``().__class__.__bases__[0].__subclasses__()`` and friends).
_FORBIDDEN_ATTRS = frozenset({
    "__globals__", "__subclasses__", "__bases__", "__mro__", "__class__",
    "__code__", "__builtins__", "__dict__", "__import__", "__getattribute__",
    "__reduce__", "__reduce_ex__", "__base__", "__closure__",
})


class _ForbiddenNodeVisitor(ast.NodeVisitor):
    """Collect sandbox-escape constructs. AST-based, so it is not fooled by
    ``from os import environ``, ``import  os`` (odd spacing), aliasing, or
    ``getattr(__builtins__, "o"+"pen")`` that a substring scan misses."""

    def __init__(self) -> None:
        self.found: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in _FORBIDDEN_MODULES:
                self.found.append(f"import {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if root in _FORBIDDEN_MODULES:
            self.found.append(f"from {node.module} import ...")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            self.found.append(f"name {node.id!r}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FORBIDDEN_ATTRS:
            self.found.append(f"attribute {node.attr!r}")
        self.generic_visit(node)


#: Anthropic pins its API by date rather than by URL path.
ANTHROPIC_VERSION = "2023-06-01"

#: Completion budget for one artifact. 2048 was too tight for reasoning
#: models: they spend the budget on reasoning tokens and return
#: finish_reason="length" with empty content, which surfaced as a
#: CoderError and silently dropped that artifact to the template path
#: (observed once per five routes on a live kimi-k2.7-code build).
#: Override with FACTORY_CODER_MAX_TOKENS.
def _call_timeout_s() -> float:
    """Per-attempt ceiling for one coder HTTP request.

    Reasoning models legitimately take many minutes to emit a handler.
    An idle-only httpx timeout is not a deadline: a streaming model
    never trips it. ``llm_watchdog`` enforces a wall-clock abort.
    Override with FACTORY_CODER_TIMEOUT_S (legacy 120/150 is remapped).
    """
    return call_timeout_s()


def _attempt_wall_s() -> float:
    """One handler/model-spec call including alternate-model retries."""
    return attempt_wall_s()


def code_max_tokens() -> int:
    try:
        return max(256, int(os.getenv("FACTORY_CODER_MAX_TOKENS", "8192")))
    except ValueError:
        return 8192


MAX_CODE_TOKENS = code_max_tokens()


def _anthropic_request(cfg: Dict[str, Any], messages: List[Dict[str, str]], model: str):
    """Build the Messages API call. Deliberately not OpenAI-shaped.

    Three differences that silently break an OpenAI-style port:
    auth is ``x-api-key`` rather than a bearer token, the ``anthropic-version``
    header is mandatory, and the system prompt is a **top-level parameter** --
    a ``{"role": "system"}`` entry inside ``messages`` is rejected. The reply
    is a list of typed content blocks, not ``choices[0].message.content``.
    """
    system = "\n\n".join(
        m["content"] for m in messages if m.get("role") == "system"
    ).strip()
    turns = [m for m in messages if m.get("role") != "system"]

    headers = {
        "content-type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
    }
    if cfg.get("api_key"):
        headers["x-api-key"] = cfg["api_key"]

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": code_max_tokens(),
        "messages": turns,
    }
    temperature = cfg.get("temperature")
    if temperature is not None:
        payload["temperature"] = temperature
    if system:
        payload["system"] = system

    url = f"{cfg['base_url'].rstrip('/')}/messages"
    return url, payload, headers


def _anthropic_text(data: Dict[str, Any]) -> str:
    """Concatenate the text blocks of a Messages response.

    Content is a list of typed blocks; non-text blocks are skipped rather than
    stringified, so a future block type cannot leak JSON into generated code.
    """
    blocks = data.get("content") or []
    if not isinstance(blocks, list):
        raise ValueError("malformed Anthropic response: content is not a list")
    text = "".join(
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )
    if not text.strip():
        raise ValueError("empty completion")
    return text


def _llm_code_call(messages: List[Dict[str, str]]) -> tuple[str, str]:
    """Text completion against the factory LLM.

    Returns ``(text, model_used)`` -- the model that ACTUALLY answered, not
    the one that was configured. Those differ whenever a fallback leg runs,
    and the difference is stamped into every emitted module as CODER_MODEL.
    Before this returned two values, a handler written by moonshot-v1-8k was
    stamped with the primary's name: provenance that names the wrong author
    is worse than none, because it is trusted.

    The attempt wall is a contextvar so fallback legs (and the validation
    retry) share one ``attempt_wall_s()`` deadline. Without that, stacked
    legs plus a second ``_llm_code_call`` can outrun the Floor's calling-NOTE
    wall while one model_call NOTE is in flight.

    Raises CoderError on failure.
    """
    token = None
    if _attempt_deadline.get() is None:
        token = _attempt_deadline.set(_wall_time.monotonic() + _attempt_wall_s())
    try:
        # One slot per completion, held across the fallback legs and their
        # 429 waits, so a capability that falls through two legs occupies one
        # slot rather than two. Retry policy itself is untouched.
        with _inflight_slot():
            return _llm_code_call_impl(messages)
    finally:
        if token is not None:
            _attempt_deadline.reset(token)


def _llm_code_call_impl(messages: List[Dict[str, str]]) -> tuple[str, str]:
    deadline_mono = _attempt_deadline.get()
    if deadline_mono is None:
        deadline_mono = _wall_time.monotonic() + _attempt_wall_s()
    from app.core.llm_config import get_factory_fallback_leg

    from .product_architect import get_factory_llm_config

    cfg = get_factory_llm_config()
    if cfg.get("mock"):
        raise CoderError("LLM mock mode — coder has no model to call")

    cross = get_factory_fallback_leg()
    if cross and cross.get("error"):
        logger.warning("cross-provider fallback not armed: %s", cross["error"])
        cross = None

    #: False when the primary has no usable credentials. The leg then runs
    #: alone rather than the coder refusing outright -- see below.
    primary_usable = not cfg.get("error")

    if cfg.get("error"):
        # Fail closed with the provider's own message; never borrow the other
        # provider's credentials to keep going.
        #
        # One narrow exception: nobody asked for a provider BY NAME, none is
        # configured, and a zero-cost leg is armed. Refusing there means every
        # artifact takes the template path on a deployment that has a working
        # free model sitting right beside it -- which is precisely the Render
        # service in KNOWN_INCOMPLETE item 2, where no LLM key is set and the
        # coder therefore cannot run at all.
        #
        # Both halves of the original rule survive. An explicit LLM_PROVIDER
        # whose key is missing still raises: a request that cannot be honoured
        # is an error, not an invitation to substitute. And a PAID leg still
        # raises, because running one unasked is the cost surprise the rule
        # exists to prevent.
        explicit = os.getenv("LLM_PROVIDER", "").strip()
        if explicit or not (cross and cross.get("is_free")):
            raise CoderError(str(cfg["error"]))
        logger.warning(
            "no factory LLM credentials (%s); running on the zero-cost "
            "fallback leg %s alone",
            cfg["error"],
            cross["model"],
        )

    provider = cfg.get("provider")
    if primary_usable and provider not in (
        "moonshot",
        "kimi",
        "claude",
        "cursor",
        "openrouter",
    ):
        raise CoderError(f"unsupported coder provider: {provider!r}")

    #: A leg is a whole endpoint -- provider, base_url, key -- not just a
    #: model name. ``fallback_model`` only ever swapped the name while reusing
    #: the primary's endpoint, which is why it could never cross vendors.
    primary_leg: Dict[str, Any] = {
        "provider": provider,
        "base_url": cfg.get("base_url", ""),
        "api_key": cfg.get("api_key", ""),
        "temperature": cfg.get("temperature"),
    }

    def _try(leg: Dict[str, Any], model: str) -> str:
        if leg.get("provider") == "claude":
            url, payload, headers = _anthropic_request(leg, messages, model)
            resp = post_with_deadline(
                url,
                json=payload,
                headers=headers,
                timeout=_call_timeout_s(),
                post=httpx.post,
                deadline_mono=deadline_mono,
            )
            resp.raise_for_status()
            return _anthropic_text(resp.json())

        headers = {"Content-Type": "application/json"}
        if leg.get("api_key"):
            headers["Authorization"] = f"Bearer {leg['api_key']}"
        if leg.get("provider") == "openrouter":
            # Attribution headers; optional to OpenRouter, useful on the
            # dashboard for telling factory traffic from everything else.
            headers["HTTP-Referer"] = "https://cerebrumdev.ai"
            headers["X-Title"] = "CerebrumDev Factory"
        url = f"{leg['base_url'].rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": code_max_tokens(),
        }
        # Send a temperature ONLY when one was configured. This hardcoded 0.2
        # made the coder unusable on every reasoning model: kimi-k2.x and k3
        # answer 400 "invalid temperature: only 1 is allowed for this model",
        # so the factory silently fell back to templates on exactly the models
        # capable enough to write working code. llm_config._llm_temperature()
        # already returns None by default for this reason; the coder was the
        # one caller ignoring it.
        temperature = leg.get("temperature")
        if temperature is not None:
            payload["temperature"] = temperature
        resp = post_with_deadline(
            url,
            json=payload,
            headers=headers,
            timeout=_call_timeout_s(),
            post=httpx.post,
            deadline_mono=deadline_mono,
        )
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"].get("content")
        if not content or not content.strip():
            # Say WHY it was empty. A bare "empty completion" costs a live
            # debugging run to interpret; finish_reason and usage identify the
            # cause for free the next time it happens. On a reasoning model the
            # usual answer is finish_reason="length" with the budget spent on
            # reasoning tokens before any content was emitted.
            usage = data.get("usage") or {}
            reasoning = choice["message"].get("reasoning_content") or ""
            raise ValueError(
                "empty completion from "
                + str(model)
                + f" (finish_reason={choice.get('finish_reason')!r}, "
                + f"max_tokens={MAX_CODE_TOKENS}, "
                + f"completion_tokens={usage.get('completion_tokens')}, "
                + f"reasoning_chars={len(reasoning)})"
            )
        return content

    def _try_with_connect_retries(leg: Dict[str, Any], model: str) -> str:
        """Transient transport failures get bounded retries with backoff.

        Two live campaign runs were lost to intermittent DNS: a single
        ``getaddrinfo failed`` at the wrong moment permanently degraded an
        artifact to the template path and the whole build failed honestly on
        it hours later. Connection-class errors retry -- an HTTP status or an
        empty completion is the model answering, and retrying those would
        just spend money on the same answer.

        One status does retry: a 429 on a zero-cost leg (``is_free``).
        That is a refusal to answer YET rather than an answer, it costs
        nothing to wait out, and the server states how long.
        """
        import time as _time

        last: Exception | None = None
        for attempt in range(3):
            try:
                return _try(leg, model)
            except httpx.HTTPStatusError as exc:
                # An HTTP status is normally the model ANSWERING, and retrying
                # it just buys the same answer twice -- which is why this
                # block does not exist for statuses in general. A 429 is the
                # exception: it is a refusal to answer yet, and the free
                # OpenRouter pool states how long to wait. Gated on the leg
                # being zero-cost so no paid call is ever retried into a bill.
                if not (
                    leg.get("is_free") and exc.response.status_code == 429
                ):
                    raise
                last = exc
                if attempt < 2:
                    _time.sleep(_retry_after_s(exc))
            # ReadTimeout / watchdog is deliberately NOT retried on the SAME
            # model here. The caller tries an alternate configured model
            # once; looping the free-tier slug just spends the same wait
            # again. Connection failures are the genuinely transient case.
            except (httpx.ConnectError, httpx.ConnectTimeout,
                    httpx.RemoteProtocolError) as exc:
                last = exc
                if attempt < 2:
                    _time.sleep(2 * (attempt + 1))
        raise last

    def _timeout_or_fail(
        message: str, cause: BaseException, timed_out: bool
    ) -> None:
        if timed_out:
            raise CoderTimeout(f"coder LLM timed out: {message}") from cause
        raise CoderError(f"coder LLM failed: {message}") from cause

    if not primary_usable:
        # The leg is the only thing there is. No primary to fall back FROM, so
        # a failure here is simply the failure -- reported with the leg named,
        # not dressed up as a fallback of something that never ran.
        try:
            return _try_with_connect_retries(cross, cross["model"]), cross["model"]
        except Exception as only:  # noqa: BLE001
            if is_timeout_error(only):
                try:
                    return (
                        _try_with_connect_retries(cross, cross["model"]),
                        cross["model"],
                    )
                except Exception as retry:  # noqa: BLE001
                    _timeout_or_fail(
                        f"{cross['model']} (the only configured leg; no "
                        f"primary credentials) after retry "
                        f"({type(only).__name__}: {only}; "
                        f"{type(retry).__name__}: {retry})",
                        retry,
                        is_timeout_error(retry),
                    )
            raise CoderError(
                f"coder LLM failed on {cross['model']} (the only configured "
                f"leg; no primary credentials): "
                f"{type(only).__name__}: {only}"
            ) from only

    try:
        return _try_with_connect_retries(primary_leg, cfg["model"]), cfg["model"]
    except Exception as first:  # noqa: BLE001 — fallback legs, then honest failure
        legs: List[tuple[Dict[str, Any], str]] = []

        # Leg 1: same endpoint, different model. The original fallback.
        same_endpoint = cfg.get("fallback_model")
        if same_endpoint and same_endpoint != cfg["model"]:
            legs.append((primary_leg, same_endpoint))

        # Leg 2: a different vendor entirely, reached only when the primary
        # vendor's own fallback also failed. That ordering matters: an
        # out-of-credit or rate-limited Moonshot account fails BOTH its models
        # identically, so a same-vendor fallback is no fallback at all for the
        # failure that actually happens most.
        if cross:
            legs.append((cross, cross["model"]))

        if not legs:
            # No alternate model configured: retry the same call once, then
            # fail closed. Do not loop the free-tier slug forever.
            if is_timeout_error(first):
                try:
                    return (
                        _try_with_connect_retries(primary_leg, cfg["model"]),
                        cfg["model"],
                    )
                except Exception as retry:  # noqa: BLE001
                    _timeout_or_fail(
                        f"{cfg['model']} after retry "
                        f"({type(first).__name__}: {first}; "
                        f"{type(retry).__name__}: {retry})",
                        retry,
                        is_timeout_error(retry),
                    )
            raise CoderError(
                f"coder LLM failed: {type(first).__name__}: {first}"
            ) from first

        # Carry every leg's MESSAGE, not just the class names. This used to
        # read "failed on primary (ValueError) and fallback
        # (HTTPStatusError)", which is unactionable: it cost a live debugging
        # run to discover the ValueError was an empty completion from a
        # starved token budget. coder_failures is often the only record
        # anyone sees, so it has to carry the reason.
        reasons = [f"primary {cfg['model']} ({type(first).__name__}: {first})"]
        timed_out = is_timeout_error(first)
        for leg, model in legs:
            try:
                text = _try_with_connect_retries(leg, model)
            except Exception as exc:  # noqa: BLE001
                reasons.append(f"fallback {model} ({type(exc).__name__}: {exc})")
                timed_out = timed_out and is_timeout_error(exc)
                continue
            if leg is not primary_leg:
                logger.warning(
                    "coder fell back across providers to %s/%s after %s failed",
                    leg.get("provider"),
                    model,
                    cfg["model"],
                )
            return text, model
        joined = " and ".join(reasons)
        if timed_out:
            raise CoderTimeout("coder LLM timed out on " + joined)
        raise CoderError("coder LLM failed on " + joined)


#: Cap on a server-stated Retry-After. The coder already runs under a build
#: budget; honouring an arbitrarily long wait would hand a third party the
#: power to stall a build.
MAX_RETRY_AFTER_S = 10.0


def _retry_after_s(exc: httpx.HTTPStatusError) -> float:
    """Seconds to wait per the response's Retry-After, clamped and defaulted."""
    raw = exc.response.headers.get("Retry-After", "")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        seconds = 5.0
    return max(0.0, min(seconds, MAX_RETRY_AFTER_S))


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.rstrip().endswith("```"):
        text = text.rstrip()[: text.rstrip().rfind("```")]
    return text.strip("\n")


def _validate_body(
    body: str, capability_id: str, block_ids: Sequence[str] | None = None,
) -> str:
    """Static gate on emitted code. Reject, never repair.

    The gate parses the emitted body inside the exact wrapper it will live in
    and walks the AST for sandbox-escape constructs (dangerous imports,
    builtins, dunder attribute access). An AST walk — not a substring scan —
    is required: ``from os import environ``, aliased imports, odd spacing, and
    ``getattr(__builtins__, "o"+"pen")`` all slip past substrings.
    """
    if not body.strip():
        raise CoderError(f"coder returned an empty body for {capability_id}")
    # Must compile inside the wrapper shape it will actually live in.
    indented = "\n".join(
        ("    " + line) if line.strip() else line for line in body.splitlines()
    )
    wrapper = (
        "async def generated_logic(context, arguments):\n" + indented + "\n"
    )
    try:
        tree = compile(
            wrapper, f"<coder:{capability_id}>", "exec", flags=ast.PyCF_ONLY_AST
        )
    except SyntaxError as exc:
        raise CoderError(
            f"coder output for {capability_id} does not compile: {exc}"
        ) from exc

    visitor = _ForbiddenNodeVisitor()
    visitor.visit(tree)
    if visitor.found:
        raise CoderError(
            f"coder output for {capability_id} contains forbidden construct(s): "
            f"{', '.join(sorted(set(visitor.found)))}"
        )

    # The body must actually return from the function it lives in. Seen live:
    # the model wrapped its whole logic in a nested ``def endpoint(...)`` that
    # nothing calls, so the real function fell through to None and every route
    # answered ResponseValidationError. A return inside a nested function does
    # not count.
    outer = tree.body[0]
    todo = list(outer.body)
    has_return = False
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Return):
            has_return = True
            break
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # a nested scope's return is not this function's return
        todo.extend(ast.iter_child_nodes(node))

    if not has_return:
        raise CoderError(
            f"coder output for {capability_id} never returns from the function "
            "body (a return inside a nested def does not count) -- the caller "
            "would receive None"
        )

    # F11 coverage, checked HERE rather than at the runtime WRITER gate.
    # writer_behaviour fails the build when a declared BLOCK_IDS entry is
    # never invoked, but the coder prompt used to say "every block whose
    # output the capability needs", which licenses skipping one. Live
    # sess_6400b6c halted on exactly that: crew_dashboard (dashboard +
    # database), daily_log_management (capture + document_engine). Catching
    # it here costs one bounded retry; catching it at the gate costs the
    # whole run, after WRITER, with TESTER and STORE_MANAGER never started.
    declared = [str(b) for b in (block_ids or []) if str(b).strip()]
    if declared:
        iterates_all = any(
            isinstance(node, ast.Name) and node.id == "BLOCK_IDS"
            for node in ast.walk(tree)
        )
        if not iterates_all:
            literals = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            missing = [b for b in declared if b not in literals]
            if missing:
                raise CoderError(
                    f"coder output for {capability_id} never invokes declared "
                    f"block(s): {', '.join(sorted(missing))}. Every id in "
                    "BLOCK_IDS must be passed to execute() (iterating "
                    "BLOCK_IDS also satisfies this)."
                )

    # Live makerspace-management (sess_39b5fec2abd346a5): the coder buried
    # the operation inside the payload dict. Dispatch reads action= only;
    # every capability then failed writer_behaviour. Reject here so the
    # model gets one bounded retry instead of a halted WRITER phase.
    if _execute_payload_carries_action(tree):
        raise CoderError(
            f"coder output for {capability_id} puts 'action' inside the "
            "execute() payload. Pass the operation only as action= "
            "(e.g. execute(block_id, record, "
            "action=BLOCK_DEFAULT_ACTIONS.get(block_id))). "
            "app/dispatch.py reads the operation only from the action= "
            "keyword; a payload 'action' key is answered "
            "'Unknown action: None' or 'unknown field(s): action'."
        )
    return indented


def _dict_literal_has_action_key(node: ast.AST) -> bool:
    """True when a dict literal (or dict() call) sets an 'action' key."""
    if isinstance(node, ast.Dict):
        return any(
            isinstance(key, ast.Constant) and key.value == "action"
            for key in node.keys
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id != "dict":
            return False
        return any(kw.arg == "action" for kw in (node.keywords or []) if kw.arg)
    return False


def _execute_payload_carries_action(tree: ast.AST) -> bool:
    """True when an execute() call puts 'action' inside its payload argument."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_execute = (
            (isinstance(func, ast.Name) and func.id == "execute")
            or (isinstance(func, ast.Attribute) and func.attr == "execute")
        )
        if not is_execute:
            continue
        payload_node = node.args[1] if len(node.args) >= 2 else None
        for kw in node.keywords or []:
            if kw.arg == "payload":
                payload_node = kw.value
        if payload_node is not None and _dict_literal_has_action_key(payload_node):
            return True
    return False


def _call_validate_retry(
    messages: List[Dict[str, str]],
    capability_id: str,
    block_ids: Sequence[str] | None = None,
) -> tuple[str, str]:
    """One LLM call, statically validated; ONE bounded retry on rejection.

    The tenth live build lost a whole capability -- and with it the run --
    because a single emitted body failed the never-returns check and the
    CoderError dropped it straight to the template. The model that wrote it
    could have fixed it in seconds if told; a full rework round to rediscover
    the same thing costs ~15 calls. Still "reject, never repair": the repair
    is another model call judged by the same gate, and a second rejection
    raises exactly as before.
    """
    token = None
    if _attempt_deadline.get() is None:
        token = _attempt_deadline.set(_wall_time.monotonic() + _attempt_wall_s())
    try:
        raw, model_used = _llm_code_call(messages)
        try:
            return (
                _validate_body(_strip_fences(raw), capability_id, block_ids),
                model_used,
            )
        except CoderTimeout:
            raise
        except CoderError as exc:
            retry = messages + [
                {"role": "assistant", "content": raw},
                {
                    "role": "user",
                    "content": (
                        "Your code was rejected by a static gate:\n"
                        f"{exc}\n"
                        "Emit the corrected body now, following every rule in the "
                        "system message."
                    ),
                },
            ]
            raw, model_used = _llm_code_call(retry)
            return (
                _validate_body(_strip_fences(raw), capability_id, block_ids),
                model_used,
            )
    finally:
        if token is not None:
            _attempt_deadline.reset(token)


_PLATFORM_SYSTEM = writer_system_brief() + """

You write ONE Python function body for a generated business platform.

Contract:
- Emit ONLY the body of:  def handle(payload: dict) -> dict
- Module-level names already available to you:
    CAPABILITY_ID : str        the capability you are implementing
    BLOCK_IDS     : list[str]  vendored blocks you may call
    BLOCK_DEFAULT_ACTIONS : dict[str, str]  each block's default action
    execute(block_id: str, payload: dict, action: str | None = None,
            params: dict | None = None) -> dict
      Runs a vendored block LOCALLY, in-process. There is no network and no
      remote store; do not attempt HTTP, and do not import anything.
- Blocks are ACTION-DISPATCHED. Pass the action that fits the capability
  (the user message lists each block's contract: actions, declared inputs,
  and the input fields its schema REQUIRES). The dict you pass as `payload`
  becomes the block's input: build it so every required input field is
  present, mapped or derived from the caller's payload.
- NEVER put "action" inside the payload dict. app/dispatch.py routes
  payload keys into the block's record and reads the operation ONLY from
  the action= keyword. A payload "action" key is answered
  "Unknown action: None" or "unknown field(s): action" and the WRITER
  gate halts the whole build. Always write:
      execute(block_id, record, action=BLOCK_DEFAULT_ACTIONS.get(block_id))
  or another action= keyword that matches the block's contract.
- The caller knows NOTHING about blocks. NEVER require a block-specific
  field (like "steps", "channel", "topic", "sql", "table", file paths, or
  team_id) from the caller's payload -- CONSTRUCT it inside the handler
  from the domain data the capability does have. The fail-closed execute
  wrapper also runs prepare_block_input for event_bus (topic), database
  (table/sql), document_engine (a real pdf/docx/xlsx path), team (the
  minted team_id from app.preconditions), notification, workflow, and
  analytics. Pass the domain record through execute(); do not invent a
  second contract the spec cannot express. Validate only the capability's
  own fields.
- The platform runs OFFLINE and its suite BLOCKS outbound network. Never
  choose webhook URLs, SMTP/email, or Slack. For notification, the Store
  only accepts known channels: set channel to "mcp" and pass block=<a
  vendored block id from the roster> (tool with the same id). Do NOT set
  channel to "in_process" — the Store answers "Unknown channel: in_process".
- execute() never raises for a block-level failure; it returns an envelope.
  Treat result.get("status") == "error" (or an "error" key in the result)
  as a failure: surface it in your return value as {"ok": False,
  "error": ...} rather than pretending success.
- Call execute() for EVERY id in BLOCK_IDS. Not "the ones you need" --
  every one. A declared block that is never invoked fails the WRITER
  behaviour gate (F11) and halts the whole build, because declaring a
  block the capability never calls is a false claim about what the
  capability does. If a block seems unnecessary, still invoke it and
  fold its result into the response; do not silently drop it.
  Iterating `for block_id in BLOCK_IDS:` is the simplest way to be sure.
- Return a JSON-serialisable dict. Include "capability": CAPABILITY_ID.
- Standard library only, and no import statements at all. No network, no
  filesystem, no subprocess, no eval/exec.
- Validate inputs and return {"ok": False, "error": "..."} for bad requests
  rather than raising.
- Do NOT define a nested function and put your logic inside it — the
  enclosing function would fall through and return None. Return directly
  from the statements you write.
- No markdown fences, no def line, no commentary — statements only, written at
  column zero. They are indented into the function for you; do not add leading
  indentation yourself or the body will not compile.
"""


_SPEC_SYSTEM = writer_system_brief() + """

You design the data model for one capability of a business platform.

Return ONLY a JSON object, no prose and no markdown fences:
{
  "entity": "<snake_case entity name, singular>",
  "fields": [
    {"name": "<snake_case>", "type": "str|int|float|bool", "required": true,
     "allowed_values": ["<one of a fixed set>", "..."],
     "min": <number>, "max": <number>}
  ]
}

Rules:
- 3 to 8 fields, ordered with identifiers first.
- Types are limited to str, int, float, bool. No nested objects, no lists.
- Do NOT include an "id" field; the platform adds its own primary key.
- Field names must be valid Python identifiers and must not shadow builtins.

Constraints are OPTIONAL, and they are the ONLY place a value restriction may
be expressed:
- "allowed_values": for a str field with a fixed vocabulary (a status, a
  severity, a category). 2 to 8 entries. Declare it whenever the field really
  does have a fixed set -- the platform enforces it and tests against it.
- "min" / "max": inclusive bounds for an int or float field.
Omit a constraint you do not mean. Anything you do NOT declare here cannot be
enforced later, so a restriction that matters must appear in this spec.
"""

_ALLOWED_FIELD_TYPES = {"str", "int", "float", "bool"}
#: LLM/SQL spellings that are ISO-text columns, not dropped fields.
#: Stored as str with a format hint so the probe/emitter sample a real time.
_TEMPORAL_TYPE_ALIASES = {
    "datetime": "datetime",
    "date": "date",
    "time": "time",
    "timestamp": "datetime",
}
#: SQL / verbose spellings the model often emits instead of str|int|float|bool.
_SQL_TYPE_ALIASES = {
    "text": "str",
    "string": "str",
    "integer": "int",
    "boolean": "bool",
    "real": "float",
}
#: Guard rails on agent-declared vocabularies. A field with one allowed value
#: is a constant, not an enum; an unbounded list is usually a hallucinated
#: free-text field.
_MIN_ALLOWED_VALUES = 2
_MAX_ALLOWED_VALUES = 8


def _clean_constraints(item: Dict[str, Any], ftype: str) -> Dict[str, Any]:
    """Validate the optional constraints on one field spec.

    Silently drops anything malformed rather than trusting it: a constraint
    the platform cannot enforce is worse than none, because the route would
    reject payloads the tests are entitled to send.
    """
    out: Dict[str, Any] = {}

    raw_allowed = item.get("allowed_values")
    if ftype == "str" and isinstance(raw_allowed, list):
        values, seen = [], set()
        for v in raw_allowed:
            if not isinstance(v, str):
                continue
            v = v.strip()
            if v and v not in seen:
                seen.add(v)
                values.append(v)
        if _MIN_ALLOWED_VALUES <= len(values) <= _MAX_ALLOWED_VALUES:
            out["allowed_values"] = values

    if ftype in ("int", "float"):
        lo, hi = item.get("min"), item.get("max")
        cast = int if ftype == "int" else float
        try:
            lo = cast(lo) if isinstance(lo, (int, float)) and not isinstance(lo, bool) else None
            hi = cast(hi) if isinstance(hi, (int, float)) and not isinstance(hi, bool) else None
        except (TypeError, ValueError):
            lo = hi = None
        # An inverted range would make every value invalid.
        if lo is not None and hi is not None and lo > hi:
            lo = hi = None
        if lo is not None:
            out["min"] = lo
        if hi is not None:
            out["max"] = hi

    return out


def generate_model_spec(
    *, capability_id: str, description: str, product_name: str, vertical: str
) -> Dict[str, Any]:
    """Ask the coder to *design* a schema, returned as validated JSON.

    Structured artifacts go through a spec rather than raw Python. The model
    is the one thing every other artifact is derived from -- persistence
    columns, route payloads, tests -- so a hallucinated import or a subtly
    broken class definition would propagate into four files instead of one.
    Asking for JSON and rendering it deterministically keeps the *design*
    with the agent and the *structure* guaranteed.
    """
    import json as _json

    user = (
        f"Platform: {product_name} (vertical: {vertical})\n"
        f"Capability id: {capability_id}\n"
        f"Capability description: {description}\n\n"
        "Design the entity this capability stores. Return the JSON now."
    )
    raw, model_used = _llm_code_call(
        [
            {"role": "system", "content": _SPEC_SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    text = _strip_fences(raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise CoderError(f"model spec for {capability_id} is not JSON")
    try:
        data = _json.loads(text[start : end + 1])
    except ValueError as exc:
        raise CoderError(f"model spec for {capability_id} is not valid JSON: {exc}") from exc

    entity = str(data.get("entity") or "").strip()
    if not entity.isidentifier():
        raise CoderError(f"model spec for {capability_id} has a bad entity name: {entity!r}")

    fields = []
    used_names: set[str] = set()
    for item in data.get("fields") or []:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        ftype = str(item.get("type") or "str").strip()
        if not raw_name or raw_name in {"id", "self"} or raw_name.startswith("_"):
            continue
        name = sanitize_python_identifier(raw_name, used=used_names)
        format_hint = _TEMPORAL_TYPE_ALIASES.get(ftype.lower())
        if format_hint:
            ftype = "str"
        else:
            ftype = _SQL_TYPE_ALIASES.get(ftype.lower(), ftype)
        if ftype not in _ALLOWED_FIELD_TYPES:
            used_names.discard(name)
            continue
        field = {
            "name": name,
            "type": ftype,
            "required": bool(item.get("required", True)),
            **_clean_constraints(item, ftype),
        }
        if format_hint:
            field["format"] = format_hint
        fields.append(field)

    # Dedupe, keeping first occurrence, and cap the width.
    seen, unique = set(), []
    for f in fields:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        unique.append(f)
    if not unique:
        raise CoderError(f"model spec for {capability_id} declared no usable fields")

    logger.info(
        "coder designed %s.%s (%d fields) via %s",
        capability_id,
        entity,
        len(unique),
        model_used,
    )
    from app.factory.build.block_obligations import ensure_record_envelope

    spec, _added = ensure_record_envelope(
        {"entity": entity, "fields": unique[:8], "model": model_used}
    )
    return spec


def get_factory_llm_config_model() -> str:
    """The CONFIGURED primary model.

    Not necessarily the model that answered any given call -- when a fallback
    leg runs, that is a different model, possibly a different vendor. Use the
    ``model`` returned alongside each completion for provenance; this is only
    for describing configuration.
    """
    from .product_architect import get_factory_llm_config

    return get_factory_llm_config().get("model", "unknown")


_ROUTE_SYSTEM = writer_system_brief() + """

You write ONE Python function body for an API route in a generated platform.

The kernel already templates GET list and GET-by-id, plus the job routes
(GET /v1/jobs, /catalog, /inventory, /capabilities, /gates, /provenance).
You only write the POST create body.

Contract:
- Emit ONLY the body of:  def endpoint(payload: dict) -> dict
- Module-level names already available to you:
    CAPABILITY_ID : str
    handle(payload: dict) -> dict    the capability handler
    save(record: dict) -> dict       persists and returns the stored record
    list_all() -> list[dict]         every stored record
- Typical shape: validate the payload, call handle(), persist the result with
  save(), and return a dict describing what happened.
- Return a JSON-serialisable dict. Return {"ok": False, "error": "..."} for a
  bad request rather than raising.

VALIDATION IS BOUNDED BY THE SPEC. You are given each field's type and, where
one exists, its allowed_values or min/max. Enforce presence, the declared
type, and those declared constraints -- and nothing else. Do NOT invent a
vocabulary, a format, a regex or a range that the spec does not state. The
platform's tests build their payloads from this same spec, so a restriction
you invent will reject a request that is valid by contract, and the build
fails with no way for either side to give way.
- Standard library only, and no import statements at all. No network, no
  filesystem, no subprocess, no eval/exec.
- Do NOT define a nested function and put your logic inside it — the
  enclosing function would fall through and return None. Return directly
  from the statements you write.
- No markdown fences, no def line, no commentary — statements only, written at
  column zero. They are indented for you; do not add leading indentation.
"""


def describe_fields(fields: List[Dict[str, Any]]) -> str:
    """Render the field contract the route must validate against, and only that."""
    lines = []
    for f in fields:
        bits = [f"{f['name']}: {f['type']}"]
        if f.get("allowed_values"):
            bits.append("one of " + ", ".join(repr(v) for v in f["allowed_values"]))
        if f.get("min") is not None:
            bits.append(f"min {f['min']}")
        if f.get("max") is not None:
            bits.append(f"max {f['max']}")
        if not f.get("required", True):
            bits.append("optional")
        lines.append("  - " + " | ".join(bits))
    return "\n".join(lines)


def generate_route_body(
    *,
    capability_id: str,
    description: str,
    entity: str,
    fields: List[Dict[str, Any]],
    work_list: Optional[List[str]] = None,
    previous_attempt: Optional[str] = None,
) -> Dict[str, Any]:
    """Write the endpoint body for one capability's route.

    Takes the full field specs, not just names: the route may enforce the
    declared constraints and nothing beyond them, so it has to be told what
    those constraints are.
    """
    lines = [
        f"Capability id: {capability_id}",
        f"Capability description: {description}",
        f"Entity being stored: {entity}",
        "Fields, with every constraint you are permitted to enforce:",
        describe_fields(fields),
    ]
    if work_list:
        lines.append(
            "\nA previous attempt failed these checks — fix them:\n"
            + "\n".join(f"- {item}" for item in work_list)
        )
    if previous_attempt:
        lines.append(
            "\nYOUR PREVIOUS ATTEMPT is below. It produced the failures "
            "above. Do not start over: keep what works and change only what "
            "the findings demand.\n----\n" + previous_attempt + "\n----"
        )
    lines.append("\nWrite the endpoint() body now.")

    body, model_used = _call_validate_retry(
        [
            {"role": "system", "content": _ROUTE_SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ],
        f"{capability_id}:route",
    )
    logger.info(
        "coder wrote platform route %s (%d lines) via %s",
        capability_id,
        body.count("\n") + 1,
        model_used,
    )
    return {"body": body, "model": model_used}


def generate_platform_handler(
    *,
    capability_id: str,
    description: str,
    block_ids: List[str],
    product_name: str,
    vertical: str,
    work_list: Optional[List[str]] = None,
    block_contracts: Optional[Dict[str, Any]] = None,
    model_fields: Optional[List[Dict[str, Any]]] = None,
    resource_obligations: Optional[str] = None,
    previous_attempt: Optional[str] = None,
    vendored_roster: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Write the ``handle`` body for one runner-built capability.

    Distinct from :func:`generate_handler_body`, which serves the legacy
    template generator: that contract is a sandboxed in-memory stub with no
    block access, whereas this one is allowed to drive vendored blocks through
    the local dispatch runtime. Same validation gate applies to both.

    ``work_list`` carries the TESTER's findings on a rework pass so the coder
    is told what failed rather than guessing from scratch. ``block_contracts``
    carries what each block actually accepts (actions, declared inputs, schema
    required fields) -- without it the coder guesses payload shapes and real
    blocks reject them with "Input validation failed".
    """
    lines = [
        f"Platform: {product_name} (vertical: {vertical})",
        f"Capability id: {capability_id}",
        f"Capability description: {description}",
        f"BLOCK_IDS available to execute(): {block_ids!r}",
    ]
    if model_fields:
        lines.append(
            "\nThe payload arriving at handle() carries the capability's data "
            "model. Validate ONLY these fields; never demand any other. "
            "If you must enforce a vocabulary, bool, or int bound, it has "
            "to appear on this list — the pilot suite builds payloads from "
            "the same spec:\n"
            + describe_fields(model_fields)
        )
    if resource_obligations:
        # A block that mints its own id cannot be driven from payload values.
        # Live: crew_dashboard passed team_id = payload["primary_crew"] -- a
        # crew name -- and got "Team access denied" from a team it had never
        # created. The block was working; the handler had invented the id.
        lines.append("\n" + resource_obligations)
    if block_contracts:
        lines.append(
            "\nBlock contracts (invoke each block with an action it supports "
            "and an input dict carrying every required field, CONSTRUCTED "
            "from the payload fields above -- the caller sends only those):\n"
            + json.dumps(block_contracts, indent=2, sort_keys=True)
        )
    if vendored_roster:
        lines.append(
            "\nEvery block vendored into this platform (a pipeline or "
            "orchestrator block's steps may reference any of these by id; "
            "a step must never reference the pipeline block itself): "
            + ", ".join(vendored_roster)
        )
    if work_list:
        lines.append(
            "\nA previous attempt failed these checks — fix them:\n"
            + "\n".join(f"- {item}" for item in work_list)
        )
    if previous_attempt:
        lines.append(
            "\nYOUR PREVIOUS ATTEMPT is below. It produced the failures "
            "above. Do not start over: keep what works and change only what "
            "the findings demand. If a block action rejected your input, "
            "either supply the fields its error names (derived from the "
            "payload) or pick a different action from that block's "
            "action_options that matches the capability's intent.\n"
            "----\n" + previous_attempt + "\n----"
        )
    lines.append("\nWrite the handle() body now.")

    body, model_used = _call_validate_retry(
        [
            {"role": "system", "content": _PLATFORM_SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ],
        capability_id,
        block_ids,
    )
    logger.info(
        "coder wrote platform handler %s (%d lines) via %s",
        capability_id,
        body.count("\n") + 1,
        model_used,
    )
    return {"body": body, "model": model_used}


def generate_handler_body(cap: CapabilitySpec, blueprint: ProductBlueprint) -> Dict[str, Any]:
    """Write the handler body for one GENERATE capability.

    Returns {"body": <indented code>, "model": <model id>}. Raises CoderError
    on any failure — the generator decides what an honest fallback looks like.
    """
    user = (
        f"Platform: {blueprint.product_name} (vertical: {blueprint.vertical})\n"
        f"Platform summary: {blueprint.summary}\n\n"
        f"Capability id: {cap.id}\n"
        f"Capability description: {cap.description}\n\n"
        "Write the generated_logic body now."
    )
    raw, model_used = _llm_code_call(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    body = _validate_body(_strip_fences(raw), cap.id)
    logger.info(
        "coder wrote %s (%d lines) via %s", cap.id, body.count("\n") + 1, model_used
    )
    return {"body": body, "model": model_used}


# -- Kernel-side agent (COLLECTOR review, TESTER extra cases) --------------
#
# The coding agent lives in WRITER for manufacturing. COLLECTOR and TESTER
# may *consult* it under their own authority: report-only binding review
# (COLLECTOR writes nothing) and extra domain cases that cannot replace
# kernel tests (TESTER writes only tests/). CLONER and STORE_MANAGER stay
# mechanical. See docs/factory/AGENT_IN_THE_KERNELS.md.

_COLLECTOR_REVIEW_SYSTEM = kernel_seat_brief("COLLECTOR") + """

The collector kernel already resolved block ids. You do NOT pick blocks and you do \
NOT change the plan. Review each capability↔block binding against the block \
contracts and return JSON only:

{"reviews": [{"capability_id": "...", "block_ids": ["..."], "verdict": "endorse" or "mismatch", "reason": "short"}]}

endorse = the blocks can plausibly serve the capability.
mismatch = strained fit an engineer would flag at tender review.
Never invent block ids. Never omit a capability you were given.
"""

_TESTER_CASES_SYSTEM = kernel_seat_brief("TESTER") + """

The tester kernel already wrote shape/persistence/dispatch tests. Propose ADDITIONAL \
domain cases as mutations of the sample payloads you are given — same keys, \
at least one value changed, no new keys. Return JSON only:

{"cases": [{"capability_id": "...", "payload": {}, "expect": "accept" or "reject", "reason": "short"}]}

expect=reject means the mutated payload should be refused.
expect=accept means it should still succeed.
Do not replace kernel tests. Do not invent capability ids or payload keys.
Do not ask to run tests over HTTP; GET /v1/gates describes coverage only.
"""


def _llm_json_object(
    messages: List[Dict[str, str]], what: str
) -> tuple[Dict[str, Any], str]:
    raw, model_used = _llm_code_call(messages)
    text = _strip_fences(raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise CoderError(f"{what} is not JSON")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise CoderError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CoderError(f"{what} is not a JSON object")
    return data, model_used


def review_capability_bindings(
    *,
    product_name: str,
    vertical: str,
    capabilities: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """COLLECTOR consults the coding agent. Report-only; raises CoderError."""
    user = (
        f"Platform: {product_name} (vertical: {vertical})\n"
        "Capability bindings and harvested contracts:\n"
        + json.dumps(capabilities, indent=2, sort_keys=True)
        + "\n\nReturn the reviews JSON now."
    )
    data, model_used = _llm_json_object(
        [
            {"role": "system", "content": _COLLECTOR_REVIEW_SYSTEM},
            {"role": "user", "content": user},
        ],
        "collector binding review",
    )
    reviews: List[Dict[str, Any]] = []
    known = {str(c.get("id") or "") for c in capabilities}
    for item in data.get("reviews") or []:
        if not isinstance(item, dict):
            continue
        cap_id = str(item.get("capability_id") or "").strip()
        if cap_id not in known:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in {"endorse", "mismatch"}:
            continue
        reviews.append(
            {
                "capability_id": cap_id,
                "block_ids": [
                    str(b) for b in (item.get("block_ids") or []) if str(b).strip()
                ],
                "verdict": verdict,
                "reason": str(item.get("reason") or "")[:300],
            }
        )
    if not reviews:
        raise CoderError("collector binding review named no known capabilities")
    logger.info(
        "coder reviewed %d/%d collector bindings via %s",
        len(reviews),
        len(known),
        model_used,
    )
    return {"reviews": reviews, "model": model_used}


def propose_domain_test_cases(
    *,
    product_name: str,
    vertical: str,
    capabilities: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """TESTER consults the coding agent for extra domain cases. Raises CoderError."""
    user = (
        f"Platform: {product_name} (vertical: {vertical})\n"
        "Kernel sample payloads (mutate these, do not replace them):\n"
        + json.dumps(capabilities, indent=2, sort_keys=True)
        + "\n\nReturn the extra cases JSON now."
    )
    data, model_used = _llm_json_object(
        [
            {"role": "system", "content": _TESTER_CASES_SYSTEM},
            {"role": "user", "content": user},
        ],
        "tester domain cases",
    )
    known = {str(c.get("id") or "") for c in capabilities}
    cases: List[Dict[str, Any]] = []
    for item in data.get("cases") or []:
        if not isinstance(item, dict):
            continue
        cap_id = str(item.get("capability_id") or "").strip()
        if cap_id not in known:
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        expect = str(item.get("expect") or "").strip().lower()
        if expect not in {"accept", "reject"}:
            continue
        cases.append(
            {
                "capability_id": cap_id,
                "payload": payload,
                "expect": expect,
                "reason": str(item.get("reason") or "")[:300],
            }
        )
    if not cases:
        raise CoderError("tester proposed no usable domain cases")
    logger.info("coder proposed %d tester domain case(s) via %s", len(cases), model_used)
    return {"cases": cases, "model": model_used}


_WHOLE_JOB_SYSTEM = writer_system_brief() + """

You are given ONE compiled brief for the whole platform (TARGET, STEP 0
INVENTORY, DO, ACCEPTANCE, FORBIDDEN). This is the entire job — not one
handle() at a time.

Return ONLY a JSON object, no prose and no markdown fences:
{
  "specs": {
    "<capability_id>": {
      "entity": "<snake_case singular>",
      "fields": [
        {"name": "<snake_case>", "type": "str|int|float|bool", "required": true,
         "allowed_values": ["open", "in_progress", "closed"]}
      ]
    }
  },
  "handlers": {
    "<capability_id>": "<column-zero Python statements for handle() body>"
  }
}

Rules:
- Include every capability id you were given. Do not invent extra ids.
- Handler bodies obey the same contract as a single handle() shot:
  execute() every BLOCK_IDS entry, action= keyword only, no imports
  (especially not app.actions / app.routes / app.main — circular
  package init fails writer_behaviour as workspace does not import),
  return a dict with capability=CAPABILITY_ID.
- status fields use the envelope vocabulary open|in_progress|closed.
- Do not claim REUSE for a block id that STEP 0 flagged missing.
- Every handler MUST accept a payload built from its own spec FIELDS +
  CONSTRAINTS (WRITER writer_behaviour baseline). Declare every required
  field and vocabulary on the spec. Do not return ok:false before
  execute() on that sample. A second, stricter contract the spec cannot
  express fails the gate: no capability accepted its own schema.
- REUSE keep-path handlers must populate BLOCK_DEFAULT_ACTIONS and pass
  action= as a keyword. execute() with action=None is
  'Unknown action: None'. That miss is reuse/accept miss: HALT before
  TESTER. Harvest formula_executor, vector_search (and other bound Store
  blocks) from vendored block.json or the factory Store map. Photographed roster:
  patient_records_management, appointment_scheduling,
  prescription_management, billing_and_invoicing,
  client_communication_portal.
- PRODUCT test_every_capability_route_accepts_payload then POSTs that
  same schema sample and runs bound blocks. A capability that binds
  workflow + event_bus (appointment_scheduling / appointment_booking /
  automated_reminders / reminders_notifications style) must prepare EACH
  event_bus step including Store step_0, step_1 and step_2+
  (block=event_bus, action=publish, topic, payload dict, message,
  channel=mcp, tool=event_bus). Do not set step input to payload.
  An event_bus-first child fails as workflow: step_0 (event_bus): error.
  An unprepared first factory child fails as
  workflow: step_1 (event_bus): error. A prepared step_1 plus an
  unprepared step_2 still fails as workflow: step_2 (event_bus): error.
  The factory wrap is not keep/done. Store workflow / kit shim reads
  input['result'] or out['result']. A schema-sample POST that omits it
  fails as workflow: RuntimeError: 'result' — prepare_block_input and
  the keep-path emit MUST attach result from the first prepared step.
  CLONER emit_result_key_access rewrites reads of name['result'] only.
  Rewriting name['result'] = into a .get() call fails as
  SyntaxError: cannot assign to function call (queue / formula_executor).
  fail-closed keep original must still rewrite reads — a whole-module
  keep of Store workflow.py leaves envelope['result'] as
  workflow: RuntimeError: 'result' (appointment_scheduling rejected a
  payload built from its own schema).
  Exact shape:
  {"block": "event_bus", "action": "publish",
   "input": {"topic": "<str>", "payload": {}, "message": "<str>",
   "channel": "mcp", "tool": "event_bus"}}.
- PRODUCT one-record round-trip then POSTs that sample and re-reads
  store.list_all(entity). Handlers return ok:true; the ROUTE persists via
  the tenant-scoped save(payload) to the alembic 0001 table — handlers
  must NOT call store.save directly (they have no tenant, Phase 2 §0.2). POST raised OperationalError: no such table: <entity> is a
  miss. Do not treat a leftover ./data/platform.db as migrated.
"""


def generate_from_compiled_brief(
    *,
    brief: str,
    capabilities: List[str],
    product_name: str,
    vertical: str,
) -> Dict[str, Any]:
    """One HTTP call for the whole compiled brief. Not a per-capability loop."""
    known = [str(c).strip() for c in capabilities if str(c).strip()]
    user = (
        f"Platform: {product_name} (vertical: {vertical})\n"
        f"Capability ids you must cover: {known!r}\n\n"
        "COMPILED BRIEF:\n"
        f"{brief}\n\n"
        "Return the specs+handlers JSON now."
    )
    data, model_used = _llm_json_object(
        [
            {"role": "system", "content": _WHOLE_JOB_SYSTEM},
            {"role": "user", "content": user},
        ],
        "compiled-brief oneshot",
    )
    specs: Dict[str, Any] = {}
    handlers: Dict[str, str] = {}
    raw_specs = data.get("specs") or {}
    raw_handlers = data.get("handlers") or {}
    if not isinstance(raw_specs, dict):
        raw_specs = {}
    if not isinstance(raw_handlers, dict):
        raw_handlers = {}
    from app.factory.build.persist_accept import bind_generate_artifacts

    raw_specs = bind_generate_artifacts(known, raw_specs)
    raw_handlers = bind_generate_artifacts(known, raw_handlers)
    for cid in known:
        spec = raw_specs.get(cid)
        if isinstance(spec, dict) and spec.get("entity"):
            specs[cid] = spec
        body = raw_handlers.get(cid)
        if isinstance(body, str) and body.strip():
            handlers[cid] = _validate_body(_strip_fences(body), cid)
    if not specs and not handlers:
        raise CoderError("compiled-brief oneshot returned no specs or handlers")
    logger.info(
        "coder oneshot wrote %d spec(s) and %d handler(s) via %s",
        len(specs),
        len(handlers),
        model_used,
    )
    return {"specs": specs, "handlers": handlers, "model": model_used}
