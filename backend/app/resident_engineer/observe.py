"""L1 Observe — health, log/error summary, block lockfile check, anomaly flags."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError, HTTPError
from urllib.request import urlopen

from app.resident_engineer.dna_loader import dna_entity_refs, load_product_dna
from app.resident_engineer.modes import AutonomyLevel
from app.resident_engineer.injection_guard import sanitize_untrusted, strip_instruction_patterns

_ERROR_LINE = re.compile(r"(error|exception|traceback|critical|fatal)", re.I)


def probe_health(url: str = "http://127.0.0.1:8000/health", timeout: float = 2.0) -> Dict[str, Any]:
    """HTTP health probe (no shell)."""
    try:
        with urlopen(url, timeout=timeout) as resp:  # nosec B310 — fixed health URL pattern
            body = resp.read().decode("utf-8", errors="replace")[:4000]
            payload = sanitize_untrusted(json.loads(body)) if body.strip().startswith("{") else {"raw": strip_instruction_patterns(body)}
            return {"ok": 200 <= resp.status < 300, "status_code": resp.status, "body": payload}
    except HTTPError as exc:
        return {"ok": False, "status_code": exc.code, "error": str(exc.reason)}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "status_code": None, "error": str(exc)}


def summarize_logs(log_text: str, *, limit: int = 20) -> Dict[str, Any]:
    """Summarize log/error lines — text treated as untrusted."""
    text = strip_instruction_patterns(log_text or "")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    error_lines = [ln for ln in lines if _ERROR_LINE.search(ln)]
    counts = Counter()
    for ln in error_lines:
        key = ln[:120]
        counts[key] += 1
    top = [{"line": k, "count": v} for k, v in counts.most_common(limit)]
    return {
        "total_lines": len(lines),
        "error_lines": len(error_lines),
        "top_errors": top,
    }


def check_block_lockfile_vs_store(
    bundle: Dict[str, Any],
    store_versions: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Compare DNA block_lockfile pins to provided Store versions (advisory).

    When ``store_versions`` is None, report pins only — no invented Store state.
    """
    lock = (bundle.get("files") or {}).get("block_lockfile.json") or {}
    pins = lock.get("pins") or []
    store_versions = store_versions or {}
    findings: List[Dict[str, Any]] = []
    for pin in pins:
        if not isinstance(pin, dict):
            continue
        bid = pin.get("block_id")
        store_ver = store_versions.get(bid) if bid else None
        findings.append(
            {
                "block_id": bid,
                "pinned_source": pin.get("source"),
                "store_version": store_ver,
                "status": "unknown" if store_ver is None else "compared",
            }
        )
    return {
        "blocks_commit": lock.get("blocks_commit"),
        "pins": findings,
        "store_versions_provided": bool(store_versions),
    }


def flag_security_anomalies(bundle: Dict[str, Any], log_summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Heuristic anomaly flags (no execution)."""
    flags: List[Dict[str, Any]] = []
    policy = (bundle.get("files") or {}).get("security_policy.json") or {}
    if policy.get("pii_in_learning") is True:
        flags.append(
            {
                "code": "pii_in_learning_enabled",
                "severity": "high",
                "message": "DNA security_policy marks pii_in_learning=true",
            }
        )
    if (log_summary.get("error_lines") or 0) > 50:
        flags.append(
            {
                "code": "elevated_error_rate",
                "severity": "medium",
                "message": f"error_lines={log_summary.get('error_lines')}",
            }
        )
    heal = policy.get("allowlisted_heal_actions") or []
    if not heal:
        flags.append(
            {
                "code": "empty_heal_allowlist",
                "severity": "info",
                "message": "No L2 heal actions allowlisted in DNA yet",
            }
        )
    return flags


def observe(
    product_root: Optional[Path] = None,
    *,
    health_url: str = "http://127.0.0.1:8000/health",
    log_text: str = "",
    store_versions: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """L1 Observe report grounded in Product DNA."""
    bundle = load_product_dna(product_root)
    refs = dna_entity_refs(bundle)
    health = probe_health(health_url)
    logs = summarize_logs(log_text)
    lock = check_block_lockfile_vs_store(bundle, store_versions)
    anomalies = flag_security_anomalies(bundle, logs)
    return {
        "level": AutonomyLevel.L1.value,
        "mode": "resident",
        "product_id": refs.get("product_id"),
        "dna_refs": refs,
        "health": health,
        "logs": logs,
        "block_versions": lock,
        "security_anomalies": anomalies,
    }
