#!/usr/bin/env python3
"""Phase-4 full platform sweep for CerebrumDev.ai.

Probes every route family on the live deployment, unauthenticated first,
then authenticated. Writes a markdown report to stdout or --out.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class ProbeResult:
    name: str
    method: str
    path: str
    want: str
    status: Optional[int]
    body: Any
    elapsed: float
    verdict: str  # PASS / FAIL / SKIP / ERROR
    note: str = ""


def _parse_sse(raw: str) -> Dict[str, Any]:
    """Parse SSE stream: return {events:[{event,data}], text: joined deltas}."""
    events: List[Dict[str, Any]] = []
    deltas: List[str] = []
    # Split on double-newline (event boundaries)
    for chunk in re.split(r"\n\n+", raw.strip()):
        if not chunk.strip():
            continue
        ev_match = re.search(r"^event:\s*(.+)$", chunk, re.MULTILINE)
        data_match = re.search(r"^data:\s*(.+)$", chunk, re.MULTILINE | re.DOTALL)
        if not ev_match or not data_match:
            continue
        event_type = ev_match.group(1).strip()
        data_raw = data_match.group(1).strip()
        # data field is json.dumps(string); decode once to get the inner string
        try:
            inner = json.loads(data_raw)
        except json.JSONDecodeError:
            inner = data_raw
        # For JSON payloads, decode again
        parsed: Any = inner
        if isinstance(inner, str):
            try:
                parsed = json.loads(inner)
            except json.JSONDecodeError:
                parsed = inner
        events.append({"event": event_type, "data": parsed})
        if event_type == "delta":
            deltas.append(str(parsed))
    return {
        "events": events,
        "text": "".join(deltas).strip(),
        "first_event": events[0]["event"] if events else "none",
        "first_data": events[0]["data"] if events else None,
    }


class SweepClient:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.results: List[ProbeResult] = []

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
    ) -> Tuple[Optional[int], Any, float]:
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        if token:
            h["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, method=method, data=data, headers=h)
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode(errors="replace")
                elapsed = time.monotonic() - start
                try:
                    return resp.status, json.loads(raw), elapsed
                except json.JSONDecodeError:
                    return resp.status, raw, elapsed
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            elapsed = time.monotonic() - start
            try:
                return e.code, json.loads(raw), elapsed
            except json.JSONDecodeError:
                return e.code, raw, elapsed
        except Exception as e:
            elapsed = time.monotonic() - start
            return None, str(e), elapsed

    def probe(
        self,
        name: str,
        method: str,
        path: str,
        want_status: Optional[int] = None,
        want_in_body: Optional[str] = None,
        want_not_in_body: Optional[str] = None,
        body: Optional[Dict[str, Any]] = None,
        token: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 60,
        check: Optional[Callable[[int, Any], bool]] = None,
    ) -> Any:
        status, resp, elapsed = self._request(method, path, body, token, headers, timeout)
        verdict = "FAIL"
        note = ""
        body_text = json.dumps(resp) if not isinstance(resp, str) else resp

        if status is None:
            verdict = "ERROR"
            note = f"request exception: {resp[:200]}"
        elif want_status is not None and status != want_status:
            verdict = "FAIL"
            note = f"expected {want_status}, got {status}"
        elif want_in_body and want_in_body not in body_text:
            verdict = "FAIL"
            note = f"expected body to contain {want_in_body!r}"
        elif want_not_in_body and want_not_in_body in body_text:
            verdict = "FAIL"
            note = f"expected body NOT to contain {want_not_in_body!r}"
        elif check is not None and not check(status, resp):
            verdict = "FAIL"
            note = "custom check failed"
        else:
            verdict = "PASS"

        self.results.append(
            ProbeResult(
                name=name,
                method=method,
                path=path,
                want=f"status={want_status}"
                + (f" body∋{want_in_body!r}" if want_in_body else "")
                + (f" body∌{want_not_in_body!r}" if want_not_in_body else ""),
                status=status,
                body=resp,
                elapsed=elapsed,
                verdict=verdict,
                note=note,
            )
        )
        return resp

    def sse_chat(
        self, session_id: str, message: str, token: str, timeout: int = 180
    ) -> Dict[str, Any]:
        h = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Accept": "text/event-stream",
        }
        data = json.dumps({"message": message}).encode()
        req = urllib.request.Request(
            f"{self.base}/v1/sessions/{session_id}/chat", method="POST", data=data, headers=h
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode(errors="replace")
                elapsed = time.monotonic() - start
                parsed = _parse_sse(raw)
                return {
                    "status": resp.status,
                    "raw": raw[:600],
                    "elapsed": elapsed,
                    **parsed,
                }
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            elapsed = time.monotonic() - start
            return {"status": e.code, "raw": raw, "elapsed": elapsed, "events": [], "text": raw}
        except Exception as e:
            elapsed = time.monotonic() - start
            return {"status": None, "raw": str(e), "elapsed": elapsed, "events": [], "text": str(e)}


def run_sweep(base_url: str) -> List[ProbeResult]:
    client = SweepClient(base_url)
    pwd = "SweepPass123!"

    # --- Public / health ------------------------------------------------------
    client.probe("health", "GET", "/health", want_status=200)
    client.probe("ready", "GET", "/ready", want_status=200)

    # --- Unauthenticated on protected families --------------------------------
    for name, path in [
        ("sessions unauth", "/v1/sessions/"),
        ("me unauth", "/v1/auth/me"),
        ("domains unauth", "/v1/domains/"),
        ("billing status unauth", "/v1/billing/status"),
    ]:
        client.probe(name, "GET", path, want_status=401)

    # --- Auth flow ------------------------------------------------------------
    email = f"sweep-{uuid.uuid4().hex[:8]}@factory.dev"
    reg = client.probe(
        "register",
        "POST",
        "/v1/auth/register",
        want_status=201,
        body={"email": email, "password": pwd},
    )
    token = reg.get("login_token") if isinstance(reg, dict) else None
    if not token:
        client.results.append(
            ProbeResult(
                name="auth dependency",
                method="-",
                path="-",
                want="login_token present",
                status=None,
                body=None,
                elapsed=0,
                verdict="FAIL",
                note="login_token missing; remaining authenticated probes skipped",
            )
        )
        return client.results

    client.probe("me", "GET", "/v1/auth/me", want_status=200, token=token)

    # --- Sessions -------------------------------------------------------------
    sess = client.probe("create session", "POST", "/v1/sessions/", want_status=200, body={}, token=token)
    session_id = sess.get("session_id") if isinstance(sess, dict) else None
    client.probe("list sessions", "GET", "/v1/sessions/", want_status=200, token=token)
    client.probe(
        "get session",
        "GET",
        f"/v1/sessions/{session_id}",
        want_status=200,
        token=token,
        check=lambda s, b: isinstance(b, dict) and b.get("session_id") == session_id,
    )

    # --- Chat: platform brief -------------------------------------------------
    brief_msg = (
        "Build me a vineyard management platform for a family winery: "
        "track fermentation tanks, barrel inventory across two cellars, "
        "harvest scheduling by sugar readings, and club member shipments."
    )
    brief = client.sse_chat(session_id, brief_msg, token, timeout=180)
    brief_data = brief.get("first_data") if isinstance(brief.get("first_data"), dict) else {}
    caps = brief_data.get("blueprint", {}).get("capabilities", []) if isinstance(brief_data, dict) else []
    client.results.append(
        ProbeResult(
            name="chat platform brief",
            method="POST",
            path=f"/v1/sessions/{session_id}/chat",
            want="200 SSE event: blueprint, source=drafted, >=3 caps",
            status=brief.get("status"),
            body=brief_data,
            elapsed=brief.get("elapsed", 0),
            verdict="PASS"
            if brief.get("status") == 200
            and brief.get("first_event") == "blueprint"
            and isinstance(brief_data, dict)
            and brief_data.get("source") == "drafted"
            and len(caps) >= 3
            else "FAIL",
            note=f"event={brief.get('first_event')}, caps={len(caps)}, source={brief_data.get('source') if isinstance(brief_data, dict) else None}",
        )
    )

    # --- Product state --------------------------------------------------------
    client.probe(
        "product state",
        "GET",
        f"/v1/sessions/{session_id}/product",
        want_status=200,
        token=token,
    )

    # --- Chat: approve --------------------------------------------------------
    approve = client.sse_chat(session_id, "approve", token, timeout=300)
    client.results.append(
        ProbeResult(
            name="chat approve",
            method="POST",
            path=f"/v1/sessions/{session_id}/chat",
            want="200 SSE event: generation",
            status=approve.get("status"),
            body=approve.get("first_data"),
            elapsed=approve.get("elapsed", 0),
            verdict="PASS"
            if approve.get("status") == 200 and approve.get("first_event") == "generation"
            else "FAIL",
            note=f"event={approve.get('first_event')}, text={approve.get('text', '')[:100]}",
        )
    )

    # --- Product package ------------------------------------------------------
    pkg = client.probe(
        "product package",
        "GET",
        f"/v1/sessions/{session_id}/product/package",
        want_status=200,
        token=token,
        timeout=120,
    )
    if isinstance(pkg, str) and pkg.startswith("PK"):
        client.results[-1].verdict = "PASS"
        client.results[-1].note = "zip PK magic present"
    elif client.results[-1].verdict == "PASS":
        client.results[-1].verdict = "FAIL"
        client.results[-1].note = "response 200 but not a zip (no PK magic)"

    # --- Conversational chat --------------------------------------------------
    conv = client.sse_chat(session_id, "what is the status of my product?", token, timeout=120)
    conv_text = (conv.get("text") or "").lower()
    # Catch invented claims ("it is deployed/live at X", "download link: http..."),
    # not honest action prompts ("available for deployment").
    invented_claim = (
        ("deployed" in conv_text or "live at" in conv_text or "download link" in conv_text)
        or ("http://" in conv_text or "https://" in conv_text)
    )
    client.results.append(
        ProbeResult(
            name="chat conversational status",
            method="POST",
            path=f"/v1/sessions/{session_id}/chat",
            want="200 SSE, grounded reply, no invented URL/deployed claim",
            status=conv.get("status"),
            body=conv.get("text"),
            elapsed=conv.get("elapsed", 0),
            verdict="PASS"
            if conv.get("status") == 200 and not invented_claim
            else "FAIL",
            note=f"events={[e['event'] for e in conv.get('events', [])]}, text={conv.get('text', '')[:120]}",
        )
    )

    # --- Store-backed chat ----------------------------------------------------
    blocks_chat = client.sse_chat(session_id, "what blocks can I add?", token, timeout=120)
    blocks_text = (blocks_chat.get("text") or "").lower()
    client.results.append(
        ProbeResult(
            name="chat store blocks",
            method="POST",
            path=f"/v1/sessions/{session_id}/chat",
            want="200 SSE, store-backed block list",
            status=blocks_chat.get("status"),
            body=blocks_chat.get("text"),
            elapsed=blocks_chat.get("elapsed", 0),
            verdict="PASS"
            if blocks_chat.get("status") == 200
            and ("block" in blocks_text or "optional" in blocks_text or "primitive" in blocks_text)
            else "FAIL",
            note=f"events={[e['event'] for e in blocks_chat.get('events', [])]}, text={blocks_chat.get('text', '')[:120]}",
        )
    )

    # --- Domains --------------------------------------------------------------
    client.probe("domains list", "GET", "/v1/domains/", want_status=200, token=token)
    client.probe(
        "domains virgin",
        "GET",
        "/v1/domains/virgin",
        want_status=200,
        token=token,
        check=lambda s, b: isinstance(b, dict) and "editions" in b,
    )
    client.probe(
        "domains source-packs",
        "GET",
        "/v1/domains/source-packs",
        want_status=200,
        token=token,
        check=lambda s, b: isinstance(b, dict) and "packs" in b,
    )
    client.probe(
        "domains rag-packs",
        "GET",
        "/v1/domains/rag-packs",
        want_status=200,
        token=token,
        check=lambda s, b: isinstance(b, dict) and "packs" in b,
    )

    # --- Golden steward (Floor draft) -----------------------------------------
    client.probe(
        "golden steward",
        "POST",
        f"/v1/sessions/{session_id}/product/draft",
        want_status=200,
        token=token,
        body={
            "brief": "Generate Projectsix private estate operations",
            "vertical_hint": "estate",
        },
        check=lambda s, b: isinstance(b, dict)
        and len((b.get("blueprint") or {}).get("capabilities") or []) >= 10,
    )

    # --- Mode kit config ------------------------------------------------------
    client.probe(
        "mode kit aviation",
        "POST",
        f"/v1/sessions/{session_id}/product/mode",
        want_status=200,
        token=token,
        body={"mode": "kit"},
    )

    # --- Cross-account isolation ----------------------------------------------
    email2 = f"sweep-{uuid.uuid4().hex[:8]}@factory.dev"
    reg2 = client.probe(
        "register second account",
        "POST",
        "/v1/auth/register",
        want_status=201,
        body={"email": email2, "password": pwd},
    )
    token2 = reg2.get("login_token") if isinstance(reg2, dict) else None
    client.probe(
        "cross-account session read",
        "GET",
        f"/v1/sessions/{session_id}",
        want_status=404,
        token=token2,
    )

    # --- Parked status endpoints ----------------------------------------------
    client.probe(
        "parked resident status",
        "GET",
        "/v1/resident/status",
        want_status=200,
        token=token,
        check=lambda s, b: isinstance(b, dict) and b.get("enabled") is False,
    )
    client.probe(
        "parked workbench status",
        "GET",
        "/v1/workbench/status",
        want_status=200,
        token=token,
        check=lambda s, b: isinstance(b, dict) and b.get("build_mode_enabled") is False,
    )
    client.probe(
        "parked change-requests status",
        "GET",
        "/v1/change-requests/status",
        want_status=200,
        token=token,
        check=lambda s, b: isinstance(b, dict) and b.get("intake_enabled") is False,
    )

    # --- Parked action endpoints ----------------------------------------------
    client.probe(
        "parked action resident observe",
        "GET",
        "/v1/resident/observe",
        want_status=503,
        token=token,
    )
    client.probe(
        "parked action workbench run",
        "POST",
        "/v1/workbench/run",
        want_status=503,
        token=token,
        body={"request_id": "sweep-req-001"},
    )
    client.probe(
        "parked action change-requests queue",
        "GET",
        "/v1/change-requests/queue",
        want_status=503,
        token=token,
    )

    # --- Billing --------------------------------------------------------------
    client.probe("billing status", "GET", "/v1/billing/status", want_status=200, token=token)
    client.probe(
        "billing checkout",
        "POST",
        "/v1/billing/checkout",
        want_status=503,
        token=token,
        body={},
        want_in_body="stripe_not_configured",
    )

    # --- Drive / deploy / train -----------------------------------------------
    client.probe(
        "drive status",
        "GET",
        f"/v1/sessions/{session_id}/drive/status",
        want_status=200,
        token=token,
    )
    client.probe(
        "deploy status",
        "GET",
        f"/v1/sessions/{session_id}/deploy/status",
        want_status=200,
        token=token,
    )
    client.probe(
        "train status",
        "GET",
        f"/v1/sessions/{session_id}/train/status",
        want_status=200,
        token=token,
    )

    return client.results


def markdown_report(base_url: str, results: List[ProbeResult]) -> str:
    now = datetime.now(timezone.utc).isoformat()
    passed = sum(1 for r in results if r.verdict == "PASS")
    failed = sum(1 for r in results if r.verdict == "FAIL")
    errors = sum(1 for r in results if r.verdict == "ERROR")
    skipped = sum(1 for r in results if r.verdict == "SKIP")

    lines = [
        "# CerebrumDev.ai Phase-4 Platform Sweep",
        "",
        f"- **Base URL:** {base_url}",
        f"- **Timestamp (UTC):** {now}",
        f"- **Probes:** {len(results)}",
        f"- **PASS:** {passed} | **FAIL:** {failed} | **ERROR:** {errors} | **SKIP:** {skipped}",
        "",
        "## Results",
        "",
        "| Verdict | Probe | Method | Path | Want | Status | Elapsed | Note |",
        "|---------|-------|--------|------|------|--------|---------|------|",
    ]
    for r in results:
        body_summary = ""
        if isinstance(r.body, dict):
            body_summary = json.dumps(r.body)[:120]
        elif isinstance(r.body, str):
            body_summary = r.body[:120]
        lines.append(
            f"| {r.verdict} | {r.name} | {r.method} | `{r.path}` | {r.want} | {r.status} | {r.elapsed:.2f}s | {r.note or body_summary} |"
        )

    lines.extend(["", "## Summary", ""])
    if failed + errors == 0:
        lines.append("All probes passed.")
    else:
        lines.append("### Failed / Errored probes")
        for r in results:
            if r.verdict in ("FAIL", "ERROR"):
                lines.append(f"- **{r.name}** ({r.method} {r.path}): {r.note}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="CerebrumDev.ai live platform sweep")
    parser.add_argument("--base-url", default="https://api.cerebrum-dev.com")
    parser.add_argument("--out", default="-", help="Output file (- for stdout)")
    args = parser.parse_args()

    results = run_sweep(args.base_url)
    report = markdown_report(args.base_url, results)

    if args.out == "-":
        print(report)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report written to {args.out}")

    return 1 if any(r.verdict in ("FAIL", "ERROR") for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
