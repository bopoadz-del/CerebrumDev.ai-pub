# Workflow Inventory — Reasoning Kernel scout

Evidence: repo + commit + file + symbol. Every entry below is re-verified
before extraction into a Domain Pack.

## The_Fork @ 8535199 — construction

### Procedure registry: `app/core/construction_knowledge.py` `_load_db` → `app/data` procedures DB

17 PRC procedures with role vocabularies; three carry explicit workflow
step sequences.

**With explicit state machines (CANDIDATE_FOR_KERNEL — workflow engine):**

- **PRC-402 Construction Non-Conformance Reporting** — 8 steps, roles:
  initiator, signatory, approver, action, participant, informed.
  `next_ncr_status()` (construction_knowledge.py L274-287) enforces the
  sequence deterministically. Classification: VERIFIED_EXECUTABLE.
- **PRC-501 Design Reviews & Acceptance** — 9 steps, no role map.
  `validate_design_status` + `check_review_timeline` enforce the
  constraints (status vocabulary, 7-day distribution rule).
  Classification: EXECUTABLE_UNVERIFIED (steps stored, no transition
  executor).
- **PRC-605 Interim Payments** — 8 steps, no role map. Paired with
  `calculate_payment` (float arithmetic — see FORMULA_INVENTORY precision
  finding). Classification: steps DOCUMENTED_ONLY; calculation
  VERIFIED_EXECUTABLE.

**Role-mapped but step-less (APPROVAL_MATRIX donor):** PRC-301, PRC-302,
PRC-401, PRC-405, PRC-502, PRC-601, PRC-606 — each carries a role
vocabulary (authorizer/approver/signatory/executor/...) with no
transition engine. Classification: DOCUMENTED_ONLY (roles) —
CANDIDATE_FOR_KERNEL approval-matrix extraction.

**Step-less and role-less:** PRC-303, PRC-403, PRC-404, PRC-406, PRC-602,
PRC-603, PRC-604 — titles only. Classification: DOCUMENTED_ONLY.

### Unified role vocabulary observed

initiator · approver · authorizer · co_authorizer · signatory ·
participant_signatory · process_owner · executor · implementer ·
reviewer · contract_administrator · informed · action · logger ·
responder · owner · programme_risk · cost_risk · register_maintainer

This vocabulary is the raw material for the kernel's PermissionResolver
and ApprovalMatrixEngine role model.

## Open items

- Read PRC-402/501/605 step texts and map each step to its permitted
  actors and required evidence (the DB has the data; extraction is next).
- Schedule.py delay-claim flow (containers/construction/schedule.py
  L16-142) — draft-notice path is generation with grounded facts;
  classify the deterministic half.
