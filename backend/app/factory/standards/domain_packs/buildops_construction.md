# Domain Pack — Cerebrum BuildOps (reference example)

The canonical Domain Pack shape for `factory.delivery_standard.render()`.
Copy this file per product and change ONLY the values — the standard prompt
itself (`../product_delivery_standard.md`) is never edited.

```yaml
# --- header ------------------------------------------------------------------
platform_name: Cerebrum BuildOps
domain: Construction and project controls
product_type: Project controls platform
target_users: Project managers, project controls engineers, planners, QS

# --- product mission (section 2) ---------------------------------------------
mission: >
  A construction project-controls platform where a project team runs
  programme, cost, change, claims, quality, safety and procurement in one
  place — with deterministic controls calculations, approval gates on every
  contractual action, and complete audit evidence.

# --- domain pack (section 3, in this exact order) -----------------------------
domain_purpose: >
  Give a construction project team one authoritative system of record for
  programme and cost performance, contractual change, and site compliance.

primary_users:
  - Project manager
  - Project controls engineer
  - Planner
  - Quantity surveyor

required_roles:
  - project_admin
  - project_manager
  - controls_engineer
  - viewer

required_product_modules:
  - Programme dashboard
  - Schedule controls
  - Cost controls
  - Change orders
  - Claims
  - QA/QC
  - Safety
  - Procurement
  - Document control
  - Project assistant

core_business_workflows:
  - Baseline programme import → progress update → variance analysis → forecast
  - Change event → estimate → internal review → client submission → determination
  - Site inspection → non-conformance → corrective action → close-out

authoritative_calculations:
  - Schedule variance
  - Cost variance
  - Earned value
  - Delay analysis
  - Change-order impact
  - Forecast at completion

domain_rules:
  - Baseline dates are immutable once approved; progress moves actuals only
  - Retention and VAT rules follow the contract, never a UI default

high_impact_actions:
  - Baseline approval
  - Forecast publication
  - Change-order recommendation
  - Claim issue
  - Contract notice

prohibited_autonomous_actions:
  - Issuing any contractual notice without human approval
  - Overwriting baseline schedule or budget values

data_sources:
  - Primavera P6 / MS Project exports (XER, MPP, XML)
  - Cost spreadsheets (XLSX, CSV)
  - Site diaries and inspection photos

required_connectors:
  - File import (XER, MPP, XLSX, CSV, PDF)
  - Email ingest for contract correspondence

required_exports:
  - Earned-value report (XLSX, PDF)
  - Change-order register (XLSX, CSV)
  - Delay-analysis pack (PDF)

security_regulatory_rules:
  - Contract documents are privileged; cross-project access must fail closed
  - Retention of contractual records for the statutory limitation period

demo_data_requirements:
  - One mid-life infrastructure project with baseline + 6 months of updates
  - Demo accounts per role, clearly labeled as demo

domain_acceptance_conditions:
  - A controls engineer reproduces an EV report matching a hand calculation
  - A change order cannot be issued without recorded approval
```
