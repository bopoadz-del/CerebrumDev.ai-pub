import json
import httpx
import logging
from typing import List, Dict, Any, Optional

from .feature_mapper import fetch_block_registry
from .block_taxonomy import BUILTIN_BLOCKS, OPTIONAL_BLOCKS
from .llm_config import (
    get_factory_fallback_leg,
    get_llm_config,
    _is_cursor_chat_host,
    _is_openrouter_base,
)
from .source_pack_loader import get_source_pack

logger = logging.getLogger(__name__)


def _build_source_pack_context(domain: str) -> str:
    """Return a source-pack guidance section for the system prompt.

    Returns an empty string when the domain has no source pack or when the
    shelf cannot be loaded, so chain generation never breaks because of
    metadata issues.
    """
    try:
        pack = get_source_pack(domain)
    except Exception:
        logger.exception("Failed to load source pack for domain=%s", domain)
        return ""

    if not pack:
        return ""

    blocks = ", ".join(pack.get("blocks", []))

    return (
        f"\nDomain guidance for {domain}:\n"
        f"Expert role: {pack.get('expert_prompt', '')}\n"
        f"Workflow: {pack.get('workflow', '')}\n"
        f"Recommended blocks: {blocks}\n"
        "Prefer the recommended source-pack blocks when proposing a chain for this domain. "
        "The domain v2 block is the primary domain-specific analysis block. "
        "For domain-specific analysis, prefer the domain v2 block over generic built-in blocks "
        "such as llm_enhancer, knowledge, memory, vector_search, or validation_pipeline. "
        "Use generic built-ins only to support ingestion, retrieval, validation, memory, or UI behavior. "
        "Do not replace the domain v2 block with generic built-ins when the user's request is clearly domain-specific. "
        "Include chat when the user needs a conversational interface or Q&A. "
        "Use recommended blocks only if they are present in the available block registry. "
        "Never invent block IDs.\n"
    )


def _build_system_prompt(
    available_blocks: List[Dict[str, Any]],
    domain: str,
    docs_summary: str,
    session_state: str = "",
) -> str:
    optional_available = [b for b in available_blocks if b.get("name") in OPTIONAL_BLOCKS]
    optional_list = "\n".join(
        f"- {b.get('name')}: {b.get('description', 'No description')}" for b in optional_available
    ) or "- (none)"
    docs_section = f"\nUploaded documents summary:\n{docs_summary}\n" if docs_summary else ""
    state_section = (
        "\nSession state (authoritative — trust this over your own inference):\n"
        f"{session_state}\n"
        if session_state
        else ""
    )
    source_pack_section = _build_source_pack_context(domain)
    return (
        "You are an AI solution architect for CerebrumDev.ai. "
        "Your job is to help users configure and optionally extend their sovereign AI instance.\n\n"
        f"Domain: {domain}\n"
        "The platform already includes these built-in blocks automatically: "
        f"{', '.join(BUILTIN_BLOCKS)}.\n"
        "You do NOT need to propose these in the chain; they are always available.\n\n"
        "Optional Fork primitives the user can add on top:\n"
        f"{optional_list}\n"
        f"{source_pack_section}"
        f"{state_section}"
        f"{docs_section}\n"
        "When responding:\n"
        "1. Be concise and conversational.\n"
        "2. Ask clarifying questions if the request is vague.\n"
        "3. If the user asks what blocks are available, tell them the domain kit + built-ins are already included, "
        "and list the optional primitives they can add.\n"
        "4. Only propose optional blocks in the chain when the user explicitly asks for that capability.\n"
        "5. When you have enough information, propose a chain in the exact JSON format below.\n"
        "6. Also extract any business rules the user mentions (e.g., 'always flag urgent RFIs') as a list of strings.\n\n"
        "Chain JSON format:\n"
        '{"blocks": [{"id": "<block_name>", "params": {...}}], "connections": [{"from": 0, "to": 1}]}\n\n'
        "Return your response as JSON with three top-level keys:\n"
        '{"message": "<conversational reply to user>", "chain": <chain JSON or null>, "rules": ["rule 1", "rule 2"]}\n'
        "Only include 'chain' when you are ready to propose one.\n"
        "7. Never claim that you or the platform have built, generated, deployed, exported, "
        "or modified anything. Platform actions are reported only by explicit system events "
        "in the chat (blueprint / generation cards).\n"
        "8. Answer status questions strictly from the Session state section above. If something "
        "is not stated there, say you do not have that information and point the user to the "
        "Factory Floor flow.\n"
        "9. Never invent URLs, prices, credentials, dates, product names, or deployment targets."
    )


def _extract_json(text: str) -> Dict[str, Any]:
    """Strip Markdown fences and parse the first JSON object in *text*."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    text = text.strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output")
    depth = 0
    end = start
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
                continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if depth != 0:
        raise ValueError("Unbalanced JSON object in model output")
    return json.loads(text[start:end])


async def _call_openai_compatible(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float | None = None,
    fallback_model: str | None = None,
) -> Dict[str, Any]:
    """Call an OpenAI-compatible Kimi chat completion endpoint.

    If *fallback_model* is provided and the primary call fails, retry once
    with the fallback model before giving up.
    """
    if _is_cursor_chat_host(base_url):
        raise RuntimeError(
            "Refusing api.cursor.com/v1/chat/completions — Cursor has no "
            "public chat-completions API (Cloud Agents /v0/agents only). "
            "Floor chat uses CEREBRUM_CHAT_LLM_*."
        )
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if _is_openrouter_base(base_url):
        headers["HTTP-Referer"] = "https://cerebrumdev.ai"
        headers["X-Title"] = "CerebrumDev Floor"

    async def _try(m: str) -> Dict[str, Any]:
        payload = {
            "model": m,
            "messages": messages,
        }
        # OpenRouter free models often reject json_object. Only pin the
        # format on native Moonshot-style hosts.
        if not _is_openrouter_base(base_url):
            payload["response_format"] = {"type": "json_object"}
        # Omit temperature unless explicitly configured: reasoning models
        # (kimi-k2.x) reject any explicit temperature other than 1.
        if temperature is not None:
            payload["temperature"] = temperature
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return _extract_json(content) if isinstance(content, str) else content

    try:
        return await _try(model)
    except Exception:
        if not fallback_model or fallback_model == model:
            raise
        return await _try(fallback_model)


async def _call_openrouter_fallback(
    messages: List[Dict[str, str]],
) -> Dict[str, Any] | None:
    """Optional OpenRouter leg after the primary chat LLM has already failed.

    Returns None when the leg is unarmed, misconfigured, or itself 401s —
    a dead OPENROUTER_API_KEY must not become a second hard failure.
    """
    leg = get_factory_fallback_leg()
    if not leg or leg.get("error"):
        if leg and leg.get("error"):
            logger.warning("OpenRouter chat fallback not armed: %s", leg["error"])
        return None
    try:
        return await _call_openai_compatible(
            leg["base_url"],
            leg["api_key"],
            leg["model"],
            messages,
            leg.get("temperature"),
            None,
        )
    except Exception as exc:
        logger.warning("OpenRouter chat fallback failed: %s", exc)
        return None


_OPENAI_COMPAT_PROVIDERS = frozenset({"deepseek", "moonshot", "kimi", "cursor", "openrouter", ""})


def _is_usable_openai_chat_cfg(cfg: Dict[str, Any]) -> bool:
    """True when Floor chat can POST OpenAI-compatible /chat/completions.

    ``provider="cursor"`` is a coding-family name; HTTP still uses
    ``CEREBRUM_CHAT_LLM_*`` / Moonshot / OpenRouter. Empty provider with a
    usable key+host must not raise "No LLM provider configured".
    ``api.cursor.com`` is never a chat host.
    """
    if not cfg.get("api_key"):
        return False
    base = str(cfg.get("base_url") or "")
    if not base or _is_cursor_chat_host(base):
        return False
    provider = str(cfg.get("provider") or "")
    return provider in _OPENAI_COMPAT_PROVIDERS


async def _call_llm(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Call the primary configured LLM, then the OpenRouter fallback.

    Primary is ``CEREBRUM_CHAT_LLM_*`` (or leftover Moonshot). ``provider``
    ``cursor`` is OpenAI-compatible on that host — never ``api.cursor.com``.
    OpenRouter is fallback only when armed.
    """
    cfg = get_llm_config()
    if cfg.get("mock"):
        raise RuntimeError("LLM mock mode - no network call")
    provider = cfg.get("provider")
    primary_exc: Exception | None = None

    if _is_cursor_chat_host(str(cfg.get("base_url", ""))):
        primary_exc = RuntimeError(
            "Refusing api.cursor.com/v1/chat/completions — use CEREBRUM_CHAT_LLM_*"
        )
    elif _is_usable_openai_chat_cfg(cfg):
        try:
            return await _call_openai_compatible(
                cfg["base_url"],
                cfg["api_key"],
                cfg["model"],
                messages,
                cfg.get("temperature"),
                cfg.get("fallback_model"),
            )
        except Exception as exc:
            primary_exc = exc
            logger.warning(
                "Primary chat LLM failed (%s %s): %s",
                provider,
                cfg.get("base_url"),
                exc,
            )
    elif provider:
        primary_exc = RuntimeError(
            f"Chat path has no caller for provider {provider!r}"
        )

    # Do not retry the same dead OpenRouter host as "fallback".
    if not _is_openrouter_base(str(cfg.get("base_url", ""))):
        fallback = await _call_openrouter_fallback(messages)
        if fallback is not None:
            return fallback

    if primary_exc:
        raise primary_exc
    raise RuntimeError("No LLM provider configured")


def _mock_response(user_message: str, domain: str, available_blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mock response for testing without an LLM.

    Built from the blocks that are AVAILABLE, never from the user's words:
    it used to add OCR when the message said "image"/"scan" and a rule when it
    said "rule"/"always" -- a phrase check over user chat deciding the chain.
    Rules come only from a real model.

    Which blocks, in what order, is what each block DECLARES: those that
    read a caller document first, then those that declare themselves a
    conversation. No block is named here.
    """
    from app.factory.store_kits import CONVERSATION, block_manifest, capability_class

    def _declared(entry: Dict[str, Any]) -> Dict[str, Any]:
        own = {k: entry[k] for k in ("reads", "capability_class") if k in entry}
        return own or block_manifest(entry.get("name"))

    readers, talkers = [], []
    for entry in available_blocks:
        name = entry.get("name")
        if not name:
            continue
        declared = _declared(entry)
        reads = {
            (r.get("kind"), r.get("scope"))
            for r in declared.get("reads") or []
            if isinstance(r, dict)
        }
        if ("file", "input_document") in reads:
            readers.append(name)
        elif capability_class(declared) == CONVERSATION:
            talkers.append(name)
    chain_blocks = [{"id": name, "params": {}} for name in sorted(readers) + sorted(talkers)]
    connections = []

    if len(chain_blocks) > 1:
        connections = [{"from": i, "to": i + 1} for i in range(len(chain_blocks) - 1)]

    rules: List[str] = []

    return {
        "message": (
            f"I've drafted a starter chain for your {domain} workflow. "
            "You can refine it by adding more details or rules."
        ),
        "chain": {"blocks": chain_blocks, "connections": connections} if chain_blocks else None,
        "rules": rules,
    }


_SOFT_FAIL_MESSAGE = (
    "I couldn't reach the configured chat model just now. "
    "Nothing was generated. Try again, or describe the platform you want "
    "and the Factory Floor will continue from there."
)


def _soft_failure_response(exc: Exception) -> Dict[str, Any]:
    """Honest Floor reply when every configured LLM hop failed.

    Must not raise — a dead OpenRouter key used to hard-fail chat with
    ``Failed to generate suggestion: 401`` even when Kimi was configured.
    """
    logger.exception("LLM call failed; returning soft failure: %s", exc)
    return {
        "message": _SOFT_FAIL_MESSAGE,
        "chain": None,
        "rules": [],
    }


async def generate_chain_suggestion(
    domain: str,
    user_message: str,
    chat_history: List[Dict[str, str]],
    docs_summary: str,
    session_state: str = "",
) -> Dict[str, Any]:
    """Generate a chain suggestion and extract rules from a user message.

    session_state: authoritative session facts (blueprint / generation
    status) used to ground the conversational reply — the model must never
    invent platform actions or status.
    """
    registry = await fetch_block_registry()
    available_blocks = list(registry.values())

    messages = [
        {
            "role": "system",
            "content": _build_system_prompt(available_blocks, domain, docs_summary, session_state),
        },
    ]
    messages.extend(chat_history)
    messages.append({"role": "user", "content": user_message})

    cfg = get_llm_config()
    try:
        # Starter-chain mock is test-only. A live keyless / unknown-provider
        # box must not loop "I've drafted a starter chain for your
        # construction workflow" — that hid LLM_PROVIDER=cursor.
        if cfg.get("mock"):
            result = _mock_response(user_message, domain, available_blocks)
        elif _is_usable_openai_chat_cfg(cfg) or (
            cfg.get("provider") and cfg.get("api_key")
        ):
            result = await _call_llm(messages)
        else:
            result = _soft_failure_response(
                RuntimeError(
                    cfg.get("error") or "No LLM provider configured"
                )
            )
    except Exception as exc:
        if cfg.get("mock"):
            logger.warning("Mock LLM path failed, using mock generator: %s", exc)
            result = _mock_response(user_message, domain, available_blocks)
        else:
            result = _soft_failure_response(exc)

    return {
        "message": result.get("message", ""),
        "chain": result.get("chain") or None,
        "rules": result.get("rules") or [],
    }


def validate_chain(chain: Dict[str, Any], available_block_names: List[str]) -> bool:
    """Basic validation: all block IDs exist."""
    if not isinstance(chain, dict):
        return False
    blocks = chain.get("blocks", [])
    if not blocks:
        return False
    for block in blocks:
        bid = block.get("id")
        if not bid or bid not in available_block_names:
            return False
    connections = chain.get("connections", [])
    for conn in connections:
        if not isinstance(conn.get("from"), int) or not isinstance(conn.get("to"), int):
            return False
    return True


def check_chain_quality(
    domain: str, chain: Optional[Dict[str, Any]], validation_passed: bool
) -> Optional[Dict[str, Any]]:
    """Soft post-validation quality check returning metadata only.

    This function never rejects a chain or mutates it. It inspects the
    domain source pack and reports whether the primary domain v2 block is
    present in the proposed chain.
    """
    if not validation_passed:
        return None
    if not chain or not isinstance(chain, dict):
        return None

    try:
        pack = get_source_pack(domain)
    except Exception:
        logger.exception("Failed to load source pack for domain=%s", domain)
        return None

    if not pack:
        return None

    from app.factory.store_kits import SHARED_REASONING, block_capability_class

    domain_v2_block = None
    for block_id in pack.get("blocks", []):
        # A block that declares itself shared reasoning support is not the
        # primary domain-specific v2 block we want to enforce here.
        if (
            isinstance(block_id, str)
            and block_id.endswith("_v2")
            and block_capability_class(block_id) != SHARED_REASONING
        ):
            domain_v2_block = block_id
            break

    if not domain_v2_block:
        return None

    proposed_ids = {block.get("id") for block in chain.get("blocks", [])}
    if domain_v2_block in proposed_ids:
        return {"status": "ok", "warnings": []}

    return {
        "status": "needs_review",
        "warnings": [
            {
                "code": "missing_domain_v2_block",
                "message": (
                    f"This {domain} chain does not include {domain_v2_block}, "
                    f"the primary {domain} analysis block."
                ),
                "suggested_block": domain_v2_block,
            }
        ],
    }
