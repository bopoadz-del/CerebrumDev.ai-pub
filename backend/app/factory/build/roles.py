"""The build roles. Each does one job, inside one lane, judged by one gate.

These are the minimum set needed to drive a real end-to-end build. They are
deliberately *not* thin wrappers around the template generator: the CLONER
vendors real block source and the WRITER writes handlers that **import that
source locally**, rather than emitting the ``httpx.post(store_url +
"/v1/execute")`` callback the old ProductGenerator path produces. That
difference is the point of the rebuild -- a delivered platform that runs
without the operator's store being up.

LLM use is optional by design. When a coder key is configured:
- the WRITER (Platform manufacturer) asks it for handler / model / route / README bodies
- the COLLECTOR (Binding surveyor) asks it to *review* capability↔block bindings (report-only)
- the TESTER (Acceptance inspector) asks it for *additional* domain cases (mutations of kernel
  payloads; they cannot replace the kernel suite)
When it is not, every kernel stays deterministic. CLONER (Block stocker) and
STORE_MANAGER (Store registrar) never call the agent. The 14-class contract is
shared: RoleRunner manufactures handlers/kernel locally and invokes
ProductGenerator class emitters (not ``generate()``) for the remaining
classes. Declared extras stay extra. CI exercises the manufacturing route
with no API key, and a dedicated keyed-path job (FACTORY_CODER_ENABLED=1,
stub keys) so the production coder path is not template-only. Which path
ran is recorded, never implied.

Each kernel publishes its job on the delivered platform:
``GET /v1/jobs`` (roster), ``GET /v1/catalog`` (COLLECTOR), ``GET /v1/inventory``
(CLONER), ``GET /v1/capabilities`` plus per-capability CRUD (WRITER),
``GET /v1/gates`` (TESTER, description only), ``GET /v1/provenance``
(STORE_MANAGER).

Implementations live in ``roles_models``, ``roles_constants``, and
``roles_handlers``. This module re-exports every previous public symbol so
existing ``from app.factory.build.roles import …`` paths keep working.
"""

from __future__ import annotations

from app.factory.build.roles_constants import (
    _BLOCK_CLASS_RE,
    _BLOCK_DEF_RE,
    _CONFTEST,
    _DISPATCH_RUNTIME,
    _GET_BLOCK_RE,
    _INSTANTIATE_HELPER,
    _PY_DEFAULTS,
    _REGISTRY_API_NAMES,
    _SAMPLE_VALUES,
    _STORE_FOREIGN_LAZY_RE,
    _STORE_FOREIGN_TOP_RE,
    _STORE_RUNTIME_RE,
)
from app.factory.build.roles_handlers import (
    ROLE_IMPLEMENTATIONS,
    _block_contract,
    _block_source_dir,
    _budget_too_low,
    _candidate_store_ids,
    _check_foreign_app_imports,
    _class_name_from_block_module,
    _closure_over_runtime,
    _coder_body,
    _coder_model_spec,
    _coder_readme,
    _coder_route_body,
    _collector_agent_review,
    _collector_block_meta,
    _capability_handler_body,
    _constraint_guard,
    _constraints_of,
    _content_digest,
    _ensure_handler_fails_closed,
    _ensure_route_persists_payload,
    _fallback_spec,
    _field_default,
    _handler_module,
    _is_payload_mutation,
    _kernel_http_readme_section,
    _looks_like_email_field,
    _payload_constraint_violations,
    _pin_source,
    _record_failure,
    _render_agent_domain_tests,
    _render_dev_requirements,
    _render_dispatch,
    _render_dockerfile,
    _render_dockerignore,
    _render_jobs_module,
    _render_kernel_bridge,
    _render_main,
    _render_models,
    _render_platform_env_example,
    _render_procfile,
    _render_release_gate,
    _render_aws_task_definition,
    _render_deploy_contract,
    _render_deploy_readme,
    _render_requirements,
    _render_routes,
    _render_store,
    _render_vendored_registry,
    _resolve_store_def,
    _rewrite_runtime_imports,
    _rewrite_shim_constructors,
    _runtime_defs_for_blocks,
    _runtime_pin,
    _sample_payload,
    _sample_value,
    _shim_needs_runtime,
    _store_block_defs,
    _templated_body,
    _templated_readme,
    _templated_route_body,
    _tester_agent_cases,
    _vendor_product_kernel,
    _vendor_runtime_slice,
    run_cloner,
    run_collector,
    run_store_manager,
    run_tester,
    run_writer,
)
from app.factory.build.roles_models import (
    RoleContext,
    RoleError,
    RoleResult,
)

__all__ = [
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
    "_capability_handler_body",
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
]
