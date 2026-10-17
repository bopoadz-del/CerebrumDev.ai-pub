#!/usr/bin/env python3
"""Local OpenAI-compatible stand-in for Factory Floor UI tests.

This is NOT a model. It answers the same `/v1/chat/completions` shape the
architect and coding agent call, so a cloud VM without a paid Kimi key can
still exercise: brief → architect_llm blueprint → runner WRITER →
agent-written artifacts.

Production never uses this. Point the backend at it only in dev:

    python3 scripts/dev_factory_llm.py --port 18765
    CEREBRUM_LLM_API_KEY=local-dev-stub-key \\
    CEREBRUM_LLM_BASE_URL=http://127.0.0.1:18765/v1 \\
    CEREBRUM_LLM_MODEL=local-dev-stub \\
    CEREBRUM_FACTORY_LLM_MODEL=local-dev-stub \\
    CEREBRUM_LLM_FALLBACK_MODEL=local-dev-stub \\
    ALLOW_ANONYMOUS_DEV=1 ENV=dev ./venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HANDLER_BODY = (
    'action = (payload or {}).get("action", "status")\n'
    'return {"ok": True, "capability": CAPABILITY_ID, "action": action}\n'
)

ROUTE_BODY = (
    "result = handle(payload or {})\n"
    'return {"ok": True, "capability": CAPABILITY_ID, "result": result}\n'
)

MODEL_SPEC = {
    "entity": "record",
    "fields": [
        {"name": "reference", "type": "str", "required": True},
        {"name": "status", "type": "str", "required": False},
    ],
}

BLUEPRINT = {
    "product_name": "Clinic Scheduling Platform",
    "vertical": "clinic_scheduling",
    "summary": "Appointments, staff roster and audit trails for a clinic.",
    "capabilities": [
        {
            "id": "appointments",
            "description": "Schedule and track clinic appointments",
            "block_ids": ["audit"],
            "strategy_hint": "GENERATE",
        },
        {
            "id": "audit",
            "description": "Audit trails for clinical operations",
            "block_ids": ["audit"],
            "strategy_hint": "REUSE",
        },
        {
            "id": "staff_roster",
            "description": "Clinician roster and coverage",
            "block_ids": [],
            "strategy_hint": "GENERATE",
        },
    ],
}


def _system_text(messages: list) -> str:
    parts = []
    for m in messages or []:
        if isinstance(m, dict) and m.get("role") == "system":
            parts.append(str(m.get("content") or ""))
    return "\n".join(parts)


def completion_for(messages: list, wants_json: bool) -> str:
    system = _system_text(messages).lower()
    last_user = ""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            last_user = str(m.get("content") or "").lower()
            break
    if "factory floor" in system or "start_coder" in system:
        if "approve" in last_user or "go ahead" in last_user or "looks good" in last_user:
            return json.dumps({"action": "start_coder", "brief": "", "refine_message": "", "message": ""})
        if "pending blueprint: yes" in last_user and not any(
            w in last_user for w in ("build me", "create a", "new platform")
        ):
            return json.dumps(
                {
                    "action": "reply",
                    "message": "Feature list is ready. Approve it to start the coding agent.",
                }
            )
        return json.dumps({"action": "draft_platform", "brief": last_user})
    if "collector kernel" in system or "capability↔block" in system or "capability-block" in system:
        return json.dumps(
            {
                "reviews": [
                    {
                        "capability_id": "appointments",
                        "block_ids": ["audit"],
                        "verdict": "endorse",
                        "reason": "audit trail fits clinical appointments",
                    }
                ]
            }
        )
    if "tester kernel" in system or "additional domain" in system:
        return json.dumps(
            {
                "cases": [
                    {
                        "capability_id": "appointments",
                        "payload": {"reference": "alt"},
                        "expect": "accept",
                        "reason": "another valid reference",
                    }
                ]
            }
        )
    if "product architect" in system or wants_json and "available blocks" in system:
        return json.dumps(BLUEPRINT)
    if "data model" in system or '"entity"' in system:
        return json.dumps(MODEL_SPEC)
    if "def handle" in system or "handle(payload" in system:
        return HANDLER_BODY
    if "def endpoint" in system or "endpoint(payload" in system:
        return ROUTE_BODY
    return "# Clinic scheduling platform\n\nGenerated locally by the Factory coding-agent path.\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys_stderr = __import__("sys").stderr
        sys_stderr.write("[dev-factory-llm] " + (fmt % args) + "\n")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            body = {}
        messages = body.get("messages") or []
        wants_json = (body.get("response_format") or {}).get("type") == "json_object"
        content = completion_for(messages, wants_json)
        payload = {
            "id": "chatcmpl-dev-stub",
            "object": "chat.completion",
            "model": body.get("model") or "local-dev-stub",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        blob = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self) -> None:  # noqa: N802
        blob = b'{"ok":true,"stub":"dev-factory-llm"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"dev-factory-llm listening on http://{args.host}:{args.port}/v1", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
