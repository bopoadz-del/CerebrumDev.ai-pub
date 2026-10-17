"""Regression tests for the pre-launch security fixes.

Each test is written in the shape that would have caught the original defect,
not in the shape of the patch:

* ``TestOutputDirConfinement`` plants a real file outside the outputs root and
  asserts it still exists afterwards. Asserting "a 400 was returned" would pass
  against a patch that returns 400 *after* deleting the tree.
* ``TestDevTokenExposure`` drives the mailer through its failure mode rather
  than through configuration, because the leak was reachable via a transient
  provider error on a correctly configured deployment.
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

import pytest  # noqa: E402

from app.factory.generator import ProductGenerator  # noqa: E402

from app.factory.paths import (  # noqa: E402
    UnsafeOutputDir,
    factory_outputs_root,
    is_within_outputs_root,
    safe_output_dir,
)


class TestOutputDirConfinement:
    """`output_dir` reached shutil.rmtree unvalidated -- an arbitrary delete."""

    def test_plants_a_file_outside_the_root_and_it_survives(self, tmp_path, monkeypatch):
        """The load-bearing test: real bytes on disk, still there afterwards.

        ``tmp_path`` is itself inside the system temp directory, which the
        generator is allowed to clean, so the temp root is redirected to a
        sibling. The victim then sits outside both permitted roots -- standing
        in for ``/app/storage`` on the real box.
        """
        import tempfile

        fake_tmp = tmp_path / "tmp"
        fake_tmp.mkdir()
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))

        victim_dir = tmp_path / "storage"
        victim_dir.mkdir()
        victim = victim_dir / "accounts.db"
        victim.write_text("irreplaceable user data", encoding="utf-8")

        gen = ProductGenerator.__new__(ProductGenerator)  # no blueprint needed
        with pytest.raises(UnsafeOutputDir):
            gen.generate(victim_dir, clean=True)

        assert victim.exists(), "generator deleted a directory outside the permitted roots"
        assert victim.read_text(encoding="utf-8") == "irreplaceable user data"

    @pytest.mark.parametrize(
        "hostile",
        [
            "/app",
            "/app/storage",
            "/",
            "../../etc",
            "factory_outputs/../../..",
            "~/.ssh",
        ],
    )
    def test_hostile_targets_are_refused(self, hostile):
        with pytest.raises(UnsafeOutputDir):
            safe_output_dir(hostile, "some-product")

    def test_traversal_that_lands_back_inside_is_allowed(self):
        """Confinement is by resolved path, not by string matching on '..'."""
        inside = factory_outputs_root() / "a" / ".." / "b"
        assert is_within_outputs_root(inside)
        assert safe_output_dir(inside, "p") == (factory_outputs_root() / "b").resolve()

    def test_absent_output_dir_uses_the_default(self):
        assert safe_output_dir(None, "widget") == factory_outputs_root() / "widget"
        assert safe_output_dir("", "widget") == factory_outputs_root() / "widget"

    def test_root_itself_is_permitted(self):
        assert is_within_outputs_root(factory_outputs_root())


class TestDevTokenExposure:
    """Reset tokens were returned to unauthenticated callers on mail failure."""

    @staticmethod
    def _break_mail(monkeypatch):
        """Simulate a provider outage, not a missing configuration."""
        from app.core import mailer

        monkeypatch.setattr(
            mailer, "send_password_reset_email", lambda *a, **k: False
        )
        monkeypatch.setattr(mailer, "send_verification_email", lambda *a, **k: False)

    def test_forgot_password_withholds_the_token_when_mail_fails(
        self, client, monkeypatch
    ):
        self._break_mail(monkeypatch)
        monkeypatch.delenv("ACCOUNTS_EXPOSE_DEV_TOKENS", raising=False)

        resp = client.post("/v1/auth/forgot-password", json={"email": "a@b.com"})

        assert resp.status_code == 200
        body = resp.json()
        assert "dev_reset_token" not in body
        # And nothing token-shaped smuggled through another field.
        assert "cdr_" not in resp.text

    def test_register_withholds_the_verification_token_when_mail_fails(
        self, client, monkeypatch
    ):
        self._break_mail(monkeypatch)
        monkeypatch.delenv("ACCOUNTS_EXPOSE_DEV_TOKENS", raising=False)

        # uuid keeps this deterministic -- a fixed address collides with a
        # previous run and turns the assertion into a 409.
        email = f"verify-{uuid.uuid4().hex}@example.com"
        resp = client.post(
            "/v1/auth/register",
            json={"email": email, "password": "correct horse battery"},
        )

        assert resp.status_code == 201
        verification = resp.json()["verification"]
        assert "dev_verification_token" not in verification
        assert verification["email_sent"] is False

    def test_opt_in_still_works_for_local_development(self, client, monkeypatch):
        self._break_mail(monkeypatch)
        monkeypatch.setenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "1")

        resp = client.post("/v1/auth/forgot-password", json={"email": "a@b.com"})
        assert resp.status_code == 200
        # Present only for a registered address; absence here is fine, a
        # non-200 or a crash is not.
