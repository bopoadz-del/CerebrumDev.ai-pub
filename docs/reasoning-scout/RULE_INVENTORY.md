# Rule Inventory — Reasoning Kernel scout

Evidence discipline: repo + exact commit + file + symbol + line. Status is
re-verified before any extraction into a Domain Pack.

## The_Fork @ 8535199 — construction

### Formal rule table: `app/core/construction_knowledge.py` CRITICAL_RULES (L66-143)

Seven named rules, each carrying rule_id, procedure (authority), and a
violation message. Classification per rule:

| rule_id | Authority | Enforced deterministically? | Classification |
|---|---|---|---|
| no_approved_on_design | PRC-501 | YES — `enforce_critical_rules` L136-160 scans text | VERIFIED_EXECUTABLE (keyword regex; see limitation below) |
| no_work_before_dd_approval | PRC-502 | NO — present in CRITICAL_RULES + injected into the agent system prompt only | DOCUMENTED_ONLY / LLM-enforced |
| rfm_not_vo | PRC-606 | NO — prompt-injected only | DOCUMENTED_ONLY / LLM-enforced |
| stop_work_resumption | PRC-406 | NO — prompt-injected only | DOCUMENTED_ONLY / LLM-enforced |
| payment_form_controlled | PRC-605 | NO — prompt-injected only | DOCUMENTED_ONLY / LLM-enforced |
| ncr_not_ir | PRC-402/PRC-405 | NO — prompt-injected only | DOCUMENTED_ONLY / LLM-enforced |
| design_review_min_distribution | PRC-501 | NO — prompt-injected only | DOCUMENTED_ONLY / LLM-enforced |

**Finding (rule-enforcement gap):** 6 of 7 formal rules are enforced by an
LLM reading a system prompt, not by code. `enforce_critical_rules` checks
exactly one (and only via keyword regex with design-context keywords —
regex `\bapprove[ds]?\b` + context words). This is the exact class the
kernel exists to fix: rules must be versioned, testable, and enforced
deterministically; the LLM may only surface them.

### Design-status vocabulary: `validate_design_status` (L193-215)

- VALID_DESIGN_STATUSES enforced; FORBIDDEN_DESIGN_STATUSES =
  {APPROVED, APPROVAL, SIGN_OFF} (PRC-501). Deterministic refuse.
  Classification: VERIFIED_EXECUTABLE (test evidence pending).

### NCR workflow sequence: `next_ncr_status` (L257-287)

- Status ladder + disposition validation (`validate_ncr_disposition`).
  Classification: VERIFIED_EXECUTABLE (test evidence pending).

### Tender evaluation: `evaluate_tender` (L457+, PRC-603)

- Weighted scoring + ranking. Classification: VERIFIED_EXECUTABLE
  (test evidence pending).

## The_Fork @ 8535199 — action router

### `app/core/action_router.py` (412 lines)

- Action→intent taxonomy with per-action descriptions incl. approval
  semantics (VO "approval state", design "submitted-on/approved-on",
  deviations "approval refs"). `needs_planning` / `best_action` /
  `hint_for_action` (L330-412) are deterministic routing helpers.
  Classification: VERIFIED_EXECUTABLE for the routing helpers;
  DOCUMENTED_ONLY for the approval semantics (descriptions, not enforced).

## Open items

- Test evidence for each rule/formula (formula scout in flight).
- The_Fork containers/construction/schedule.py (2754 lines) — delay-claim
  notice drafting (L16-116) is text generation with contract-data
  grounding; the deterministic pieces (milestone facts extraction) need
  classification.
