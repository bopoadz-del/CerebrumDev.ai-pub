#!/usr/bin/env python3
"""Ops jobs that need the live API, run on Actions (.github/workflows/ops.yml).

Anything that has to reach the live backend runs here, on a runner, never from
a laptop or an agent sandbox: the runner holds the deploy secrets and can reach
the API. Every job writes its answer to the run summary AND to a JSON record
the workflow commits to the ``ops-results`` branch, so the answer outlives the
run's logs and can be read with nothing but repository access.

Jobs (``python3 scripts/ops.py <job> ...``):

  ledger-dump    the build ledger of one session as the Factory reports it
                 (build-status: phase trail, failure, the runner rule's
                 decisions, the writer's activity log), the session's own
                 record, and the full ledger when the server serves it.
  gate-dispatch  run the cerebrum-builds Store gate on a branch, wait for its
                 verdict and read the score and the failing checks; with a
                 session, then press Continue on it -- the normal Floor path
                 that resumes the platform at its failing phase with a fresh
                 rework budget -- and follow the build to its end.
  export-check   download a session's export through the API, audit it
                 against the export rule (``builds_push.is_exported``) and
                 assert Your Platforms lists the platform with its download.

The TARGET is data the caller passes: a session id, or any other identifier
the session's record carries (a platform id, a cerebrum-builds branch). The
session is found by asking every smoke-roster account in turn -- each account
sees only its own sessions (another account's is a 404), so the owner is the
one that answers. Nothing here knows a blueprint, a platform or a session.

Fails closed: an API that cannot be reached, a refused smoke gate, a target
no roster account owns -- each ends the job non-zero with one readable line
saying which, never an empty success.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "https://api.cerebrum-dev.com"
GITHUB_API = "https://api.github.com"

#: Lines of the writer's activity log a summary shows (the JSON keeps all).
SUMMARY_LOG_LINES = 40

#: Shapes that must never reach a summary or a committed record, whatever a
#: log line happened to echo. Redaction is by shape, never by a known value.
_SECRET_SHAPES = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer|basic|token)\s+\S+"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bcd[a-z]_[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)(\s*[=:]\s*)[^\s,;\"']{6,}"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@"),
)


class OpsError(RuntimeError):
    """A job that cannot give an answer. The message is the reason."""


def redact(text: Any) -> str:
    out = str(text if text is not None else "")
    for shape in _SECRET_SHAPES:
        out = shape.sub("[redacted]", out)
    return out


def redact_tree(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): redact_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_tree(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    return value


# -- HTTP ----------------------------------------------------------------------

Req = Callable[..., Tuple[int, Any]]


def http_req(
    method: str,
    path: str,
    body: Any = None,
    *,
    token: Optional[str] = None,
    base: str = DEFAULT_BASE,
    raw: bool = False,
    headers: Optional[Mapping[str, str]] = None,
    retries: int = 2,
    timeout: float = 120.0,
) -> Tuple[int, Any]:
    """One API call. Status 0 means no HTTP answer at all (the body names why)."""
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    hdrs.update(headers or {})
    url = path if path.startswith("http") else base.rstrip("/") + path
    last: Tuple[int, Any] = (0, {"detail": "no attempt"})
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=hdrs,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                data = resp.read()
                return resp.status, (data if raw else json.loads(data or b"null"))
        except urllib.error.HTTPError as exc:
            data = exc.read()
            try:
                parsed: Any = data if raw else json.loads(data or b"null")
            except ValueError:
                parsed = {"detail": data[:300].decode(errors="replace")}
            last = (exc.code, parsed)
            if exc.code in (502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return last
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            last = (0, {"detail": f"{type(exc).__name__}: {reason}"})
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
    return last


def _detail(body: Any) -> str:
    if isinstance(body, Mapping):
        return str(body.get("detail") or body.get("error") or "")[:300]
    if isinstance(body, (bytes, bytearray)):
        return body[:300].decode(errors="replace")
    return str(body or "")[:300]


def require_reachable(req: Req, base: str) -> Dict[str, Any]:
    """The live API answers /health with status ok, or the job ends here."""
    status, body = req("GET", "/health", base=base, retries=1)
    if status == 0:
        raise OpsError(f"API unreachable at {base}: {_detail(body)}")
    if status != 200 or not isinstance(body, Mapping) or body.get("status") != "ok":
        raise OpsError(f"API at {base} is not healthy: /health http={status} {_detail(body)}")
    _vs, version = req("GET", "/version", base=base, retries=1)
    return {"health": "ok", "git_sha": (version or {}).get("git_sha") if isinstance(version, Mapping) else None}


# -- the smoke roster ----------------------------------------------------------

_ROSTER_SIZE = re.compile(r"1\.\.(\d+)")


def roster_tokens(req: Req, base: str, gate: str) -> List[str]:
    """A login token for every account of the smoke roster, index 0 first.

    The server declares the roster's size; asking for zero principals is
    refused with that size in the answer, so the size is read, never assumed.
    """
    if not gate:
        raise OpsError("SMOKE_GATE_TOKEN is not set: the job cannot sign in to any roster account")
    hdrs = {"X-Smoke-Gate": gate}
    status, body = req("POST", "/v1/auth/smoke-login", {"principals": 0}, base=base, headers=hdrs)
    if status == 0:
        raise OpsError(f"API unreachable at {base}: {_detail(body)}")
    if status in (401, 404):
        raise OpsError(f"smoke gate refused: smoke-login http={status} {_detail(body)}")
    match = _ROSTER_SIZE.search(_detail(body)) if status == 400 else None
    size = int(match.group(1)) if match else 2
    status, body = req("POST", "/v1/auth/smoke-login", {"principals": size}, base=base, headers=hdrs)
    if status != 200 or not isinstance(body, Mapping):
        raise OpsError(f"smoke-login (principals={size}) http={status} {_detail(body)}")
    tokens = body.get("login_tokens")
    if not isinstance(tokens, list):
        tokens = [body.get("login_token"), body.get("login_token_b")]
    tokens = [t for t in tokens if isinstance(t, str) and t]
    if not tokens:
        raise OpsError("smoke-login answered with no login token")
    return tokens


def find_sessions(req: Req, base: str, tokens: Sequence[str], target: str) -> List[Dict[str, Any]]:
    """Every session a roster account owns that ``target`` names.

    A session id is asked of each account directly. Any other identifier is
    looked for in each owned session's record (exact token match), so a
    platform id or a builds branch finds the session that built it.
    """
    target = str(target or "").strip()
    if not target:
        raise OpsError("no target: pass a session id or an identifier its record carries")
    found: List[Dict[str, Any]] = []
    for index, token in enumerate(tokens):
        status, body = req("GET", f"/v1/sessions/{target}", base=base, token=token)
        if status == 200 and isinstance(body, Mapping):
            found.append({"session_id": target, "account_index": index, "token": token, "state": body})
            continue
        if status == 0:
            raise OpsError(f"API unreachable at {base}: {_detail(body)}")
    if found:
        return found
    # A leading word boundary only: an id may be given by its prefix.
    pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(target))
    for index, token in enumerate(tokens):
        status, listing = req("GET", "/v1/sessions/", base=base, token=token)
        if status == 0:
            raise OpsError(f"API unreachable at {base}: {_detail(listing)}")
        if status != 200 or not isinstance(listing, list):
            continue
        for card in listing:
            sid = (card or {}).get("session_id") if isinstance(card, Mapping) else None
            if not sid:
                continue
            status, state = req("GET", f"/v1/sessions/{sid}", base=base, token=token)
            if status == 200 and pattern.search(json.dumps(state, default=str)):
                found.append({"session_id": sid, "account_index": index, "token": token, "state": state})
    if not found:
        raise OpsError(
            f"no smoke-roster account ({len(tokens)} checked) owns a session named by {target!r}"
        )
    return found


# -- ledger-dump ---------------------------------------------------------------


def _session_record(state: Mapping[str, Any]) -> Dict[str, Any]:
    pd = state.get("product_design") if isinstance(state.get("product_design"), Mapping) else {}
    gen = pd.get("generation") if isinstance(pd.get("generation"), Mapping) else {}
    return {
        "session_id": state.get("session_id"),
        "phase": state.get("phase"),
        "phase_status": state.get("phase_status"),
        "platform_id": pd.get("platform_id"),
        "product_id": gen.get("product_id"),
        "output_dir": gen.get("output_dir"),
        "triggered_by": gen.get("triggered_by"),
        "last_error": pd.get("last_error"),
        "generation": gen,
    }


def ledger_dump(req: Req, base: str, session: Mapping[str, Any]) -> Dict[str, Any]:
    sid, token = session["session_id"], session["token"]
    status, build = req("GET", f"/v1/sessions/{sid}/product/build-status", base=base, token=token)
    if status != 200:
        raise OpsError(f"build-status for {sid} http={status} {_detail(build)}")
    lstatus, ledger = req("GET", f"/v1/sessions/{sid}/product/ledger", base=base, token=token)
    record = {
        "session_id": sid,
        "account_index": session["account_index"],
        "session": _session_record(session["state"]),
        "build_status": (build or {}).get("build") if isinstance(build, Mapping) else build,
        "ledger": ledger if lstatus == 200 else None,
        "ledger_http": lstatus,
    }
    return redact_tree(record)


def _md_failure(failure: Any) -> str:
    if not isinstance(failure, Mapping):
        return "none"
    keys = ("gate", "phase", "check", "reason", "detail")
    return "; ".join(f"{k}={failure.get(k)}" for k in keys if failure.get(k))


def ledger_summary(dump: Mapping[str, Any]) -> str:
    b = dump.get("build_status") if isinstance(dump.get("build_status"), Mapping) else {}
    s = dump.get("session") or {}
    lines = [
        f"### Ledger of `{dump.get('session_id')}` (roster account {dump.get('account_index')})",
        "",
        f"- platform `{s.get('platform_id')}` product `{s.get('product_id')}`",
        f"- state **{b.get('state')}** honesty `{b.get('honesty')}` "
        f"phase {b.get('phases_done')}/{b.get('phases_total')} current `{b.get('current_phase')}`",
        f"- failure: {_md_failure(b.get('failure'))}",
        f"- stopped: {json.dumps(b.get('stopped'), default=str)[:400]}",
        f"- last_error: {str(s.get('last_error') or '')[:400]}",
        f"- full ledger served: {'yes' if dump.get('ledger') is not None else 'no (http %s)' % dump.get('ledger_http')}",
        "",
        "Phase trail:",
        "",
    ]
    for step in b.get("phase_trail") or []:
        lines.append(f"- {json.dumps(step, default=str)[:300]}")
    lines += ["", "Runner decisions:", ""]
    for decision in b.get("decisions") or []:
        lines.append(f"- {json.dumps(decision, default=str)[:300]}")
    lines += ["", f"Activity log (last {SUMMARY_LOG_LINES}):", "", "```"]
    for note in (b.get("activity_log") or [])[-SUMMARY_LOG_LINES:]:
        lines.append(f"{note.get('ts')} {note.get('role')}: {str(note.get('text'))[:240]}")
    lines += ["```", ""]
    logs = dump.get("service_log") or {}
    if logs.get("ok"):
        tail = logs.get("lines") or []
        lines += [f"Service log ({len(tail)} lines naming the session; last {SUMMARY_LOG_LINES}):", "", "```"]
        lines += [str(line)[:300] for line in tail[-SUMMARY_LOG_LINES:]]
        lines += ["```", ""]
    else:
        lines += [f"Service log: not read ({logs.get('reason')})", ""]
    return "\n".join(lines)


def service_log_tail(terms: Sequence[str], *, since_s: float, run: Callable[..., Any] = None) -> Dict[str, Any]:
    """The backend service's own log lines naming any of ``terms`` (CloudWatch,
    through the deploy credentials). Best effort: no credentials, no awslogs
    driver or no permission is reported as the reason, never as silence."""
    import subprocess

    run = run or subprocess.run
    cluster = os.environ.get("ECS_CLUSTER", "")
    service = os.environ.get("ECS_SERVICE", "")
    if not (cluster and service and os.environ.get("AWS_ACCESS_KEY_ID")):
        return {"ok": False, "reason": "no ECS_CLUSTER/ECS_SERVICE or AWS credentials in this job"}

    def aws(*argv: str) -> Any:
        proc = run(["aws", *argv, "--output", "json"], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise OpsError(f"aws {argv[0]} {argv[1]}: {redact(proc.stderr.strip())[:300]}")
        return json.loads(proc.stdout or "null")

    try:
        svc = aws("ecs", "describe-services", "--cluster", cluster, "--services", service)
        td_arn = svc["services"][0]["taskDefinition"]
        td = aws("ecs", "describe-task-definition", "--task-definition", td_arn)["taskDefinition"]
        options = {}
        for container in td.get("containerDefinitions") or []:
            conf = container.get("logConfiguration") or {}
            if conf.get("logDriver") == "awslogs":
                options = conf.get("options") or {}
                break
        group = options.get("awslogs-group")
        if not group:
            return {"ok": False, "reason": f"task definition {td_arn} has no awslogs log group"}
        start_ms = str(int((time.time() - since_s) * 1000))
        lines: List[str] = []
        for term in [t for t in terms if t]:
            got = aws(
                "logs", "filter-log-events", "--log-group-name", group,
                "--filter-pattern", json.dumps(str(term)), "--start-time", start_ms,
                "--max-items", "400",
            )
            for event in (got or {}).get("events") or []:
                lines.append(redact(f"{event.get('timestamp')} {event.get('message', '').rstrip()}"))
        return {"ok": True, "group": group, "lines": sorted(set(lines))[-800:]}
    except OpsError as exc:
        return {"ok": False, "reason": str(exc)}
    except (KeyError, IndexError, ValueError, OSError) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


# -- gate-dispatch -------------------------------------------------------------


def gh_req(method: str, path: str, token: str, body: Any = None, raw: bool = False) -> Tuple[int, Any]:
    return http_req(
        method,
        path if path.startswith("http") else GITHUB_API + path,
        body,
        raw=raw,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _gh_download(url: str, token: str) -> bytes:
    """An artifact zip. The redirect target refuses a bearer token, so the
    redirect is followed without one."""

    class _DropAuth(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
            follow = super().redirect_request(req, fp, code, msg, headers, newurl)
            if follow is not None:
                follow.remove_header("Authorization")
            return follow

    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.build_opener(_DropAuth).open(request, timeout=120) as resp:
        return resp.read()


_SCORE = re.compile(r"(\d+)\s*/\s*(\d+)")


def gate_verdict(repo: str, branch: str, token: str) -> Dict[str, Any]:
    """The Store gate's verdict on ``branch``'s head: the ``store-gate``
    commit status (score) and the failing checks from its typed artifact."""
    status, ref = gh_req("GET", f"/repos/{repo}/commits/{branch}", token)
    if status != 200 or not isinstance(ref, Mapping):
        raise OpsError(f"{repo}@{branch}: cannot read the branch head (http {status} {_detail(ref)})")
    sha = str(ref.get("sha") or "")
    status, statuses = gh_req("GET", f"/repos/{repo}/commits/{sha}/statuses?per_page=100", token)
    row = next(
        (s for s in (statuses or []) if isinstance(s, Mapping) and s.get("context") == "store-gate"),
        None,
    )
    out: Dict[str, Any] = {"branch": branch, "sha": sha, "state": None, "description": None}
    if row is None:
        return out
    out["state"], out["description"] = row.get("state"), row.get("description")
    match = _SCORE.search(str(row.get("description") or ""))
    if match:
        out["passed"], out["total"] = int(match.group(1)), int(match.group(2))
    status, arts = gh_req("GET", f"/repos/{repo}/actions/artifacts?name=store-gate-{sha}&per_page=20", token)
    for art in (arts or {}).get("artifacts") or [] if isinstance(arts, Mapping) else []:
        if art.get("expired"):
            continue
        try:
            blob = _gh_download(str(art["archive_download_url"]), token)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                name = next(n for n in zf.namelist() if n.endswith(".json"))
                payload = json.loads(zf.read(name).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 -- an unreadable artifact is reported, not fatal
            out["artifact_error"] = f"{type(exc).__name__}: {exc}"
            continue
        out["checks"] = _failing_checks(payload)
        break
    return out


def _failing_checks(payload: Any) -> List[Dict[str, Any]]:
    """Every failed line of a store-gate payload, whatever its nesting."""
    failed: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            verdict = str(node.get("status") or node.get("result") or node.get("outcome") or "").lower()
            name = node.get("name") or node.get("id") or node.get("check")
            if name and (node.get("passed") is False or verdict in {"fail", "failed", "failure", "red"}):
                failed.append(
                    {"name": name, "detail": redact(str(node.get("detail") or node.get("reason") or ""))[:500]}
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return failed


def carry_mains_gate_onto(repo: str, branch: str, token: str) -> str:
    """Make ``branch`` carry cerebrum-builds ``main``'s whole Store gate; return
    the head sha the gate will judge. The SAME carry every checkpoint makes
    (branch_attach.carry_gate_onto_branch)."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.factory.build.branch_attach import carry_gate_onto_branch  # noqa: PLC0415
    from app.factory.build.builds_push import BUILDS_REPO_ENV, BUILDS_TOKEN_ENV  # noqa: PLC0415

    return carry_gate_onto_branch(branch, {BUILDS_TOKEN_ENV: token, BUILDS_REPO_ENV: repo})


def dispatch_gate(
    repo: str,
    branch: str,
    token: str,
    *,
    wait_s: float,
    poll_s: float = 20.0,
    carry: Callable[[str, str, str], str] = carry_mains_gate_onto,
) -> Dict[str, Any]:
    """Run the Store gate on ``branch`` and read its verdict.

    ``workflow_dispatch`` runs store-gate.yml and .github/store_gate/* from the
    dispatched branch's OWN tree. Live 2026-10-10 (store-gate run 38040326004)
    this dispatched a branch cut before a gate fix: its stale helper ran the
    harness from the mounted checkout and a false 22/22 was written. So
    ``main``'s whole gate is carried onto the branch first -- no commit when it
    already carries it -- and a carry that cannot be made fails closed before
    anything is dispatched."""
    try:
        carried = str(carry(repo, branch, token) or "").strip()
    except Exception as exc:  # noqa: BLE001 -- named, and nothing is dispatched
        raise OpsError(f"cannot carry main's Store gate onto {repo}@{branch}: {redact(exc)}") from exc
    if not carried:
        raise OpsError(f"cannot carry main's Store gate onto {repo}@{branch}: no head sha")
    started = time.time()
    status, body = gh_req(
        "POST", f"/repos/{repo}/actions/workflows/store-gate.yml/dispatches", token, {"ref": branch}
    )
    if status not in (200, 204):
        raise OpsError(f"store-gate dispatch on {repo}@{branch} refused: http {status} {_detail(body)}")
    run: Optional[Mapping[str, Any]] = None
    deadline = started + wait_s
    while time.time() < deadline:
        time.sleep(poll_s)
        status, runs = gh_req(
            "GET",
            f"/repos/{repo}/actions/workflows/store-gate.yml/runs?branch={branch}"
            "&event=workflow_dispatch&per_page=5",
            token,
        )
        for candidate in (runs or {}).get("workflow_runs") or [] if isinstance(runs, Mapping) else []:
            created = time.mktime(time.strptime(candidate["created_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            if created >= started - 60:
                run = candidate
                break
        if run is not None and run.get("status") == "completed":
            break
        if run is not None:
            status, fresh = gh_req("GET", f"/repos/{repo}/actions/runs/{run['id']}", token)
            if status == 200 and isinstance(fresh, Mapping) and fresh.get("status") == "completed":
                run = fresh
                break
    if run is None:
        raise OpsError(f"store-gate run for {repo}@{branch} never appeared within {int(wait_s)} s")
    if run.get("status") != "completed":
        raise OpsError(f"store-gate run {run.get('html_url')} still {run.get('status')} after {int(wait_s)} s")
    verdict = gate_verdict(repo, branch, token)
    verdict["gate_carried_to"] = carried
    verdict["run"] = run.get("html_url")
    verdict["conclusion"] = run.get("conclusion")
    return verdict


def continue_build(req: Req, base: str, session: Mapping[str, Any], *, wait_s: float, poll_s: float = 60.0) -> Dict[str, Any]:
    """Press Continue (the typed Floor action) and follow the build to its end."""
    sid, token = session["session_id"], session["token"]
    status, reply = req(
        "POST", f"/v1/sessions/{sid}/chat", {"message": "", "action": "continue"},
        base=base, token=token, raw=True, timeout=300,
    )
    text = reply.decode(errors="replace") if isinstance(reply, (bytes, bytearray)) else str(reply)
    out: Dict[str, Any] = {"continue_http": status, "continue_reply": redact(text[-1500:])}
    if status != 200:
        return out
    deadline = time.time() + wait_s
    build: Dict[str, Any] = {}
    while time.time() < deadline:
        time.sleep(poll_s)
        _s, body = req("GET", f"/v1/sessions/{sid}/product/build-status", base=base, token=token)
        build = (body or {}).get("build") if isinstance(body, Mapping) else {}
        build = build if isinstance(build, Mapping) else {}
        if build.get("state") in {"succeeded", "failed", "stalled"}:
            break
    out["build_state"] = build.get("state")
    out["failure"] = build.get("failure")
    out["decisions"] = build.get("decisions")
    out["activity_tail"] = (build.get("activity_log") or [])[-SUMMARY_LOG_LINES:]
    return redact_tree(out)


# -- export-check --------------------------------------------------------------


def _export_rule() -> Callable[[Path], bool]:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.factory.build.builds_push import is_exported  # noqa: PLC0415

    return is_exported


def strip_audit(blob: bytes, is_exported: Callable[[Path], bool]) -> Dict[str, Any]:
    """Every entry of the export must be one the export rule ships."""
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
    leaked = [n for n in names if not is_exported(Path(n))]
    return {"files": len(names), "bytes": len(blob), "leaked": leaked, "ok": not leaked and bool(names)}


def export_check(req: Req, base: str, session: Mapping[str, Any], is_exported: Callable[[Path], bool]) -> Dict[str, Any]:
    sid, token = session["session_id"], session["token"]
    status, blob = req("GET", f"/v1/sessions/{sid}/product/package", base=base, token=token, raw=True, timeout=300)
    out: Dict[str, Any] = {"session_id": sid, "package_http": status}
    if status != 200 or not isinstance(blob, (bytes, bytearray)) or blob[:2] != b"PK":
        out["detail"] = _detail(blob)
        out["ok"] = False
        return out
    out["strip"] = strip_audit(bytes(blob), is_exported)
    # Your Platforms is the account's session listing: the platform must be
    # there, with a generation the page offers for download.
    lstatus, listing = req("GET", "/v1/sessions/", base=base, token=token)
    card = next(
        (c for c in (listing or []) if isinstance(c, Mapping) and c.get("session_id") == sid),
        None,
    ) if lstatus == 200 and isinstance(listing, list) else None
    pstatus, product = req("GET", f"/v1/sessions/{sid}/product", base=base, token=token)
    gen = (product or {}).get("generation") if isinstance(product, Mapping) else None
    out["your_platforms"] = {
        "listing_http": lstatus,
        "listed": card is not None,
        "card": card,
        "product_http": pstatus,
        "has_generation": bool(gen),
    }
    out["ok"] = bool(out["strip"]["ok"] and card is not None and gen)
    return redact_tree(out)


# -- output --------------------------------------------------------------------


def write_outputs(out_dir: Path, name: str, record: Any, summary: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(record, indent=2, default=str) + "\n")
    (out_dir / f"{name}.md").write_text(summary + "\n")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write(summary + "\n")
    print(summary)


# Log lines that mark a process starting, stopping or dying -- general to any
# uvicorn/Python service, nothing about one product.
LIFECYCLE_TERMS = (
    "Started server process", "Shutting down", "Finished server process",
    "Killed", "MemoryError", "Traceback", "SIGTERM", "orphan",
)


def _aws_runner():
    import subprocess

    return subprocess.run


def service_events(*, since_s: float, run: Callable[..., Any] = None) -> Dict[str, Any]:
    """Why the backend service restarted, from ECS and CloudWatch (deploy
    credentials): the service's own events, its stopped tasks with their stop
    reason and exit codes, the memory/CPU maxima, and the lifecycle log lines --
    all inside the last ``since_s`` seconds. Raises OpsError with the reason
    when it cannot read them; never answers with silence."""
    import datetime as _dt

    run = run or _aws_runner()
    cluster = os.environ.get("ECS_CLUSTER", "")
    service = os.environ.get("ECS_SERVICE", "")
    if not (cluster and service and os.environ.get("AWS_ACCESS_KEY_ID")):
        raise OpsError("no ECS_CLUSTER/ECS_SERVICE or AWS credentials in this job")

    def aws(*argv: str) -> Any:
        proc = run(["aws", *argv, "--output", "json"], capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise OpsError(f"aws {argv[0]} {argv[1]}: {redact(proc.stderr.strip())[:300]}")
        return json.loads(proc.stdout or "null") or {}

    now = time.time()
    start = now - float(since_s)

    def _epoch(value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return _dt.datetime.fromisoformat(str(value)).timestamp()
        except ValueError:
            return 0.0

    svc = (aws("ecs", "describe-services", "--cluster", cluster, "--services", service).get("services") or [{}])[0]
    events = [
        {"at": e.get("createdAt"), "message": redact(str(e.get("message", "")))}
        for e in svc.get("events") or []
        if _epoch(e.get("createdAt")) >= start
    ]
    arns = aws("ecs", "list-tasks", "--cluster", cluster, "--service-name", service,
               "--desired-status", "STOPPED").get("taskArns") or []
    stopped: List[Dict[str, Any]] = []
    if arns:
        for task in aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", *arns[:100]).get("tasks") or []:
            stopped.append({
                "task": task.get("taskArn"),
                "startedAt": task.get("startedAt"),
                "stoppedAt": task.get("stoppedAt"),
                "stopCode": task.get("stopCode"),
                "stoppedReason": redact(str(task.get("stoppedReason", ""))),
                "containers": [
                    {"name": c.get("name"), "exitCode": c.get("exitCode"), "reason": redact(str(c.get("reason", "")))}
                    for c in task.get("containers") or []
                ],
            })
    def iso(t: float) -> str:
        return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    metrics: Dict[str, Any] = {}
    for name in ("MemoryUtilization", "CPUUtilization"):
        points = aws(
            "cloudwatch", "get-metric-statistics", "--namespace", "AWS/ECS", "--metric-name", name,
            "--dimensions", f"Name=ClusterName,Value={cluster}", f"Name=ServiceName,Value={service}",
            "--start-time", iso(start), "--end-time", iso(now), "--period", "60", "--statistics", "Maximum",
        ).get("Datapoints") or []
        top = max(points, key=lambda p: p.get("Maximum") or 0, default={})
        metrics[name] = {"max": top.get("Maximum"), "at": top.get("Timestamp"), "points": len(points)}
    td = aws("ecs", "describe-task-definition", "--task-definition", svc.get("taskDefinition", "")).get("taskDefinition") or {}
    group = ""
    for container in td.get("containerDefinitions") or []:
        conf = container.get("logConfiguration") or {}
        if conf.get("logDriver") == "awslogs":
            group = (conf.get("options") or {}).get("awslogs-group", "")
            break
    lines: List[str] = []
    if group:
        for term in LIFECYCLE_TERMS:
            got = aws("logs", "filter-log-events", "--log-group-name", group,
                      "--filter-pattern", json.dumps(term), "--start-time", str(int(start * 1000)),
                      "--max-items", "200")
            for event in got.get("events") or []:
                lines.append(redact(f"{event.get('timestamp')} {str(event.get('message', '')).rstrip()}"))
    return {
        "ok": True, "since_s": since_s, "task_definition": svc.get("taskDefinition"),
        "events": events, "stopped_tasks": stopped, "metrics": metrics,
        "log_group": group, "lifecycle_log": sorted(set(lines))[-400:],
    }


def service_events_summary(record: Mapping[str, Any]) -> str:
    lines = [f"### Service events (last {int(record.get('since_s') or 0)} s)", "",
             f"- task definition `{record.get('task_definition')}`"]
    for name, m in (record.get("metrics") or {}).items():
        lines.append(f"- {name} max **{m.get('max')}** at {m.get('at')} ({m.get('points')} points)")
    lines += ["", "Stopped tasks:", ""]
    for t in record.get("stopped_tasks") or []:
        exits = ", ".join(f"{c.get('name')} exit {c.get('exitCode')} {c.get('reason')}" for c in t.get("containers") or [])
        lines.append(f"- {t.get('stoppedAt')} `{t.get('stopCode')}` {t.get('stoppedReason')} ({exits})")
    lines += ["", "ECS events:", ""]
    lines += [f"- {e.get('at')} {e.get('message')}" for e in record.get("events") or []]
    lines += ["", "Lifecycle log:", "", "```"]
    lines += [str(line)[:300] for line in (record.get("lifecycle_log") or [])[-80:]]
    lines += ["```"]
    return "\n".join(lines) + "\n"


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)[:80] or "target"


def main(argv: Optional[Sequence[str]] = None, *, req: Req = http_req) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("job", choices=["ledger-dump", "gate-dispatch", "export-check", "service-events"])
    ap.add_argument("--target", default="", help="session id, or an identifier its record carries")
    ap.add_argument("--branch", default="", help="gate-dispatch: the cerebrum-builds branch to gate")
    ap.add_argument("--repros", default="", help="a cycle's repros.json; --target may then be a run name")
    ap.add_argument("--builds-repo", default=os.environ.get("BUILDS_REPO", "bopoadz-del/cerebrum-builds"))
    ap.add_argument("--base", default=os.environ.get("FACTORY_BASE_URL") or DEFAULT_BASE)
    ap.add_argument("--out", default="ops-out")
    ap.add_argument("--wait-s", type=float, default=float(os.environ.get("OPS_WAIT_S") or 9000))
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    base = args.base
    try:
        target = args.target.strip()
        if args.repros and target:
            recorded = json.loads(Path(args.repros).read_text())
            # release_cycle.py writes {name: result} under "repros".
            runs = recorded.get("repros") or recorded.get("runs") or {}
            run = runs.get(target) if isinstance(runs, Mapping) else None
            if isinstance(run, Mapping) and run.get("session_id"):
                target = str(run["session_id"])
        if args.job == "service-events":
            # Never touches the API: it may be the thing that is down.
            record = service_events(since_s=float(os.environ.get("OPS_EVENTS_SINCE_S") or 6 * 3600),
                                    run=_aws_runner())
            write_outputs(out_dir, "service_events", record, service_events_summary(record))
            return 0
        if args.job == "gate-dispatch" and not (args.branch or target):
            raise OpsError("gate-dispatch needs --branch, --target, or both")
        gate_record: Optional[Dict[str, Any]] = None
        if args.job == "gate-dispatch" and args.branch:
            gh_token = os.environ.get("BUILDS_GITHUB_TOKEN", "")
            if not gh_token:
                raise OpsError("BUILDS_GITHUB_TOKEN is not set: cannot dispatch the Store gate")
            gate_record = dispatch_gate(args.builds_repo, args.branch, gh_token, wait_s=min(args.wait_s, 3600))
            if not target:
                summary = gate_summary(gate_record)
                write_outputs(out_dir, f"gate_{_safe_name(args.branch)}", gate_record, summary)
                return 0
        live = require_reachable(req, base)
        tokens = roster_tokens(req, base, os.environ.get("SMOKE_GATE_TOKEN", "").strip())
        sessions = find_sessions(req, base, tokens, target)
        rc = 0
        for session in sessions:
            sid = session["session_id"]
            if args.job == "ledger-dump":
                record = {"live": live, **ledger_dump(req, base, session)}
                out_name = Path(str((record.get("session") or {}).get("output_dir") or "")).name
                record["service_log"] = service_log_tail(
                    [sid, out_name], since_s=float(os.environ.get("OPS_LOG_SINCE_S") or 3 * 86400)
                )
                write_outputs(out_dir, f"ledger_{_safe_name(sid)}", record, ledger_summary(record))
            elif args.job == "gate-dispatch":
                follow = continue_build(req, base, session, wait_s=args.wait_s)
                record = {"live": live, "session_id": sid, "gate": gate_record, "continue": follow}
                summary = (gate_summary(gate_record) if gate_record else "") + continue_summary(sid, follow)
                write_outputs(out_dir, f"gate_{_safe_name(sid)}", record, summary)
                rc = rc or (0 if follow.get("build_state") == "succeeded" else 1)
            else:
                record = {"live": live, **export_check(req, base, session, _export_rule())}
                write_outputs(out_dir, f"export_{_safe_name(sid)}", record, export_summary(record))
                rc = rc or (0 if record.get("ok") else 1)
        return rc
    except OpsError as exc:
        message = f"ops {args.job} failed closed: {redact(exc)}"
        write_outputs(out_dir, f"error_{args.job}", {"ok": False, "error": message}, f"**{message}**")
        print(f"::error::{message}")
        return 2


def gate_summary(gate: Mapping[str, Any]) -> str:
    lines = [
        f"### Store gate on `{gate.get('branch')}` @ `{str(gate.get('sha'))[:12]}`",
        "",
        f"- main's Store gate carried onto the branch: head `{str(gate.get('gate_carried_to') or '')[:12]}`",
        f"- run {gate.get('run')} conclusion **{gate.get('conclusion')}**",
        f"- store-gate status **{gate.get('state')}**: {gate.get('description')}",
    ]
    for check in gate.get("checks") or []:
        lines.append(f"- FAILED `{check.get('name')}`: {str(check.get('detail'))[:300]}")
    return "\n".join(lines) + "\n\n"


def continue_summary(sid: str, follow: Mapping[str, Any]) -> str:
    lines = [
        f"### Continue on `{sid}`",
        "",
        f"- continue http {follow.get('continue_http')}; build ended **{follow.get('build_state')}**",
        f"- failure: {_md_failure(follow.get('failure'))}",
        "",
        "Runner decisions:",
        "",
    ]
    for decision in follow.get("decisions") or []:
        lines.append(f"- {json.dumps(decision, default=str)[:300]}")
    return "\n".join(lines) + "\n"


def export_summary(record: Mapping[str, Any]) -> str:
    strip = record.get("strip") or {}
    yp = record.get("your_platforms") or {}
    lines = [
        f"### Export check on `{record.get('session_id')}`: **{'PASS' if record.get('ok') else 'FAIL'}**",
        "",
        f"- package http {record.get('package_http')} {record.get('detail') or ''}",
        f"- strip audit: {strip.get('files')} files, {strip.get('bytes')} B, leaked {len(strip.get('leaked') or [])}",
        f"- Your Platforms: listed={yp.get('listed')} has_generation={yp.get('has_generation')}",
    ]
    for name in (strip.get("leaked") or [])[:30]:
        lines.append(f"  - leaked `{name}`")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
