"""Smoke: every pre-split roles.py symbol still resolves on the facade."""

from __future__ import annotations

import app.factory.build.roles as roles
import app.factory.build.roles_constants as roles_constants
import app.factory.build.roles_handlers as roles_handlers
import app.factory.build.roles_models as roles_models

# Frozen at the 74cdc694 split. Adding a symbol is fine; dropping one is not.
_PRE_SPLIT_SYMBOLS = (
    "ROLE_IMPLEMENTATIONS",
    "RoleContext",
    "RoleError",
    "RoleResult",
    "_BLOCK_CLASS_RE",
    "_BLOCK_DEF_RE",
    "_CONFTEST",
    "_DISPATCH_RUNTIME",
    "_GET_BLOCK_RE",
    "_INSTANTIATE_HELPER",
    "_PY_DEFAULTS",
    "_REGISTRY_API_NAMES",
    "_SAMPLE_VALUES",
    "_STORE_FOREIGN_LAZY_RE",
    "_STORE_FOREIGN_TOP_RE",
    "_STORE_RUNTIME_RE",
    "_block_contract",
    "_block_source_dir",
    "_budget_too_low",
    "_candidate_store_ids",
    "_check_foreign_app_imports",
    "_class_name_from_block_module",
    "_closure_over_runtime",
    "_coder_body",
    "_coder_model_spec",
    "_coder_readme",
    "_coder_route_body",
    "_collector_agent_review",
    "_collector_block_meta",
    "_constraint_guard",
    "_constraints_of",
    "_content_digest",
    "_ensure_handler_fails_closed",
    "_ensure_route_persists_payload",
    "_fallback_spec",
    "_field_default",
    "_handler_module",
    "_is_payload_mutation",
    "_kernel_http_readme_section",
    "_looks_like_email_field",
    "_payload_constraint_violations",
    "_pin_source",
    "_record_failure",
    "_render_agent_domain_tests",
    "_render_dev_requirements",
    "_render_dispatch",
    "_render_dockerfile",
    "_render_dockerignore",
    "_render_jobs_module",
    "_render_kernel_bridge",
    "_render_main",
    "_render_models",
    "_render_platform_env_example",
    "_render_procfile",
    "_render_release_gate",
    "_render_aws_task_definition",
    "_render_deploy_contract",
    "_render_deploy_readme",
    "_render_requirements",
    "_render_routes",
    "_render_store",
    "_render_vendored_registry",
    "_resolve_store_def",
    "_rewrite_runtime_imports",
    "_rewrite_shim_constructors",
    "_runtime_defs_for_blocks",
    "_runtime_pin",
    "_sample_payload",
    "_sample_value",
    "_shim_needs_runtime",
    "_store_block_defs",
    "_capability_handler_body",
    "_templated_body",
    "_templated_readme",
    "_templated_route_body",
    "_tester_agent_cases",
    "_vendor_product_kernel",
    "_vendor_runtime_slice",
    "run_cloner",
    "run_collector",
    "run_store_manager",
    "run_tester",
    "run_writer",
)


def test_templated_readme_uses_venv_not_system_pip():
    readme = roles_handlers._templated_readme("Parks Desk", ["crew"], ["team"])
    assert "python3 -m venv .venv" in readme
    assert ".venv/bin/pip install -c constraints.txt -r requirements.txt -r requirements-dev.txt" in readme
    assert "\npip install -r requirements.txt\n" not in readme


def test_pre_split_roles_symbols_still_resolve():
    missing = [name for name in _PRE_SPLIT_SYMBOLS if not hasattr(roles, name)]
    assert missing == [], f"roles.py facade dropped symbols: {missing}"


def test_roles_facade_reexports_extracted_modules():
    assert roles.RoleContext is roles_models.RoleContext
    assert roles.RoleResult is roles_models.RoleResult
    assert roles.RoleError is roles_models.RoleError
    assert roles.run_writer is roles_handlers.run_writer
    assert roles.run_collector is roles_handlers.run_collector
    assert roles._CONFTEST is roles_constants._CONFTEST
    assert roles._DISPATCH_RUNTIME is roles_constants._DISPATCH_RUNTIME
    assert roles.ROLE_IMPLEMENTATIONS is roles_handlers.ROLE_IMPLEMENTATIONS


def test_roles_facade_all_covers_pre_split_and_resolves():
    """__all__ keeps every pre-split name; extra split helpers may stay private."""
    assert set(_PRE_SPLIT_SYMBOLS) <= set(roles.__all__)
    missing = [name for name in roles.__all__ if not hasattr(roles, name)]
    assert missing == []
