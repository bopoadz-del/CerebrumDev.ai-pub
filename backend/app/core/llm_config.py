"""Unified LLM provider configuration for CerebrumDev.ai.

Two paths are configured independently:

* Chat / chain_generator / platform_chat_llm path: ``get_llm_config()``
  - Preferred: ``CEREBRUM_CHAT_LLM_API_KEY / BASE_URL / MODEL`` (authoritative)
  - Fallback: leftover ``CEREBRUM_LLM_*`` / ``KIMI_*`` / ``ANTHROPIC_*``
  - Cursor keys never select a chat-completions host. Cursor has no public
    ``/v1/chat/completions`` (Cloud Agents is ``/v0/agents`` only).
  - Do not invent OpenRouter as the chat host when ``OPENROUTER_API_KEY``
    is absent. A leftover ``CEREBRUM_LLM_BASE_URL=openrouter.ai`` plus a
    dead OpenRouter key must not steal a healthy leftover Moonshot primary
    when ``CEREBRUM_CHAT_*`` is unset.

* Factory Product Architect / platform CLI path: ``get_factory_llm_config()``
  - Preferred: ``CEREBRUM_FACTORY_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: ``CEREBRUM_CHAT_*`` then ``CEREBRUM_LLM_*`` / leftover ``KIMI_*``
  - ``LLM_PROVIDER=cursor`` HTTP draft uses ``CEREBRUM_CHAT_*``, not Cursor.
  - Factory coding / Cursor BA stays on ``CURSOR_*``
    (``FACTORY_CODE_PROVIDER=cursor`` / cli-pivot).

``LLM_PROVIDER`` accepts ``cursor``, ``kimi``/``moonshot`` (aliased to kimi)
and ``claude``/``anthropic`` (aliased to claude).

``LLM_PROVIDER=cursor`` names the Factory coding family (Cursor BA keys).
It is valid and render.yaml may pin it. HTTP Floor chat uses
``CEREBRUM_CHAT_LLM_*`` — those Cursor keys are not chat-completions
credentials.

Claude remains an opt-in HTTP provider. Leftover Kimi/Moonshot credentials
still resolve when ``LLM_PROVIDER`` is unset or ``kimi``/``moonshot``.
Selection is deliberate, never accidental -- :func:`_detect_provider`
resolves to Kimi whenever leftover Kimi credentials are present, *even if
Claude credentials are also present*, so nobody's bill changes by having a
second key in the environment. Claude is used only when
``LLM_PROVIDER=claude`` is set explicitly, or when Kimi has no credentials
and Claude does.

Selecting a provider whose key is missing is a loud error. It never falls
through to the other provider -- a silent switch is a cost surprise, which is
a product bug.

That rule governs the PRIMARY provider. A cross-provider *fallback leg* --
see :func:`get_factory_fallback_leg` -- is available to the factory coder
only, runs only after the primary has already failed, and is pinned to a
zero-priced model so it cannot create the cost surprise the rule exists to
prevent.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List
from urllib.parse import urlparse, urlsplit

#: Cursor BA key names (Factory coding / cli-pivot). Not /chat/completions
#: credentials — Cursor has no public OpenAI-compatible chat API.
CURSOR_KEY_ENVS = (
    "CURSOR_API_KEY",
    "CURSOR_AGENT_API_KEY",
    "FACTORY_CURSOR_API_KEY",
)

#: DeepSeek is the primary provider and OpenRouter is the fallback. These are the
#: DEFAULTS used when no *_LLM_BASE_URL / *_LLM_MODEL is configured.
#:
#: They used to be `https://api.moonshot.ai/v1` and `kimi-k2.7-code`, a retired
#: provider. That is not a cosmetic default: `_scoped_path_endpoint` returns on the
#: FIRST prefix with an API key and takes host+model from that prefix alone, so a
#: prefix with a key but no BASE_URL fell through to the Moonshot constant and the
#: loop never reached the prefix where the real DeepSeek endpoint was configured.
#: A DeepSeek key was POSTed to Moonshot, 401'd, and the Floor reported "LLM
#: drafting failed, falling back" -- shipping deterministic-template blueprints
#: while every key in the environment was valid. Diagnosed live on build
#: sess_f427772fcfcc4087 and worked around with two env vars; this is the fix.
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
#: The retired host, kept as a NAMED legacy constant for one reason: an operator
#: who sets KIMI_API_KEY and nothing else is explicitly naming that provider, and
#: sending their key anywhere else would be the same defect mirrored -- a named key
#: POSTed to a host that will 401 it. It is never a default on its own.
MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"
#: Both default to the same id, matching the explicit DeepSeek branch further down
#: rather than inventing a sibling model name that might 404.
DEFAULT_PRIMARY_MODEL = "deepseek-chat"
DEFAULT_FALLBACK_MODEL = "deepseek-chat"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
#: Cursor's Cloud Agents API. It is not an OpenAI-compatible chat host: a
#: chat base URL on this host is refused (see ``_is_cursor_chat_host``).
CURSOR_AGENTS_BASE_URL = "https://api.cursor.com/v0/agents"
DEFAULT_OPENROUTER_FALLBACK_MODEL = "minimax/minimax-m3:free"


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def env_key_present(name: str) -> bool:
    """True when this process sees a non-empty value for ``name``.

    Whitespace-only counts as absent. Used by ``/ready`` so a dashboard
    "filled" secret can be distinguished from a key the runtime actually
    received — without echoing the secret.
    """
    return bool(os.getenv(name, "").strip())


#: Env vars whose values must never appear in unauthenticated ``/ready``.
_READY_SECRET_ENVS = (
    "CEREBRUM_CHAT_LLM_API_KEY",
    "CEREBRUM_LLM_API_KEY",
    "CEREBRUM_FACTORY_LLM_API_KEY",
    "KIMI_API_KEY",
    "OPENROUTER_API_KEY",
    "FACTORY_LLM_FALLBACK_API_KEY",
    "CURSOR_API_KEY",
    "CURSOR_AGENT_API_KEY",
    "FACTORY_CURSOR_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLAUDE_API_KEY",
    "DEEPSEEK_API_KEY",
)


def _redact_ready_secrets(text: str, *extra: str) -> str:
    """Replace known secret values in an error string. Names of env vars stay."""
    if not text:
        return ""
    secrets = [value for value in extra if value]
    for name in _READY_SECRET_ENVS:
        value = os.getenv(name, "").strip()
        if value:
            secrets.append(value)
    out = text
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            out = out.replace(secret, "[redacted]")
    return out


def _chat_http_host(base_url: str) -> str:
    """Hostname only of a resolved chat ``base_url``. Empty when unset/invalid."""
    raw = (base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return (parsed.hostname or "").rstrip(".")


def llm_ready_details() -> Dict[str, Any]:
    """Unauthenticated ``/ready`` LLM facts. Booleans, host, error — no secrets.

    Proves whether this process sees Floor chat HTTP keys. A Render
    dashboard "filled" ``CEREBRUM_CHAT_LLM_API_KEY`` is not evidence the
    runtime received it; these flags are.
    """
    api_key = ""
    host = ""
    error = ""
    try:
        cfg = get_llm_config()
        api_key = str(cfg.get("api_key") or "")
        host = _chat_http_host(str(cfg.get("base_url") or ""))
        error = str(cfg.get("error") or "")
    except Exception as exc:  # noqa: BLE001 — /ready must not raise
        error = str(exc)
    return {
        "cerebrum_chat_llm_api_key_present": env_key_present(
            "CEREBRUM_CHAT_LLM_API_KEY"
        ),
        "cerebrum_llm_api_key_present": env_key_present("CEREBRUM_LLM_API_KEY"),
        "kimi_api_key_present": env_key_present("KIMI_API_KEY"),
        "openrouter_api_key_present": env_key_present("OPENROUTER_API_KEY"),
        "cursor_api_key_present": env_key_present("CURSOR_API_KEY"),
        "llm_provider": os.getenv("LLM_PROVIDER") or "",
        # Whether THIS process sees the writer-seam switch. A Render
        # dashboard-set FACTORY_CODEWHALE_WRITER is not evidence the
        # runtime received it — this flag is (live-factory
        # sess_620b8581fb224bea: service env said 1, writer still took
        # the compiled-brief leg).
        "factory_codewhale_writer_armed": _codewhale_writer_armed(),
        "factory_codewhale_writer_env": os.getenv("FACTORY_CODEWHALE_WRITER", ""),
        "chat_http_base_url_host": host,
        "chat_http_api_key_present": bool(api_key.strip()),
        "chat_http_error": _redact_ready_secrets(error, api_key.strip()),
    }


def _codewhale_writer_armed() -> bool:
    """Same predicate the writer role uses, evaluated in this process."""
    try:
        from app.factory.build.writer_control import writer_uses_codewhale

        return bool(writer_uses_codewhale())
    except Exception:  # noqa: BLE001 — /ready must not raise
        return False


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _kimi_key(*prefixes: str) -> str:
    """Resolve a Kimi API key from prefixed env vars, then shared fallbacks."""
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_API_KEY")
    candidates.extend(["KIMI_API_KEY", "CEREBRUM_LLM_API_KEY"])
    return _env_first(*candidates)


def _host_of(url: str) -> str:
    """The parsed host of ``url`` (lower-cased by the parser), or ''."""
    try:
        return urlsplit((url or "").strip()).hostname or ""
    except ValueError:
        return ""


def _is_openrouter_base(base_url: str) -> bool:
    """``base_url`` is on the declared OpenRouter endpoint's host.

    A host compared with the host of :data:`OPENROUTER_BASE_URL`, both parsed
    -- not a search of the URL's text.
    """
    host = _host_of(base_url)
    return bool(host) and host == _host_of(OPENROUTER_BASE_URL)


def _is_cursor_chat_host(base_url: str) -> bool:
    """True for a chat base URL on Cursor's Cloud Agents host.

    Cursor Cloud Agents live at :data:`CURSOR_AGENTS_BASE_URL`. There is no
    public OpenAI-compatible chat-completions API on that host.
    """
    host = _host_of(base_url)
    return bool(host) and host == _host_of(CURSOR_AGENTS_BASE_URL)


def _first_off_openrouter(slots, default: str = "") -> str:
    """First set value among ``slots`` whose own endpoint is not OpenRouter.

    ``slots`` are ``(value_env, base_url_env)`` pairs: a key or model belongs
    to the endpoint configured beside it (``X_LLM_API_KEY`` with
    ``X_LLM_BASE_URL``). Which provider a value is for is read from that
    typed configuration -- never from the value's spelling (a key prefix, a
    ``/`` or a variant suffix in a model id).
    """
    for value_env, base_env in slots:
        value = os.getenv(value_env, "").strip()
        base = os.getenv(base_env, "").strip() if base_env else ""
        if value and not _is_openrouter_base(base):
            return value
    return default


def _openrouter_key(*prefixes: str) -> str:
    """Resolve an OpenRouter key for a kimi-wire OpenRouter base_url.

    Path-prefixed ``*_LLM_API_KEY`` still wins (operator override). Shared
    Moonshot names (``KIMI_API_KEY`` / ``CEREBRUM_LLM_API_KEY``) are excluded
    — those keys 401 on OpenRouter.
    """
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["OPENROUTER_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY"])
    return _env_first(*candidates)


def _resolve_kimi_api_key(base_url: str, *prefixes: str) -> str:
    """Pick the key that belongs to the resolved host.

    When ``base_url`` is OpenRouter, never fall back to Moonshot keys.
    """
    if _is_openrouter_base(base_url):
        return _openrouter_key(*prefixes)
    return _kimi_key(*prefixes)


def _env_first_skipping(*names: str, default: str = "", reject=None) -> str:
    """Like ``_env_first`` but skip values ``reject(value)`` says are unusable."""
    skip = reject or (lambda _value: False)
    for name in names:
        value = os.getenv(name, "").strip()
        if value and not skip(value):
            return value
    return default


def _native_moonshot_key(*prefixes: str) -> str:
    """Moonshot credentials only — never a key configured for OpenRouter.

    Path-prefixed ``*_LLM_API_KEY`` still wins when its own base URL is not
    OpenRouter. A key whose slot points at OpenRouter (``CEREBRUM_LLM_API_KEY``
    beside ``CEREBRUM_LLM_BASE_URL=openrouter``) does not count as a native
    primary; OpenRouter stays the fallback leg.

    Unless the operator DECLARED the provider (``LLM_PROVIDER=kimi`` or a
    ``KIMI_*`` key): then the shared key slot is that provider's by
    declaration, and an OpenRouter base left beside it is the stale value --
    rejected by ``_kimi_base_url(skip_openrouter=True)``.
    """
    slots = [(f"{prefix}_LLM_API_KEY", f"{prefix}_LLM_BASE_URL") for prefix in prefixes]
    slots.extend(
        [("KIMI_API_KEY", "KIMI_BASE_URL"), ("CEREBRUM_LLM_API_KEY", "CEREBRUM_LLM_BASE_URL")]
    )
    if _names_kimi_explicitly():
        return _env_first(*(value_env for value_env, _base in slots))
    return _first_off_openrouter(slots)


def _default_base_url() -> str:
    """DeepSeek, unless the environment explicitly names Kimi credentials.

    A blanket DeepSeek default would mirror the very defect it fixes: an operator
    with only KIMI_API_KEY set would have that key POSTed to DeepSeek and 401'd. So
    the default follows the credential that is actually present -- an environment
    that configures nothing, or configures DeepSeek, gets DeepSeek; one that names
    Kimi gets Kimi, because it asked.
    """
    if _names_kimi_explicitly():
        return MOONSHOT_BASE_URL
    return DEEPSEEK_BASE_URL


def _names_kimi_explicitly() -> bool:
    """Whether this environment ASKED for the retired provider.

    Two ways to ask, and both are explicit: ``LLM_PROVIDER=kimi``/``moonshot``, or a
    ``KIMI_*`` credential. Either one means the default host and model must be that
    provider's, because sending a named key to a host that will 401 it is the same
    defect as the one the DeepSeek default fixes -- just pointing the other way.
    """
    if os.getenv("KIMI_API_KEY", "").strip():
        return True
    return os.getenv("LLM_PROVIDER", "").strip().lower() in ("kimi", "moonshot")


def _kimi_base_url(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_BASE_URL")
    candidates.extend(["KIMI_BASE_URL", "CEREBRUM_LLM_BASE_URL"])
    reject = _is_openrouter_base if skip_openrouter else None
    return _env_first_skipping(
        *candidates, default=_default_base_url(), reject=reject
    )


def _default_model() -> str:
    """Same rule as the base URL: the model must belong to the host the key goes to."""
    return "kimi-k2.7-code" if _names_kimi_explicitly() else DEFAULT_PRIMARY_MODEL


def _default_fallback_model() -> str:
    return "moonshot-v1-8k" if _names_kimi_explicitly() else DEFAULT_FALLBACK_MODEL


def _kimi_model(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_MODEL")
    candidates.extend(["KIMI_MODEL", "CEREBRUM_LLM_MODEL"])
    # KIMI_MODEL / KIMI_BASE_URL are still READ, because a live service may still
    # have them set and silently ignoring a value someone configured is its own
    # defect. They are legacy aliases only: the DEFAULT is DeepSeek, so an
    # environment that configures nothing can no longer be routed to a retired
    # provider. Override with {PREFIX}_LLM_MODEL or CEREBRUM_LLM_MODEL.
    if skip_openrouter:
        slots = [(f"{prefix}_LLM_MODEL", f"{prefix}_LLM_BASE_URL") for prefix in prefixes]
        slots.extend(
            [("KIMI_MODEL", "KIMI_BASE_URL"), ("CEREBRUM_LLM_MODEL", "CEREBRUM_LLM_BASE_URL")]
        )
        return _first_off_openrouter(slots, default=_default_model())
    return _env_first(*candidates, default=_default_model())


def _kimi_fallback_model(*prefixes: str, default: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_FALLBACK_MODEL")
    candidates.extend(["KIMI_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"])
    if skip_openrouter:
        slots = [
            (f"{prefix}_LLM_FALLBACK_MODEL", f"{prefix}_LLM_BASE_URL") for prefix in prefixes
        ]
        slots.extend(
            [
                ("KIMI_FALLBACK_MODEL", "KIMI_BASE_URL"),
                ("CEREBRUM_LLM_FALLBACK_MODEL", "CEREBRUM_LLM_BASE_URL"),
            ]
        )
        return _first_off_openrouter(slots, default=default)
    return _env_first(*candidates, default=default)


def _non_cursor_base(value: str, default: str | None = None) -> str:
    if value and not _is_cursor_chat_host(value):
        return value
    return _default_base_url() if default is None else default


def _scoped_path_endpoint(*prefixes: str) -> Dict[str, str] | None:
    """Use the first path-scoped ``*_LLM_API_KEY`` as an authoritative triple.

    ``CEREBRUM_CHAT_LLM_*`` is Floor chat. Leftover ``KIMI_*`` / ``CURSOR_*``
    must not replace a present scoped key. ``api.cursor.com`` is never a
    chat host — Cursor has no public ``/v1/chat/completions``.
    """
    for prefix in prefixes:
        key = os.getenv(f"{prefix}_LLM_API_KEY", "").strip()
        if not key:
            continue
        base = _non_cursor_base(os.getenv(f"{prefix}_LLM_BASE_URL", "").strip(), default="")
        if not base:
            shared = _env_first("CEREBRUM_LLM_BASE_URL", "KIMI_BASE_URL")
            base = _non_cursor_base(shared)
        model = os.getenv(f"{prefix}_LLM_MODEL", "").strip() or _env_first(
            "CEREBRUM_LLM_MODEL", "KIMI_MODEL", default=_default_model()
        )
        fallback = os.getenv(f"{prefix}_LLM_FALLBACK_MODEL", "").strip() or _env_first(
            "CEREBRUM_LLM_FALLBACK_MODEL",
            "KIMI_FALLBACK_MODEL",
            default=_default_fallback_model(),
        )
        return {
            "api_key": key,
            "base_url": base,
            "model": model,
            "fallback_model": fallback,
        }
    return None


def _resolve_kimi_primary(*prefixes: str) -> Dict[str, str]:
    """Pick the HTTP chat/architect endpoint.

    A present path-scoped key (``CEREBRUM_CHAT_LLM_API_KEY``) owns host and
    model, including an operator-set OpenRouter base. Leftover ``KIMI_*``
    must not steal that triple.

    When the scoped key is absent, a leftover Moonshot key owns the host so
    a leftover ``CEREBRUM_LLM_BASE_URL=openrouter.ai`` cannot 401 through a
    missing ``OPENROUTER_API_KEY``.
    """
    scoped = _scoped_path_endpoint(*prefixes)
    if scoped:
        return scoped
    native = _native_moonshot_key(*prefixes)
    if native:
        return {
            "base_url": _kimi_base_url(*prefixes, skip_openrouter=True),
            "api_key": native,
            "model": _kimi_model(*prefixes, skip_openrouter=True),
            "fallback_model": _kimi_fallback_model(
                *prefixes, default=_default_fallback_model(), skip_openrouter=True
            ),
        }
    base_url = _non_cursor_base(_kimi_base_url(*prefixes))
    return {
        "base_url": base_url,
        "api_key": _resolve_kimi_api_key(base_url, *prefixes),
        "model": _kimi_model(*prefixes),
        "fallback_model": _kimi_fallback_model(*prefixes, default=_default_fallback_model()),
    }


def _kimi_config(*prefixes: str) -> Dict[str, Any]:
    endpoint = _resolve_kimi_primary(*prefixes)
    return {
        "provider": "kimi",
        "api_key": endpoint["api_key"],
        "base_url": endpoint["base_url"],
        "model": endpoint["model"],
        "fallback_model": endpoint["fallback_model"],
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
        "temperature": _llm_temperature(),
    }


def _factory_kimi_config(*prefixes: str) -> Dict[str, Any]:
    """Factory config with a code-oriented fallback model default."""
    # Same primary-resolution as chat: Moonshot key wins the host so a
    # leftover OpenRouter base_url cannot steal architect / Floor chat.
    # fallback_model default stays moonshot-v1-8k — a different live
    # model from kimi-k2.7-code so a 429 is not replayed into itself.
    return _kimi_config(*prefixes)


def _claude_key(*prefixes: str) -> str:
    """Resolve a Claude API key. Mirrors _kimi_key's prefix-then-shared order.

    ``CEREBRUM_LLM_API_KEY`` is shared with Kimi deliberately: it is the
    provider-agnostic name already in use, and which provider consumes it is
    decided by LLM_PROVIDER, not by the variable.
    """
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "CEREBRUM_LLM_API_KEY"])
    return _env_first(*candidates)


def _claude_base_url(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = [f"{prefix}_LLM_BASE_URL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_BASE_URL", "CEREBRUM_LLM_BASE_URL"])
    reject = _is_openrouter_base if skip_openrouter else None
    return _env_first_skipping(
        *candidates, default="https://api.anthropic.com/v1", reject=reject
    )


def _claude_model(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = [f"{prefix}_LLM_MODEL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_MODEL", "CLAUDE_MODEL", "CEREBRUM_LLM_MODEL"])
    if skip_openrouter:
        slots = [(f"{prefix}_LLM_MODEL", f"{prefix}_LLM_BASE_URL") for prefix in prefixes]
        slots.extend(
            [
                ("ANTHROPIC_MODEL", "ANTHROPIC_BASE_URL"),
                ("CLAUDE_MODEL", "ANTHROPIC_BASE_URL"),
                ("CEREBRUM_LLM_MODEL", "CEREBRUM_LLM_BASE_URL"),
            ]
        )
        return _first_off_openrouter(slots, default="claude-sonnet-4-5")
    return _env_first(*candidates, default="claude-sonnet-4-5")


def _claude_fallback_model(*prefixes: str, default: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = [f"{prefix}_LLM_FALLBACK_MODEL" for prefix in prefixes]
    candidates.extend(["ANTHROPIC_FALLBACK_MODEL", "CEREBRUM_LLM_FALLBACK_MODEL"])
    if skip_openrouter:
        slots = [
            (f"{prefix}_LLM_FALLBACK_MODEL", f"{prefix}_LLM_BASE_URL") for prefix in prefixes
        ]
        slots.extend(
            [
                ("ANTHROPIC_FALLBACK_MODEL", "ANTHROPIC_BASE_URL"),
                ("CEREBRUM_LLM_FALLBACK_MODEL", "CEREBRUM_LLM_BASE_URL"),
            ]
        )
        return _first_off_openrouter(slots, default=default)
    return _env_first(*candidates, default=default)


def _native_claude_key(*prefixes: str) -> str:
    """Anthropic keys only — skip a key configured for OpenRouter and leave
    shared Moonshot keys alone."""
    slots = [(f"{prefix}_LLM_API_KEY", f"{prefix}_LLM_BASE_URL") for prefix in prefixes]
    slots.extend([("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"), ("CLAUDE_API_KEY", "ANTHROPIC_BASE_URL")])
    if normalise_provider(os.getenv("LLM_PROVIDER", "")) == "claude":
        # Declared provider: its key slots are its own (see _native_moonshot_key).
        return _env_first(*(value_env for value_env, _base in slots))
    return _first_off_openrouter(slots)


def _claude_config(*prefixes: str) -> Dict[str, Any]:
    native = _native_claude_key(*prefixes)
    skip = bool(native)
    api_key = native or _claude_key(*prefixes)
    return {
        "provider": "claude",
        "api_key": api_key,
        "base_url": _claude_base_url(*prefixes, skip_openrouter=skip),
        "model": _claude_model(*prefixes, skip_openrouter=skip),
        "fallback_model": _claude_fallback_model(
            *prefixes, default="claude-haiku-4-5-20251001", skip_openrouter=skip
        ),
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("CLAUDE_MOCK"),
        "temperature": _llm_temperature(),
    }


def _factory_claude_config(*prefixes: str) -> Dict[str, Any]:
    """Factory config. Same shape as _factory_kimi_config, different provider."""
    cfg = _claude_config(*prefixes)
    cfg["fallback_model"] = _claude_fallback_model(
        *prefixes, default="claude-haiku-4-5-20251001"
    )
    return cfg


def _llm_temperature() -> float | None:
    """Optional temperature override (LLM_TEMPERATURE).

    None means: do not send a temperature at all — the provider applies the
    model default. Required for reasoning models (kimi-k2.x) which reject
    any explicit temperature other than 1.
    """
    raw = os.getenv("LLM_TEMPERATURE", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def normalise_provider(name: str) -> str:
    """``moonshot`` -> ``kimi``, ``anthropic`` -> ``claude``. Others pass through."""
    name = (name or "").strip().lower()
    if name == "moonshot":
        return "kimi"
    if name == "anthropic":
        return "claude"
    return name


SUPPORTED_PROVIDERS = ("deepseek", "cursor", "kimi", "claude")


def _cursor_http_config(*prefixes: str) -> Dict[str, Any]:
    """HTTP talk for ``LLM_PROVIDER=cursor`` uses ``CEREBRUM_CHAT_LLM_*``.

    Cursor has no public ``/v1/chat/completions`` (Cloud Agents is
    ``/v0/agents``). ``CURSOR_*`` keys arm Background Agents
    (``FACTORY_CODE_PROVIDER=cursor`` / cli-pivot). Floor chat uses the
    OpenAI-compatible ``CEREBRUM_CHAT_LLM_*`` triple already on Render,
    then leftover ``CEREBRUM_LLM_*`` / Moonshot. Never invent OpenRouter
    when ``OPENROUTER_API_KEY`` is absent.
    """
    # CEREBRUM_CHAT_LLM_API_KEY is authoritative for Floor HTTP even when
    # LLM_PROVIDER=cursor (coding-family name). Read it first; leftover
    # Kimi / CEREBRUM_LLM_* only fill gaps. Never use CURSOR_* here.
    endpoint = _scoped_path_endpoint("CEREBRUM_CHAT", *prefixes) or _resolve_kimi_primary(
        "CEREBRUM_CHAT", *prefixes
    )
    if not endpoint.get("api_key"):
        direct = os.getenv("CEREBRUM_CHAT_LLM_API_KEY", "").strip()
        if direct:
            endpoint = {
                **endpoint,
                "api_key": direct,
                "base_url": endpoint.get("base_url")
                or _non_cursor_base(
                    os.getenv("CEREBRUM_CHAT_LLM_BASE_URL", "").strip()
                ),
            }
    if _is_cursor_chat_host(str(endpoint.get("base_url", ""))):
        endpoint = {
            **endpoint,
            "base_url": _non_cursor_base(
                _env_first("CEREBRUM_LLM_BASE_URL", "KIMI_BASE_URL")
            ),
        }

    cfg: Dict[str, Any] = {
        "provider": "cursor",
        "api_key": endpoint.get("api_key", ""),
        "base_url": endpoint.get("base_url", ""),
        "model": endpoint.get("model", ""),
        "fallback_model": endpoint.get("fallback_model", ""),
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("CURSOR_MOCK"),
        "temperature": _llm_temperature(),
    }
    if not cfg["api_key"]:
        cfg["error"] = (
            "LLM_PROVIDER=cursor: HTTP Floor chat uses CEREBRUM_CHAT_LLM_API_KEY "
            "(or CEREBRUM_LLM_API_KEY / leftover Moonshot). "
            "CURSOR_API_KEY / CURSOR_AGENT_API_KEY / FACTORY_CURSOR_API_KEY "
            "arm Background Agents (FACTORY_CODE_PROVIDER=cursor), not chat "
            "completions. Cursor has no public /v1/chat/completions."
        )
    return cfg


def _has_kimi_credentials() -> bool:
    """A Kimi-specific key, or the shared key with no Claude-specific key.

    The shared ``CEREBRUM_LLM_API_KEY`` is ambiguous by design. It counts as
    Kimi unless the environment says otherwise, which keeps every existing
    single-key deployment on Kimi exactly as before.
    """
    if _env_first("CEREBRUM_CHAT_LLM_API_KEY", "CEREBRUM_FACTORY_LLM_API_KEY", "KIMI_API_KEY"):
        return True
    shared = _env_first("CEREBRUM_LLM_API_KEY")
    if not shared:
        return False
    # A shared key alongside an explicit Anthropic key belongs to Kimi only if
    # no Anthropic-specific key is set; otherwise it is genuinely ambiguous and
    # we still prefer Kimi (see _detect_provider) -- this only decides whether
    # Kimi credentials are considered *present at all*.
    return True


def _has_claude_credentials() -> bool:
    return bool(_env_first("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"))


def _detect_provider() -> str:
    """Auto-detect HTTP chat provider from configured credentials.

    Cursor BA keys never select a chat-completions host. ``CURSOR_*`` arms
    Factory coding only. ``CEREBRUM_CHAT_*`` / leftover Kimi credentials
    win chat.

    Kimi wins whenever those credentials are present, even if Claude
    credentials are also present. Claude is auto-selected only when it is
    the only HTTP provider configured.
    """
    chat_base = _kimi_base_url("CEREBRUM_CHAT")
    if (
        _scoped_path_endpoint("CEREBRUM_CHAT")
        or _resolve_kimi_api_key(chat_base, "CEREBRUM_CHAT")
        or _has_kimi_credentials()
    ):
        return "kimi"
    if _has_claude_credentials():
        return "claude"
    return ""


def _deepseek_config(*prefixes: str) -> Dict[str, Any]:
    """DeepSeek OpenAI-compatible chat/architect config.

    A present path-scoped triple (``CEREBRUM_FACTORY_LLM_*`` /
    ``CEREBRUM_CHAT_LLM_*``) owns host and model; otherwise
    ``DEEPSEEK_API_KEY`` + the default deepseek endpoint.
    """
    scoped = _scoped_path_endpoint(*prefixes)
    if scoped:
        # The fallback belongs to this DeepSeek endpoint: a path-scoped
        # ``{PREFIX}_LLM_FALLBACK_MODEL`` the operator set for it, else the
        # DeepSeek default. The shared/legacy fallback slots the scoped
        # triple would otherwise inherit are another provider's, so they are
        # not read here -- rather than read and then rejected by spelling.
        fallback = _env_first(
            *(f"{prefix}_LLM_FALLBACK_MODEL" for prefix in prefixes),
            default=DEFAULT_FALLBACK_MODEL,
        )
        return {
            "provider": "deepseek",
            "api_key": scoped["api_key"],
            "base_url": scoped["base_url"],
            "model": scoped["model"],
            "fallback_model": fallback,
            "mock": False,
            "temperature": _llm_temperature(),
        }
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return {
            "provider": "deepseek",
            "api_key": "",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "fallback_model": "deepseek-chat",
            "mock": False,
            "temperature": _llm_temperature(),
            "error": "deepseek selected but DEEPSEEK_API_KEY is not set",
        }
    return {
        "provider": "deepseek",
        "api_key": api_key,
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "fallback_model": "deepseek-chat",
        "mock": False,
        "temperature": _llm_temperature(),
    }


def _config_for_provider(provider: str, *prefixes: str) -> Dict[str, Any]:
    if provider == "deepseek":
        return _deepseek_config(*prefixes)
    if provider == "kimi":
        return _kimi_config(*prefixes)
    if provider == "claude":
        return _claude_config(*prefixes)
    if provider == "cursor":
        return _cursor_http_config(*prefixes)
    raise RuntimeError(f"unsupported LLM provider: {provider!r}")


def _openai_compatible_chat_cfg(
    *,
    provider: str,
    api_key: str,
    base_url: str,
    model: str = "",
    fallback_model: str = "",
    mock: bool = False,
) -> Dict[str, Any] | None:
    """Usable OpenAI-compatible Floor chat config, or None.

    ``api.cursor.com`` is never a chat-completions host. A present
    ``CEREBRUM_CHAT_LLM_API_KEY`` plus Moonshot / OpenRouter / other
    OpenAI-compatible base must not collapse to ``provider=""``.
    """
    if not api_key or not base_url or _is_cursor_chat_host(base_url):
        return None
    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "model": model or _default_model(),
        "fallback_model": fallback_model or _default_fallback_model(),
        "mock": mock,
        "temperature": _llm_temperature(),
    }


def get_llm_config() -> Dict[str, Any]:
    """Return resolved LLM config for the chat/chain_generator path."""
    explicit = normalise_provider(os.getenv("LLM_PROVIDER", ""))
    provider = explicit or _detect_provider()

    if provider in SUPPORTED_PROVIDERS:
        cfg = _config_for_provider(provider, "CEREBRUM_CHAT")
        # Mock+no-key wipe is only for *auto-detected* kit chat (stay offline).
        # An explicit ``LLM_PROVIDER=cursor`` must keep provider="cursor".
        # Wiping it to "" made Floor chat log "No LLM provider configured"
        # while /ready still counted CURSOR_* as llm_configured.
        if cfg["mock"] and not cfg["api_key"] and not explicit:
            return {
                "provider": "",
                "api_key": "",
                "base_url": "",
                "model": "",
                "mock": True,
            }
        return cfg

    # Last resort: CEREBRUM_CHAT_LLM_* is a live OpenAI-compatible triple
    # even when LLM_PROVIDER is missing or an unknown alias. Empty provider
    # here is what _call_llm logs as "No LLM provider configured".
    scoped = _scoped_path_endpoint("CEREBRUM_CHAT")
    if scoped:
        rescued = _openai_compatible_chat_cfg(
            provider="kimi",
            api_key=scoped.get("api_key", ""),
            base_url=scoped.get("base_url", ""),
            model=scoped.get("model", ""),
            fallback_model=scoped.get("fallback_model", ""),
            mock=_truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
        )
        if rescued:
            return rescued

    return {
        "provider": "",
        "api_key": "",
        "base_url": "",
        "model": "",
        # Preserve mock intent for callers even when no live provider is selected
        "mock": _truthy("CEREBRUM_LLM_MOCK") or _truthy("KIMI_MOCK"),
    }


def get_factory_llm_config() -> Dict[str, Any]:
    """Factory Product Architect HTTP draft — CEREBRUM_CHAT when cursor, leftover Kimi, or Claude.

    ``LLM_PROVIDER=cursor`` names the coding family (Cursor BA / cli-pivot).
    HTTP architect draft uses ``CEREBRUM_CHAT_*`` / leftover Moonshot.
    Cursor keys never go to ``api.cursor.com/v1/chat/completions``.

    Fails closed per provider: asking for a provider whose HTTP key is absent
    is an error carrying that provider's name.
    """
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    explicit = normalise_provider(raw)
    if raw and explicit not in SUPPORTED_PROVIDERS:
        return {
            "provider": "",
            "api_key": "",
            "base_url": "",
            "model": "",
            "mock": False,
            "error": (
                f"Factory architect supports {' / '.join(SUPPORTED_PROVIDERS)} "
                f"(cursor is accepted); LLM_PROVIDER={raw} is not allowed for "
                "product architecture"
            ),
        }

    provider = explicit or _detect_provider() or "kimi"

    if provider == "deepseek":
        return _deepseek_config("CEREBRUM_FACTORY")

    if provider == "cursor":
        cfg = _cursor_http_config("CEREBRUM_FACTORY")
        if cfg["mock"]:
            return cfg
        if not cfg["api_key"]:
            cfg["error"] = (
                "LLM_PROVIDER=cursor: HTTP architect draft uses "
                "CEREBRUM_CHAT_LLM_API_KEY (or CEREBRUM_FACTORY_LLM_API_KEY / "
                "leftover Moonshot). CURSOR_API_KEY / CURSOR_AGENT_API_KEY / "
                "FACTORY_CURSOR_API_KEY arm Background Agents, not chat "
                "completions. Cursor has no public /v1/chat/completions."
            )
        return cfg

    if provider == "claude":
        cfg = _factory_claude_config("CEREBRUM_FACTORY")
        if cfg["mock"]:
            return cfg
        if not cfg["api_key"]:
            cfg["error"] = (
                "Factory architect was asked for Claude but ANTHROPIC_API_KEY "
                "(or CEREBRUM_LLM_API_KEY) is not set; refusing to fall back to "
                "another provider — set the key or unset LLM_PROVIDER"
            )
        return cfg

    cfg = _factory_kimi_config("CEREBRUM_FACTORY")
    if cfg["mock"]:
        return cfg
    if not cfg["api_key"]:
        if _is_openrouter_base(str(cfg.get("base_url", ""))):
            cfg["error"] = (
                "Factory architect base_url is OpenRouter but OPENROUTER_API_KEY "
                "(or FACTORY_LLM_FALLBACK_API_KEY) is not set; "
                "KIMI_API_KEY / CEREBRUM_LLM_API_KEY are Moonshot credentials "
                "and will 401 on this host"
            )
        elif _names_kimi_explicitly():
            # The operator asked for this provider by name, so name THEIR variable.
            cfg["error"] = (
                "Factory architect was asked for Kimi/Moonshot (LLM_PROVIDER or a "
                "KIMI_* credential) but KIMI_API_KEY (or CEREBRUM_LLM_API_KEY) is not "
                "set; set the key, or unset LLM_PROVIDER to use the DeepSeek default. "
                "Set KIMI_MOCK=1 for tests"
            )
        else:
            # Name the vars an operator should actually set. This said "requires
            # KIMI_API_KEY" unconditionally -- pointing whoever read it at a retired
            # provider, which is the one thing a fail-closed message must not do.
            cfg["error"] = (
                "Factory architect has no LLM credential: set "
                "CEREBRUM_FACTORY_LLM_API_KEY, or CEREBRUM_CHAT_LLM_API_KEY, or "
                "CEREBRUM_LLM_API_KEY (DeepSeek is the default host; set the matching "
                "*_LLM_BASE_URL for any other provider). Set KIMI_MOCK=1 for tests. "
                "DEEPSEEK_API_KEY is the coding CLI's credential and does not arm "
                "this path."
            )
    return cfg


# -- Cross-provider fallback leg (OpenRouter) ------------------------------
#
# The rule above -- never fall through to another provider -- is about COST
# SURPRISE: a silent switch that moves a bill is a product bug. That rule
# governs which provider is PRIMARY, and it stands.
#
# A fallback leg is a different question. It runs only after the primary has
# already failed, so the alternative it is measured against is not "a cheaper
# provider", it is "no artifact at all". And a leg pinned to a ``:free`` slug
# cannot move a bill, so the reason for the prohibition does not reach it.
#
# The invariant is preserved literally: a non-free fallback model is refused
# unless FACTORY_LLM_FALLBACK_ALLOW_PAID=1 says otherwise. Setting
# OPENROUTER_API_KEY is the explicit act that arms the leg -- there is no
# path where it turns on by itself.
#
# OPENROUTER_BASE_URL / DEFAULT_OPENROUTER_FALLBACK_MODEL are defined at
# module top so chat config can reuse them. The ``:free`` suffix is
# load-bearing -- plain ``minimax/minimax-m3`` is the paid tier.

SUPPORTED_FALLBACK_PROVIDERS = ("openrouter",)


def _is_free_slug(model: str) -> bool:
    """OpenRouter's model id grammar is ``author/slug[:variant]``; the
    zero-priced tier is the ``free`` variant. The id is split by that grammar
    and its variant field compared -- not searched for a substring."""
    _slug, sep, variant = model.strip().lower().rpartition(":")
    return bool(sep) and variant == "free"


def get_factory_fallback_leg() -> Dict[str, Any] | None:
    """The cross-provider fallback leg for the factory coder, or None.

    Returns a leg with its OWN endpoint and credentials -- unlike
    ``fallback_model``, which only swaps the model name while reusing the
    primary's base_url and key. Crossing vendors needs the whole triple.

    Returns None when unarmed (no key, or explicitly disabled). Returns a leg
    carrying ``error`` when it is armed but misconfigured, so the caller can
    report why the leg did not run instead of silently having no fallback.
    """
    if _env_first("FACTORY_LLM_FALLBACK_PROVIDER", default="openrouter").lower() in {
        "none",
        "off",
        "disabled",
    }:
        return None

    api_key = _env_first("OPENROUTER_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY")
    if not api_key:
        return None

    model = _env_first(
        "FACTORY_LLM_FALLBACK_MODEL",
        "OPENROUTER_MODEL",
        default=DEFAULT_OPENROUTER_FALLBACK_MODEL,
    )

    leg: Dict[str, Any] = {
        "provider": "openrouter",
        "api_key": api_key,
        "base_url": _env_first("OPENROUTER_BASE_URL", default=OPENROUTER_BASE_URL),
        "model": model,
        "temperature": _llm_temperature(),
        # Whether this leg can spend money. Two decisions hang off it, and
        # both are the same cost argument, so they share one flag rather than
        # drifting apart:
        #
        #  * a 429 is retried per Retry-After (free slugs are served from a
        #    shared upstream pool and answer 429 within seconds -- measured
        #    live 2026-08-25 on the first call with an unused key); retrying
        #    a PAID 429 would spend money on the same answer;
        #  * the leg may run with no primary configured at all, which would
        #    be a cost surprise if the leg were billable.
        "is_free": _is_free_slug(model),
    }

    if not _is_free_slug(model) and not _truthy("FACTORY_LLM_FALLBACK_ALLOW_PAID"):
        leg["error"] = (
            f"fallback model {model!r} is not a ':free' slug and "
            "FACTORY_LLM_FALLBACK_ALLOW_PAID is not set; refusing to arm a "
            "fallback leg that can spend money without being asked to"
        )
    return leg


def active_provider() -> str:
    return get_llm_config()["provider"]
