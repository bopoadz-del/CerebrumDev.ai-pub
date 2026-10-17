# Reasoning Kernel Program — Status (2026-09-17)

The 8-phase mission (mirror of `docs/REASONING_KERNEL.md`). This file is
the running ledger; the final structured report is delivered per Phase 8.

## Phase 1 — COMPLETE (scout)

- 8 inventory docs in `docs/reasoning-scout/`: PLATFORM_INVENTORY,
  FORMULA_INVENTORY, RULE_INVENTORY, WORKFLOW_INVENTORY,
  APPROVAL_MATRIX_INVENTORY, AUTHORITY_SOURCE_INVENTORY,
  DUPLICATION_AND_CONFLICT_REPORT, DONOR_CAPABILITY_MATRIX.
- Key findings: The_Fork declares 7 formal PRC rules but only 1/7 is
  executed in code — 6 are LLM-enforced via system prompt (flagged, not
  fixed — do not modify donor repos). Projectten holds the API shell;
  the scoring engine lives in projectseven. **Cerebrum-BuildOps does
  not exist** (repo not found on GitHub) — recorded as not-found, not
  pending. Every item classified VERIFIED/TESTED/DOCUMENTED_ONLY/... per
  the mission vocabulary.

## Phase 2 — COMPLETE (pack standard)

- `app/reasoning_kernel/schemas.py` in Cerebrum-Blocks: PackManifest,
  FormulaSpec, RuleSpec, DecisionTableSpec, WorkflowSpec,
  ApprovalPolicySpec, DomainPack. Pydantic, `extra="forbid"`,
  certification enum (candidate/technically_verified/
  domain_review_required/domain_approved/deprecated).

## Phase 3 — COMPLETE (kernel)

- All 22 components in `app/reasoning_kernel/` (Cerebrum-Blocks):
  DomainPackLoader/Validator, OntologyRegistry, FormulaRegistry +
  DeterministicFormulaExecutor, UnitAndCurrencyValidator (fail-closed in
  executor), RuleEngine, DecisionTableEngine, WorkflowEngine,
  ApprovalMatrixEngine, EvidenceRequirementEngine, AuthorityResolver,
  PermissionResolver, ReasoningPlanner, ActionPlanValidator,
  ConflictDetector, AssumptionRegistry, RefusalEngine,
  VerificationOracleRunner, ExplanationComposer, ProvenanceRecorder,
  DomainPackVersionManager.
- Standard ReasoningResult with the exact status vocabulary; digests
  exclude `explanation` (model prose can never move a digest).
- 54/54 kernel tests green.

## Phase 4 — COMPLETE (compiler)

- CerebrumDev.ai `feat/domain-intelligence-compiler`,
  `backend/app/domain_compiler/`: AST-based DonorScout (test-evidence
  indexing), CandidatePackGenerator (candidate-only output, duplicate +
  contradiction flags, missing-test templates, stateDiagram-v2 diagrams,
  role-matrix drafts, oracle templates), validator binding the kernel's
  REAL schemas from the Cerebrum-Blocks checkout, package/publish/install
  with packaging refusal for invalid packs.
- 9/9 compiler tests + live chain against The_Fork @8535199 (68
  discoveries, 7 duplicates/contradictions flagged, pack validates).
- Rule: the compiler never certifies; promotion is the store gate's job.

## Phase 5 — PARTIAL (packs extracted)

5 candidate packs in Cerebrum-Blocks `domain_packs/` with per-artifact
provenance (donor repo/commit/path/symbol/notes):
- construction_ops (The_Fork @8535199) — formulas oracle-verified
  150/150 in The_Fork's own venv
- finance_ops (Projecttwo @2367463)
- insurance_ops (Projectfifteen @6c27cba)
- retail_ops (RetailOpsReference @49c9bf2)
- investment_analysis (projectseven @c744f89)
- + equipment_maintenance (Phase 7, domain_approved neutral sample)

## Phase 6 — DONE for extracted formulas

- Known-answer, boundary, invalid-input, boolean-refusal, precision and
  rounding tests in `tests/core/test_reasoning_kernel_pack*.py` and
  `neutral_platform/tests/test_oracles.py`; construction pack formulas
  run against The_Fork's own venv (150/150).

## Phase 7 — COMPLETE (neutral proof platform)

- `Cerebrum-Blocks/neutral_platform/`: equipment-maintenance pack +
  kernel-wired FastAPI platform. Login, tenant isolation (404-not-403),
  inspection -> deterministic rules -> risk score -> action -> approval
  level -> correct-role approval -> approved transition -> unsupported
  transition refused -> explanation -> XLSX/PDF -> verified audit chain.
- CI evidence (workflow `neutral-platform`, branch
  feat/cerebrum-reasoning-kernel):
  - kernel suite: 54 passed
  - E2E on SQLite: 17 passed
  - E2E on PostgreSQL (postgres:16 service): 1 passed — real run, not
    skipped
  - docker job: image build + live smoke against postgres:16 (health,
    login, inspection) — success
  - first run caught a real defect: flat `neutral_app` imports broke the
    Docker layout; fixed with package-relative imports (commit 05e8f1a8).

## Phase 8 — DONE

- Final structured report: `docs/reasoning-scout/FINAL_REPORT.md` and
  delivered in the session report.

## Branches (no auto-merge anywhere)

- Cerebrum-Blocks `feat/cerebrum-reasoning-kernel` — kernel + packs +
  neutral platform.
- CerebrumDev.ai `feat/domain-intelligence-compiler` — scout docs +
  compiler.
- Projectsix — no change needed (no genuinely reusable
  authority/approval capability required correction).

## Known limitations (honest)

- Neutral platform auth is an explicit demo stub; production needs SSO.
- Kernel engines hold state in memory (platform mirrors to DB).
- 6/7 The_Fork PRC rules are prompt-enforced in the donor; the pack
  records this rather than pretending they are executable.
- BuildOps repo does not exist; nothing pending there.
