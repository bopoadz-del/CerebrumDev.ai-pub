"""Store Manager authority + publish gates."""

from __future__ import annotations

import pytest

from app.factory.store_manager import (
    StoreAuthorityError,
    StoreOp,
    assert_store_op_allowed,
    evaluate_publish_gates,
    requires_user_approval,
    store_manager_manifest,
)


def test_manifest_lists_autonomous_and_approval_ops():
    m = store_manager_manifest()
    assert "STORE_PUBLISH_PATCH" in m["autonomous_ops"]
    assert "STORE_PUBLISH_MAJOR" in m["user_approval_ops"]
    assert "STORE_DELETE_BLOCK" in m["user_approval_ops"]


def test_major_requires_user_approval():
    assert requires_user_approval(StoreOp.STORE_PUBLISH_MAJOR)
    with pytest.raises(StoreAuthorityError):
        assert_store_op_allowed(StoreOp.STORE_PUBLISH_MAJOR, user_approved=False)
    assert_store_op_allowed(StoreOp.STORE_PUBLISH_MAJOR, user_approved=True)


def test_patch_publish_fails_closed_without_checklist():
    result = evaluate_publish_gates(StoreOp.STORE_PUBLISH_PATCH, {})
    assert result.allowed is False
    assert "contract_validation" in result.missing


def test_patch_publish_allowed_when_gates_pass():
    checklist = {
        "generic_reusable_value": True,
        "contract_validation": True,
        "security_validation": True,
        "store_test_suite_passing": True,
        "source_product_tests_passing": True,
        "independent_fixture_passing": True,
        "no_customer_specific_data_or_logic": True,
        "version_and_migration_documented": True,
        "rollback_available": True,
    }
    result = evaluate_publish_gates(StoreOp.STORE_PUBLISH_PATCH, checklist)
    assert result.allowed is True
