"""Two-input test for the SUPPORT_EMAIL config source."""

import pytest

from app.core.support_contact import SUPPORT_EMAIL_DEFAULT, support_email


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("", SUPPORT_EMAIL_DEFAULT),
        ("   ", SUPPORT_EMAIL_DEFAULT),
        ("help@example.test", "help@example.test"),
        ("support@acme.com", "support@acme.com"),
    ],
)
def test_support_email(monkeypatch, env_value, expected):
    if env_value == "":
        monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    else:
        monkeypatch.setenv("SUPPORT_EMAIL", env_value)
    assert support_email() == expected
