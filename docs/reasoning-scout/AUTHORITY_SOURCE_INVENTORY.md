# Authority Source Inventory — Reasoning Kernel scout

Every extracted artifact must name the authority that makes it true.
This inventory records the authority vocabularies observed in donors.

## The_Fork @ 8535199 — construction

### Procedure codes as authority

The_Fork anchors rules and formulas to a controlled-procedure registry
(`app/core/construction_knowledge.py` `_load_db`, `app/data`):
PRC-301..606 (17 procedures). Examples verified in code:

- PRC-302 — risk methodology (`score_risk`)
- PRC-501 — design review vocabulary + distribution rule
- PRC-502 — Design Directive approval gate
- PRC-603 — tender evaluation
- PRC-605 — interim payment calculation
- PRC-606 — change management (RFM vs VO)

Classification: DOCUMENTED_ONLY as authority registry — machine-readable
procedure ids exist, but the authoritative TEXT behind each id lives in
the procedures DB (JSON) + system prompt, not in executable form.

### Named-role authority

Role vocabulary recorded in WORKFLOW_INVENTORY.md (authorizer, approver,
signatory, co_authorizer, ...). DOCUMENTED_ONLY — no executable
role-resolution.

### Known-answer oracles

Test batteries per formula (tests/test_construction_formulas.py etc.)
are the de-facto verification oracle for construction math.
Classification: VERIFIED_EXECUTABLE (150/150 passing).

## Open

- REASONING_KERNEL.md cites donors with cited-public-source formulas
  (transcription oracles). Locate those citations when scouting
  FinanceOps/Projectfifteen.
