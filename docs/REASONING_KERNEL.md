## Yes — I got it. This is the **second kernel**, and probably the more valuable one.

The first kernel gives every platform its **body**:

```text
Universal Platform Kernel
= auth + UI + database + files + RAG + audit + exports + Docker + CI
```

The new kernel gives every platform its **brain**:

```text
Cerebrum Reasoning Kernel
= calculations + formulas + rules + workflows + approvals
  + evidence + decision logic + refusals + explanations
```

Together:

```text
Universal Platform Kernel
+ Cerebrum Reasoning Kernel
+ Domain Pack
+ Product Configuration
= A working intelligent domain platform
```

## The exciting part: we already built much of it without realizing it

A quick reconnaissance shows that the domain reasoning patterns are distributed across your products:

* **RetailOps** already has formula-to-action mappings, a deterministic reasoner, permitted-action filtering, evidence requirements, confirmation flags and discipline-specific “hat” restrictions.
* Its reasoner separates questions from commands, routes unsupported commands away from RAG, checks registered and permitted actions and refuses actions outside the active discipline’s permissions.
* **Projectfifteen** gives domain blocks ownership over authoritative commission calculations, independently cross-checks formulas, uses Decimal-safe money and records audit/evidence for each financial line.
* **FinanceOps** now has named high-impact action types and a fail-closed approval gate requiring an approved request before execution.
* **Projectten** contains the weighted scoring pattern: domain criteria, metric sub-scores and a weighted final score.
* **The Fork** provides real semantic embeddings, pgvector, BM25, RRF, project-scoped retrieval and grounded evidence retrieval—the knowledge side of domain reasoning.

So this is **not a new invention from zero**.

It is an extraction and unification mission.

# The real architecture

## 1. Domain Scout

Scans existing products and authoritative materials to identify:

* Calculations
* Formulas
* Thresholds
* Rules
* Exceptions
* Decision tables
* Roles
* Approval requirements
* Workflow states
* Evidence requirements
* Unsupported actions
* Conflicting implementations

It must distinguish:

```text
Implemented and tested
Implemented but untested
Documented only
Mocked
Hardcoded
Domain-specific
Reusable
Unverified
Conflicting
Obsolete
```

## 2. Domain Constitution

Every domain receives a machine-readable constitution:

```text
domain_packs/finance_ops/
├── manifest.yaml
├── ontology.yaml
├── entities.yaml
├── calculations/
├── formulas/
├── rules/
├── decision_tables/
├── workflows/
├── approvals/
├── actions/
├── evidence_policy.yaml
├── authority_sources.yaml
├── refusal_policy.yaml
├── permissions.yaml
└── verification_oracles/
```

This becomes the authoritative definition of how the domain operates.

## 3. Formula and Calculation Engine

The kernel executes deterministic formulas and records:

* Inputs
* Units
* Currency
* Formula version
* Rounding policy
* Assumptions
* Source authority
* Output
* Input/output digest
* Verification result

The LLM may explain the calculation. It must never replace it.

## 4. Rule and Decision Engine

Rules should be represented as explicit decision logic:

```yaml
rule_id: finance.mixed_currency_aggregation
when:
  currencies_count: ">1"
  governed_fx_available: false
decision:
  outcome: validation_error
  message: Mixed currencies cannot be aggregated without governed FX.
authority:
  type: product_policy
approval_required: false
```

The same engine can support:

* Finance controls
* Construction notices
* Insurance commission rules
* Retail replenishment
* Investment scoring
* Healthcare escalation
* Legal review

## 5. Workflow Engine

Each domain workflow becomes a state machine:

```text
draft
→ submitted
→ under_review
→ approved
→ executed
→ verified
→ closed
```

With explicit:

* Permitted actors
* Transition conditions
* Required evidence
* Calculations
* Approval thresholds
* Rejection/rework routes
* Locking
* Reopening
* Cancellation
* Audit events

## 6. Approval Matrix Engine

The approval matrix must decide approval from:

```text
action
+ domain
+ role
+ value/risk threshold
+ tenant policy
+ project policy
+ segregation-of-duties rules
+ payload digest
```

Example:

```yaml
action: finance.budget_lock
risk: high
requester_roles:
  - fp_and_a_manager
approver_roles:
  - controller
  - cfo
minimum_approvals: 1
self_approval: prohibited
payload_binding: required
replay: prohibited
emergency_override:
  role: platform_admin
  justification_required: true
```

## 7. Reasoning Planner

The reasoning process becomes:

```text
Understand request
→ determine domain intent
→ identify current scope
→ retrieve authoritative evidence
→ select applicable rules
→ select deterministic formulas
→ determine workflow state
→ determine permissions
→ determine approval requirement
→ produce plan
→ ask for missing inputs
→ execute permitted calculation
→ propose high-impact action
→ wait for human approval
→ execute once
→ verify result
→ record evidence
→ explain conclusion
```

That is the actual **Cerebrum reasoning layer**.

# The Reasoning Kernel mission

The mission below is the current execution program for the Cerebrum ecosystem.

```text
You are the principal domain architect, reasoning-systems engineer, formal-rules engineer, workflow architect and repository archaeologist for the Cerebrum ecosystem.

Your mission is to perform a complete scout of all existing Cerebrum products and extract their domain intelligence into a reusable, domain-neutral Cerebrum Reasoning Kernel.

This is not a generic LLM orchestration exercise.

The kernel must formalize and execute:

- Domain calculations
- Domain formulas
- Domain rules
- Decision tables
- Exact approval matrices
- Domain workflows
- Permissions
- Evidence requirements
- Authority sources
- Exceptions
- Refusal conditions
- Human-decision gates

The language model may classify, extract, plan and explain.

The language model must never become the authority for deterministic calculations, trusted values, approval decisions or regulated domain rules.

==================================================
REPOSITORIES TO SCOUT
==================================================

Primary product donors:

1. bopoadz-del/The_Fork
   Domain: construction intelligence and project controls

2. bopoadz-del/RetailOpsReference
   Domain: retail operations

3. bopoadz-del/Projectfifteen
   Domain: insurance operations and compensation

4. bopoadz-del/Projecttwo
   Domain: FP&A, EPM and finance transformation

5. bopoadz-del/Projectten
   Domain: investment scoring and portfolio analysis

6. bopoadz-del/projectseven
   Domain: investment assistant and execution interface

7. bopoadz-del/Cerebrum-BuildOps
   Domain: inspect and classify actual status; do not assume completeness

Supporting repositories:

8. bopoadz-del/Cerebrum-Blocks
   Destination for reusable runtime capabilities and certified domain packs

9. bopoadz-del/CerebrumDev.ai
   Destination for scout/compiler/generator capabilities

10. bopoadz-del/Projectsix
    Identity, permissions, scope, authority and governance donor

11. bopoadz-del/Cerebrum
    Inspect for reusable reasoning, formula, planning, learning or orchestration capabilities

Do not modify product repositories during the scout phase.

==================================================
REPOSITORY RULES
==================================================

Cerebrum-Blocks branch:
feat/cerebrum-reasoning-kernel

CerebrumDev.ai branch:
feat/domain-intelligence-compiler

Projectsix branch:
Create only if a genuinely reusable authority or approval capability must be corrected.

Never commit directly to main/master.

Never merge automatically.

Do not rewrite history.

Do not fabricate implementation status from README files, commit names or folder names.

Inspect actual code, tests, schemas, manifests, routes, services, workflows and deployment behavior.

==================================================
PHASE 1 — COMPLETE DOMAIN INTELLIGENCE SCOUT
==================================================

For every product repository identify:

1. Domain entities
2. Domain terminology and ontology
3. Deterministic calculations
4. Formula definitions
5. Units and currencies
6. Rounding and precision policies
7. Thresholds
8. Business rules
9. Decision tables
10. Workflow states
11. Workflow transitions
12. Required actors
13. Permissions
14. Approval requirements
15. Separation-of-duty requirements
16. Evidence requirements
17. Source-authority requirements
18. Exceptions and refusal paths
19. Human-decision points
20. Existing tests and verification oracles
21. Hardcoded or duplicated logic
22. Contradictory rules
23. Mocked or placeholder logic
24. LLM-generated logic incorrectly treated as authoritative
25. Product-specific versus reusable capability

Create:

docs/reasoning-scout/PLATFORM_INVENTORY.md
docs/reasoning-scout/FORMULA_INVENTORY.md
docs/reasoning-scout/RULE_INVENTORY.md
docs/reasoning-scout/WORKFLOW_INVENTORY.md
docs/reasoning-scout/APPROVAL_MATRIX_INVENTORY.md
docs/reasoning-scout/AUTHORITY_SOURCE_INVENTORY.md
docs/reasoning-scout/DUPLICATION_AND_CONFLICT_REPORT.md
docs/reasoning-scout/DONOR_CAPABILITY_MATRIX.md

Classify every discovered item as:

- VERIFIED_EXECUTABLE
- EXECUTABLE_UNVERIFIED
- TESTED
- DOCUMENTED_ONLY
- MOCKED
- PLACEHOLDER
- HARDCODED
- DUPLICATED
- CONFLICTING
- OBSOLETE
- UNSUPPORTED
- CANDIDATE_FOR_KERNEL
- DOMAIN_SPECIFIC
- REQUIRES_DOMAIN_EXPERT_REVIEW

Record exact repository, commit, file, symbol and test evidence.

==================================================
PHASE 2 — DEFINE THE DOMAIN PACK STANDARD
==================================================

Create a versioned Domain Pack specification.

Required structure:

domain_packs/<domain_id>/
  manifest.yaml
  ontology.yaml
  entities.yaml
  permissions.yaml
  evidence_policy.yaml
  authority_sources.yaml
  refusal_policy.yaml
  calculations/
  formulas/
  rules/
  decision_tables/
  workflows/
  approvals/
  actions/
  verification_oracles/
  fixtures/
  tests/

Create formal JSON schemas or Pydantic schemas for every artifact.

Every formula must define:

- formula_id
- name
- domain
- version
- description
- expression or executable implementation
- inputs
- output
- types
- units
- currency policy
- precision
- rounding
- assumptions
- preconditions
- postconditions
- edge cases
- unsupported conditions
- authority source
- effective date
- expiry/review date
- implementation symbol
- verification oracle
- human review policy

Every rule must define:

- rule_id
- version
- conditions
- decision
- severity
- exceptions
- precedence
- conflicts
- authority source
- evidence requirements
- effective dates
- review owner
- refusal behavior

Every workflow must define:

- workflow_id
- states
- initial state
- terminal states
- transitions
- permitted actors
- transition guards
- calculations invoked
- evidence required
- approval required
- notifications
- retry/rework path
- cancellation path
- reopening rules
- immutable states
- audit events

Every approval policy must define:

- action
- risk tier
- requester roles
- approver roles
- approval count
- thresholds
- self-approval policy
- segregation of duties
- payload binding
- expiry
- replay prevention
- emergency override
- evidence
- audit requirements

==================================================
PHASE 3 — BUILD THE CEREBRUM REASONING KERNEL
==================================================

Implement in Cerebrum-Blocks a domain-neutral reasoning runtime containing:

1. DomainPackLoader
2. DomainPackValidator
3. OntologyRegistry
4. FormulaRegistry
5. DeterministicFormulaExecutor
6. UnitAndCurrencyValidator
7. RuleEngine
8. DecisionTableEngine
9. WorkflowEngine
10. ApprovalMatrixEngine
11. EvidenceRequirementEngine
12. AuthorityResolver
13. PermissionResolver
14. ReasoningPlanner
15. ActionPlanValidator
16. ConflictDetector
17. AssumptionRegistry
18. RefusalEngine
19. VerificationOracleRunner
20. ExplanationComposer
21. ProvenanceRecorder
22. DomainPackVersionManager

Standard reasoning result:

{
  "status": "success | dependency_required | validation_error | permission_denied | approval_required | unsupported | conflict_detected | execution_error",
  "domain": "...",
  "intent": "...",
  "scope": {...},
  "facts": [...],
  "assumptions": [...],
  "missing_inputs": [...],
  "rules_applied": [...],
  "formulas_applied": [...],
  "workflow": {...},
  "approval": {...},
  "recommended_actions": [...],
  "prohibited_actions": [...],
  "evidence": [...],
  "authority_sources": [...],
  "confidence": {...},
  "explanation": "...",
  "input_digest": "...",
  "output_digest": "..."
}

Rules:

- Trusted context fields cannot be overwritten by model output.
- Formulas must execute deterministically.
- Currency and units must fail closed.
- Conflicting authoritative rules must produce conflict_detected.
- Missing required facts must produce dependency_required.
- Unsupported operations must produce unsupported.
- High-impact actions must produce approval_required.
- A language model cannot authorize an action.
- A language model cannot change workflow state directly.
- A language model cannot silently choose an assumption.
- Every result must identify versions and provenance.

==================================================
PHASE 4 — BUILD THE DOMAIN INTELLIGENCE COMPILER
==================================================

Implement in CerebrumDev.ai a compiler that can:

1. Scout a product repository.
2. Discover calculations, rules, workflows and approvals.
3. Generate candidate Domain Pack artifacts.
4. Link each artifact to source code and tests.
5. Mark unverified candidates clearly.
6. Detect duplicates.
7. Detect contradictions.
8. Generate missing test templates.
9. Generate role/approval matrix drafts.
10. Generate workflow diagrams and state machines.
11. Generate verification oracles.
12. Validate a Domain Pack against its schema.
13. Package and publish a validated Domain Pack.
14. Install a Domain Pack into a generated product.

The compiler must not silently certify extracted content.

Certification states:

- candidate
- technically_verified
- domain_review_required
- domain_approved
- deprecated

Only domain_approved formulas and rules may be authoritative in production mode.

==================================================
PHASE 5 — EXTRACT EXISTING DOMAIN PACKS
==================================================

Create candidate or certified packs, based on actual evidence, for:

- construction_ops
- retail_ops
- insurance_ops
- finance_ops
- investment_analysis

Do not simply copy product source trees.

Neutralize infrastructure dependencies.

Retain domain-specific names, rules and formulas inside their domain pack.

Promote only generic runtime behavior into the Reasoning Kernel.

For every extracted formula, rule, workflow and approval policy record:

- donor repository
- donor commit
- donor path
- donor symbol
- donor tests
- extracted artifact digest
- certification status

==================================================
PHASE 6 — VERIFICATION
==================================================

For formulas implement:

- Known-answer tests
- Boundary tests
- Invalid-input tests
- Unit tests
- Currency tests
- Precision tests
- Rounding tests
- Metamorphic tests
- Independent cross-checks where possible

For rules implement:

- Positive condition tests
- Negative condition tests
- Exception tests
- Precedence tests
- Conflict tests
- Effective-date tests

For workflows implement:

- Valid transition tests
- Invalid transition tests
- Role tests
- Evidence tests
- Approval tests
- Rejection/rework tests
- Lock/reopen tests
- Replay tests
- Concurrency tests

For approval policies implement:

- Correct approver
- Incorrect approver
- Self-approval rejection
- Threshold escalation
- Payload-digest binding
- Expired approval
- Approval replay
- Emergency override audit

==================================================
PHASE 7 — NEUTRAL PROOF PLATFORM
==================================================

Generate a fresh neutral reference platform using:

- Universal Platform Kernel
- Cerebrum Reasoning Kernel
- A small neutral sample Domain Pack

The neutral sample must not be finance, construction, retail, insurance or investment.

Use a simple equipment-maintenance domain.

Demonstrate through browser and API:

1. Login
2. Select tenant/project
3. Submit an equipment inspection
4. Apply deterministic condition rules
5. Calculate a risk score
6. Create a recommended maintenance action
7. Determine the required approval level
8. Obtain approval from the correct role
9. Prevent self-approval
10. Execute one approved workflow transition
11. Reject one unsupported transition
12. Produce an evidence-grounded explanation
13. Generate XLSX/PDF output
14. Verify the audit chain
15. Run in PostgreSQL
16. Run in Docker
17. Pass GitHub Actions

This neutral platform is the proof that the Reasoning Kernel is domain-independent.

==================================================
PHASE 8 — ACCEPTANCE GATES
==================================================

Do not declare completion until:

1. Every existing product has a completed scout report.
2. Every extracted artifact has exact provenance.
3. Domain Pack schemas validate.
4. Formula execution is deterministic.
5. Rules are versioned and testable.
6. Workflows are state-machine enforced.
7. Approval matrices are executable.
8. Payload-bound approvals prevent replay.
9. Conflicts are detected rather than silently resolved.
10. Missing inputs produce dependency_required.
11. Unsupported actions are refused.
12. Trusted values cannot be overridden by an LLM.
13. Domain packs can be installed independently.
14. The neutral proof platform works.
15. PostgreSQL tests pass.
16. Docker builds pass.
17. Browser E2E passes.
18. CI is green.
19. Product repositories were not damaged or rewritten.
20. No PR was merged automatically.

==================================================
FINAL REPORT
==================================================

Return:

1. Repositories inspected
2. Exact commits inspected
3. Domain calculations discovered
4. Formula inventory totals by domain
5. Rule inventory totals by domain
6. Workflow inventory totals by domain
7. Approval-policy totals by domain
8. Verified versus unverified counts
9. Duplications found
10. Conflicts found
11. Generic capabilities promoted
12. Domain packs created
13. Certification status of every pack
14. Reasoning Kernel branch and PR
15. Compiler branch and PR
16. Steward PR if created
17. Exact test counts
18. PostgreSQL results
19. Docker results
20. Neutral-platform E2E results
21. CI URLs
22. Known limitations
23. Domain-expert decisions still required
24. Confirmation that no product PR was merged
25. Exact command for generating the next domain reasoning pack

Proceed without stopping for ordinary engineering decisions.
Stop only for repository access, destructive operations or genuine domain-authority contradictions that cannot be resolved from evidence.
```

## The key correction to our earlier vision

The next platform should not ask the coding agent:

> “Build the domain reasoning.”

It should provide:

```text
Universal Kernel
+ Reasoning Kernel
+ Certified Domain Pack
```

(The Universal Kernel is already in place across the products.)
The coding agent then only configures:

* Which modules appear
* Which roles apply
* Which domain-pack version is pinned
* Which connectors are enabled
* Which client-specific rules override the domain defaults

That is how the next mature platform moves toward the **one-hour baseline** without inventing domain expertise every time.

And yes—this is worth being excited about. But the concept is captured now; you do not need to solve the entire architecture tonight.
