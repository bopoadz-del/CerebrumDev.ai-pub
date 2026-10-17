# Formula Inventory — Reasoning Kernel scout

Evidence: repo + exact commit + file + symbol + test evidence. Donor repos
are read-only during the scout; formulas are extracted into Domain Packs
only in later phases, with provenance.

## The_Fork @ 8535199 — construction

### `app/lib/construction_formulas.py` (1038 lines) — ~40 formulas

Structural/concrete/site engineering calculators, all deterministic,
float + `round()` arithmetic, per-formula display precision:

- `dewatering_uplift_check` (L37) — uplift vs counterweight, FoS; 3dp
- `diaphragm_wall_panel_volume` (L81) — tremie waste factor 1.10; 2dp
- `dewatering_well_point_spacing` (L97)
- `formwork_striking_time` (L144) — FormworkStrikingResult (L134)
- `fineness_modulus` (L175) — sieve pass %, 2dp
- `concrete_mix_design_sg` (L182), `concrete_mix_slip_form` (L209)
- `modulus_of_elasticity_concrete` (L220) — 15000·√(fck·10)
- `beam_deflection_ss_udl` (L225) / `beam_deflection_cantilever_udl` (L232)
- `modulus_of_rupture` (L239) — ACI split-cylinder cross-check
- `thermal_shrinkage_equivalence` (L252) / `concrete_thermal_cracking_check` (L260)
- `unit_weight_concrete` (L276) / `shear_stress_check` (L283)
- `post_tensioning_force` (L295) / `composite_column_design` (L321)
- `wind_load_on_formwork` (L341) / `foundation_bearing_pressure` (L359)
- `precast_beam_erection_check` (L377)
- `crane_planning` (L396) / `crane_cost_estimate` (L474) — SAR currency
- `cost_buildup_concrete` (L530) / `cost_buildup_rebar` (L566) /
  `cost_buildup_formwork` (L592) — SAR/m3, SAR/t, SAR/m2
- `mobilization_cost_estimate` (L632) — remote-area factor
- `supervision_ratio` (L710) / `electrical_installation_sequence` (L759)
- `plumbing_flow_programme` (L772) / `grout_pressure_calc` (L797)
- `concrete_maturity_strength` (L818) — maturity index ratio
- `_build_calculator_registry` (L852) / `available_calculations` (L939)

### `app/lib/construction_formulas_planning.py` (456 lines)

- `critical_path_float` (L12) / `progress_quantity` (L50) /
  `productivity_manpower_duration` (L105) / `pe_unit_convert` (L285) /
  `material_consumption` (L331) / `concrete_mix_proportions` (L376)

### `app/lib/construction_formulas_quantities.py` (433 lines)

- `concrete_volume` (L203) / `rebar_weight` (L254) / `rebar_by_area` (L277)
  / `interior_finishes_takeoff` (L306) / `resource_line_cost` (L379)
- Waste-factor governance: `documented_waste_enabled` (L45) /
  `documented_concrete_waste_factor` (L133) — compose-vs-extract honesty
  path (see DUPLICATION_AND_CONFLICT_REPORT)

### `app/lib/pm_computations.py` (818 lines) — CPM engine

- `topological_order` (L26) / `cpm_forward_pass` (L65) /
  `cpm_backward_pass` (L99) / `calculate_float` (L125) / `compute_cpm`
  (L150) / `resource_histogram` (L209) / `gantt_data` (L282) /
  `compress_schedule` (L296) / XER parsing (L335-533) / look-ahead
  (L650) / Excel write (L755)
- NOTE: already ported into the Store as `primavera_parser`
  (Cerebrum-Blocks, wave 1.6) — cross-repo DUPLICATED by design; the
  Store copy is the canonical runtime.

### `app/core/construction_knowledge.py`

- `score_risk` (L289, PRC-302) — 1-5 × 1-5 grid, GREEN/AMBER/RED bands
- `calculate_payment` (L318, PRC-605) — retention, net due, % complete
- `calculate_evm` (L349) — SPI/CPI/SV/CV + EAC/ETC/VAC; documented
  policy: unrounded CPI feeds EAC, rounding is display-only
- `evaluate_tender` (L457, PRC-603) — weighted scoring/ranking

### `app/agents/formulas.py` (147 lines) — binding layer

- `resolve_binding` (L92) / `run_formula` (L123) /
  `validate_manifest_bindings` (L134) / `allowed_actions_for_hat` (L111) —
  formula registry bound to hat manifests. CANDIDATE_FOR_KERNEL
  (FormulaRegistry + PermissionResolver pattern).

## Test evidence (run 2026-09-17, The_Fork's own venv)

`tests/test_construction_formulas.py`, `test_formula_additions.py`,
`test_formula_quantities_earthwork.py`, `test_formula_qc_commercial_safety.py`,
`test_calculator_envelope_is_honest.py` — **150/150 passed**.
Known-answer and boundary cases per formula (e.g.
`test_dewatering_uplift_check_cannot_stop`,
`test_diaphragm_wall_volume_with_tremie_waste`).
Classification: **VERIFIED_EXECUTABLE** for the tested surface.

## Cross-cutting findings

1. **Money precision:** all cost buildups and `calculate_payment` use
   `float` + `round()`, not Decimal. FinanceOps and Projectfifteen already use
   Decimal-safe money. Kernel policy: Decimal conversion at the kernel
   boundary + per-formula precision declarations; donor code stays
   untouched in Phase 1.
2. **Currency:** SAR assumed/hardcoded in cost buildups — currency must
   become an explicit input in the extracted pack (UnitAndCurrencyValidator).
3. **Waste factors:** hardcoded (1.10 tremie; documented waste path in
   quantities module) — extraction as versioned constants with authority
   sources, not magic numbers.
4. **Formula registry already exists** (`_build_calculator_registry`,
   agents/formulas.py) — the kernel's FormulaRegistry has a working
   donor implementation.

## Open items

- Boundary/negative tests for planning module formulas (progress_quantity,
  productivity_manpower_duration) — verify test coverage before
  VERIFIED_EXECUTABLE.
- `calculate_evm` float CV/SV sign conventions vs other EVM
  implementations (Projectfifteen/finance donors) — conflict check pending.
