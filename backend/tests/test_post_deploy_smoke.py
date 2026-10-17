"""Post-deploy smoke: wait for ready; skip gated checks without a token."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = REPO_ROOT / "scripts" / "post_deploy_smoke.py"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "post-deploy-smoke.yml"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("post_deploy_smoke", SMOKE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smoke(monkeypatch):
    # CI sets GITHUB_SHA to the PR commit. Tests that do not opt into the
    # SHA gate must see a local-run env so wait_for_ready stays health/ready.
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    mod = _load_smoke()
    mod.FAILURES.clear()
    # These tests drive main() with a stubbed req(). The security-header
    # probe is the one check that does NOT go through req -- it reads raw
    # response headers -- so it would reach the real internet from a unit
    # test. Stub it healthy by default; the tests that are ABOUT it override
    # this and assert the DEAD path.
    mod.live_headers = lambda url: {h: "stub" for h in mod.API_REQUIRED_HEADERS}
    return mod


class TestHasGatedCredentials:
    def test_empty_token_is_unset(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        assert smoke.has_gated_credentials() is False

    def test_whitespace_token_is_unset(self, smoke, monkeypatch):
        monkeypatch.setenv("SMOKE_GATE_TOKEN", "   ")
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        assert smoke.has_gated_credentials() is False

    def test_token_counts(self, smoke, monkeypatch):
        monkeypatch.setenv("SMOKE_GATE_TOKEN", "render-secret")
        assert smoke.has_gated_credentials() is True

    def test_email_pair_counts(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.setenv("SMOKE_EMAIL", "ops@example.com")
        monkeypatch.setenv("SMOKE_PASSWORD", "secret")
        assert smoke.has_gated_credentials() is True


class TestWaitForReady:
    def test_retries_until_ready_then_returns_true(self, smoke):
        calls = {"n": 0}

        def fake_req(method, path, **kwargs):
            calls["n"] += 1
            if calls["n"] < 4:
                return 503, {"status": "not_ready"}
            if path == "/health":
                return 200, {"status": "ok"}
            return 200, {"status": "ready"}

        slept = []
        assert smoke.wait_for_ready(
            timeout_s=30,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=slept.append,
        )
        assert calls["n"] >= 4
        assert smoke.FAILURES == []

    def test_timeout_records_dead_and_returns_false(self, smoke):
        def fake_req(method, path, **kwargs):
            return 502, {"raw": "bounce"}

        ok = smoke.wait_for_ready(
            timeout_s=0,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=lambda _s: None,
        )
        assert ok is False
        assert "health/ready wait" in smoke.FAILURES

    def test_surface_is_ready_requires_health_ok_and_ready_status(self, smoke):
        assert smoke.surface_is_ready(200, {"status": "ok"}, 200, {"status": "ready"})
        assert not smoke.surface_is_ready(200, {"status": "ok"}, 503, {"status": "not_ready"})
        assert not smoke.surface_is_ready(502, {}, 502, {})
        # HTTP 200 with a missing/degraded health body is a bounce, not ready.
        assert not smoke.surface_is_ready(200, {}, 200, {"status": "ready"})
        assert not smoke.surface_is_ready(200, {"status": "degraded"}, 200, {"status": "ready"})
        assert not smoke.surface_is_ready(200, None, 200, {"status": "ready"})

    def test_bounce_then_matching_sha_succeeds(self, smoke, monkeypatch):
        want = "55f5c8d3b117211fe2e436e8ec76a13483ae79f7"
        monkeypatch.setenv("GITHUB_SHA", want)
        rounds = {"n": 0}

        def fake_req(method, path, **kwargs):
            # Round 1: mid-bounce — health HTTP 200 but status missing, old SHA.
            # Round 2+: health ok, ready, matching SHA (prefix or full).
            if path == "/health":
                rounds["n"] += 1
                if rounds["n"] == 1:
                    return 200, {}
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                if rounds["n"] == 1:
                    return 200, {"git_sha": "725425e636c30774fc13fb82f7318313dca12a69"}
                return 200, {"git_sha": want}
            raise AssertionError(path)

        slept = []
        assert smoke.wait_for_ready(
            timeout_s=30,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=slept.append,
        )
        assert rounds["n"] >= 2
        assert smoke.FAILURES == []

    def test_timeout_when_sha_never_matches_is_dead(self, smoke, monkeypatch):
        monkeypatch.setenv("GITHUB_SHA", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")

        def fake_req(method, path, **kwargs):
            if path == "/health":
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                return 200, {"git_sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}
            raise AssertionError(path)

        ok = smoke.wait_for_ready(
            timeout_s=0,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=lambda _s: None,
        )
        assert ok is False
        assert "health/ready wait" in smoke.FAILURES

    def test_github_sha_unset_does_not_sha_gate(self, smoke, monkeypatch):
        monkeypatch.delenv("GITHUB_SHA", raising=False)

        def fake_req(method, path, **kwargs):
            assert path != "/version", "must not probe /version when GITHUB_SHA unset"
            if path == "/health":
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            raise AssertionError(path)

        assert smoke.wait_for_ready(
            timeout_s=5,
            interval_s=0.01,
            req_fn=fake_req,
            sleeper=lambda _s: None,
        )
        assert smoke.FAILURES == []
        # Unique-prefix helper: full vs short, and refuse non-unique shorts.
        full = "55f5c8d3b117211fe2e436e8ec76a13483ae79f7"
        assert smoke.git_sha_matches(full, full)
        assert smoke.git_sha_matches(full, "55f5c8d")
        assert smoke.git_sha_matches("55f5c8d", full)
        assert not smoke.git_sha_matches(full, "55f5c8e")
        assert not smoke.git_sha_matches(full, "55f")
        assert smoke.git_sha_matches("anything", "")


class TestUnauthenticatedSkip:
    def test_skip_annotation_is_a_notice_not_a_failure(self, smoke, capsys, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        smoke.emit_gated_skip_annotation()
        out = capsys.readouterr().out
        assert "SMOKE SKIP:" in out
        assert "::notice title=Post-deploy smoke::" in out
        assert "gated" in out.lower()
        assert smoke.FAILURES == []

    def test_record_unauthenticated_surface_does_not_require_redis(self, smoke):
        def fake_req(method, path, **kwargs):
            if path == "/health":
                return 200, {"status": "ok", "redis": {"configured": False}}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                return 200, {"git_sha": "abc123456789"}
            raise AssertionError(path)

        smoke.req = fake_req
        smoke.record_unauthenticated_surface(
            health=fake_req("GET", "/health"),
            ready=fake_req("GET", "/ready"),
            version=fake_req("GET", "/version"),
        )
        assert smoke.FAILURES == []
        assert "redis" not in " ".join(smoke.FAILURES)

    def test_finish_passes_when_ungated_and_clean(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        smoke.FAILURES.clear()
        with pytest.raises(SystemExit) as exc:
            smoke.finish()
        assert exc.value.code == 0

    def test_finish_fails_on_timeout_even_without_token(self, smoke, monkeypatch):
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        smoke.FAILURES.append("health/ready wait")
        with pytest.raises(SystemExit) as exc:
            smoke.finish()
        assert exc.value.code == 1

    def test_main_bounce_then_sha_then_unauth_skip_passes(self, smoke, monkeypatch, capsys):
        """CI path: wait out bounce + matching SHA, then skip-pass without a token."""
        import sys

        want = "55f5c8d3b117211fe2e436e8ec76a13483ae79f7"
        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        monkeypatch.setenv("GITHUB_SHA", want)
        monkeypatch.setenv("SMOKE_READY_WAIT_S", "30")
        monkeypatch.setenv("SMOKE_READY_INTERVAL_S", "0.01")
        monkeypatch.setattr(sys, "argv", ["post_deploy_smoke.py", "https://api.cerebrum-dev.com"])

        rounds = {"n": 0}

        def fake_req(method, path, **kwargs):
            if path == "/health":
                rounds["n"] += 1
                if rounds["n"] == 1:
                    return 200, {}  # status=None — the #283 job failure mode
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                if rounds["n"] == 1:
                    return 200, {"git_sha": "725425e636c30774fc13fb82f7318313dca12a69"}
                return 200, {"git_sha": want}
            raise AssertionError(path)

        smoke.req = fake_req
        with pytest.raises(SystemExit) as exc:
            smoke.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "SMOKE SKIP:" in out
        assert "SMOKE PASS: unauthenticated surface only" in out
        assert "SMOKE FAIL" not in out
        assert rounds["n"] >= 2
        assert smoke.FAILURES == []

    def test_main_unset_sha_does_not_require_version_match(self, smoke, monkeypatch, capsys):
        import sys

        monkeypatch.delenv("SMOKE_GATE_TOKEN", raising=False)
        monkeypatch.delenv("SMOKE_EMAIL", raising=False)
        monkeypatch.delenv("SMOKE_PASSWORD", raising=False)
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        monkeypatch.setenv("SMOKE_READY_WAIT_S", "5")
        monkeypatch.setenv("SMOKE_READY_INTERVAL_S", "0.01")
        monkeypatch.setattr(sys, "argv", ["post_deploy_smoke.py", "https://api.cerebrum-dev.com"])

        def fake_req(method, path, **kwargs):
            if path == "/health":
                return 200, {"status": "ok"}
            if path == "/ready":
                return 200, {"status": "ready"}
            if path == "/version":
                return 200, {"git_sha": "not-the-commit-under-test"}
            raise AssertionError(path)

        smoke.req = fake_req
        with pytest.raises(SystemExit) as exc:
            smoke.main()
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "SMOKE SKIP:" in out
        assert "SMOKE PASS: unauthenticated surface only" in out


class TestResolveBaseAndWorkflow:
    def test_import_does_not_treat_pytest_argv_as_host(self, smoke):
        assert smoke.BASE == smoke.DEFAULT_BASE
        assert smoke.resolve_base(["pytest", "tests/test_post_deploy_smoke.py"]) == (
            smoke.DEFAULT_BASE
        )
        assert (
            smoke.resolve_base(["smoke", "https://api.cerebrum-dev.com"])
            == "https://api.cerebrum-dev.com"
        )

    def test_workflow_does_not_hardcode_a_token(self):
        text = WORKFLOW_PATH.read_text(encoding="utf-8")
        assert "${{ secrets.SMOKE_GATE_TOKEN }}" in text
        assert "SMOKE_READY_WAIT_S" in text
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("SMOKE_GATE_TOKEN:") and "secrets." not in stripped:
                raise AssertionError(f"hardcoded smoke token line: {line}")

    def test_script_still_retries_transient_and_points_at_live_host(self):
        text = SMOKE_PATH.read_text(encoding="utf-8")
        assert "https://api.cerebrum-dev.com" in text
        assert "cerebrumdev-backend.onrender.com" not in text
        assert "TRANSIENT" in text and "502" in text
        assert "SMOKE_READY_WAIT_S" in text
        assert "has_gated_credentials" in text
        assert "GITHUB_SHA" in text
        assert "git_sha_matches" in text


class TestSecurityHeaderProbe:
    """The live twin: headers a browser receives, not headers a file declares.

    Three files declare the frontend's headers and two tests assert those
    declarations. On 2026-09-10 all five were green while
    ``curl -I https://www.cerebrum-dev.com/`` returned one of the five.
    These tests are about the check that can tell the difference.
    """

    def test_a_complete_header_set_is_live(self, smoke, capsys):
        smoke.FAILURES.clear()
        smoke.live_headers = lambda url: {
            h: "stub" for h in smoke.API_REQUIRED_HEADERS
        }
        smoke.record_security_headers("https://api.example.test", "https://www.example.test")
        assert smoke.FAILURES == []
        assert "[LIVE] security headers (api)" in capsys.readouterr().out

    def test_a_missing_header_is_dead_and_names_it(self, smoke, capsys):
        smoke.FAILURES.clear()

        def partial(url):
            got = {h: "stub" for h in smoke.API_REQUIRED_HEADERS}
            if "www." in url:  # the frontend, as measured on 2026-09-10
                return {"x-content-type-options": "nosniff"}
            return got

        smoke.live_headers = partial
        smoke.record_security_headers("https://api.example.test", "https://www.example.test")
        assert smoke.FAILURES == ["security headers (frontend)"]
        out = capsys.readouterr().out
        assert "[DEAD] security headers (frontend)" in out
        assert "content-security-policy" in out, "a DEAD check must name what is missing"

    def test_an_unreachable_origin_is_dead_not_silently_ok(self, smoke):
        smoke.FAILURES.clear()
        smoke.live_headers = lambda url: None
        smoke.record_security_headers("https://api.example.test", "https://www.example.test")
        assert len(smoke.FAILURES) == 2


class TestSmokeWaitsForTheRollout:
    """A smoke that races the deploy measures the build queue, not the deploy.

    It used to start on the same push as deploy-aws with a 5-minute window, so
    every deploy read red and every red read as "AWS was slow". The smoke now
    runs after the deploy, fails if the deploy failed, and waits (cap 20 min)
    for the DEPLOYED commit; the deploy itself waits for ECS to report the
    rollout COMPLETED.
    """

    ROOT = Path(__file__).resolve().parents[2]

    def _wf(self, name):
        return (self.ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")

    def test_smoke_runs_after_deploy_not_beside_it(self):
        import yaml

        wf = yaml.safe_load(self._wf("post-deploy-smoke.yml"))
        on = wf.get("on") or wf.get(True)
        assert "push" not in on
        assert on["workflow_run"]["workflows"] == ["deploy-aws"]
        text = self._wf("post-deploy-smoke.yml")
        assert "workflow_run.conclusion != 'success'" in text
        assert 'SMOKE_READY_WAIT_S: "1200"' in text
        # The deployed head is the commit the cycle must see live: the resolve
        # job waits for /version to serve it, and every smoke expects exactly
        # the commit resolve verified.
        assert "EXPECT: ${{ github.event.workflow_run.head_sha" in text
        smoke_env = wf["jobs"]["live-smoke"]["steps"][-1]["env"]
        assert smoke_env["SMOKE_EXPECTED_SHA"] == "${{ needs.resolve.outputs.sha }}"

    def test_deploy_waits_for_ecs_rollout_completed_with_a_cap(self):
        text = self._wf("deploy-aws.yml")
        assert "rolloutState" in text
        assert "COMPLETED) break" in text and "FAILED)" in text
        assert "1200" in text

    def test_expected_sha_prefers_the_deployed_head(self, monkeypatch):
        mod = _load_smoke()
        monkeypatch.setenv("GITHUB_SHA", "a" * 40)
        monkeypatch.setenv("SMOKE_EXPECTED_SHA", "b" * 40)
        assert mod.expected_git_sha() == "b" * 40
        monkeypatch.delenv("SMOKE_EXPECTED_SHA")
        assert mod.expected_git_sha() == "a" * 40


def _info(payload: dict) -> str:
    import json

    return "event: info\ndata: " + json.dumps(json.dumps(payload)) + "\n\n"


class TestChatUntilDrafted:
    def test_answers_questions_until_the_floor_drafts(self, smoke, monkeypatch):
        replies = iter([
            _info({"sse": "info", "elicitation": True}),
            _info({"sse": "info", "elicitation": True}),
            "event: blueprint\ndata: {}\n\n",
        ])
        sent = []

        def _chat(sid, tok, msg, action=None, value=None):
            sent.append((msg, action))
            return next(replies)

        monkeypatch.setattr(smoke, "chat", _chat)
        raw, turns = smoke.chat_until_drafted("s", "t", "brief")
        assert turns == 3 and "event: blueprint" in raw
        # The brief goes in as free text; ending the questions is the TYPED
        # draft action, never a sentence the Factory has to interpret.
        assert sent[0] == ("brief", None)
        assert sent[1:] == [("brief", "draft"), ("brief", "draft")]

    def test_a_first_turn_draft_needs_no_answer(self, smoke, monkeypatch):
        monkeypatch.setattr(smoke, "chat", lambda sid, tok, msg, **kw: "event: blueprint\ndata: {}\n\n")
        assert smoke.chat_until_drafted("s", "t", "brief")[1] == 1

    def test_a_server_that_never_stops_asking_is_bounded(self, smoke, monkeypatch):
        monkeypatch.setattr(smoke, "chat", lambda sid, tok, msg, **kw: _info({"elicitation": True}))
        raw, turns = smoke.chat_until_drafted("s", "t", "brief")
        assert turns == smoke.MAX_SMOKE_ELICITATION_TURNS and "blueprint" not in raw



def test_the_smoke_approves_with_the_typed_action(smoke):
    """No approve -> no build: the smoke starts the build with the typed
    approve action, never by sending the word 'approve' as chat text."""
    import inspect

    src = inspect.getsource(smoke)
    assert 'chat(sid, tok, "", action="approve")' in src
    assert 'chat(sid, tok, "approve")' not in src


def test_the_smoke_chooses_a_build_level_before_approve(smoke):
    """The Floor refuses to build without the user's typed level: the smoke
    chooses one explicitly, from the shared spec, BEFORE it approves."""
    import inspect

    src = inspect.getsource(smoke)
    choose = src.index('action="set_build_level", value=SMOKE_BUILD_LEVEL')
    confirm = src.index('chat(sid, tok, "", action="confirm_intake")')
    assert choose < confirm < src.index('chat(sid, tok, "", action="approve")')
    assert smoke.typed_action("set_build_level", smoke.SMOKE_BUILD_LEVEL) == {
        "action": "set_build_level",
        "value": smoke.SMOKE_BUILD_LEVEL,
    }


def test_chat_sends_the_typed_action_in_the_body(smoke, monkeypatch):
    import json

    seen = {}

    class _Resp:
        def read(self):
            return b"event: done\ndata: \n\n"

    def _urlopen(rq, timeout=None):
        seen["body"] = json.loads(rq.data.decode())
        return _Resp()

    monkeypatch.setattr(smoke.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(smoke, "BASE", "http://zorblat.test", raising=False)
    smoke.chat("s", "t", "", action="approve")
    assert seen["body"] == {"message": "", "action": "approve"}


def test_build_level_check_decodes_the_intake_event(smoke):
    """The intake event's data is JSON-encoded (escaped quotes on the wire).
    A raw-substring test could never match and read DEAD on a correct confirm
    (live 79449900); the check decodes the event instead."""
    live = (
        'event: intake\n'
        'data: "{\\"declared\\": {\\"vertical\\": null, \\"country\\": null, '
        '\\"currency\\": null, \\"build_level\\": \\"production\\"}, \\"proposal\\": null}"\n\n'
    )
    intake = smoke.sse_events(live, "intake")[-1]
    assert intake["declared"]["build_level"] == smoke.SMOKE_BUILD_LEVEL
    assert intake["proposal"] is None
    # The wrong level, or a pending proposal, must not pass.
    other = smoke.sse_events(live.replace("production", "prototype"), "intake")[-1]
    assert other["declared"]["build_level"] != smoke.SMOKE_BUILD_LEVEL
    pending = smoke.sse_events(live.replace('\\"proposal\\": null', '\\"proposal\\": {}'), "intake")[-1]
    assert pending["proposal"] is not None


def _export_answers(smoke, monkeypatch, package_https, state="building"):
    """Stub req(): the package endpoint answers ``package_https`` in turn;
    build-status answers ``state`` while the server is up."""
    seq = list(package_https)

    def fake_req(method, path, body=None, token=None, raw=False):
        if path.endswith("/product/package"):
            http = seq.pop(0) if len(seq) > 1 else seq[0]
            fake_req.last = http
            return http, (b"PK\x03\x04zip" if http == 200 else b"{}")
        if fake_req.last in smoke.TRANSIENT:
            return fake_req.last, {}
        return 200, {"build": {"state": "succeeded" if fake_req.last == 200 else state}}

    fake_req.last = 0
    monkeypatch.setattr(smoke, "req", fake_req)
    clock = {"t": 0.0}
    return dict(
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        clock=lambda: clock["t"],
    )


def test_a_restart_while_waiting_for_the_zip_is_not_the_builds_verdict(smoke, monkeypatch):
    """Live 2026-10-08 03:19 UTC: smoke B read the export DEAD on http=504
    while the restarted server was already resuming its WRITER."""
    kw = _export_answers(smoke, monkeypatch, [409, 504, 502, 503, 409, 200])
    http, blob, build = smoke.wait_for_export("s", "t", wait_s=600, **kw)
    assert http == 200 and blob[:2] == b"PK"


def test_a_failed_build_still_ends_the_wait(smoke, monkeypatch):
    kw = _export_answers(smoke, monkeypatch, [409], state="failed")
    http, _blob, build = smoke.wait_for_export("s", "t", wait_s=600, **kw)
    assert http == 409 and build.get("state") == "failed"


def test_a_non_transient_answer_still_ends_the_wait(smoke, monkeypatch):
    kw = _export_answers(smoke, monkeypatch, [409, 404])
    http, _blob, _build = smoke.wait_for_export("s", "t", wait_s=600, **kw)
    assert http == 404


def _declaring_server(smoke, monkeypatch, *, ready_at, deadline_in_s):
    """build-status declares the build's own deadline; the zip is ready at
    ``ready_at`` client seconds."""
    clock = {"t": 0.0}

    def fake_req(method, path, body=None, token=None, raw=False):
        now = clock["t"]
        if path.endswith("/product/package"):
            return (200, b"PK\x03\x04zip") if now >= ready_at else (409, b"{}")
        return 200, {"build": {
            "state": "succeeded" if now >= ready_at else "building",
            "deadline": {"deadline_in_s": deadline_in_s - now, "anchor": "RUN_STARTED"},
        }}

    monkeypatch.setattr(smoke, "req", fake_req)
    return dict(
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s),
        clock=lambda: clock["t"],
    )


def test_a_healthy_build_past_the_client_number_is_still_waited_for(smoke, monkeypatch):
    """Live 2026-10-08 (98d3a356): smoke A gave up after ~58 min on a build at
    "2/5 writer working" -- the build's own ceiling was hours away."""
    kw = _declaring_server(smoke, monkeypatch, ready_at=5000, deadline_in_s=7200)
    http, blob, _build = smoke.wait_for_export("s", "t", wait_s=3480, **kw)
    assert http == 200 and blob[:2] == b"PK"


def test_the_builds_own_deadline_passing_ends_the_wait(smoke, monkeypatch):
    kw = _declaring_server(smoke, monkeypatch, ready_at=10_000, deadline_in_s=300)
    http, _blob, _build = smoke.wait_for_export("s", "t", wait_s=9_000, **kw)
    assert http == 409
    assert kw["clock"]() < 400  # ended at the build's deadline, not the 9000 s fallback


def test_an_outage_keeps_the_last_declared_deadline(smoke, monkeypatch):
    clock = {"t": 0.0}

    def fake_req(method, path, body=None, token=None, raw=False):
        now = clock["t"]
        if 100 <= now < 4000:
            return 504, ({} if not raw else b"")
        if path.endswith("/product/package"):
            return (200, b"PK\x03\x04zip") if now >= 4000 else (409, b"{}")
        return 200, {"build": {"state": "building",
                               "deadline": {"deadline_in_s": 7200 - now}}}

    monkeypatch.setattr(smoke, "req", fake_req)
    http, _b, _ = smoke.wait_for_export(
        "s", "t", wait_s=60,
        sleep=lambda s: clock.__setitem__("t", clock["t"] + s), clock=lambda: clock["t"],
    )
    assert http == 200


def test_an_outage_longer_than_the_wait_ends_it(smoke, monkeypatch):
    kw = _export_answers(smoke, monkeypatch, [504])
    http, _blob, _build = smoke.wait_for_export("s", "t", wait_s=60, **kw)
    assert http == 504
