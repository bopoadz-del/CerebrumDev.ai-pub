# Donor Capability Matrix — Reasoning Kernel scout

Which kernel capability each donor can already supply (evidence-backed),
so Phase 3 REUSES working code instead of reinventing it.

Legend: ✓ verified in code+test · ~ exists but unverified/partial ·
— absent · (LLM) enforced by the model, not code

| Capability | The_Fork | FinanceOps | Projectfifteen | Retail | Projectten | Steward | Cerebrum-Blocks (destination) |
|---|---|---|---|---|---|---|---|---|
| Deterministic formula execution | ✓ 150/150 tests | pending | pending | pending | pending | — | ✓ finance_ops/formula_executor_v2/sympy_reasoning |
| Formula registry + binding | ✓ agents/formulas.py | pending | pending | pending | pending | — | ~ |
| Rule table with authority ids | ✓ CRITICAL_RULES (1/7 executed) | pending | pending | pending | pending | — | — (kernel target) |
| Rule enforcement (deterministic) | ✓ regex, partial | pending | pending | pending | pending | — | — (kernel target) |
| Decision tables / scoring grids | ✓ score_risk, evaluate_tender | pending | pending | pending | ✓ weighted scoring (pending verify) | — | ~ |
| Workflow state machine | ✓ NCR sequence only | pending | pending | pending | pending | — | — (kernel target) |
| Approval matrix (executable) | — (roles only) | ✓ cited fail-closed gate (pending verify) | pending | pending | pending | ✓ cited (pending verify) | — (kernel target) |
| Evidence requirements | ~ (VO/rationale refs) | pending | ✓ cited per-line audit (pending verify) | ✓ cited (pending verify) | pending | ✓ cited (pending verify) | ~ |
| Refusal/envelope honesty | ✓ calculator envelope tests | pending | pending | pending | pending | — | ✓ inbound_webhook/sandbox/estate blocks |
| Money as Decimal | — (float+round) | ✓ cited (pending verify) | ✓ cited (pending verify) | pending | pending | — | ✓ finance_ops Decimal |
| Unit conversion | ✓ pe_unit_convert | pending | pending | pending | pending | — | ~ |
| Role/permission resolver | ~ (hat manifests) | pending | pending | ✓ cited (pending verify) | pending | ✓ cited (pending verify) | ~ tier gates |
| Verification oracles (tests as truth) | ✓ | pending | pending | pending | pending | — | ✓ suites green |

## Scout progress

- The_Fork: formulas ✓ (150/150), rules ✓, workflows ✓ (registry-level),
  approvals ✓ (registry-level). Deep-scout COMPLETE for formulas;
  workflow step-texts + schedule.py pending.
- Remaining donors: pending.
