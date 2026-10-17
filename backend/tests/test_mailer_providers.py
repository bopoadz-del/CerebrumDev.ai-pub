"""Email provider selection: Resend, SMTP, dev-mode fallback."""

from __future__ import annotations


from app.core import mailer


def test_no_provider_configured_returns_false(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert mailer.email_configured() is False
    assert mailer.send_verification_email("dev@example.com", "cdv_token") is False
    assert mailer.send_password_reset_email("dev@example.com", "cdr_token") is False


def test_resend_configured_without_key_does_not_send(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert mailer.email_configured() is False


def test_resend_send_failure_falls_back_to_false(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "invalid-key-for-unit-test")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    # httpx will fail to authenticate; function returns False honestly.
    assert mailer.send_verification_email("dev@example.com", "cdv_token") is False


def test_smtp_configured_flag(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    assert mailer.email_configured() is True


def test_legacy_smtp_configured_alias(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    assert mailer.smtp_configured() is True


def test_frontend_url_is_public_never_returns_the_url(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://www.cerebrum-dev.com")
    assert mailer.frontend_url_is_public() is True
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    assert mailer.frontend_url_is_public() is False
    monkeypatch.setenv("FRONTEND_URL", "http://127.0.0.1:5173")
    assert mailer.frontend_url_is_public() is False
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    assert mailer.frontend_url_is_public() is False


def test_sender_prefers_resend_from_then_smtp(monkeypatch):
    monkeypatch.delenv("RESEND_FROM", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    assert mailer._sender() == "no-reply@cerebrum.dev"

    monkeypatch.setenv("SMTP_USER", "smtp-user@example.com")
    assert mailer._sender() == "smtp-user@example.com"

    monkeypatch.setenv("SMTP_FROM", "smtp-from@example.com")
    assert mailer._sender() == "smtp-from@example.com"

    monkeypatch.setenv("RESEND_FROM", "resend@example.com")
    assert mailer._sender() == "resend@example.com"
