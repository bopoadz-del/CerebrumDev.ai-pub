"""Unified LLM provider configuration for CerebrumDev.ai.

Two paths are configured independently:

* Chat / chain_generator / platform_chat_llm path: ``get_llm_config()``
  - Preferred: ``CEREBRUM_CHAT_LLM_API_KEY / BASE_URL / MODEL`` (authoritative)
  - Fallback: the shared ``CEREBRUM_LLM_*`` slot / ``ANTHROPIC_*``
  - Cursor keys never select a chat-completions host. Cursor has no public
    ``/v1/chat/completions`` (Cloud Agents is ``/v0/agents`` only).
  - Do not invent OpenRouter as the chat host when ``OPENROUTER_API_KEY``
    is absent. A leftover ``CEREBRUM_LLM_BASE_URL=openrouter.ai`` plus a
    dead OpenRouter key must not steal a healthy shared-slot primary
    when ``CEREBRUM_CHAT_*`` is unset.

* Factory Product Architect / platform CLI path: ``get_factory_llm_config()``
  - Preferred: ``CEREBRUM_FACTORY_LLM_API_KEY / BASE_URL / MODEL``
  - Fallback: ``CEREBRUM_CHAT_*`` then ``CEREBRUM_LLM_*``
  - ``LLM_PROVIDER=cursor`` HTTP draft uses ``CEREBRUM_CHAT_*``, not Cursor.
  - Factory coding / Cursor BA stays on ``CURSOR_*``
    (``FACTORY_CODE_PROVIDER=cursor`` / cli-pivot).

``LLM_PROVIDER`` accepts the names in :data:`PROVIDER_TABLE` whose role is
``primary`` (``claude`` also answers to ``anthropic``). Any other name is a
configuration error and no call is made. The retired vendor is not in the
table: not as a provider, an alias, a default, a fallback or a host.

``LLM_PROVIDER=cursor`` names the Factory coding family (Cursor BA keys).
It is valid and render.yaml may pin it. HTTP Floor chat uses
``CEREBRUM_CHAT_LLM_*`` — those Cursor keys are not chat-completions
credentials.

With ``LLM_PROVIDER`` unset, the provider is the ONE vendor whose
credentials are present (:func:`_credential_providers`): the shared
OpenAI-compatible slots (``CEREBRUM_CHAT_LLM_*`` / ``CEREBRUM_FACTORY_LLM_*``
/ ``CEREBRUM_LLM_*``) resolve to ``deepseek``, whose host is the default
when no base URL is set; an Anthropic key resolves to ``claude``. None, or
both, is an explicit error naming the settings to set -- a second key in
the environment never picks a vendor by itself.

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
#: They used to be a retired provider's host and model. That is not a cosmetic
#: default: `_scoped_path_endpoint` returns on the FIRST prefix with an API key and
#: takes host+model from that prefix alone, so a prefix with a key but no BASE_URL
#: fell through to the retired host and a DeepSeek key was POSTed there, 401'd, and
#: the Floor shipped deterministic-template blueprints while every key in the
#: environment was valid. The retired provider is now out of the provider table
#: entirely (owner rule, item F-K1): no member, alias, default, fallback or host.
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
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


def _shared_key(*prefixes: str) -> str:
    """Resolve an OpenAI-compatible API key: prefixed env vars, then the shared slot."""
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_API_KEY")
    candidates.append("CEREBRUM_LLM_API_KEY")
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
    """Resolve an OpenRouter key for an OpenRouter base_url.

    Path-prefixed ``*_LLM_API_KEY`` still wins (operator override). The
    shared ``CEREBRUM_LLM_API_KEY`` is excluded -- it belongs to the default
    host and would 401 on OpenRouter.
    """
    candidates: List[str] = [f"{prefix}_LLM_API_KEY" for prefix in prefixes]
    candidates.extend(["OPENROUTER_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY"])
    return _env_first(*candidates)


def _resolve_shared_api_key(base_url: str, *prefixes: str) -> str:
    """Pick the key that belongs to the resolved host.

    When ``base_url`` is OpenRouter, never fall back to the shared key.
    """
    if _is_openrouter_base(base_url):
        return _openrouter_key(*prefixes)
    return _shared_key(*prefixes)


def _env_first_skipping(*names: str, default: str = "", reject=None) -> str:
    """Like ``_env_first`` but skip values ``reject(value)`` says are unusable."""
    skip = reject or (lambda _value: False)
    for name in names:
        value = os.getenv(name, "").strip()
        if value and not skip(value):
            return value
    return default


def _native_shared_key(*prefixes: str) -> str:
    """A shared-slot key that is not configured for OpenRouter.

    Path-prefixed ``*_LLM_API_KEY`` still wins when its own base URL is not
    OpenRouter. A key whose slot points at OpenRouter (``CEREBRUM_LLM_API_KEY``
    beside ``CEREBRUM_LLM_BASE_URL=openrouter``) does not count as a native
    primary; OpenRouter stays the fallback leg.
    """
    slots = [(f"{prefix}_LLM_API_KEY", f"{prefix}_LLM_BASE_URL") for prefix in prefixes]
    slots.append(("CEREBRUM_LLM_API_KEY", "CEREBRUM_LLM_BASE_URL"))
    return _first_off_openrouter(slots)


def _default_base_url() -> str:
    """The DeepSeek host: the one default for a shared slot with no base URL."""
    return DEEPSEEK_BASE_URL


def _shared_base_url(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_BASE_URL")
    candidates.append("CEREBRUM_LLM_BASE_URL")
    reject = _is_openrouter_base if skip_openrouter else None
    return _env_first_skipping(
        *candidates, default=_default_base_url(), reject=reject
    )


def _default_model() -> str:
    """Same rule as the base URL: the model belongs to the default host."""
    return DEFAULT_PRIMARY_MODEL


def _default_fallback_model() -> str:
    return DEFAULT_FALLBACK_MODEL


def _shared_model(*prefixes: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_MODEL")
    candidates.append("CEREBRUM_LLM_MODEL")
    if skip_openrouter:
        slots = [(f"{prefix}_LLM_MODEL", f"{prefix}_LLM_BASE_URL") for prefix in prefixes]
        slots.append(("CEREBRUM_LLM_MODEL", "CEREBRUM_LLM_BASE_URL"))
        return _first_off_openrouter(slots, default=_default_model())
    return _env_first(*candidates, default=_default_model())


def _shared_fallback_model(*prefixes: str, default: str, skip_openrouter: bool = False) -> str:
    candidates: List[str] = []
    for prefix in prefixes:
        candidates.append(f"{prefix}_LLM_FALLBACK_MODEL")
    candidates.append("CEREBRUM_LLM_FALLBACK_MODEL")
    if skip_openrouter:
        slots = [
            (f"{prefix}_LLM_FALLBACK_MODEL", f"{prefix}_LLM_BASE_URL") for prefix in prefixes
        ]
        slots.append(("CEREBRUM_LLM_FALLBACK_MODEL", "CEREBRUM_LLM_BASE_URL"))
        return _first_off_openrouter(slots, default=default)
    return _env_first(*candidates, default=default)


def _non_cursor_base(value: str, default: str | None = None) -> str:
    if value and not _is_cursor_chat_host(value):
        return value
    return _default_base_url() if default is None else default


def _scoped_path_endpoint(*prefixes: str) -> Dict[str, str] | None:
    """Use the first path-scoped ``*_LLM_API_KEY`` as an authoritative triple.

    ``CEREBRUM_CHAT_LLM_*`` is Floor chat. Leftover ``CURSOR_*`` must not
    replace a present scoped key. ``api.cursor.com`` is never a chat host —
    Cursor has no public ``/v1/chat/completions``.
    """
    for prefix in prefixes:
        key = os.getenv(f"{prefix}_LLM_API_KEY", "").strip()
        if not key:
            continue
        base = _non_cursor_base(os.getenv(f"{prefix}_LLM_BASE_URL", "").strip(), default="")
        if not base:
            base = _non_cursor_base(_env_first("CEREBRUM_LLM_BASE_URL"))
        model = os.getenv(f"{prefix}_LLM_MODEL", "").strip() or _env_first(
            "CEREBRUM_LLM_MODEL", default=_default_model()
        )
        fallback = os.getenv(f"{prefix}_LLM_FALLBACK_MODEL", "").strip() or _env_first(
            "CEREBRUM_LLM_FALLBACK_MODEL", default=_default_fallback_model()
        )
        return {
            "api_key": key,
            "base_url": base,
            "model": model,
            "fallback_model": fallback,
        }
    return None


def _resolve_shared_primary(*prefixes: str) -> Dict[str, str]:
    """Pick the HTTP chat/architect endpoint from the shared slots.

    A present path-scoped key (``CEREBRUM_CHAT_LLM_API_KEY``) owns host and
    model, including an operator-set OpenRouter base.

    When the scoped key is absent, a shared key not configured for OpenRouter
    owns the host, so a leftover ``CEREBRUM_LLM_BASE_URL=openrouter.ai``
    cannot 401 through a missing ``OPENROUTER_API_KEY``.
    """
    scoped = _scoped_path_endpoint(*prefixes)
    if scoped:
        return scoped
    native = _native_shared_key(*prefixes)
    if native:
        return {
            "base_url": _shared_base_url(*prefixes, skip_openrouter=True),
            "api_key": native,
            "model": _shared_model(*prefixes, skip_openrouter=True),
            "fallback_model": _shared_fallback_model(
                *prefixes, default=_default_fallback_model(), skip_openrouter=True
            ),
        }
    base_url = _non_cursor_base(_shared_base_url(*prefixes))
    return {
        "base_url": base_url,
        "api_key": _resolve_shared_api_key(base_url, *prefixes),
        "model": _shared_model(*prefixes),
        "fallback_model": _shared_fallback_model(*prefixes, default=_default_fallback_model()),
    }


def _llm_mock() -> bool:
    """The offline switch for the OpenAI-compatible path: ``CEREBRUM_LLM_MOCK``.

    It selects no provider, host or model.
    """
    return _truthy("CEREBRUM_LLM_MOCK")


def llm_mock_enabled() -> bool:
    """Public read of the offline switch, for ``/ready``."""
    return _llm_mock()


def _claude_key(*prefixes: str) -> str:
    """Resolve a Claude API key. Mirrors _shared_key's prefix-then-shared order.

    ``CEREBRUM_LLM_API_KEY`` is shared with the deepseek slot deliberately: it is the
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
    the shared slot alone."""
    slots = [(f"{prefix}_LLM_API_KEY", f"{prefix}_LLM_BASE_URL") for prefix in prefixes]
    slots.extend([("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"), ("CLAUDE_API_KEY", "ANTHROPIC_BASE_URL")])
    if normalise_provider(os.getenv("LLM_PROVIDER", "")) == "claude":
        # Declared provider: its key slots are its own (see _native_shared_key).
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
    """Factory config. Same shape as the deepseek config, different provider."""
    cfg = _claude_config(*prefixes)
    cfg["fallback_model"] = _claude_fallback_model(
        *prefixes, default="claude-haiku-4-5-20251001"
    )
    return cfg


def _llm_temperature() -> float | None:
    """Optional temperature override (LLM_TEMPERATURE).

    None means: do not send a temperature at all — the provider applies the
    model default. Required for reasoning models which reject
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
    """``anthropic`` -> ``claude``. Others pass through (and are checked
    against :data:`PROVIDER_TABLE` by the caller)."""
    name = (name or "").strip().lower()
    if name == "anthropic":
        return "claude"
    return name


#: THE provider table (WK-2). Every name a config may resolve and every name
#: an HTTP model caller (the coder, the architect, the model ladder) may
#: dispatch on is a key here, with the one role it may play: ``primary`` (may
#: be selected by ``LLM_PROVIDER`` or by credentials) or ``fallback`` (only
#: the cross-provider leg of :func:`get_factory_fallback_leg`). A name that
#: is not a key is refused before any call.
#: ``wire`` is the request shape a caller must build for that provider:
#: ``openai`` (``/chat/completions``) or ``anthropic`` (the Messages API).
PROVIDER_TABLE: Dict[str, Dict[str, str]] = {
    "deepseek": {"role": "primary", "wire": "openai"},
    "cursor": {"role": "primary", "wire": "openai"},
    "claude": {"role": "primary", "wire": "anthropic"},
    "openrouter": {"role": "fallback", "wire": "openai"},
}

SUPPORTED_PROVIDERS = tuple(n for n, row in PROVIDER_TABLE.items() if row["role"] == "primary")

#: Every table name a caller may POST to with the OpenAI-compatible shape.
OPENAI_WIRE_PROVIDERS = tuple(n for n, row in PROVIDER_TABLE.items() if row["wire"] == "openai")

#: The primary names an HTTP caller accepts: the same tuple, by identity, so
#: the config can never resolve a provider name the coder then refuses.
HTTP_CALL_PROVIDERS = SUPPORTED_PROVIDERS


def _cursor_http_config(*prefixes: str) -> Dict[str, Any]:
    """HTTP talk for ``LLM_PROVIDER=cursor`` uses ``CEREBRUM_CHAT_LLM_*``.

    Cursor has no public ``/v1/chat/completions`` (Cloud Agents is
    ``/v0/agents``). ``CURSOR_*`` keys arm Background Agents
    (``FACTORY_CODE_PROVIDER=cursor`` / cli-pivot). Floor chat uses the
    OpenAI-compatible ``CEREBRUM_CHAT_LLM_*`` triple already on Render,
    then the shared ``CEREBRUM_LLM_*`` slot. Never invent OpenRouter
    when ``OPENROUTER_API_KEY`` is absent.
    """
    # CEREBRUM_CHAT_LLM_API_KEY is authoritative for Floor HTTP even when
    # LLM_PROVIDER=cursor (coding-family name). Read it first; the shared
    # CEREBRUM_LLM_* slot only fills gaps. Never use CURSOR_* here.
    endpoint = _scoped_path_endpoint("CEREBRUM_CHAT", *prefixes) or _resolve_shared_primary(
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
                _env_first("CEREBRUM_LLM_BASE_URL")
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
            "(or CEREBRUM_LLM_API_KEY). "
            "CURSOR_API_KEY / CURSOR_AGENT_API_KEY / FACTORY_CURSOR_API_KEY "
            "arm Background Agents (FACTORY_CODE_PROVIDER=cursor), not chat "
            "completions. Cursor has no public /v1/chat/completions."
        )
    return cfg


def _has_shared_credentials() -> bool:
    """A key in one of the shared OpenAI-compatible slots (the deepseek row)."""
    return bool(
        _env_first(
            "CEREBRUM_CHAT_LLM_API_KEY",
            "CEREBRUM_FACTORY_LLM_API_KEY",
            "CEREBRUM_LLM_API_KEY",
        )
    )


def _has_claude_credentials() -> bool:
    return bool(_env_first("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"))


#: The settings an operator sets to choose a provider. Names only.
_PROVIDER_SETTINGS_HINT = (
    "set LLM_PROVIDER to one of {names}, with that provider's key "
    "(deepseek: CEREBRUM_FACTORY_LLM_API_KEY / CEREBRUM_CHAT_LLM_API_KEY / "
    "CEREBRUM_LLM_API_KEY / DEEPSEEK_API_KEY; claude: ANTHROPIC_API_KEY; "
    "cursor: CEREBRUM_CHAT_LLM_API_KEY)"
)


def _credential_providers() -> List[str]:
    """Every primary provider whose credentials are present, in table order.

    Cursor BA keys never select a chat-completions host (``CURSOR_*`` arms
    Factory coding only). An OpenRouter base with its key counts for the
    shared slot, because that is where the operator pointed it.
    """
    found: List[str] = []
    chat_base = _shared_base_url("CEREBRUM_CHAT")
    if (
        _scoped_path_endpoint("CEREBRUM_CHAT")
        or _resolve_shared_api_key(chat_base, "CEREBRUM_CHAT")
        or _has_shared_credentials()
    ):
        found.append("deepseek")
    if _has_claude_credentials():
        found.append("claude")
    return found


def _detect_provider() -> str:
    """The one provider the credentials name, or ``''``.

    ``''`` when no credentials are present OR when two vendors' are: a second
    key in the environment never chooses a vendor by itself. See
    :func:`_unresolved_provider_error` for the message.
    """
    found = _credential_providers()
    if len(found) == 1:
        return found[0]
    return ""


def _unresolved_provider_error() -> str:
    """Why no provider was resolved with ``LLM_PROVIDER`` unset. Names only."""
    found = _credential_providers()
    hint = _PROVIDER_SETTINGS_HINT.format(names=" / ".join(SUPPORTED_PROVIDERS))
    if len(found) > 1:
        return (
            "LLM_PROVIDER is unset and credentials for more than one provider "
            f"are present ({' and '.join(found)}); the choice is ambiguous: {hint}"
        )
    return f"LLM_PROVIDER is unset and no provider credentials are present: {hint}"


def _deepseek_config(*prefixes: str) -> Dict[str, Any]:
    """DeepSeek OpenAI-compatible chat/architect config.

    A present path-scoped triple (``CEREBRUM_FACTORY_LLM_*`` /
    ``CEREBRUM_CHAT_LLM_*``) owns host and model; otherwise
    ``DEEPSEEK_API_KEY`` + the default deepseek endpoint; otherwise the
    shared ``CEREBRUM_LLM_*`` slot (host defaults to DeepSeek).
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
            "mock": _llm_mock(),
            "temperature": _llm_temperature(),
        }
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        shared = _resolve_shared_primary(*prefixes)
        cfg: Dict[str, Any] = {
            "provider": "deepseek",
            "api_key": shared["api_key"],
            "base_url": shared["base_url"],
            "model": shared["model"],
            "fallback_model": shared["fallback_model"],
            "mock": _llm_mock(),
            "temperature": _llm_temperature(),
        }
        if not cfg["api_key"]:
            cfg["error"] = (
                "deepseek selected but no key is set: set "
                + " or ".join(f"{prefix}_LLM_API_KEY" for prefix in prefixes)
                + (" or " if prefixes else "")
                + "CEREBRUM_LLM_API_KEY or DEEPSEEK_API_KEY"
            )
        return cfg
    return {
        "provider": "deepseek",
        "api_key": api_key,
        "base_url": DEEPSEEK_BASE_URL,
        "model": DEFAULT_PRIMARY_MODEL,
        "fallback_model": DEFAULT_FALLBACK_MODEL,
        "mock": _llm_mock(),
        "temperature": _llm_temperature(),
    }


def _config_for_provider(provider: str, *prefixes: str) -> Dict[str, Any]:
    if provider == "deepseek":
        return _deepseek_config(*prefixes)
    if provider == "claude":
        return _claude_config(*prefixes)
    if provider == "cursor":
        return _cursor_http_config(*prefixes)
    raise RuntimeError(f"unsupported LLM provider: {provider!r}")


def _no_provider_cfg(error: str, *, config_error: bool = False) -> Dict[str, Any]:
    """No provider resolved: an explicit error, and no call can be made.

    ``config_error`` marks a WRONG setting (a name outside the table, or two
    vendors' credentials with no name) as opposed to an absent one. A caller
    may run a zero-cost fallback leg when nothing is configured; it must not
    when the configuration is wrong, or the leg hides the error.
    """
    return {
        "provider": "",
        "api_key": "",
        "base_url": "",
        "model": "",
        # Preserve mock intent for callers even when no live provider is selected
        "mock": _llm_mock(),
        "error": error,
        "config_error": config_error,
    }


def _unresolved_provider_cfg() -> Dict[str, Any]:
    """``LLM_PROVIDER`` unset and no single vendor named by credentials."""
    return _no_provider_cfg(
        _unresolved_provider_error(), config_error=len(_credential_providers()) > 1
    )


def _unknown_provider_error(raw: str) -> str:
    return (
        f"LLM_PROVIDER={raw} is not in the provider table; "
        + _PROVIDER_SETTINGS_HINT.format(names=" / ".join(SUPPORTED_PROVIDERS))
    )


def get_llm_config() -> Dict[str, Any]:
    """Return resolved LLM config for the chat/chain_generator path.

    A provider is named by ``LLM_PROVIDER`` or by the ONE vendor whose
    credentials are present. A name outside the table, no credentials, or two
    vendors' credentials is a config error (``provider=''`` plus ``error``):
    no caller makes a call.
    """
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    explicit = normalise_provider(raw)
    if explicit and explicit not in SUPPORTED_PROVIDERS:
        return _no_provider_cfg(_unknown_provider_error(raw), config_error=True)
    provider = explicit or _detect_provider()
    if not provider:
        return _unresolved_provider_cfg()

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


def get_factory_llm_config() -> Dict[str, Any]:
    """Factory Product Architect HTTP draft config.

    ``LLM_PROVIDER=cursor`` names the coding family (Cursor BA / cli-pivot).
    HTTP architect draft uses ``CEREBRUM_CHAT_*`` / the shared slot.
    Cursor keys never go to ``api.cursor.com/v1/chat/completions``.

    Fails closed: a name outside the table, an unresolved or ambiguous
    provider, or a named provider whose key is absent is an error naming the
    settings to set.
    """
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    explicit = normalise_provider(raw)
    if raw and explicit not in SUPPORTED_PROVIDERS:
        cfg = _no_provider_cfg(_unknown_provider_error(raw), config_error=True)
        cfg["mock"] = False
        return cfg

    provider = explicit or _detect_provider()
    if not provider:
        return _unresolved_provider_cfg()

    if provider == "cursor":
        cfg = _cursor_http_config("CEREBRUM_FACTORY")
        if cfg["mock"]:
            return cfg
        if not cfg["api_key"]:
            cfg["error"] = (
                "LLM_PROVIDER=cursor: HTTP architect draft uses "
                "CEREBRUM_CHAT_LLM_API_KEY (or CEREBRUM_FACTORY_LLM_API_KEY / "
                "CEREBRUM_LLM_API_KEY). CURSOR_API_KEY / CURSOR_AGENT_API_KEY / "
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

    # Factory first, then the chat slot (module docstring): the same slots
    # :func:`_credential_providers` counts, so a key that names deepseek is
    # a key this config reads.
    cfg = _deepseek_config("CEREBRUM_FACTORY", "CEREBRUM_CHAT")
    if cfg["mock"] or not cfg.get("error"):
        return cfg
    if _is_openrouter_base(str(cfg.get("base_url", ""))):
        cfg["error"] = (
            "Factory architect base_url is OpenRouter but OPENROUTER_API_KEY "
            "(or FACTORY_LLM_FALLBACK_API_KEY) is not set; "
            "CEREBRUM_LLM_API_KEY is the default host's credential "
            "and will 401 on this host"
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

SUPPORTED_FALLBACK_PROVIDERS = tuple(
    n for n, row in PROVIDER_TABLE.items() if row["role"] == "fallback"
)


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

    The leg's provider is gated against :data:`PROVIDER_TABLE`: a name whose
    role is not ``fallback`` returns a leg carrying ``error`` and no
    endpoint, so no caller can POST to it.
    """
    name = _env_first("FACTORY_LLM_FALLBACK_PROVIDER", default="openrouter").lower()
    if name in {"none", "off", "disabled"}:
        return None
    if name not in SUPPORTED_FALLBACK_PROVIDERS:
        return {
            "provider": name,
            "api_key": "",
            "base_url": "",
            "model": "",
            "is_free": False,
            "error": (
                f"FACTORY_LLM_FALLBACK_PROVIDER={name} is not a fallback provider "
                f"in the provider table ({' / '.join(SUPPORTED_FALLBACK_PROVIDERS)}"
                ", or none)"
            ),
        }

    api_key = _env_first("OPENROUTER_API_KEY", "FACTORY_LLM_FALLBACK_API_KEY")
    if not api_key:
        return None

    model = _env_first(
        "FACTORY_LLM_FALLBACK_MODEL",
        "OPENROUTER_MODEL",
        default=DEFAULT_OPENROUTER_FALLBACK_MODEL,
    )

    leg: Dict[str, Any] = {
        "provider": name,
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


# -- Boot validation ---------------------------------------------------------


class ProviderConfigError(RuntimeError):
    """The provider settings are wrong. Raised at start, not at the first call."""


_FALLBACK_OFF = {"none", "off", "disabled"}


def provider_setting_errors() -> List[str]:
    """Every error in the provider SETTINGS, by setting name. No values of keys.

    Checks what is wrong, not what is absent: ``LLM_PROVIDER`` naming a
    provider outside :data:`PROVIDER_TABLE`; ``LLM_PROVIDER`` unset with two
    vendors' credentials present; ``FACTORY_LLM_FALLBACK_PROVIDER`` naming a
    provider whose table role is not ``fallback``. A box with no key at all
    still boots (template fallback in dev).
    """
    errors: List[str] = []
    raw = os.getenv("LLM_PROVIDER", "").strip().lower()
    if raw and normalise_provider(raw) not in SUPPORTED_PROVIDERS:
        errors.append(_unknown_provider_error(raw))
    if not raw and len(_credential_providers()) > 1:
        errors.append(_unresolved_provider_error())
    leg_name = os.getenv("FACTORY_LLM_FALLBACK_PROVIDER", "").strip().lower()
    if leg_name and leg_name not in _FALLBACK_OFF and leg_name not in SUPPORTED_FALLBACK_PROVIDERS:
        errors.append(
            f"FACTORY_LLM_FALLBACK_PROVIDER={leg_name} is not a fallback provider "
            f"in the provider table ({' / '.join(SUPPORTED_FALLBACK_PROVIDERS)}, or none)"
        )
    return errors


def validate_provider_at_boot() -> None:
    """Fail the start when the provider settings are wrong (see above)."""
    errors = provider_setting_errors()
    if errors:
        raise ProviderConfigError(
            "LLM provider settings are invalid: " + " | ".join(errors)
        )
