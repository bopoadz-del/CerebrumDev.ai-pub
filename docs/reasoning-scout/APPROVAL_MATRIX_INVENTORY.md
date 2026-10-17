# Approval Matrix Inventory — Reasoning Kernel scout

Evidence: repo + commit + file + symbol. Re-verified before extraction.

## The_Fork @ 8535199 — construction

The_Fork has no executable approval-matrix engine. Its approval knowledge
is a role/authority vocabulary attached to procedures (see
WORKFLOW_INVENTORY.md) plus hardcoded approval rules:

- `no_work_before_dd_approval` (PRC-502) — Employer approval gate before
  work. DOCUMENTED_ONLY / LLM-enforced.
- `stop_work_resumption` (PRC-406) — PMC Project Director written
  sign-off required. DOCUMENTED_ONLY / LLM-enforced.
- `payment_form_controlled` (PRC-605) — VP Programme Management
  approval for controlled-document changes. DOCUMENTED_ONLY /
  LLM-enforced.
- `validate_design_status` — FORBIDDEN_DESIGN_STATUSES refuse
  APPROVED/APPROVAL/SIGN_OFF on design docs (PRC-501). VERIFIED_EXECUTABLE.
- `enforce_critical_rules` — keyword-regex enforcement of exactly one
  rule (no_approved_on_design). VERIFIED_EXECUTABLE but partial.

**Finding:** the approval authority is real (named roles, named
procedures) but enforcement is split between one regex path and the LLM
system prompt. This is the clearest CANDIDATE_FOR_KERNEL:
ApprovalMatrixEngine needs action + role + threshold + payload binding +
self-approval refusal — none of which exists here deterministically.

## Next donors to check for executable approval machinery

- Projecttwo (fail-closed approval gate — REASONING_KERNEL.md
  cites "named high-impact action types and a fail-closed approval
  gate").
- Projectsix (governance scaffold: allowlist + approval +
  append-only audit + injection guard — store EXECUTION_PLAN Wave 2).
- Projectfifteen (evidence + audit per financial line).
