"""SEC-1: an event carrying each sensitive kind goes in; none comes out."""

from __future__ import annotations

import json
import sys
import types

import pytest

from app.core import event_scrub, observability
from app.core.event_scrub import REDACTED, is_secret_kind, scrub_event, scrub_text

# Each value below is synthetic and stands for one kind. The test asserts
# that the value is absent anywhere in the scrubbed event.
SECRET_CASES = [
    (
        "authorization header",
        {"request": {"headers": {"Authorization": "Bearer a1b2c3d4e5f6g7"}}},
        "a1b2c3d4e5f6g7",
    ),
    ("cookie header", {"request": {"headers": {"Cookie": "sid=q9w8e7r6t5"}}}, "q9w8e7r6t5"),
    ("request cookies", {"request": {"cookies": {"sid": "z1x2c3v4b5"}}}, "z1x2c3v4b5"),
    ("header pair list", {"request": {"headers": [["X-Api-Key", "k7j6h5g4f3"]]}}, "k7j6h5g4f3"),
    ("token field", {"extra": {"refresh_token": "t0k3nv4lu3x"}}, "t0k3nv4lu3x"),
    ("api key field", {"contexts": {"svc": {"api_key": "plainkey991"}}}, "plainkey991"),
    ("password field", {"extra": {"form": {"password": "hunter2-xyz"}}}, "hunter2-xyz"),
    ("pwd field", {"tags": {"pwd": "shortpw77"}}, "shortpw77"),
    (
        "uri connection string",
        {"message": "connect postgres://admin:s3cr3tpw@db.internal:5432/x"},
        "s3cr3tpw",
    ),
    ("kv connection string", {"message": "Server=db;User=u;Password=kvpass123;"}, "kvpass123"),
    ("pwd connection string", {"message": "host=db pwd=kvpass456 port=5"}, "kvpass456"),
    (
        "email in text",
        {"exception": {"values": [{"value": "no user jane.doe@example.org"}]}},
        "jane.doe@example.org",
    ),
    ("email field", {"user": {"email": "ops@example.net"}}, "ops@example.net"),
    (
        "bearer in text",
        {"breadcrumbs": {"values": [{"message": "sent bearer qwe.rty.uio"}]}},
        "qwe.rty.uio",
    ),
    (
        "token in url query",
        {"request": {"url": "https://h.example/cb?token=qs-tok-321&x=1"}},
        "qs-tok-321",
    ),
    ("request body", {"request": {"data": {"note": "body-text-555"}}}, "body-text-555"),
    ("request body string", {"request": {"body": "raw-body-666"}}, "raw-body-666"),
    (
        "frame locals",
        {
            "exception": {
                "values": [
                    {"stacktrace": {"frames": [{"function": "f", "vars": {"x": "local-var-777"}}]}}
                ]
            }
        },
        "local-var-777",
    ),
    (
        "provider key shape",
        {"message": "used sk-ABCDEFGHIJKLMNOPQRSTUV"},
        "sk-ABCDEFGHIJKLMNOPQRSTUV",
    ),
]


@pytest.mark.parametrize("label,event,secret", SECRET_CASES, ids=[c[0] for c in SECRET_CASES])
def test_no_secret_kind_leaves(label, event, secret):
    out = scrub_event(event)
    assert out is not None
    assert secret not in json.dumps(out)


@pytest.mark.parametrize(
    "event,path,expected",
    [
        (
            {"exception": {"values": [{"type": "ValueError", "value": "bad quantity 7"}]}},
            ("exception", "values", 0, "value"),
            "bad quantity 7",
        ),
        (
            {
                "exception": {
                    "values": [
                        {"stacktrace": {"frames": [{"function": "load_rows", "lineno": 41}]}}
                    ]
                }
            },
            ("exception", "values", 0, "stacktrace", "frames", 0, "function"),
            "load_rows",
        ),
        ({"tags": {"keywords": "a,b", "author": "team"}}, ("tags", "author"), "team"),
        (
            {"release": "0123456789abcdef0123456789abcdef01234567"},
            ("release",),
            "0123456789abcdef0123456789abcdef01234567",
        ),
        ({"level": "error", "headers": [["Accept", "text/html"]]}, ("headers", 0, 1), "text/html"),
    ],
)
def test_ordinary_error_still_reports(event, path, expected):
    out = scrub_event(event)
    for step in path:
        out = out[step]
    assert out == expected


@pytest.mark.parametrize(
    "name,secret",
    [
        ("Authorization", True),
        ("X-CSRF-Token", True),
        ("DATABASE_URL", True),
        ("session", True),
        ("key", True),
        ("keywords", False),
        ("author", False),
        ("value", False),
    ],
)
def test_secret_kind_is_by_field_kind(name, secret):
    assert is_secret_kind(name) is secret


@pytest.mark.parametrize(
    "text,kept",
    [
        ("redis://u:p4ss@cache:6379/0", "redis://" + REDACTED + "@cache:6379/0"),
        ("plain words stay", "plain words stay"),
    ],
)
def test_scrub_text_keeps_shape(text, kept):
    assert scrub_text(text) == kept


@pytest.mark.parametrize("depth", [20, 40])
def test_deep_nesting_is_cut(depth):
    event = {"extra": {}}
    node = event["extra"]
    for _ in range(depth):
        node["n"] = {}
        node = node["n"]
    node["leaf"] = "deep-leaf-888"
    out = json.dumps(scrub_event(event))
    assert "deep-leaf-888" not in out
    assert REDACTED in out


@pytest.mark.parametrize("event", [None, ["not", "a", "dict"]])
def test_failed_scrub_drops_event(event):
    assert scrub_event(event) is None


@pytest.mark.parametrize("payload", [("a", "b"), ("x", "token")])
def test_tuples_are_scrubbed_as_lists(payload):
    out = scrub_event({"extra": {"t": payload}})
    assert out["extra"]["t"] == list(payload)


def _fake_sentry(monkeypatch, calls):
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kw: calls.append(kw)
    integrations = types.ModuleType("sentry_sdk.integrations")
    fastapi_mod = types.ModuleType("sentry_sdk.integrations.fastapi")
    fastapi_mod.FastApiIntegration = lambda: "fastapi"
    logging_mod = types.ModuleType("sentry_sdk.integrations.logging")
    logging_mod.LoggingIntegration = lambda: "logging"
    for name, mod in {
        "sentry_sdk": fake,
        "sentry_sdk.integrations": integrations,
        "sentry_sdk.integrations.fastapi": fastapi_mod,
        "sentry_sdk.integrations.logging": logging_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.mark.parametrize("dsn", ["https://k@o0.ingest.example/1", "https://k@o0.ingest.example/2"])
def test_init_wires_the_scrub_hook(monkeypatch, dsn):
    calls: list[dict] = []
    _fake_sentry(monkeypatch, calls)
    monkeypatch.setenv("SENTRY_DSN", dsn)
    monkeypatch.setattr(observability, "_initialized", False)
    observability.init_sentry()
    assert len(calls) == 1
    seen = calls[0]
    assert seen["dsn"] == dsn
    assert seen["before_send"] is event_scrub.scrub_event
    assert seen["before_send_transaction"] is event_scrub.scrub_event
    assert seen["max_request_body_size"] == "never"
    assert seen["include_local_variables"] is False
    assert seen["send_default_pii"] is False


@pytest.mark.parametrize("repeats", [2, 3])
def test_init_runs_once_per_process(monkeypatch, repeats):
    calls: list[dict] = []
    _fake_sentry(monkeypatch, calls)
    monkeypatch.setenv("SENTRY_DSN", "https://k@o0.ingest.example/1")
    monkeypatch.setattr(observability, "_initialized", False)
    for _ in range(repeats):
        observability.init_sentry()
    assert len(calls) == 1
