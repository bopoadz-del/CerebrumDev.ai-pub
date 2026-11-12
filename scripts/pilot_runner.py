#!/usr/bin/env python3
"""Pilot run: one full build on the LIVE deployment with two uploaded docs.

ORDERS_02NOV_1420Z + DECISION_FACTORY_pilot_run_plan. Runs the Harbourline
Civil Works pilot against the live Factory API: logs in with the SAME
verified-principal resolver post-deploy-smoke uses (SMOKE_GATE_TOKEN, else
SMOKE_EMAIL+SMOKE_PASSWORD -- a value is never printed), creates a session,
uploads the two synthetic fixtures (tests/pilot/fixtures/harbourline_*.pdf/.xlsx)
via POST /v1/sessions/{sid}/upload BEFORE the build, then drives the five
stages through the live API with per-stage checks:

  1 UPLOAD+INDEX  both docs accepted and indexed into the session corpus.
  2 BRIEF (F-10c-a) the drafted brief carries document:<filename> requirements.
  3 BUILD          COLLECTOR..STORE reaches a terminal success; export zip ships.
  4 CORPUS (F-10c-b) the uploads reached the product corpus (no corpus finding;
                   a finding, if any, is VISIBLE -- never silent).
  5 PRODUCT RUN + RESUME  /product/pilot reaches Store-green and a second call
                   RESUMES the same workspace (resume-not-restart), never a fresh build.

Each stage prints one line PASS/FAIL + evidence; any FAIL exits non-zero. A
PILOT_RESULT.md is written (job artifact + GitHub step summary).

Usage: python3 scripts/pilot_runner.py [base_url]
Live tests run only through the repo-secret-backed GitHub workflow (pilot-live.yml);
no lane holds live tokens -- the workflow resolves SMOKE_GATE_TOKEN from the live
ECS task definition at run time (masked) and exports it to the env. STRICT
(ORDERS_1808Z): with no verified principal the run FAILS (exit 1), never a
skip-pass -- a pilot that cannot run its stages must go RED, not green.
"""
from __future__ import annotations

import io
import json
import mimetypes
import os
import sys
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import post_deploy_smoke as smoke  # reuse the live-API helpers + auth resolver

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "pilot" / "fixtures"
DOC_PDF = "harbourline_contract.pdf"
DOC_XLSX = "harbourline_boq.xlsx"
PROJECT_BRIEF = (
    "Build a contracts-administration platform for the Harbourline Civil Works "
    "project: progress claims, delay-damages tracking, QA/ITP records, and a "
    "document register. Use the uploaded contract and BOQ as the source of record."
)
BUILD_LEVEL = "production"  # the full ladder + full acceptance floor (incl. STORE)

Check = Tuple[bool, str]


# ---- pure stage checks (unit-tested; no network) ---------------------------

def upload_indexed(result: Dict[str, Any], expected_files: set) -> Check:
    """Both uploaded docs parsed and indexed into the session corpus.

    ``result`` is GET /upload/result. Pass needs a completed status, chunks
    indexed, a collection bound, and no failed files.
    """
    status = str((result or {}).get("status") or "")
    chunks = int((result or {}).get("total_chunks") or 0)
    collection = (result or {}).get("indexed_collection")
    failed = list((result or {}).get("failed_files") or [])
    ok = (
        status in ("completed", "completed_with_warnings")
        and chunks > 0
        and bool(collection)
        and not failed
    )
    return ok, f"status={status} chunks={chunks} collection={bool(collection)} failed={failed}"


def brief_cites_documents(product: Dict[str, Any], filenames: List[str]) -> Check:
    """F-10c-a: the drafted brief (or blueprint) cites each uploaded document.

    uploaded_docs_digest labels each upload ``document:<filename>`` and folds it
    into the brief used for drafting. We accept the citation in the stored
    ``brief`` or anywhere in the serialized ``blueprint``.
    """
    brief = str((product or {}).get("brief") or "")
    blueprint = json.dumps((product or {}).get("blueprint") or {}, ensure_ascii=False)
    hay = brief + "\n" + blueprint
    missing = [f for f in filenames if f"document:{f}" not in hay]
    return (not missing), (
        f"cited={[f for f in filenames if f not in [m for m in missing]]} missing={missing}"
    )


def build_reached_store(build: Dict[str, Any], zip_ok: bool) -> Check:
    """Stage 3: the build ran COLLECTOR..STORE to a terminal success + a zip.

    A real production build ends with a downloadable package (the STORE gate
    passed). ``zip_ok`` is the /product/package 200 + PK signature.
    """
    state = str((build or {}).get("state") or "")
    done = (build or {}).get("phases_done")
    total = (build or {}).get("phases_total")
    phases_complete = done is not None and total is not None and done == total and total >= 1
    ok = zip_ok and phases_complete and state not in ("failed", "stalled")
    return ok, f"state={state} phases={done}/{total} zip={zip_ok}"


def corpus_seed_ok(upload: Dict[str, Any], ledger: List[Dict[str, Any]]) -> Check:
    """F-10c-b: the uploaded docs reached the product corpus, with no SILENT
    failure. A corpus finding (upload.message / failed_files, or a ledger NOTE
    carrying ``corpus_seed_finding``) means the seed did NOT complete -- and it
    is a VISIBLE finding, which is the fail-closed contract, but the pilot stage
    FAILS so it is never mistaken for success.
    """
    msg = str((upload or {}).get("message") or "")
    failed = list((upload or {}).get("failed_files") or [])
    finding_on_upload = msg.startswith("documents were not indexed into the product corpus:") or any(
        str(f).startswith("documents were not indexed into the product corpus:") for f in failed
    )
    ledger_finding = any(
        isinstance(e, dict) and (e.get("payload") or {}).get("corpus_seed_finding")
        for e in (ledger or [])
    )
    ok = not finding_on_upload and not ledger_finding
    note = ""
    if not ok:
        note = msg or next(
            (str((e.get("payload") or {}).get("corpus_seed_finding")) for e in (ledger or [])
             if isinstance(e, dict) and (e.get("payload") or {}).get("corpus_seed_finding")),
            "corpus finding present",
        )
    return ok, (f"seeded_ok (no corpus finding)" if ok else f"corpus finding: {note}")


def resume_not_restart(first: Dict[str, Any], second: Dict[str, Any]) -> Check:
    """Stage 5: a second /product/pilot RESUMES the same workspace.

    The output_dir must not change, and the second call must report the cycle
    already complete / already running / resumed -- never a fresh generation.
    """
    d1 = str(((first or {}).get("generation") or {}).get("output_dir") or "")
    d2 = str(((second or {}).get("generation") or {}).get("output_dir") or "")
    same_dir = bool(d1) and d1 == d2
    resumed = bool(
        (second or {}).get("already_complete")
        or (second or {}).get("already_running")
        or (second or {}).get("resumed")
    )
    ok = same_dir and resumed
    return ok, f"same_output_dir={same_dir} resumed={resumed}"


# ---- live multipart upload (smoke.req is JSON-only) -------------------------

def upload_files(sid: str, token: str, paths: List[Path]) -> Tuple[int, Any]:
    boundary = "----pilot" + uuid.uuid4().hex
    body = io.BytesIO()
    for p in paths:
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="files"; filename="{p.name}"\r\n'.encode()
        )
        body.write(f"Content-Type: {ctype}\r\n\r\n".encode())
        body.write(p.read_bytes())
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    rq = urllib.request.Request(
        smoke.BASE + f"/v1/sessions/{sid}/upload",
        method="POST",
        data=body.getvalue(),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(rq, timeout=300) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        try:
            return e.code, json.loads(e.read() or b"null")
        except Exception:
            return e.code, {"raw": "http error"}
    except Exception as e:  # noqa: BLE001
        return 0, {"raw": str(e)[:200]}


def wait_upload_terminal(sid: str, token: str, wait_s: int = 600) -> Dict[str, Any]:
    deadline = time.monotonic() + wait_s
    last = {}
    while time.monotonic() < deadline:
        _s, body = smoke.req("GET", f"/v1/sessions/{sid}/upload/status", token=token)
        last = body if isinstance(body, dict) else {}
        if str(last.get("status") or "") in ("completed", "completed_with_warnings", "failed"):
            break
        time.sleep(5)
    return last


# ---- the five stages over the live API -------------------------------------

RESULTS: List[Tuple[str, bool, str]] = []

#: F-G3 (ORDERS_08NOV_0020Z): on ANY stage FAIL the pilot prints the last
#: ledger events into the step output and PILOT_RESULT -- a silent stop is
#: never left unexplained.
#: ORDERS_10NOV_1940Z (narrows 1925Z item 1): the pilot still fetches the FULL
#: build ledger through the gated endpoint it already uses, but it prints and
#: uploads only a REDACTED event TIMELINE -- ts, phase, event, failure
#: class/kind, the short reason string and counts (files/steps/idle seconds).
#: Never the ledger payload, never brief/prompt text, document names, paths,
#: session ids or tokens: the full ledger stays server-side, and the repo's
#: run logs/artifacts are public.
LAST_SID: str | None = None
LAST_TOKEN: str | None = None
LEDGER_TAIL: List[str] = []
PILOT_TIMELINE_NAME = "PILOT_TIMELINE.jsonl"
#: Ledger event kinds that are a run/phase verdict (the terminal events).
TERMINAL_REASON_KINDS = frozenset({"RUN_FAILED", "PHASE_ABORTED", "GATE_FAILED"})
#: Named worker / model-call reason markers, carried in ``kind``, ``detail`` or
#: a typed payload key (``stall`` / ``failure_kind`` / ``marker``).
REASON_PREFIXES = ("worker_", "model_call_")
#: The ONLY payload keys the redacted timeline keeps: numeric counts of files,
#: steps and idle seconds (the count fields the ledger writes). Every other
#: payload key -- including briefs, prompts, document names, paths, session ids
#: and tokens -- is dropped.
COUNT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "files": ("files_on_disk", "stall_files", "files"),
    "steps": ("steps_reported", "stall_steps", "steps"),
    "idle_s": ("stall_idle_s", "since_s", "idle_s"),
}


def record(stage: str, ok: bool, evidence: str) -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {stage} -- {evidence}")
    RESULTS.append((stage, ok, evidence))


def fetch_full_ledger(sid: str, token: str) -> List[Any]:
    """The FULL build ledger for a session, fetched server-side only.

    Every event, untruncated -- never written, printed or uploaded as-is:
    callers redact it (:func:`render_timeline` / :func:`write_pilot_timeline`)
    before anything leaves the CI run.
    """
    _s, ledger = smoke.req("GET", f"/v1/sessions/{sid}/product/ledger", token=token)
    events = (ledger or {}).get("events") if isinstance(ledger, dict) else ledger
    return events if isinstance(events, list) else []


def failure_kind_of(ev: Dict[str, Any]) -> str:
    """The failure class/kind for a redacted timeline entry.

    A terminal verdict kind, a typed ``failure_kind`` / ``stall`` / ``marker``
    in the payload, or a named worker_*/model_call_* marker in ``kind`` or
    ``detail``. Empty when the event claims no failure class.
    """
    kind = str(ev.get("kind") or "")
    if kind.upper() in TERMINAL_REASON_KINDS:
        return kind
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    for key in ("failure_kind", "stall", "marker"):
        value = str(payload.get(key) or "")
        if value:
            return value
    detail = str(ev.get("detail") or "")
    if detail.lower().startswith(REASON_PREFIXES):
        return detail
    if kind.lower().startswith(REASON_PREFIXES):
        return kind
    return ""


def counts_of(ev: Dict[str, Any]) -> Dict[str, Any]:
    """The numeric counts a redacted timeline entry keeps: files, steps and
    idle seconds, read only from the ledger's typed count fields."""
    payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
    out: Dict[str, Any] = {}
    for label, keys in COUNT_FIELDS.items():
        for key in keys:
            raw = payload.get(key)
            if raw is None:
                continue
            try:
                num = float(raw)
            except (TypeError, ValueError):
                continue
            out[label] = int(num) if label in ("files", "steps") else num
            break
    return out


def redact_event(ev: Any) -> Dict[str, Any]:
    """The ONLY fields of a ledger event the public pilot may print/upload.

    ts, phase, event, failure class/kind, the short reason string and counts.
    The payload never appears: briefs, prompts, document names, paths, session
    ids and tokens stay server-side.
    """
    if not isinstance(ev, dict):
        return {"event": "unreadable", "reason": str(ev)[:80]}
    return {
        "ts": str(ev.get("ts") or ""),
        "phase": str(ev.get("role") or "-"),
        "event": str(ev.get("kind") or ""),
        "failure_kind": failure_kind_of(ev),
        "reason": str(ev.get("detail") or "")[:200],
        "counts": counts_of(ev),
    }


def render_timeline(events: Any, limit: int = 20) -> List[str]:
    """Render up to ``limit`` ledger events as REDACTED one-line entries.

    Each line carries the allowed timeline fields only; the payload never
    appears (its counts are surfaced as ``counts={...}``).
    """
    if not isinstance(events, list) or not events:
        return ["no ledger events"]
    lines: List[str] = []
    for ev in events[-limit:]:
        red = redact_event(ev)
        counts = red["counts"]
        counts_s = ",".join(f"{k}={v}" for k, v in sorted(counts.items()))
        line = (
            f"{red['ts']} phase={red['phase']} event={red['event']} "
            f"failure_kind={red['failure_kind'] or '-'} {red['reason']}"
        )
        if counts_s:
            line += f" counts={{{counts_s}}}"
        lines.append(line)
    return lines


def is_reason_event(ev: Dict[str, Any]) -> bool:
    """True for a terminal verdict or a named worker_*/model_call_* marker.

    Matches the event's own ``kind`` / ``detail`` and the typed payload markers
    that name a worker or model-call reason (``stall`` / ``failure_kind`` /
    ``marker``, or the model-call lifecycle fields) -- never by searching
    arbitrary prose for a phrase someone happened to write.
    """
    kind = str(ev.get("kind") or "")
    detail = str(ev.get("detail") or "")
    payload = ev.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if kind.upper() in TERMINAL_REASON_KINDS:
        return True
    if kind.lower().startswith(REASON_PREFIXES) or detail.lower().startswith(REASON_PREFIXES):
        return True
    for key in ("stall", "failure_kind", "marker"):
        if str(payload.get(key) or "").lower().startswith(REASON_PREFIXES):
            return True
    return bool(
        "model_call" in payload
        or "model_call_state" in payload
        or "model_call_deadline_s" in payload
    )


def render_reason_events(events: Any) -> List[str]:
    """Every terminal-reason event, one line each with its timestamp.

    The run's terminal reason (a verdict, or a worker_*/model_call_* marker),
    not the generic tail -- so a failed pilot names why it failed.
    """
    if not isinstance(events, list):
        return ["no ledger events to filter"]
    lines: List[str] = []
    for ev in events:
        if isinstance(ev, dict) and is_reason_event(ev):
            lines.extend(render_timeline([ev], limit=1))
    if not lines:
        return ["no RUN_FAILED / PHASE_ABORTED / GATE_FAILED / worker_* / model_call_* event found"]
    return lines


def write_pilot_timeline(events: Any, path: Path | str | None = None) -> bool:
    """Write the REDACTED timeline to PILOT_TIMELINE.jsonl (one event per line).

    ``path`` overrides the target for tests; the pilot writes next to
    PILOT_RESULT.md at the repo root so the workflow can upload it. Only the
    redacted event dicts are written -- the full ledger stays server-side.
    """
    if not isinstance(events, list):
        return False
    target = Path(path) if path is not None else (REPO_ROOT / PILOT_TIMELINE_NAME)
    try:
        lines = [json.dumps(redact_event(ev), ensure_ascii=False, default=str) for ev in events]
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return True
    except OSError:
        return False


def principal_required_result(has_creds: bool) -> Check:
    """STRICT (ORDERS_1808Z): the pilot REQUIRES a verified principal.

    A missing principal is a FAIL (the pilot cannot run its five stages), never
    a skip-pass. Returns (ok, evidence).
    """
    if has_creds:
        return True, "verified principal present"
    return False, (
        "no verified-principal secret (SMOKE_GATE_TOKEN, or SMOKE_EMAIL+SMOKE_PASSWORD) "
        "-- pilot is strict and cannot run"
    )


def brief_diag_summary(product: Dict[str, Any]) -> str:
    """The persisted draft diagnostic (product_design.brief_diag, #157/#148),
    quoted for the pilot's BRIEF evidence -- chunks / source_files /
    digest_folded / loaded_from / worker / upload_worker. "none" when absent.
    """
    diag = (product or {}).get("brief_diag")
    if not diag:
        return "none"
    return json.dumps(diag, ensure_ascii=False, default=str)[:400]


def run_pilot(base: str) -> int:
    global LAST_SID, LAST_TOKEN
    smoke.BASE = base
    print(f"pilot run against {base}\n")
    if not smoke.wait_for_ready(allow_descendant=True):
        record("surface ready", False, "health/ready/version did not come up")
        return finish()

    ok0, ev0 = principal_required_result(smoke.has_gated_credentials())
    record("0 PRINCIPAL", ok0, ev0)
    if not ok0:
        # STRICT (ORDERS_1808Z): a missing verified principal is a FAIL, never a
        # skip-pass. The pilot's whole job is to run the five stages, so "could
        # not run" goes RED, not green -- unlike post_deploy_smoke, which
        # skip-passes so an absent secret never fails the deploy pipeline.
        print("::error title=Pilot::no verified-principal secret -- pilot cannot run (strict)")
        return finish()

    token, _b = smoke.verified_tokens()
    if not token:
        record("login", False, "verified-principal login failed")
        return finish()

    s, r = smoke.req("POST", "/v1/sessions/", {}, token=token)
    sid = (r or {}).get("session_id")
    record("session create", s in (200, 201) and bool(sid), f"http={s}")
    if not sid:
        return finish()
    LAST_SID = sid
    LAST_TOKEN = token

    # Stage 1 -- UPLOAD + INDEX (before the build)
    paths = [FIXTURES / DOC_PDF, FIXTURES / DOC_XLSX]
    us, _ub = upload_files(sid, token, paths)
    term = wait_upload_terminal(sid, token)
    _rs, result = smoke.req("GET", f"/v1/sessions/{sid}/upload/result", token=token)
    ok1, ev1 = upload_indexed(result if isinstance(result, dict) else {}, {DOC_PDF, DOC_XLSX})
    record("1 UPLOAD+INDEX", ok1 and us in (200, 201), f"upload_http={us} status={term.get('status')} {ev1}")

    # Stage 2 -- BRIEF (F-10c-a document:<filename> citations)
    raw, turns = smoke.chat_until_drafted(sid, token, PROJECT_BRIEF)
    _ps, product = smoke.req("GET", f"/v1/sessions/{sid}/product", token=token)
    product = product if isinstance(product, dict) else {}
    ok2, ev2 = brief_cites_documents(product, [DOC_PDF, DOC_XLSX])
    # ORDERS_08NOV_0250Z: quote the PERSISTED diag (product_design.brief_diag,
    # #157/#148) -- chunks/source_files/digest_folded/loaded_from/worker/
    # upload_worker -- not the fragile SSE regex, so cited=[] is traced to the
    # state, never guessed.
    record("2 BRIEF (F-10c-a)", ok2, f"turns={turns} {ev2} brief_diag={brief_diag_summary(product)}")

    # Stage 3 -- BUILD through STORE + export zip
    smoke.chat(sid, token, "", action="set_build_level", value=BUILD_LEVEL)
    smoke.chat(sid, token, "", action="confirm_intake")
    smoke.chat(sid, token, "", action="approve")
    zs, blob, build = smoke.wait_for_export(sid, token, wait_s=smoke.BUILD_WAIT_S)
    zip_ok = zs == 200 and isinstance(blob, (bytes, bytearray)) and blob[:2] == b"PK"
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist() if zip_ok else []
    ok3, ev3 = build_reached_store(build, zip_ok and len(names) > 5)
    record("3 BUILD->STORE", ok3, f"zip_http={zs} files={len(names)} {ev3}")

    # Stage 4 -- CORPUS (F-10c-b): uploads reached the product corpus, visibly
    _us2, upload_state = smoke.req("GET", f"/v1/sessions/{sid}/upload/status", token=token)
    _ls, ledger = smoke.req("GET", f"/v1/sessions/{sid}/product/ledger", token=token)
    ledger_events = (ledger or {}).get("events") if isinstance(ledger, dict) else ledger
    ok4, ev4 = corpus_seed_ok(
        upload_state if isinstance(upload_state, dict) else {},
        ledger_events if isinstance(ledger_events, list) else [],
    )
    record("4 CORPUS (F-10c-b)", ok4, ev4)

    # Stage 5 -- PRODUCT RUN (Store-green) + resume-not-restart
    s5a, first = smoke.req("POST", f"/v1/sessions/{sid}/product/pilot", {}, token=token)
    first = first if isinstance(first, dict) else {}
    s5b, second = smoke.req("POST", f"/v1/sessions/{sid}/product/pilot", {}, token=token)
    second = second if isinstance(second, dict) else {}
    ok5, ev5 = resume_not_restart(first, second)
    run_ok = s5a in (200, 201) and bool(first.get("ok"))
    record("5 PRODUCT RUN + RESUME", run_ok and ok5, f"run_http={s5a} ok={first.get('ok')} {ev5}")

    return finish()


def render_result_md() -> str:
    lines = ["# PILOT_RESULT -- Harbourline Civil Works", ""]
    for stage, ok, ev in RESULTS:
        lines.append(f"- **{'PASS' if ok else 'FAIL'}** {stage} — {ev}")
    failed = [s for s, ok, _ in RESULTS if not ok]
    verdict = "FAIL" if (failed or not RESULTS) else "PASS"
    lines += ["", f"**{verdict}**: {len(RESULTS) - len(failed)}/{len(RESULTS)} stages green"]
    if LEDGER_TAIL:
        lines += ["", "## build timeline (redacted)"]
        lines += [f"- {line}" for line in LEDGER_TAIL]
    return "\n".join(lines) + "\n"


def finish() -> int:
    # F-G3 (ORDERS_10NOV_1940Z): a failure must carry the REDACTED timeline and
    # its terminal reason in the run, never a silent stop; the full ledger
    # stays server-side.
    failed = [s for s, ok, _ in RESULTS if not ok]
    if failed and LAST_SID and LAST_TOKEN and not LEDGER_TAIL:
        try:
            events = fetch_full_ledger(LAST_SID, LAST_TOKEN)
            if write_pilot_timeline(events):
                LEDGER_TAIL.append(
                    f"redacted timeline written to {PILOT_TIMELINE_NAME} ({len(events)} events)"
                )
            LEDGER_TAIL.extend(render_timeline(events, limit=20))
            LEDGER_TAIL.append("terminal reason:")
            LEDGER_TAIL.extend(render_reason_events(events))
        except Exception as exc:  # noqa: BLE001 -- ledger fetch never changes the verdict
            LEDGER_TAIL.append(f"ledger fetch error: {exc}")
    md = render_result_md()
    try:
        (REPO_ROOT / "PILOT_RESULT.md").write_text(md, encoding="utf-8")
    except OSError:
        pass
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(md)
        except OSError:
            pass
    print("\n" + md)
    # STRICT: green only when there is >=1 stage and every stage passed.
    return 0 if RESULTS and all(ok for _s, ok, _e in RESULTS) else 1


def main() -> None:
    base = smoke.resolve_base(sys.argv)
    sys.exit(run_pilot(base))


if __name__ == "__main__":
    main()
