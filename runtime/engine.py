"""Engine profile — one function: complete(messages). Kimi-only cloud_api."""
from __future__ import annotations
import json, os, urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List
from . import stop

@dataclass(frozen=True)
class Engine:
    profile: str
    model: str
    complete: Callable[[List[Dict[str, str]]], str]

def _env(*names: str) -> str:
    return next((v for n in names if (v := os.getenv(n, "").strip())), "")

def _chat(base_url: str, api_key: str, model: str, messages: List[Dict[str, str]]) -> str:
    payload = {"model": model, "messages": messages}
    # Omit temperature unless set; reasoning models reject explicit values != 1.
    if (t := os.getenv("LLM_TEMPERATURE", "").strip()):
        try: payload["temperature"] = float(t)
        except ValueError: pass
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", **({"Authorization": f"Bearer {api_key}"} if api_key else {})}
    req = urllib.request.Request(f"{base_url.rstrip('/')}/chat/completions", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode())["choices"][0]["message"]["content"]

def _profile() -> Dict[str, str]:
    name = _env("ENGINE_PROFILE") or "cloud_api"
    if name != "cloud_api":
        stop.halt("engine_profile_unknown", {"profile": name}, "ENGINE_PROFILE: cloud_api only (Kimi).")
    return {"name": name, "base": _env("CEREBRUM_LLM_BASE_URL") or "https://api.moonshot.cn/v1",
            "key": _env("CEREBRUM_LLM_API_KEY"), "model": _env("CEREBRUM_LLM_MODEL") or "moonshot-v1-8k",
            "fallback_model": _env("CEREBRUM_LLM_FALLBACK_MODEL") or "kimi-k2.5-code"}

def resolve() -> Engine:
    p = _profile()
    if not p["key"]:
        stop.halt("engine_key_missing", {"profile": p["name"]}, "Set CEREBRUM_LLM_API_KEY for the Kimi engine.")
    def complete(messages: List[Dict[str, str]]) -> str:
        last_err = ""
        for model in (p["model"], p["fallback_model"]):
            if not model or model == p["model"] and model != p["fallback_model"]:
                continue
            try: return _chat(p["base"], p["key"], model, messages)
            except Exception as exc: last_err = str(exc)  # noqa: BLE001
        stop.halt("engine_failed_twice", {"profile": p["name"], "error": last_err}, "Restore engine connectivity, then resume the run.")
        return ""
    return Engine(profile=p["name"], model=p["model"], complete=complete)
