#!/usr/bin/env python3
"""Run Steward live oracle suite against a deployed URL (cold-start retries OK)."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit


SUITE_PATH = (_estate_kit()
    / "evaluation"
    / "steward_live_oracle_v1.json")


def _http_get(url: str, retries: int = 6) -> Tuple[int, Dict[str, Any] | str]:
    last_err: Optional[str] = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            # Free-tier edge 404 during wake — retry.
            if exc.code in {404, 502, 503, 520} and attempt < retries:
                time.sleep(min(5 * attempt, 30))
                last_err = f"HTTP {exc.code}: {body[:200]}"
                continue
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            time.sleep(min(5 * attempt, 30))
    return 0, last_err or "request failed"


def _eval_case(base: str, case: Dict[str, Any]) -> Dict[str, Any]:
    params = {k: str(v) for k, v in (case.get("params") or {}).items()}
    url = f"{base.rstrip('/')}{case['endpoint']}?{urllib.parse.urlencode(params)}"
    status, body = _http_get(url)
    row: Dict[str, Any] = {
        "id": case["id"],
        "name": case["name"],
        "query": case.get("query") or params.get("q"),
        "expected_layer": case.get("expected_layer"),
        "actual_layer": None,
        "cited_source": None,
        "pass": False,
        "http_status": status,
        "detail": "",
        "insufficiency": None,
        "embedding_provider": None,
        "url": url,
    }
    if status != 200 or not isinstance(body, dict):
        row["detail"] = f"non-200 or non-json: {body!r}"[:500]
        return row

    row["embedding_provider"] = body.get("embedding_provider") or (
        (body.get("config") or {}).get("embedding_provider")
    )
    insufficiency = bool(body.get("insufficiency"))
    row["insufficiency"] = insufficiency
    hits: List[Dict[str, Any]] = list(body.get("hits") or [])
    top = hits[0] if hits else None
    if top:
        row["actual_layer"] = top.get("layer")
        cite = top.get("citation") or {}
        row["cited_source"] = cite.get("source_id") or top.get("doc_id")

    failures: List[str] = []
    expect_insuf = bool(case.get("expect_insufficiency"))
    if expect_insuf:
        if not insufficiency or int(body.get("hit_count") or 0) != 0 or hits:
            failures.append("expected honest insufficiency (no hits)")
    else:
        if insufficiency or not hits:
            failures.append("expected hits but got insufficiency/empty")
        expected_layer = case.get("expected_layer")
        if expected_layer is not None and top and top.get("layer") != expected_layer:
            failures.append(
                f"expected layer {expected_layer}, got {top.get('layer')}"
            )
        expect_doc = case.get("expect_top_doc_id")
        if expect_doc and top and top.get("doc_id") != expect_doc:
            failures.append(f"expected top doc {expect_doc}, got {top.get('doc_id')}")
        if case.get("require_citation") and top:
            cite = top.get("citation") or {}
            if not cite.get("source_id"):
                failures.append("missing citation.source_id")
            for field in case.get("require_citation_fields") or []:
                if cite.get(field) in (None, ""):
                    failures.append(f"missing citation.{field}")
        for banned in case.get("forbid_layers") or []:
            if any(h.get("layer") == banned for h in hits):
                failures.append(f"cross-layer leak: found layer {banned}")
        for banned in case.get("forbid_doc_ids") or []:
            if any(h.get("doc_id") == banned for h in hits):
                failures.append(f"isolation leak: found doc {banned}")
        for banned in case.get("forbid_property_ids") or []:
            if any(h.get("property_id") == banned for h in hits):
                failures.append(f"property isolation leak: {banned}")
        for needle in case.get("excerpt_must_contain") or []:
            blob = " ".join(
                f"{h.get('excerpt','')} {h.get('title','')}" for h in hits
            )
            if needle not in blob:
                failures.append(f"excerpt missing {needle!r}")
        for sub in case.get("forbid_doc_id_substrings") or []:
            if any(sub.lower() in str(h.get("doc_id") or "").lower() for h in hits):
                failures.append(f"forbidden doc substring {sub!r}")

    row["pass"] = not failures
    row["detail"] = "; ".join(failures) if failures else "ok"
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default="https://projectsix.onrender.com",
        help="Live Steward base URL",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/factory/certification-runs/steward-live-oracle-latest.json"),
    )
    parser.add_argument("--md-out", type=Path, default=None)
    args = parser.parse_args()

    suite = json.loads(SUITE_PATH.read_text(encoding="utf-8"))
    # Warm / discover embedding + force fixture bootstrap (POST)
    dual_status, dual_body = _http_get(f"{args.base_url.rstrip('/')}/v1/rag/dual")
    embedding = None
    if dual_status == 200 and isinstance(dual_body, dict):
        embedding = dual_body.get("embedding_provider") or (
            (dual_body.get("runtime") or {}).get("embedding_provider")
        ) or ((dual_body.get("config") or {}).get("embedding_provider"))
        persistence = (dual_body.get("runtime") or {}).get("persistence_adapter")
        if persistence:
            print(f"persistence_adapter={persistence}", file=sys.stderr)
    try:
        req = urllib.request.Request(
            f"{args.base_url.rstrip('/')}/v1/rag/bootstrap",
            method="POST",
            data=b"{}",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"bootstrap warning: {exc}", file=sys.stderr)
    results = [_eval_case(args.base_url, case) for case in suite["cases"]]
    passed = sum(1 for r in results if r["pass"])
    embedding_used = embedding or next(
        (r["embedding_provider"] for r in results if r.get("embedding_provider")),
        "unknown",
    )
    report = {
        "suite_id": suite["suite_id"],
        "issue": suite.get("issue"),
        "base_url": args.base_url,
        "ran_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "embedding_provider": embedding_used,
        "pass_count": passed,
        "total": len(results),
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    md_path = args.md_out or args.out.with_suffix(".md")
    lines = [
        f"# Steward live oracle — {report['verdict']} ({passed}/{len(results)})",
        "",
        f"- **URL:** `{args.base_url}`",
        f"- **Ran (UTC):** `{report['ran_at_utc']}`",
        f"- **Embedding used:** `{embedding_used}`",
        f"- **Suite:** `{suite['suite_id']}` (issue #{suite.get('issue')})",
        "",
        "| # | Case | Query | Expected layer | Actual layer | Cited source | Pass/Fail |",
        "|---|------|-------|----------------|--------------|--------------|-----------|",
    ]
    for r in results:
        mark = "PASS" if r["pass"] else "FAIL"
        lines.append(
            "| {id} | {name} | {query} | {el} | {al} | {src} | **{mark}** |".format(
                id=r["id"],
                name=r["name"],
                query=(r["query"] or "").replace("|", "/"),
                el=r["expected_layer"] if r["expected_layer"] is not None else "—",
                al=r["actual_layer"] if r["actual_layer"] is not None else ("∅" if r["insufficiency"] else "—"),
                src=(r["cited_source"] or ("(insufficiency)" if r["insufficiency"] else "—")),
                mark=mark,
            )
        )
    fails = [r for r in results if not r["pass"]]
    if fails:
        lines.extend(["", "## Failures", ""])
        for r in fails:
            lines.append(f"- **#{r['id']} {r['name']}:** {r['detail']}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "pass_count": passed, "total": len(results), "embedding": embedding_used, "out": str(args.out)}, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
