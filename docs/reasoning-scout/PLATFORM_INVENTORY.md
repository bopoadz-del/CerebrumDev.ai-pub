# Platform Inventory — Reasoning Kernel scout

Program: extract domain intelligence (calculations, formulas, rules,
decision tables, workflows, approvals, evidence, authority, refusals)
from every existing Cerebrum product into a domain-neutral Reasoning
Kernel + certified Domain Packs. Mission: REASONING_KERNEL.md.

Evidence discipline: every classification below carries a repository,
an exact commit, and a file/symbol anchor. Nothing is inferred from
READMEs, folder names, or commit messages.

## Repositories in scope

| # | Repo | Domain | Local path | Commit inspected | Branch | Status |
|---|---|---|---|---|---|---|
| 1 | bopoadz-del/The_Fork | construction | C:\Users\user\The_Fork | 8535199 | main | pending deep scout |
| 2 | bopoadz-del/RetailOpsReference | retail | C:\Users\user\RetailOpsReference | 49c9bf2 | main | pending |
| 3 | bopoadz-del/Projectfifteen | insurance | C:\Users\user\Projectfifteen | 6c27cba | main | pending |
| 4 | bopoadz-del/Projecttwo | finance | C:\Users\user\Projecttwo | 2367463 | feat/projecttwo-platform-v1 | pending |
| 5 | bopoadz-del/Projectten | investment | NOT CLONED | — | — | needs clone |
| 6 | bopoadz-del/projectseven | investment UI | C:\Users\user\projectseven | c744f89 | main | pending |
| 7 | bopoadz-del/Cerebrum-BuildOps | (classify) | NOT CLONED | — | — | needs clone |
| 8 | bopoadz-del/Cerebrum-Blocks | store (destination) | C:\Users\user\Cerebrum-Blocks | f177ffe9 | feat/cerebrum-reasoning-kernel | kernel home |
| 9 | bopoadz-del/CerebrumDev.ai | factory/compiler (destination) | C:\Users\user\CerebrumDev.ai | bb0247ec | feat/domain-intelligence-compiler | compiler home |
| 10 | bopoadz-del/Projectsix | governance | C:\Users\user\Projectsix | 32f773d | main | pending |
| 11 | bopoadz-del/Cerebrum | platform donor | C:\Users\user\Cerebrum | 55d45a2 | main | pending |

## Standing rules (from the mission)

- Never commit to main/master in any destination repo; never merge
  automatically; never rewrite history.
- Product (donor) repositories are read-only during the scout.
- Classification vocabulary: VERIFIED_EXECUTABLE, EXECUTABLE_UNVERIFIED,
  TESTED, DOCUMENTED_ONLY, MOCKED, PLACEHOLDER, HARDCODED, DUPLICATED,
  CONFLICTING, OBSOLETE, UNSUPPORTED, CANDIDATE_FOR_KERNEL,
  DOMAIN_SPECIFIC, REQUIRES_DOMAIN_EXPERT_REVIEW.
- The LLM may classify/extract/plan/explain. It never becomes the
  authority for calculations, trusted values, approvals, or regulated
  rules.

## Known store-side ground truth (feeds DONOR_CAPABILITY_MATRIX)

Cerebrum-Blocks @ f177ffe9: 133 blocks, 115 signed registry entries,
19 domain kits. Real engines already in place that the kernel must
REUSE, not duplicate: `app/core/finance_ops.py` (Decimal money,
normalizers, stable digests), `finance_coa_governance` (hierarchy
validation + lifecycle gate), `finance_import` (file entry +
idempotency), `inbound_webhook` (HMAC), `formula_executor_v2`,
`sympy_reasoning`, `validation_pipeline`, `workbench` gates.

## Scout order

1. The_Fork (construction) — largest formula/workflow surface.
2. Projecttwo — approvals + money rules (approval matrix donor).
3. Projectfifteen — commission formulas + evidence records.
4. RetailOpsReference — retail reasoner + permission filtering.
5. Projectten + projectseven — scoring patterns (clone first).
6. Cerebrum — reusable reasoning/orchestration donor (clone/refresh).
7. Projectsix — authority/approval capabilities.
8. Cerebrum-BuildOps — classify actual status.
