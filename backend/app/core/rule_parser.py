import json
import logging
import os
from typing import Any, List, Dict
import httpx

from .llm_config import get_llm_config, active_provider

logger = logging.getLogger(__name__)


def parse_rules(rule_texts: List[str]) -> List[Dict[str, str]]:
    """Convert free-text rules into structured rule objects.

    The rule model returns each rule's typed trigger and action. Without one,
    a rule is kept verbatim and NOT interpreted: nothing reads the user's
    words to guess which part is a condition and which an action (the
    if/then/always word split that used to do that is deleted -- owner rule:
    decisions never come from prose).
    """
    if not rule_texts:
        return []

    provider = active_provider()
    if provider:
        try:
            return _parse_with_llm(provider, rule_texts)
        except Exception as exc:
            logger.warning("LLM rule parsing failed, falling back to deterministic parser: %s", exc)

    return _parse_naive(rule_texts)


def _parse_naive(rule_texts: List[str]) -> List[Dict[str, str]]:
    """Keep each rule verbatim, uninterpreted (no rule model answered)."""
    parsed = []
    for text in rule_texts:
        text = text.strip()
        if not text:
            continue
        parsed.append({
            "raw": text,
            "trigger": "",
            "action": "",
            "code_snippet": _generate_snippet(text, "", ""),
        })
    return parsed


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


def _call_openai_sync(base_url: str, api_key: str, model: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Call an OpenAI-compatible chat completion endpoint synchronously."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    # Omit temperature unless explicitly configured: reasoning models
    # (kimi-k2.x) reject any explicit value other than 1.
    raw_temp = os.getenv("LLM_TEMPERATURE", "").strip()
    if raw_temp:
        try:
            payload["temperature"] = float(raw_temp)
        except ValueError:
            pass
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


def _parse_with_llm(provider: str, rule_texts: List[str]) -> List[Dict[str, str]]:
    """Ask the configured Kimi LLM to extract trigger/action/snippet for each rule."""
    cfg = get_llm_config()
    system_prompt = (
        "You are a business-rule parser. Given a list of free-text rules, "
        "return a JSON object with a single key 'rules' containing a list of objects. "
        "Each object must have the keys: raw (string), trigger (string), action (string), "
        "code_snippet (string, a short Python-like snippet)."
    )
    user_prompt = json.dumps({"rules": rule_texts}, ensure_ascii=False)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if provider in ("moonshot", "kimi"):
        result = _call_openai_sync(cfg["base_url"], cfg["api_key"], cfg["model"], messages)
    else:
        return _parse_naive(rule_texts)

    parsed = []
    for item in result.get("rules", []):
        raw = item.get("raw", "")
        if not raw:
            continue
        trigger = str(item.get("trigger") or "")
        action = str(item.get("action") or "")
        parsed.append({
            "raw": raw,
            "trigger": trigger,
            "action": action,
            "code_snippet": item.get("code_snippet") or _generate_snippet(raw, trigger, action),
        })
    return parsed or _parse_naive(rule_texts)


def _generate_snippet(text: str, trigger: str, action: str) -> str:
    """A placeholder Python snippet from the rule's TYPED trigger and action.

    A rule without both (no rule model answered) becomes a comment only --
    it is recorded, not executed on a guess.
    """
    comment = " ".join(text.splitlines())
    if not (trigger and action):
        return f"# Rule (not interpreted -- no rule model answered): {comment}\n"
    return (
        f"# Rule: {comment}\n"
        f"if context.matches({trigger!r}):\n"
        f"    context.apply_action({action!r})\n"
    )
