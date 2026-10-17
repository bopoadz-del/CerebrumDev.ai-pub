# Duplication and Conflict Report — Reasoning Kernel scout

Only evidence-backed findings. Re-verified at extraction time.

## The_Fork @ 8535199

### DUPLICATED (by design): CPM engine

`app/lib/pm_computations.py` (compute_cpm, forward/backward pass,
resource histogram, XER parsing) was ported into the Store as
`primavera_parser` (Cerebrum-Blocks wave 1.6, commit lineage in the
store's EXECUTION_PLAN ledger). The Store copy is the canonical runtime;
The_Fork's copy is the donor. Extraction must not create a third copy —
the kernel consumes the Store block.

### CONFLICTING (risk): concrete-volume waste path

`app/lib/construction_formulas_quantities.py` carries BOTH:
- a documented waste-factor path (`documented_waste_enabled` /
  `documented_concrete_waste_factor`), and
- `concrete_volume` with an internal waste treatment.
Plus `diaphragm_wall_panel_volume` hardcodes 1.10 tremie waste.
Three waste notions (documented factor, takeoff waste, tremie waste)
coexist — extraction must separate them as distinct versioned
constants, not merge them silently.

### CONFLICTING (risk): EVM convention checks

`calculate_evm` uses EV/PV/AC with CV=EV−AC, SV=EV−PV. Other donors
(FinanceOps, Projectfifteen) have their own EVM/earned-value code. Cross-donor
comparison pending — a real conflict here must produce
`conflict_detected` in the kernel, not a silent pick.

### HARDCODED

- Tremie waste 1.10, retention default 0.05 (PRC-605), SAR currency,
  remote-area factors in `mobilization_cost_estimate`, concrete
  MoE coefficient 15000, deflection coefficients (5/384, 1/8).
  All must become versioned constants with authority sources at
  extraction.

### LLM-enforced rules (authority placed on the model)

Six of seven CRITICAL_RULES are prompt-injected, not executed (see
RULE_INVENTORY.md). This is the core defect class the kernel removes:
the model is currently the enforcement engine for contractual rules.

## Open

- Cross-repo duplication scan (construction math exists in The_Fork,
  Cerebrum-Blocks blocks, and possibly Cerebrum) — pending Phase 1
  sweep of remaining donors.
