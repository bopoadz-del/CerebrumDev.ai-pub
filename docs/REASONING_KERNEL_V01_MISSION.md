You are the principal reasoning-systems engineer and Factory architect for CerebrumDev.ai.

Build the first production-shaped version of the Cerebrum Reasoning Kernel:

Cerebrum Reasoning Kernel v0.1 — Standard Control Layer

This implementation belongs canonically in:

bopoadz-del/CerebrumDev.ai

It does not belong in Cerebrum-Blocks.

Do not perform the exhaustive multi-platform domain scout in this execution.
That will be a later phase.

This execution must establish the stable reasoning contracts and runtime that can manage existing domain kits, existing product services and standard platform features immediately.

==================================================
REPOSITORY AND BRANCH
==================================================

Repository:
bopoadz-del/CerebrumDev.ai

Base branch:
master

Working branch:
feat/reasoning-control-layer-v0

Do not commit directly to master.
Do not merge automatically.
Do not modify Cerebrum-Blocks.
Do not rewrite product-domain calculations.

Use the current CerebrumDev.ai Factory, CapabilityPlanner, ProductBlueprint, ProductGenerator, Product DNA, agent hats and workflow generation as the foundation.

==================================================
MISSION
==================================================

Create a domain-neutral reasoning control layer that sits between:

- User or agent request
- Existing domain kit
- Existing product-local domain services
- Standard platform features
- Authority and approval
- Execution
- Verification
- Evidence and audit

The kernel must determine:

1. Domain
2. Intent
3. Registered action
4. Required inputs
5. Applicable formula references
6. Applicable rule references
7. Current and requested workflow state
8. Required permissions
9. Approval requirement
10. Evidence requirement
11. Correct execution adapter
12. Verification requirement
13. Standard outcome
14. Explanation boundary

The kernel must not become a generic LLM agent.

An LLM may classify ambiguous intent and explain a verified result.

An LLM may not:

- Override trusted context
- Execute formulas authoritatively
- Grant permission
- Approve actions
- Change workflow state
- Invent missing inputs
- Select unsupported actions
- Modify deterministic results

==================================================
CANONICAL PACKAGE
==================================================

Create:

backend/app/reasoning_kernel/
  __init__.py
  contracts.py
  context.py
  profile.py
  registry.py
  intent.py
  dependencies.py
  planner.py
  permissions.py
  approvals.py
  workflows.py
  execution.py
  verification.py
  outcomes.py
  explanation.py
  provenance.py
  adapters/
    __init__.py
    capability.py
    domain_service.py
    authority.py
    approval.py
    workflow.py
    knowledge.py
    jobs.py
    evidence.py
    audit.py
    export.py
  schemas/
    reasoning_profile.schema.json
    domain_action.schema.json
    reasoning_plan.schema.json
    reasoning_outcome.schema.json

==================================================
STANDARD CONTRACTS
==================================================

Implement:

1. ReasoningContext
2. DomainActionSpec
3. DomainReasoningProfile
4. ReasoningPlan
5. ReasoningOutcome
6. AdapterResult
7. VerificationResult

ReasoningContext must contain server-trusted:

- user_id
- tenant_id
- project_id
- roles
- permissions
- request_id
- trusted_context_digest

Reserved trusted fields must never be accepted from model-generated or user-supplied action arguments.

DomainActionSpec must support:

- action_id
- domain
- intent aliases
- implementation type
- Store block id or product service symbol
- required inputs
- optional inputs
- trusted context requirements
- formula references
- rule references
- workflow reference
- required permissions
- approval policy reference
- evidence requirements
- verification oracle reference
- read-only/high-impact classification
- idempotency policy
- execution mode: synchronous or durable_job

==================================================
STANDARD OUTCOMES
==================================================

Use only:

- success
- dependency_required
- validation_error
- permission_denied
- approval_required
- unsupported
- conflict_detected
- execution_error

No endpoint or planner may invent another top-level status.

==================================================
REASONING SEQUENCE
==================================================

Implement:

request
→ trusted context lock
→ domain resolution
→ intent resolution
→ registered action lookup
→ required-input resolution
→ formula/rule reference resolution
→ workflow-state validation
→ permission check
→ approval-policy check
→ immutable execution plan
→ adapter execution
→ result verification
→ evidence and audit
→ explanation

The execution plan must be hashed.

Approval must bind to the exact plan and payload digest.

The approved payload must execute no more than once.

==================================================
ADAPTER MODEL
==================================================

Implement ports for:

- Store capability execution
- Existing product-local services
- Authority/RBAC
- Approval records
- Workflow state
- RAG/knowledge
- Durable jobs
- Evidence
- Audit
- Exports

The default adapters may be in-memory/test implementations.

Do not create fake production claims.

Every adapter must expose its implementation identity and provenance.

==================================================
FACTORY INTEGRATION
==================================================

Extend product_blueprint.v1 or introduce a backwards-compatible reasoning_profile section supporting:

- domain profile id
- domain kit id/version
- action profile path
- standard feature adapters
- reasoning-kernel version
- reasoning enabled flag

Do not break existing blueprints.

Extend CapabilityPlanner so the product plan records:

- Reasoning profile
- Action mappings
- Adapter mappings
- Unsupported reasoning actions

Extend ProductGenerator so generated products receive a pinned runtime snapshot under:

app/cerebrum_reasoning_kernel/

Also generate:

product-dna/reasoning_profile.json
product-dna/domain_action_catalog.json
product-dna/formula_reference_catalog.json
product-dna/rule_reference_catalog.json
product-dna/approval_policy_catalog.json
product-dna/workflow_catalog.json
product-dna/reasoning_provenance.json

Update checksum manifests and Product DNA tests.

The canonical implementation remains in CerebrumDev.ai.
Generated products receive a versioned snapshot.

==================================================
FINANCEOPS REFERENCE PROFILE
==================================================

Create a FinanceOps reference reasoning profile based on the actual public contracts and current product structure of:

bopoadz-del/Projecttwo
branch:
feat/projecttwo-platform-v1

Do not copy the FinanceOps application into CerebrumDev.ai.

Map at minimum:

- actuals ingestion
- budget creation
- forecast generation
- workforce planning
- allocation
- scenario publication
- CoA activation
- reconciliation
- Finance Assistant query
- XLSX/PDF export

Reference the existing product service or Store block that owns each implementation.

Where detailed formulas or rules have not yet been scouted, mark them:

- implementation_owned
- domain_review_required

Do not invent formulas.

High-impact mappings must include:

- scenario publication
- CoA activation
- budget lock
- export distribution
- journal posting if exposed

These must return approval_required unless the exact plan digest has a valid approval.

==================================================
NEUTRAL TEST PROFILE
==================================================

Create a small equipment-maintenance test profile that proves domain neutrality.

Required actions:

- inspect equipment
- calculate condition score
- recommend maintenance
- approve maintenance
- close work order

Demonstrate:

- missing input → dependency_required
- unknown action → unsupported
- missing permission → permission_denied
- high-impact action → approval_required
- approved exact digest → success
- altered payload after approval → permission or approval failure
- invalid workflow transition → validation_error
- repeated execution → refused
- successful action → evidence and audit references

==================================================
TESTS
==================================================

Add tests for:

- Contract schema validation
- Trusted-context protection
- Action registration
- Duplicate action rejection
- Unknown action refusal
- Missing input detection
- Permission checks
- Approval digest binding
- Approval expiry
- Approval replay prevention
- Workflow state transitions
- Invalid transitions
- Adapter failure handling
- Verification failure
- Evidence creation
- Audit creation
- Explanation cannot alter result
- ProductBlueprint backwards compatibility
- ProductGenerator reasoning snapshot
- Product DNA checksum update
- FinanceOps profile validation
- Neutral profile end-to-end reasoning flow

Existing CerebrumDev.ai tests must remain green.

==================================================
ACCEPTANCE
==================================================

Do not declare completion until:

1. The Reasoning Kernel source exists only canonically in CerebrumDev.ai.
2. Generated products receive a pinned snapshot.
3. Existing domain kits can be managed without rewriting them.
4. Product-local services can be registered as implementations.
5. Standard platform features are accessed through adapters.
6. Trusted context cannot be overridden.
7. Missing inputs fail closed.
8. Unsupported actions fail closed.
9. Permissions fail closed.
10. High-impact actions require exact payload-bound approval.
11. Workflow transitions are enforced.
12. Results are verified before explanation.
13. Evidence and audit references are automatic.
14. FinanceOps reference profile validates.
15. Neutral profile passes end to end.
16. Existing Factory generation remains backward compatible.
17. CI is green.
18. No Store repository was modified.
19. No PR was merged automatically.

==================================================
FINAL REPORT
==================================================

Return:

- Branch
- PR
- Final SHA
- Files created
- Existing Factory files changed
- Contracts implemented
- Adapters implemented
- FinanceOps actions mapped
- Actions marked domain_review_required
- Neutral-profile result
- Test counts
- CI result
- Backwards-compatibility result
- Product DNA changes
- Known limitations
- Exact command to generate a product with reasoning enabled
- Confirmation that Cerebrum-Blocks was not modified
- Confirmation that no PR was merged

Proceed now.