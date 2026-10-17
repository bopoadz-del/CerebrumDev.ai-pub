"""The secret scrubber's patterns are DATA, and entropy catches the rest.

Owner ruling 2026-10-05: the scrubber is a security control, never deleted;
its patterns live in app/factory/security/secret_patterns.json, loaded at run
time, used only to substitute [redacted]. Every key below is invented and
assembled at run time, so no key-shaped literal is committed.
"""

from __future__ import annotations

import random
import string

from app.factory.build import sanitize

_ALNUM = string.ascii_letters + string.digits


def _invented(prefix: str, alphabet: str, n: int, seed: int) -> str:
    rng = random.Random(seed)
    return prefix + "".join(rng.choice(alphabet) for _ in range(n))


def test_every_provider_format_in_the_data_file_is_redacted():
    planted = [
        _invented("s" + "k-", _ALNUM, 32, 1),
        _invented("AK" + "IA", string.ascii_uppercase + string.digits, 16, 2),
        _invented("gh" + "p_", _ALNUM, 36, 3),
        _invented("xo" + "xb-", _ALNUM + "-", 30, 4),
    ]
    for key in planted:
        out = sanitize.sanitize_for_status(f"call failed with {key} attached")
        assert key not in out and "[redacted]" in out, key


def test_a_plain_high_entropy_token_is_redacted_by_entropy():
    token = _invented("", _ALNUM + "+/", 28, 5)  # no provider prefix, < 40 chars
    assert sanitize._entropy(token) >= 4.2
    out = sanitize.sanitize_for_status(f"token {token} rejected")
    assert token not in out and "[redacted]" in out


def test_ordinary_prose_and_operator_identifiers_survive():
    text = (
        "TESTER failed at tests/factory/test_zorblat_flow.py::test_round_trip "
        "for sess_0123456789abcdef on deploy 4f3c2a1b9e8d7c6b5a4f3e2d1c0b9a8f "
        "because the quux service answered 503"
    )
    assert sanitize.sanitize_for_status(text) == text


def test_the_patterns_come_from_the_data_file():
    assert sanitize._rules()["loaded"] is True
    assert sanitize.PATTERNS_PATH.name == "secret_patterns.json"


def test_an_unreadable_data_file_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(sanitize, "PATTERNS_PATH", tmp_path / "missing.json")
    sanitize._rules.cache_clear()
    try:
        out = sanitize.sanitize_for_status("zorblat_quux_identifier_1234 ok")
        assert "zorblat_quux_identifier_1234" not in out  # redacts MORE without data
    finally:
        sanitize._rules.cache_clear()
