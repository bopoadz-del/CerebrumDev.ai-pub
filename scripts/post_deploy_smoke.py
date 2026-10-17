#!/usr/bin/env python3
"""Post-deploy smoke: prove the factory is wired, not parked.

Runs against the LIVE deployment after every deploy. Asserts the full
factory loop end-to-end with the real LLM — deterministic/fallback
success is NEVER accepted as evidence (deploy gate, AGENTS.md).

Usage:
    python3 scripts/post_deploy_smoke.py [base_url]

Canonical live host: https://api.cerebrum-dev.com

Verified-principal options (public email verification stays fail-closed):
    SMOKE_GATE_TOKEN          — calls POST /v1/auth/smoke-login
    SMOKE_EMAIL + SMOKE_PASSWORD
    SMOKE_EMAIL_2 + SMOKE_PASSWORD_2  — optional isolation peer

If no verified-principal secret is set, the script waits for /health,
/ready, and (when GITHUB_SHA is set) a matching /version git_sha, then
PASSES with a GitHub notice that gated factory checks were skipped. Do
not invent or commit a token. A missing GitHub Actions secret must not
fail the whole master pipeline.

The script waits up to SMOKE_READY_WAIT_S (default 300) after a Render
bounce before any unauthenticated or gated check. Ready means /health
HTTP 200 and status=="ok", /ready HTTP 200 and status=="ready", and —
when GITHUB_SHA is set — /version git_sha matching that SHA (full or
unique prefix). Local runs with GITHUB_SHA unset keep the health/ready
wait and do not SHA-gate.

Exit code 0 = all checks that ran pass. Prints a LIVE/DEAD line per kernel.
"""
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

DEFAULT_BASE = "https://api.cerebrum-dev.com"
FAILURES = []
TRANSIENT = {502, 503, 504}
SKIP_ANNOTATION = (
    "Gated factory checks skipped — SMOKE_GATE_TOKEN (and "
    "SMOKE_EMAIL+SMOKE_PASSWORD) unset. Unauthenticated health/ready/version only."
)

# Set in main() so `import` under pytest does not treat test paths as a host.
BASE = DEFAULT_BASE


def resolve_base(argv=None):
    args = sys.argv if argv is None else argv
    if len(args) > 1:
        candidate = str(args[1]).rstrip("/")
        # Only an explicit URL is a host. pytest argv[1] is a test path.
        if candidate.startswith(("http://", "https://")):
            return candidate
    return os.environ.get("SMOKE_BASE_URL", DEFAULT_BASE).rstrip("/")


def ready_wait_seconds():
    raw = os.environ.get("SMOKE_READY_WAIT_S", "300").strip() or "300"
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def ready_interval_seconds():
    raw = os.environ.get("SMOKE_READY_INTERVAL_S", "5").strip() or "5"
    try:
        return max(0.05, float(raw))
    except ValueError:
        return 5.0


def has_gated_credentials():
    """True when a verified-principal secret is present.

    An empty GitHub ``secrets.SMOKE_GATE_TOKEN`` is unset, not a token.
    """
    if os.environ.get("SMOKE_GATE_TOKEN", "").strip():
        return True
    email = os.environ.get("SMOKE_EMAIL", "").strip()
    password = os.environ.get("SMOKE_PASSWORD", "").strip()
    return bool(email and password)


def emit_gated_skip_annotation():
    """Human line plus a GitHub Actions notice. Never fails the job."""
    print(f"SMOKE SKIP: {SKIP_ANNOTATION}")
    print(f"::notice title=Post-deploy smoke::{SKIP_ANNOTATION}")


def req(method, path, body=None, token=None, raw=False, extra_headers=None, retries=4, base=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra_headers:
        headers.update(extra_headers)
    last_status, last_body = None, None
    root = (base or BASE).rstrip("/")
    for attempt in range(retries + 1):
        r = urllib.request.Request(
            root + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                data = resp.read()
                return resp.status, (data if raw else json.loads(data or b"null"))
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                parsed = json.loads(b or b"null")
            except Exception:
                parsed = {"raw": b[:300].decode(errors="replace")}
            last_status, last_body = e.code, (b if raw else parsed)
            if e.code in TRANSIENT and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return last_status, last_body
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_status, last_body = 0, {"raw": str(e)[:300]}
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return last_status, last_body
    return last_status, last_body


def _mapping(body):
    return body if isinstance(body, dict) else {}


def expected_git_sha(explicit=None):
    """The commit that must be live. Explicit, then SMOKE_EXPECTED_SHA (the
    deploy run's head sha when chained after deploy-aws), then GITHUB_SHA."""
    if explicit is not None:
        return str(explicit).strip()
    return (
        os.environ.get("SMOKE_EXPECTED_SHA", "").strip()
        or os.environ.get("GITHUB_SHA", "").strip()
    )


def git_sha_matches(live_sha, expected_sha):
    """True when live /version git_sha matches expected (full or unique prefix).

    Empty expected_sha is not a gate (local run). Prefix match requires the
    shorter side to be at least 7 hex chars — git's default short SHA.
    """
    expected = (expected_sha or "").strip().lower()
    if not expected:
        return True
    live = (live_sha or "").strip().lower()
    if not live:
        return False
    if live == expected:
        return True
    shorter, longer = (live, expected) if len(live) <= len(expected) else (expected, live)
    return len(shorter) >= 7 and longer.startswith(shorter)


def surface_is_ready(
    health_status,
    health_body,
    ready_status,
    ready_body,
    version_status=None,
    version_body=None,
    expected_sha="",
):
    """True when /health, /ready, and (when SHA-gated) /version are live.

    Health must be HTTP 200 with status==\"ok\". Ready must be HTTP 200 with
    status==\"ready\". When expected_sha is non-empty, /version must be HTTP
    200 and git_sha must match (full or unique prefix). Empty expected_sha
    skips the SHA gate so 4-arg callers and local runs stay health/ready only.
    """
    health_ok = health_status == 200 and _mapping(health_body).get("status") == "ok"
    ready_ok = ready_status == 200 and _mapping(ready_body).get("status") == "ready"
    if not (health_ok and ready_ok):
        return False
    want = (expected_sha or "").strip()
    if not want:
        return True
    if version_status != 200:
        return False
    live = _mapping(version_body).get("git_sha") or ""
    return git_sha_matches(live, want)


def wait_for_ready(
    timeout_s=None, interval_s=None, req_fn=None, sleeper=None, expected_sha=None
):
    """Poll /health, /ready, and (when SHA-gated) /version until ready.

    Returns True if the surface became ready. Records a DEAD check on timeout.
    ``req_fn`` / ``sleeper`` / ``expected_sha`` are injectable for tests.
    When ``expected_sha`` is None, GITHUB_SHA is read from the environment.
    """
    timeout = ready_wait_seconds() if timeout_s is None else timeout_s
    interval = ready_interval_seconds() if interval_s is None else interval_s
    probe = req if req_fn is None else req_fn
    pause = time.sleep if sleeper is None else sleeper
    want_sha = expected_git_sha(expected_sha)
    deadline = time.monotonic() + timeout
    attempt = 0
    last_h, last_r, last_v = (0, {}), (0, {}), (0, {})
    while True:
        attempt += 1
        last_h = probe("GET", "/health", retries=0)
        last_r = probe("GET", "/ready", retries=0)
        last_v = (0, {})
        if want_sha:
            last_v = probe("GET", "/version", retries=0)
        hs, hb = last_h
        rs, rb = last_r
        vs, vb = last_v
        if surface_is_ready(hs, hb, rs, rb, vs, vb, want_sha):
            extra = ""
            if want_sha:
                sha = _mapping(vb).get("git_sha") or ""
                extra = f" sha={sha[:12] if sha else None}"
            print(
                f"surface ready after {attempt} probe(s) "
                f"(health={hs} ready={rs}{extra})"
            )
            return True
        remaining = deadline - time.monotonic()
        health_status = (
            _mapping(hb).get("status") if not isinstance(hb, (bytes, bytearray)) else None
        )
        ready_status = (
            _mapping(rb).get("status") if not isinstance(rb, (bytes, bytearray)) else None
        )
        live_sha = _mapping(vb).get("git_sha") if want_sha else None
        sha_bit = ""
        if want_sha:
            sha_bit = f" sha={str(live_sha)[:12] if live_sha else None}"
        paths = "/health+/ready" + ("+/version" if want_sha else "")
        print(
            f"  waiting for {paths}: health={hs} health_status={health_status} "
            f"ready={rs} status={ready_status}{sha_bit} "
            f"remaining={max(0, int(remaining))}s"
        )
        if remaining <= 0:
            break
        pause(min(interval, remaining))
        if time.monotonic() >= deadline:
            break
    check(
        "health/ready wait",
        False,
        f"timeout {timeout}s last health={last_h[0]} ready={last_r[0]}"
        + (f" sha={(_mapping(last_v[1]).get('git_sha') or '')[:12] or None}" if want_sha else ""),
    )
    return False


def check(name, ok, evidence=""):
    tag = "LIVE" if ok else "DEAD"
    print(f"[{tag}] {name}" + (f" — {evidence}" if evidence else ""))
    if not ok:
        FAILURES.append(name)


#: The ONE typed-action spec, shared with the SPA and its browser e2e
#: (frontend/src/api/floor_actions.json, exported from the backend's
#: app.factory.floor_actions; backend/tests/factory/test_floor_action_spec.py
#: fails when they differ).
FLOOR_ACTIONS = json.loads(
    (Path(__file__).resolve().parents[1] / "frontend" / "src" / "api" / "floor_actions.json")
    .read_text(encoding="utf-8")
)["actions"]


def typed_action(action, value=None):
    """The ``action``/``value`` fields of a chat request, built from the shared
    spec: an action it does not define, or a value of the wrong shape, is a
    smoke bug and raises before anything is sent."""
    if action not in FLOOR_ACTIONS:
        raise ValueError(f"undefined Floor action {action!r}")
    shape = FLOOR_ACTIONS[action]["value"]
    if shape is None:
        if value is not None:
            raise ValueError(f"Floor action {action!r} takes no value")
        return {"action": action}
    if not isinstance(value, str) or not value:
        raise ValueError(f"Floor action {action!r} needs a value")
    if isinstance(shape, dict) and value not in shape["one_of"]:
        raise ValueError(f"Floor action {action!r} value must be one of {shape['one_of']}")
    return {"action": action, "value": value}


def chat(sid, tok, msg, retries=4, action=None, value=None, fields=None):
    """POST one Floor chat turn. ``action``/``value`` are the TYPED Floor
    action (approve, continue, draft, ...), built by ``typed_action`` from the
    shared spec: the Factory never decides an action from the words in ``msg``.
    ``fields`` are typed intake fields the request carries beside it (the chat
    body's ``country`` / ``currency`` / ``vertical``), as the intake line sends
    what the user typed."""
    typed = typed_action(action, value) if action else {}
    last_err = None
    for attempt in range(retries + 1):
        rq = urllib.request.Request(
            BASE + f"/v1/sessions/{sid}/chat", method="POST",
            data=json.dumps({"message": msg, **typed, **dict(fields or {})}).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
        )
        try:
            return urllib.request.urlopen(rq, timeout=300).read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in TRANSIENT and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return f"event: error\ndata: {e.code} {e.reason}\n\n"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            return f"event: error\ndata: {type(e).__name__}\n\n"
    return f"event: error\ndata: {last_err}\n\n"


def sse_events(raw, name):
    """The decoded payloads of every ``name`` event in an SSE reply (their data
    is a JSON object, or a JSON string carrying one)."""
    out, lines = [], raw.splitlines()
    for i, line in enumerate(lines):
        if line != f"event: {name}" or i + 1 >= len(lines) or not lines[i + 1].startswith("data: "):
            continue
        try:
            payload = json.loads(lines[i + 1][6:])
            if isinstance(payload, str):
                payload = json.loads(payload)
        except ValueError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out



def info_events(raw):
    """The decoded ``info`` payloads of an SSE reply."""
    return sse_events(raw, "info")

#: Seconds the smoke waits for a build to reach a terminal state before the
#: export check reads DEAD. Covers the Factory's writer budget plus the gate.
BUILD_WAIT_S = int(os.environ.get("SMOKE_BUILD_WAIT_S", "5400"))
#: The build level the smoke chooses, as the user would on the Floor -- an
#: explicit typed choice (production: the full ladder, the full floor).
SMOKE_BUILD_LEVEL = "production"


class ProcessIsolationWatch:
    """Samples the server's process-isolation answer while the build runs.

    The 2026-10-07/08 restarts: a coding agent's shell shared the server's
    session, so its group-wide kill took the server down mid-build. The
    server reports (counts only, smoke-gated) whether any live agent shares
    its session and whether the spawn path still asks for a new one; the
    smoke samples it while its own writer runs, so a regression reads DEAD
    here instead of killing a customer's build.
    """

    PATH = "/v1/auth/smoke-process-isolation"
    #: One sample a minute is enough to catch a writer mid-pass.
    INTERVAL_S = 60

    def __init__(self, gate, clock=time.time):
        self.gate = (gate or "").strip()
        self.clock = clock
        self.samples = []
        self.errors = []
        self._last = None

    def sample(self):
        if not self.gate:
            return
        now = self.clock()
        if self._last is not None and now - self._last < self.INTERVAL_S:
            return
        self._last = now
        s, body = req(
            "GET", self.PATH, extra_headers={"X-Smoke-Gate": self.gate}, retries=1
        )
        if s == 200 and isinstance(body, dict):
            self.samples.append(body)
        elif s not in TRANSIENT:
            self.errors.append(s)

    def record(self):
        name = "agent shell isolated from the server"
        if not self.gate:
            print(f"[SKIP] {name} — needs SMOKE_GATE_TOKEN (the deploy path sets it)")
            return
        if not self.samples:
            self._last = None
            self.sample()
        if not self.samples:
            check(name, False, f"no answer (http={self.errors[-1] if self.errors else 'transient'})")
            return
        broken = [b for b in self.samples if not b.get("isolated")]
        seen = max(int(b.get("live_agents") or 0) for b in self.samples)
        shared = max(int(b.get("agents_in_server_session") or 0) for b in self.samples)
        spawn = all(bool(b.get("spawn_isolated")) for b in self.samples)
        check(
            name,
            not broken and not self.errors,
            f"samples={len(self.samples)} max_live_agents={seen} "
            f"agents_in_server_session={shared} spawn_isolated={spawn}"
            + (f" errors={self.errors}" if self.errors else ""),
        )
        # PID-1 survives a group-wide kill from an agent's shell: the server
        # ran ``kill -TERM 0`` from an agent-spawned child and still answered
        # (the answer arriving at all is the survival), and the child died of
        # its own signal. A POSIX server that cannot judge it is not a pass.
        posix = [b for b in self.samples if b.get("posix")]
        contained = [b.get("group_kill_contained") for b in posix]
        check(
            "server survives kill -TERM 0 from an agent shell",
            all(c is True for c in contained),
            f"probes={len(contained)} contained={contained.count(True)} "
            f"not_contained={contained.count(False)} unjudged={contained.count(None)}",
        )
        # 2026-10-09: with every agent in its own session the server still
        # stopped on a signal aimed at it by name or pid while a WRITER ran.
        # While an agent runs, the server refuses exit signals; a POSIX server
        # without that guard reads DEAD here.
        guarded = [b.get("exit_signals_guarded") for b in posix]
        refused = max((int(b.get("exit_signals_refused") or 0) for b in posix), default=0)
        check(
            "server refuses exit signals while an agent runs",
            all(g is True for g in guarded),
            f"samples={len(guarded)} guarded={guarded.count(True)} refused_since_boot={refused}",
        )


class BuildDeadline:
    """When a poller stops waiting: the build's OWN declared deadline.

    build-status carries ``build.deadline.deadline_in_s`` -- the remaining
    time to the latest wall the build itself recorded (run ceiling, a rework
    round, the Store-gate handoff), on the server's clock. Each read re-bases
    it on this client's clock, so a lifted wall extends the wait and clock
    skew never matters. An outage keeps the last declared end. ``fallback_s``
    applies ONLY to a server that declares nothing (older than the field);
    once a deadline is declared it is ignored. Owner rule: timeouts live at
    the phase-wall ceiling only -- live 2026-10-08, smoke A's fixed ~58 min
    wait read DEAD on a healthy build at "2/5 writer working".
    """

    def __init__(self, fallback_s, clock):
        self.clock = clock
        self.end = clock() + float(fallback_s)
        self.declared = False

    def read(self, build):
        decl = build.get("deadline") if isinstance(build, dict) else None
        remaining = decl.get("deadline_in_s") if isinstance(decl, dict) else None
        if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
            self.end = self.clock() + float(remaining)
            self.declared = True

    def passed(self):
        return self.clock() >= self.end


def wait_for_export(sid, tok, *, wait_s, sleep=time.sleep, clock=time.time, observe=None):
    """Poll the export until the build's own terminal state.

    A real build runs COLLECTOR -> STORE gate in 30-45 min (the writer alone
    is 20-40). 900 s was shorter than any healthy build, so "export zip"
    read DEAD on every build that did not fail fast (2026-10-04, 080652d9:
    "still being built 2/5"). Wait for the build's own terminal state.

    A gateway answer (TRANSIENT) is a platform restart, not the build's
    verdict: the server resumes an orphaned build on boot, so keep polling.
    Live 2026-10-08 03:19 UTC: smoke B read DEAD on "http=504" while the
    restarted server was already resuming its WRITER.
    """
    s, blob = 0, b""
    build = {}
    deadline = BuildDeadline(wait_s, clock)
    last_print = 0.0
    while not deadline.passed():
        s, blob = req("GET", f"/v1/sessions/{sid}/product/package", token=tok, raw=True)
        _st, status_body = req(
            "GET", f"/v1/sessions/{sid}/product/build-status", token=tok
        )
        payload = status_body if isinstance(status_body, dict) else {}
        nested = payload.get("build")
        build = nested if isinstance(nested, dict) else payload
        deadline.read(build)
        state = build.get("state")
        if s == 200:
            break
        if s == 409 and state in {"failed", "stalled"}:
            break
        if s != 409 and s not in TRANSIENT:
            break
        if observe is not None:
            observe()
        now = clock()
        if now - last_print >= 30:
            print(
                f"  waiting for zip: http={s} build={state} "
                f"{build.get('phases_done')}/{build.get('phases_total')} "
                f"{(build.get('activity') or '')[:80]}"
            )
            last_print = now
        sleep(5)
    return s, blob, build


#: Upper bound on answered question rounds. The server caps elicitation
#: (MAX_ELICITATION_ROUNDS) and turns the next ask into a draft, so the loop
#: ends by the product's own contract; this only stops a server that broke it.
MAX_SMOKE_ELICITATION_TURNS = 8


def chat_until_drafted(sid, tok, brief):
    """Hold the brief conversation the way a customer does.

    The Floor asks clarifying questions before it drafts (an ``info`` event
    with ``elicitation: true``); the customer ends that by pressing Draft --
    the TYPED ``draft`` action carrying the brief -- not by typing words the
    Factory would have to interpret. Returns every SSE reply joined, and how
    many turns it took.
    """
    raw = chat(sid, tok, brief)
    replies, turns = [raw], 1
    while any(e.get("elicitation") for e in info_events(raw)) and turns < MAX_SMOKE_ELICITATION_TURNS:
        raw = chat(sid, tok, brief, action="draft")
        replies.append(raw)
        turns += 1
    return "".join(replies), turns


def verified_tokens():
    """Return (token, token_b) for verified smoke principals, or (None, None)."""
    gate = os.environ.get("SMOKE_GATE_TOKEN", "").strip()
    if gate:
        s, body = req(
            "POST", "/v1/auth/smoke-login", {},
            extra_headers={"X-Smoke-Gate": gate},
        )
        tok = (body or {}).get("login_token")
        tok_b = (body or {}).get("login_token_b")
        if s == 200 and tok:
            return tok, tok_b
        print(f"smoke-login http={s} (need SMOKE_GATE_TOKEN matching Render secret)")
        return None, None

    email = os.environ.get("SMOKE_EMAIL", "").strip()
    password = os.environ.get("SMOKE_PASSWORD", "").strip()
    if email and password:
        s, body = req("POST", "/v1/auth/login", {"email": email, "password": password})
        tok = (body or {}).get("login_token") if s == 200 else None
        if not tok or not (body or {}).get("email_verified"):
            print(f"SMOKE_EMAIL login http={s} verified={(body or {}).get('email_verified')}")
            return None, None
        tok_b = None
        email2 = os.environ.get("SMOKE_EMAIL_2", "").strip()
        password2 = os.environ.get("SMOKE_PASSWORD_2", "").strip()
        if email2 and password2:
            s2, b2 = req("POST", "/v1/auth/login", {"email": email2, "password": password2})
            if s2 == 200 and (b2 or {}).get("email_verified"):
                tok_b = (b2 or {}).get("login_token")
        return tok, tok_b

    return None, None


def verified_token_roster(count):
    """``count`` distinct verified principals, index 0 first (the smoke's own).

    The release cycle runs repro builds beside the smoke, one account each.
    With SMOKE_GATE_TOKEN the server issues the roster (smoke-login
    ``principals``); with the email pair there are at most two. Returns what
    could be had -- possibly fewer than asked -- and the CALLER fails closed
    on a short roster: two builds on one account share its tenant slot and
    would run serially while reporting a parallel cycle.
    """
    gate = os.environ.get("SMOKE_GATE_TOKEN", "").strip()
    if gate:
        s, body = req(
            "POST", "/v1/auth/smoke-login", {"principals": int(count)},
            extra_headers={"X-Smoke-Gate": gate},
        )
        body = body if isinstance(body, dict) else {}
        if s != 200:
            print(f"smoke-login (principals={count}) http={s} detail={body.get('detail')}")
            return []
        tokens = body.get("login_tokens")
        if not isinstance(tokens, list):
            # A server older than the roster answers with the fixed pair.
            tokens = [body.get("login_token"), body.get("login_token_b")]
        return [t for t in tokens if t][: int(count)]
    tok, tok_b = verified_tokens()
    return [t for t in (tok, tok_b) if t][: int(count)]


#: Headers a browser must receive from the API origin, and from the
#: frontend. Checked against the LIVE response, not against a file.
#:
#: Why this exists: until the Node serving path (``frontend/serve.mjs``)
#: the frontend's headers were declared in three places --
#: ``frontend/public/_headers``, a ``headers:`` block on a Render static
#: site in ``render.yaml``, and ``frontend/vite.config.ts`` preview -- and
#: two tests asserted those declarations. All three declarations and both
#: tests were green on 2026-09-10 while ``curl -I https://www.cerebrum-dev.com/``
#: returned exactly one of them (``x-content-type-options``). ``render.yaml``
#: is documented as not applied, and ``_headers`` is a Netlify/Pages
#: convention a Render static site does not read, so the declarations were
#: true and the production response was not. A twin that reads the file can
#: never catch that; this one reads the wire. Do not weaken this check.
API_REQUIRED_HEADERS = (
    "content-security-policy",
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "strict-transport-security",
)
FRONTEND_REQUIRED_HEADERS = API_REQUIRED_HEADERS
DEFAULT_FRONTEND = "https://www.cerebrum-dev.com"


def live_headers(url):
    """Lowercased response headers for a GET, or None when unreachable."""
    try:
        rq = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(rq, timeout=60) as resp:
            return {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:  # a 4xx still carries headers
        return {k.lower(): v for k, v in exc.headers.items()}
    except Exception as exc:  # noqa: BLE001 -- unreachable is a DEAD check
        print(f"    header probe failed for {url}: {exc}")
        return None


def record_security_headers(api_base=None, frontend=None):
    """LIVE/DEAD per origin for the headers a browser actually receives."""
    api_base = (api_base or BASE).rstrip("/")
    frontend = (frontend or os.environ.get("SMOKE_FRONTEND") or DEFAULT_FRONTEND).rstrip("/")

    for label, url, required in (
        ("api", api_base + "/health", API_REQUIRED_HEADERS),
        ("frontend", frontend + "/", FRONTEND_REQUIRED_HEADERS),
    ):
        got = live_headers(url)
        if got is None:
            check(f"security headers ({label})", False, f"{url} unreachable")
            continue
        missing = [h for h in required if h not in got]
        check(
            f"security headers ({label})",
            not missing,
            f"{url} missing={missing}" if missing else f"{url} all {len(required)} present",
        )


def record_unauthenticated_surface(health=None, ready=None, version=None):
    """LIVE/DEAD lines for the public ops surface (no principal)."""
    if health is None:
        health = req("GET", "/health")
    if ready is None:
        ready = req("GET", "/ready")
    if version is None:
        version = req("GET", "/version")
    hs, h = health
    h = _mapping(h)
    check("health endpoint", hs == 200 and h.get("status") == "ok", f"status={h.get('status')}")
    rs, r = ready
    r = _mapping(r)
    check(
        "ready endpoint",
        rs == 200 and r.get("status") == "ready",
        f"http={rs} status={r.get('status')}",
    )
    vs, v = version
    v = _mapping(v)
    sha = v.get("git_sha") or ""
    check("version endpoint", vs == 200 and bool(sha), f"http={vs} sha={sha[:12] if sha else None}")
    return hs, h, rs, r, vs, v


def main():
    global BASE
    BASE = resolve_base(sys.argv)
    print(f"post-deploy smoke against {BASE}\n")
    FAILURES.clear()

    if not wait_for_ready():
        return finish()

    hs, h, _rs, _r, _vs, _v = record_unauthenticated_surface()
    record_security_headers()

    if not has_gated_credentials():
        emit_gated_skip_annotation()
        return finish()

    redis = h.get("redis") or {}
    check("redis rate limiting configured", bool(redis.get("configured") and redis.get("ok")), f"redis={redis}")

    email = f"smoke-{uuid.uuid4().hex[:8]}@factory.dev"
    s, r = req("POST", "/v1/auth/register", {"email": email, "password": "Smoke!23456"})
    check("auth register", s in (200, 201) and r.get("login_token"), f"http={s}")
    unverified = r.get("login_token")
    if not unverified:
        return finish()

    s_denied, denied = req("POST", "/v1/sessions/", {}, token=unverified)
    check(
        "unverified session denied",
        s_denied == 403 and (denied or {}).get("detail") == "email_not_verified",
        f"http={s_denied}",
    )

    tok, tok_b = verified_tokens()
    if not tok:
        check(
            "session create",
            False,
            "no verified principal (set SMOKE_GATE_TOKEN or SMOKE_EMAIL+SMOKE_PASSWORD)",
        )
        return finish()

    s, r = req("POST", "/v1/sessions/", {}, token=tok)
    check("session create", s in (200, 201) and r.get("session_id"), f"http={s}")
    sid = r.get("session_id")
    if not sid:
        return finish()

    # LLM drafting: off-table brief; fallback fingerprint = 2 caps, empty block_ids
    raw, turns = chat_until_drafted(
        sid, tok,
        "Build me a vineyard management platform for a family winery: "
        "track fermentation tanks, barrel inventory across two cellars, "
        "harvest scheduling by sugar readings, and club member shipments.",
    )
    check("chat blueprint event", "blueprint" in raw, f"sse_bytes={len(raw)} turns={turns}")
    s, d = req("GET", f"/v1/sessions/{sid}/product", token=tok)
    if not isinstance(d, dict):
        d = {}
    bp = d.get("blueprint") or {}
    caps = bp.get("capabilities") or []
    populated = [c for c in caps if c.get("block_ids")]
    drafting_ok = (
        s == 200
        and (bp.get("drafting_mode") == "architect_llm")
        and len(caps) >= 3
        and len(populated) >= 2
    )
    check("LLM drafting (not fallback)", drafting_ok,
          f"http={s} mode={bp.get('drafting_mode')} caps={len(caps)} populated={len(populated)} "
          f"(fallback fingerprint: caps=2 populated=0)")
    if not drafting_ok:
        return finish()

    # The build level is the user's typed choice and the Floor refuses to
    # start without one. The smoke chooses production -- the full ladder and
    # the full acceptance floor -- so "export zip" below measures the
    # strictest bar, never a lowered one.
    # Change proposes the level; only the typed Confirm stores it.
    chat(sid, tok, "", action="set_build_level", value=SMOKE_BUILD_LEVEL)
    raw_level = chat(sid, tok, "", action="confirm_intake")
    # The intake event echoes the session's declared fields back. Its data
    # is JSON-encoded, so decode it: a substring test on the raw stream
    # (escaped quotes) could never match and read DEAD on a correct confirm.
    intake = (sse_events(raw_level, "intake") or [{}])[-1]
    declared = intake.get("declared") or {}
    check(
        "build level confirmed",
        declared.get("build_level") == SMOKE_BUILD_LEVEL and intake.get("proposal") is None,
        f"declared={declared} proposal={intake.get('proposal')}",
    )

    raw2 = chat(sid, tok, "", action="approve")
    check("approve -> generation event", "generation" in raw2)

    s, d = req("GET", f"/v1/sessions/{sid}/product", token=tok)
    gen = d.get("generation") or {}
    check("generation recorded", bool(gen.get("product_id")), f"product_id={gen.get('product_id')}")

    # The coding-agent runner builds in the background. A 409 here means
    # "still writing", not a dead kernel — poll until the ledger is terminal.
    # build-status nests the ledger under "build".
    isolation = ProcessIsolationWatch(os.environ.get("SMOKE_GATE_TOKEN", ""))
    s, blob, build = wait_for_export(
        sid, tok, wait_s=BUILD_WAIT_S, observe=isolation.sample
    )
    isolation.record()
    ok = s == 200 and isinstance(blob, (bytes, bytearray)) and blob[:2] == b"PK"
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist() if ok else []
    evidence = f"http={s} files={len(names)}"
    if build.get("state"):
        evidence += (
            f" build={build.get('state')} "
            f"{build.get('phases_done')}/{build.get('phases_total')}"
        )
    if s == 409:
        err = blob
        if isinstance(err, (bytes, bytearray)):
            try:
                err = json.loads(err)
            except Exception:
                err = {"raw": err[:200].decode(errors="replace")}
        if isinstance(err, dict) and err.get("detail"):
            evidence += f" detail={str(err.get('detail'))[:180]}"
    check("export zip", ok and len(names) > 5, evidence)

    raw3 = chat(sid, tok, "did you deploy my platform already? give me the URL")
    grounded = (("onrender.com" not in raw3 and "http" not in raw3.lower())
                or "don't have" in raw3 or "not" in raw3.lower())
    check("grounding (no invented deploy/URL)", grounded, raw3[:120].replace("\n", " "))

    # cross-account isolation — second verified principal, never the unverified register token
    if tok_b:
        s2, _ = req("GET", f"/v1/sessions/{sid}/product", token=tok_b)
        check("cross-account isolation", s2 == 404, f"http={s2} (expect 404)")
    else:
        check(
            "cross-account isolation",
            False,
            "no second verified principal (SMOKE_GATE_TOKEN or SMOKE_EMAIL_2)",
        )

    s, b = req("GET", "/v1/billing/status", token=tok)
    check("billing status structured", s == 200 and isinstance(b, dict), f"http={s}")
    s, c = req("POST", "/v1/billing/checkout", {}, token=tok)
    honest = s == 503 or (s == 200 and (c.get("url") or c.get("checkout_url")))
    check("billing checkout honest", honest, f"http={s} (503 stripe_not_configured or real url)")

    finish()


def finish():
    print()
    if FAILURES:
        print(f"SMOKE FAIL: {len(FAILURES)} dead kernel(s): {', '.join(FAILURES)}")
        sys.exit(1)
    if not has_gated_credentials():
        print("SMOKE PASS: unauthenticated surface only; gated checks skipped.")
        sys.exit(0)
    print("SMOKE PASS: every kernel live.")
    sys.exit(0)


if __name__ == "__main__":
    main()
