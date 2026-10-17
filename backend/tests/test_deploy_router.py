"""Tests for the deploy router platform target."""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.session_store import create_session


client = TestClient(app)


def test_deploy_platform_target():
    """The platform target returns a packaged status and a platform download URL."""
    session = create_session(session_id="sess_platform_test", user_id="test_user")
    session.chain_approved = True
    session.proposed_chain = {"blocks": [], "connections": []}
    # Mock the kit resolution / packaging so we don't need a real kit on disk.
    with patch("app.routers.deploy.package_platform_session") as mock_package:
        mock_package.return_value = {
            "zip_path": "/tmp/platform.zip",
            "service_name": "cerebrum-platform-medical-sess",
            "api_key": "cd-platform-key",
            "env_vars": {},
        }
        response = client.post(f"/v1/sessions/{session.session_id}/deploy?target=platform")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "packaged"
    assert "variant=platform" in data["download_url"]
    assert "docker compose up" in data["message"].lower() or "render.yaml" in data["message"].lower()


def test_deploy_invalid_target():
    """An unknown target returns 400."""
    session = create_session(session_id="sess_invalid_target", user_id="test_user")
    response = client.post(f"/v1/sessions/{session.session_id}/deploy?target=unknown")
    assert response.status_code == 400


def test_deploy_default_target_is_platform():
    """Omitting the target parameter now defaults to the platform packager."""
    session = create_session(session_id="sess_default_target", user_id="test_user")
    session.chain_approved = True
    session.proposed_chain = {"blocks": [], "connections": []}
    with patch("app.routers.deploy.package_platform_session") as mock_package:
        mock_package.return_value = {
            "zip_path": "/tmp/platform.zip",
            "service_name": "cerebrum-platform-medical-sess",
            "api_key": "cd-platform-key",
            "env_vars": {},
        }
        response = client.post(f"/v1/sessions/{session.session_id}/deploy")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "packaged"
    assert "variant=platform" in data["download_url"]
    mock_package.assert_called_once()


def test_deploy_edge_opt_in_carries_preview_label():
    """Explicit edge target still packages and surfaces the preview description."""
    session = create_session(session_id="sess_edge_opt_in", user_id="test_user")
    session.chain_approved = True
    session.proposed_chain = {"blocks": [], "connections": []}
    with patch("app.routers.deploy.package_session") as mock_package:
        mock_package.return_value = {
            "zip_path": "/tmp/package.zip",
            "package_dir": "/tmp/package",
            "service_name": "cerebrumdev-medical-sess",
            "api_key": "cd-deploy-key",
            "env_vars": {},
        }
        with patch("app.routers.deploy.generate_edge_package") as mock_edge:
            mock_edge.return_value = "/tmp/edge.zip"
            response = client.post(f"/v1/sessions/{session.session_id}/deploy?target=edge")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "packaged"
    assert "variant=edge" in data["download_url"]
    assert "lightweight preview" in data["message"].lower()
    assert "not standalone" in data["message"].lower()
    assert "engine checkout" in data["message"].lower()
