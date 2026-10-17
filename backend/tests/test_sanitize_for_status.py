"""F5: the status/log sanitisation line.

Keys and auth headers are never rendered to logs or the Floor; hostnames
and short opaque tokens (deploy ids, session ids) pass through untouched,
because an operator needs them to name what they are looking at.
"""

from __future__ import annotations

import secrets

from app.factory.build.sanitize import sanitize_for_status

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_KEY_1 = "sk-" + __import__("secrets").token_hex(13)


class TestSanitizeForStatus:
    def test_hostnames_survive(self):
        assert (
            sanitize_for_status("deploy https://api.render.com/v1/services/srv-x/deploys")
            == "deploy https://api.render.com/v1/services/srv-x/deploys"
        )

    def test_short_tokens_survive(self):
        assert (
            sanitize_for_status("receipt dep-dakod9jl550s73ev30j0 accepted")
            == "receipt dep-dakod9jl550s73ev30j0 accepted"
        )

    def test_bearer_headers_are_redacted(self):
        assert (
            sanitize_for_status("Authorization: Bearer abc123\nGET /v1/deploys")
            == "[redacted]\nGET /v1/deploys"
        )

    def test_api_key_headers_are_redacted(self):
        assert (
            sanitize_for_status(f"X-Api-Key: rnd_{secrets.token_hex(12)}")
            == "[redacted]"
        )

    def test_key_assignments_keep_the_name_and_drop_the_value(self):
        assert (
            sanitize_for_status("DEEPSEEK_API_KEY=sk-secretvalue1234")
            == "DEEPSEEK_API_KEY=[redacted]"
        )

    def test_provider_style_keys_are_redacted(self):
        assert (
            sanitize_for_status("failed with " + _GENERATED_KEY_1 + "")
            == "failed with [redacted]"
        )

    def test_long_blob_secrets_are_redacted(self):
        blob = "x" * 64
        assert sanitize_for_status(f"sig {blob} rejected") == "sig [redacted] rejected"

    def test_none_and_non_strings_pass_through(self):
        assert sanitize_for_status(None) == ""
        assert sanitize_for_status("") == ""

    # Live 2026-09-30 (automotive build): the long-blob rule blanked the
    # pytest node id and the workspace path out of the TESTER failure
    # banner -- the exact tokens the owner needed. Identifier-shaped
    # tokens (lowercase snake_case / path segments) now survive; secrets
    # keep redacting.



    def test_a_long_pytest_node_id_survives(self):
        line = (
            "FAILED tests/test_negative_floor_counter_cases.py::"
            "test_negative_ai_use_case_registry_rejects_unknown_stage_and_cross_tenant"
            " - failed on setup"
        )
        out = sanitize_for_status(line)
        assert "[redacted]" not in out
        assert (
            "test_negative_ai_use_case_registry_rejects_unknown_stage_and_cross_tenant"
            in out
        )


    def test_a_long_workspace_path_survives(self):
        # Dashed product ids ride inside the path (automotive-aiops).
        line = (
            'failed on setup with "file '
            "/workspaces/automotive-aiops/tests/test_negative_floor_counter_cases"
            '.py, line 12"'
        )
        out = sanitize_for_status(line)
        assert "[redacted]" not in out
        assert "automotive-aiops/tests/test_negative_floor_counter_cases" in out


    def test_real_secrets_are_still_redacted(self):
        # Provider-style key.
        assert "sk-" + "aB1xk9Qw" * 3 not in sanitize_for_status(
            "key sk-" + "aB1xk9Qw" * 3
        )
        # Mixed-case JWT-ish blob.
        blob = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9eyJzdWIiOiIx"
        assert blob not in sanitize_for_status("token " + blob)
        # Auth header line.
        assert "Bearer" not in sanitize_for_status("Authorization: Bearer abc123")
        # key=value assignment.
        out = sanitize_for_status("DEEPSEEK_API_KEY=supersecretvalue")
        assert "supersecretvalue" not in out
        assert "DEEPSEEK_API_KEY" in out


    def test_a_pure_hex_blob_stays_redacted(self):
        # 48-char lowercase hex has no separators -- could be a token; unchanged.
        blob = "a" * 8 + "0123456789abcdef" * 3
        assert blob not in sanitize_for_status("hash " + blob)
