"""Prometheus /metrics is an ops surface — master key, not world-open."""

from __future__ import annotations

# A fake password for this test, generated per run (no value is stored in the repo).
_GENERATED_PW = "test-" + __import__("secrets").token_hex(6)

# Secret-shaped env vars that must never appear in a scrape body.
_FORBIDDEN_IN_SCRAPE = (
    "CEREBRUM_DEV_API_KEY",
    "KIMI_API_KEY",
    "CEREBRUM_LLM_API_KEY",
    "STRIPE_SECRET_KEY",
    "ACCOUNTS_DATABASE_URL",
    "SMOKE_GATE_TOKEN",
    "SENTRY_DSN",
)


def _scrape_metrics(client, monkeypatch, master_key: str = "master-secret") -> str:
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", master_key)
    res = client.get("/metrics", headers={"Authorization": f"Bearer {master_key}"})
    assert res.status_code == 200
    return res.text


def _counter_value(body: str, *, method: str, handler: str, status: str) -> float:
    for line in body.splitlines():
        if not line.startswith("http_requests_total{"):
            continue
        labels = line.split("{", 1)[1].split("}", 1)[0]
        parts = dict(item.split("=", 1) for item in labels.split(",") if "=" in item)
        if (
            parts.get("method") == f'"{method}"'
            and parts.get("handler") == f'"{handler}"'
            and parts.get("status") == f'"{status}"'
        ):
            return float(line.rsplit(" ", 1)[-1])
    return 0.0


def test_metrics_unauth_is_401_when_master_key_is_configured(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.get("/metrics")
    assert res.status_code == 401


def test_metrics_wrong_key_is_401(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_metrics_authorized_is_200_prometheus_text(client, monkeypatch):
    body = _scrape_metrics(client, monkeypatch)
    assert "cerebrumdev_up" in body
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "http_requests_in_flight" in body
    assert "cerebrumdev_build_info" in body
    assert "process_cpu_seconds_total" in body or "python_info" in body
    content_type = client.get(
        "/metrics",
        headers={"Authorization": "Bearer master-secret"},
    ).headers.get("content-type", "")
    assert content_type.startswith("text/plain")


def test_metrics_absent_master_key_is_404_not_world_open(client, monkeypatch):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    res = client.get("/metrics")
    assert res.status_code == 404


def test_http_request_counter_increments_after_traffic(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    before = _scrape_metrics(client, monkeypatch)
    before_count = _counter_value(
        before, method="GET", handler="/health", status="200"
    )

    client.get("/health")
    client.get("/health")

    after = _scrape_metrics(client, monkeypatch)
    after_count = _counter_value(after, method="GET", handler="/health", status="200")
    assert after_count >= before_count + 2


def test_metrics_output_never_echoes_configured_secrets(client, monkeypatch):
    secrets = {
        "CEREBRUM_DEV_API_KEY": "super-secret-master-key-value",
        "KIMI_API_KEY": "sk-kimi-test-not-real",
        "CEREBRUM_LLM_API_KEY": "llm-key-should-not-leak",
        "STRIPE_SECRET_KEY": "sk_live_test_should_not_leak",
        "ACCOUNTS_DATABASE_URL": "postgresql://user:" + _GENERATED_PW + "@db.example/internal",
        "SMOKE_GATE_TOKEN": "smoke-gate-token-value",
        "SENTRY_DSN": "https://abc@o123.ingest.sentry.io/456",
        "RENDER_GIT_COMMIT": "abc123def456789",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)

    body = _scrape_metrics(client, monkeypatch, master_key=secrets["CEREBRUM_DEV_API_KEY"])

    for key in _FORBIDDEN_IN_SCRAPE:
        assert secrets[key] not in body, f"{key} value leaked into /metrics"
    assert "abc123def456789" in body
    assert 'git_sha="abc123def456789"' in body or 'git_sha_short="abc123d"' in body
