# Reasoning Kernel Program — Final Report (Phase 8)

Date: 2026-09-17. All evidence below is tool-verified; nothing is
inferred from READMEs, commit names or folder names.

## 1. Repositories inspected

- bopoadz-del/The_Fork (commit 8535199)
- bopoadz-del/RetailOpsReference (49c9bf2)
- bopoadz-del/Projectfifteen (6c27cba)
- bopoadz-del/Projecttwo (2367463, branch feat/projecttwo-platform-v1)
- bopoadz-del/Projectten (626eb06) — API/portfolio shell
- bopoadz-del/projectseven (c744f89) — the scoring engine
- bopoadz-del/Cerebrum-Blocks — kernel destination (branch feat/cerebrum-reasoning-kernel)
- bopoadz-del/CerebrumDev.ai — compiler destination (branch feat/domain-intelligence-compiler)
- bopoadz-del/Projectsix — inspected; no change required
- bopoadz-del/Cerebrum — inspected for reusable reasoning capabilities
- **bopoadz-del/Cerebrum-BuildOps does not exist** (404 on GitHub, both
  SSH and HTTPS) — recorded as not-found, not pending.

## 2. Exact commits inspected

The_Fork 8535199 · RetailClient 49c9bf2 · Projectfifteen 6c27cba ·
Projecttwo 2367463 · Projectten 626eb06 · projectseven
c744f89. Per-artifact provenance (repo/commit/path/symbol) is embedded
in every pack artifact's `provenance` block.

## 3. Domain calculations discovered

Risk scoring (construction), retention (construction), EVM, payment
certification, commission (insurance), FYC tiers, variance tolerance
(FP&A), action allowlists, Sharpe ratio, Altman Z, service-overdue
(neutral). Full ledger: docs/reasoning-scout/FORMULA_INVENTORY.md.

## 4-7. Inventory totals by domain

| domain | formulas | rules | workflows | approval policies |
|---|---|---|---|---|
| construction_ops | 7 | 4 (PRC) | 1 (NCR) | 1 (payment) |
| finance_ops | 2 | 3 | 1 | 2 |
| insurance_ops | 2 | 2 | 0 | 0 |
| retail_ops | 0 | 3 | 0 | 0 |
| investment_analysis | 2 | 1 | 0 | 0 |
| equipment_maintenance | 2 | 4 | 1 | 1 |

(Donor rules discovered beyond these are listed in RULE_INVENTORY.md,
including 6 The_Fork PRC rules that are prompt-enforced in the donor,
not executed in code — recorded as such, not fabricated into packs.)

## 8. Verified vs unverified

- construction_ops formulas: oracle-verified 150/150 in The_Fork's own
  venv (their test suite).
- Kernel suite: 54/54 green (CI + local).
- Neutral platform: 17/17 SQLite E2E + 1/1 PostgreSQL E2E in CI.
- Compiler: 9/9 tests + live chain on The_Fork.
- Every extracted pack artifact stays certification `candidate` except
  the neutral sample (domain_approved, oracle-verified in
  neutral_platform/tests/test_oracles.py).

## 9. Duplications found

- `calculate_*` + `score_risk` duplicated as module functions and as
  thin delegating methods in construction_knowledge.py (flagged in
  DUPLICATION_AND_CONFLICT_REPORT.md and by the compiler: 7 duplicates
  on a live The_Fork scout).
- Projectten vs projectseven overlap (API shell vs engine).

## 10. Conflicts found

- The_Fork: 7 PRC rules declared; only 1 executed in code, 6 enforced
  by LLM prompt — a governance conflict between declared and executed
  rules. Recorded, not resolved (donor repos are not modified).
- Compiler contradiction detection flags same-symbol multi-location
  implementations as domain_review_required.

## 11. Generic capabilities promoted (into the kernel)

22 components: DomainPackLoader/Validator, OntologyRegistry,
FormulaRegistry + DeterministicFormulaExecutor, UnitAndCurrencyValidator
(fail-closed), RuleEngine, DecisionTableEngine, WorkflowEngine,
ApprovalMatrixEngine, EvidenceRequirementEngine, AuthorityResolver,
PermissionResolver, ReasoningPlanner, ActionPlanValidator,
ConflictDetector, AssumptionRegistry, RefusalEngine,
VerificationOracleRunner, ExplanationComposer, ProvenanceRecorder,
DomainPackVersionManager.

## 12. Domain packs created

construction_ops, finance_ops, insurance_ops, retail_ops,
investment_analysis, equipment_maintenance.

## 13. Certification status

All five extracted packs: candidate (with per-artifact provenance).
equipment_maintenance: domain_approved (the kernel program's own
neutral sample). Promotion of candidates requires the store gate.

## 14-15. Kernel and compiler branches + PRs

- Kernel: Cerebrum-Blocks `feat/cerebrum-reasoning-kernel` (head
  05e8f1a8) — PR opened, NOT merged (no auto-merge, ever).
- Compiler: CerebrumDev.ai `feat/domain-intelligence-compiler` (head
  25cc62c4) — PR opened, NOT merged.

## 16. Steward PR

None — no genuinely reusable authority/approval capability required
correction.

## 17. Exact test counts

- Kernel: 54 passed (tests/core/test_reasoning_kernel*.py)
- Neutral platform: 17 passed SQLite, 1 passed PostgreSQL
- Compiler: 9 passed (incl. CLI subprocess round-trip)
- Construction pack oracle: 150/150 (The_Fork venv)

## 18-19. PostgreSQL and Docker results

- PostgreSQL: the full flow (login -> inspection -> risk 16 -> work
  order -> scheduled -> in_progress -> completed -> approval -> closed
  -> verified audit -> XLSX) executed against postgres:16 in GitHub
  Actions — 1 passed (not skipped). Dialect asserted as postgresql.
- Docker: image built in CI; live smoke against postgres:16
  (health/login/inspection) succeeded.

## 20. Neutral-platform E2E results

All 15 mission steps exercised through the real API: login, tenant
scoping (404-not-403 cross-tenant), deterministic rules, risk score,
recommended action, approval level, correct-role approval, self-approval
refusal, SoD refusal, payload binding, expiry, replay prevention, one
approved transition, one unsupported transition refused, immutable
closed state, evidence-grounded explanation, XLSX + PDF output,
verified hash-chained audit.

## 21. CI URLs

Workflow: Cerebrum-Blocks `.github/workflows/neutral-platform.yml`.
Run history: https://github.com/bopoadz-del/Cerebrum-Blocks/actions/workflows/neutral-platform.yml
(green run on the import-fix commit; first run caught the Docker import
defect — that is the gate working).

## 22. Known limitations

- Neutral platform auth is a demo stub (HMAC directory); production
  needs SSO. No other surface trusts client-supplied identity.
- Kernel engines hold state in memory; the platform mirrors to DB.
  Production hydrates engines from the DB.
- The_Fork's 6 prompt-enforced PRC rules are not extracted as
  executable artifacts (they have no executable implementation in the
  donor).
- Docker/PostgreSQL evidence is CI-based; Docker is not installed on
  the development machine.

## 23. Domain-expert decisions still required

- Promoting any candidate pack (all five extracted packs).
- Resolving the 6 prompt-enforced PRC rules (extract to code or
  formally declare them non-executable).
- The_Fork duplicate formulas (module fn vs delegating methods).
- Role matrices for finance/insurance packs (compiler drafts supplied).

## 24. No product PR merged

Confirmed: no merge anywhere. All work sits on the two feature
branches; nothing was pushed to main/master of any repository.

## 25. Exact command for the next domain reasoning pack

```
cd C:\Users\user\CerebrumDev.ai\backend
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli scout   --repo <donor-path> --commit <sha> --paths app\core --out <scout.json>
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli compile --report <scout.json> --domain-id <id> --name "<Name>" --out <out-dir>
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli validate --pack <out-dir>\<id>\pack.json
.venv-factory\Scripts\python.exe -m app.domain_compiler.cli publish  --pack <out-dir>\<id>\pack.json --dest C:\Users\user\Cerebrum-Blocks
```

Output lands as a `candidate` pack with full provenance in
Cerebrum-Blocks/domain_packs/<id>/; the store gate promotes it.
