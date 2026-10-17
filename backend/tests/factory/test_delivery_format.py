"""Delivery format: the client's choice at request time (zip | github_repo)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.factory.blueprint import ProductBlueprint
from app.factory.build import github_delivery


def _bp(**kw):
    base = dict(
        schema_version="product_blueprint.v1",
        product_id="clinic",
        product_name="Clinic",
        vertical="health",
        summary="s",
        capabilities=[{"id": "patients", "description": "Patient records"}],
    )
    base.update(kw)
    return ProductBlueprint(**base)


class TestDeliveryFormat:
    def test_defaults_to_zip(self):
        assert _bp().delivery_format == "zip"

    def test_rejects_unknown_formats(self):
        with pytest.raises(ValidationError):
            _bp(delivery_format="s3_bucket")

    def test_accepts_github_repo(self):
        assert _bp(delivery_format="github_repo").delivery_format == "github_repo"


class TestGithubDelivery:
    def test_no_token_is_a_named_failure(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CEREBRUM_BUILDS_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(RuntimeError) as exc:
            github_delivery.push_workspace_repo(
                tmp_path, product_id="clinic", session_id="sess_abc"
            )
        assert "github_delivery_failed" in str(exc.value)
