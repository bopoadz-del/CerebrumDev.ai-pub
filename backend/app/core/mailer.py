"""Transactional email for account flows.

Provider order:
  1. Resend API (RESEND_API_KEY) — easiest reliable path.
  2. SMTP (SMTP_HOST / SMTP_USER / SMTP_PASS) — generic provider path.
  3. Honest dev-mode fallback (token surfaced in the API response).

No external dependencies beyond stdlib + httpx (already a project dep).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import smtplib
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx

_log = logging.getLogger(__name__)


def _sender() -> str:
    return (
        os.getenv("RESEND_FROM", "").strip()
        or os.getenv("SMTP_FROM", "").strip()
        or os.getenv("SMTP_USER", "").strip()
        or "no-reply@cerebrum.dev"
    )


def _frontend_url() -> str:
    return os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")


def frontend_url_is_public() -> bool:
    """True when FRONTEND_URL is set to a non-localhost origin.

    Used by ``/health`` so operators can confirm reset/verify mail will not
    link to localhost. Never returns the URL itself.
    """
    raw = (os.getenv("FRONTEND_URL") or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    parsed = urlparse(lowered if "://" in lowered else f"https://{lowered}")
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        return False
    # Loopback by the address grammar (all of 127/8 and ::1), and the
    # reserved ``localhost`` name and its subdomains (RFC 6761) by the host's
    # last DNS label -- compared as a label, never searched for in the text.
    try:
        if ipaddress.ip_address(host).is_loopback:
            return False
    except ValueError:
        pass
    return host.split(".")[-1] != "localhost"


def _resend_configured() -> bool:
    return bool(os.getenv("RESEND_API_KEY", "").strip())


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip())


def email_configured() -> bool:
    """True when any outbound email provider is configured."""
    return _resend_configured() or _smtp_configured()


def _send_resend(to: str, subject: str, text: str) -> bool:
    """Send via Resend REST API. Returns False on any failure."""
    key = os.getenv("RESEND_API_KEY", "").strip()
    if not key:
        return False
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": _sender(),
                    "to": to,
                    "subject": subject,
                    "text": text,
                },
            )
            resp.raise_for_status()
            return True
    except httpx.HTTPStatusError as exc:
        # Status only — response bodies can include recipient addresses.
        # A 422 is typically an unverified sender domain or test-mode
        # recipient restriction, not a bad reset token.
        _log.warning("resend_email_failed status=%s", exc.response.status_code)
        return False
    except Exception:
        _log.warning("resend_email_failed")
        return False


def _send_smtp(to: str, subject: str, text: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return False
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASS", "")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _sender()
    msg["To"] = to
    msg.set_content(text)
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
    return True


def _send_email(to: str, subject: str, text: str) -> bool:
    """Try providers in priority order."""
    if _resend_configured() and _send_resend(to, subject, text):
        return True
    if _smtp_configured() and _send_smtp(to, subject, text):
        return True
    return False


def send_verification_email(email: str, token: str) -> bool:
    """Send the verification link; returns False when no provider is configured."""
    link = f"{_frontend_url()}/verify-email?token={token}"
    return _send_email(
        email,
        "Verify your CerebrumDev.ai account",
        "Welcome to CerebrumDev.ai.\n\n"
        f"Verify your email address:\n{link}\n\n"
        "The link expires in 24 hours. If you did not register, ignore this email.\n",
    )


def send_password_reset_email(email: str, token: str) -> bool:
    """Send the password-reset link; returns False when no provider is configured."""
    link = f"{_frontend_url()}/reset-password?token={token}"
    return _send_email(
        email,
        "Reset your CerebrumDev.ai password",
        "A password reset was requested for your CerebrumDev.ai account.\n\n"
        f"Set a new password:\n{link}\n\n"
        "The link expires in 1 hour and signs out all existing sessions. "
        "If you did not request this, ignore this email.\n",
    )


# Legacy name kept for compatibility.
smtp_configured = email_configured
