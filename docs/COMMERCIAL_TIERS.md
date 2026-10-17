# Cerebrum Commercial Tiers — Free · Pro · Team

**Status:** GOLD — ratified by the Founder, 2026-07-22
**Relationship to other law:** `docs/PLATFORM_TIERS.md` remains the *internal development-phase* doctrine (Phase 1 = now, Phase 2 = paid scout, later). This document is the *external commercial* model. Customers never see the word "Phase".

---

## 1. Names (customer-facing)

```text
Cerebrum Free
Cerebrum Pro
Cerebrum Team
```

"Phase 1" and "Phase 2" stay inside the factory. The customer should not need to understand our architecture.

## 2. The boundary (the whole model in one line)

> **Free understands the domain. Paid understands your organisation.**

The product never charges for basic intelligence. It charges for **personalisation, private data, collaboration and organisational control**.

## 3. The tier table

| Capability | Free | Pro | Team / Business |
|---|---:|---:|---:|
| Registered account | Yes | Yes | Yes |
| Users | 1 | 1 | Multiple |
| Domain-specific platform | Yes | Yes | Yes |
| Standard Reasoning Layer | Yes | Yes | Yes |
| Preloaded domain RAG (Layer 1) | Yes | Yes | Yes |
| Domain calculations and rules | Yes | Yes | Yes |
| Personal document upload | **Tiny trial (launch, §5)** | Yes | Yes |
| Private second RAG (Layer 2) | Trial index only (§5) | Yes | Yes |
| Persistent private knowledge | No | Yes | Yes |
| Projects/workspaces | 1 | Several | Organisation-wide |
| XLSX/PDF exports | Limited | Full | Full |
| Multi-user profiles | No | No | Yes |
| RBAC | No | No | Yes |
| Approval workflows | No | Personal confirmation only | Full role-based approvals |
| Organisation audit trail | No | Basic activity history | Full audit/evidence |
| Admin/user management | No | No | Yes |
| Connectors | No | Limited later | Yes |
| Usage limits | Low | Higher | Contract-based |
| Billing | Upgrade only | Subscription | Seats/organisation |

## 4. Two Founder corrections, ratified

1. **Registration exists for free users too.** Payment and subscription enforcement are platform infrastructure, not paid benefits.
2. **Multi-user and RBAC are a Team tier**, never bundled automatically into the cheapest personal subscription.

## 5. Launch amendment — the free tiny upload + tiny RAG trial (ratified for launch)

Free users at launch receive:

- Up to **three documents**
- A **temporary seven-day private index**
- Small page/storage limit
- No sharing
- No organisation memory

Purpose: the free experience must show the moment **"it understands my document."** Then the paywall appears for persistent private RAG, more documents, more projects and full exports. The trial index expires honestly — visible countdown, no silent persistence, no conversion of trial data into organisation memory.

## 6. The two-RAG structure

```text
RAG Layer 1 — Domain Knowledge
Provided by Cerebrum. Available to free and paid users.

RAG Layer 2 — Private Organisation Knowledge
Uploaded by the customer. Paid (launch trial excepted, §5).
Tenant-isolated and project-scoped.
```

The reasoning layer queries them separately — *"Domain rule says… / Your company document says… / Your current project record says…"* — and must **never** mix one customer's private documents with another customer's, nor silently let an uploaded document replace domain authority.

## 7. Where every feature belongs technically

- **Universal Platform Kernel:** registration, login, subscription status, payment provider, entitlement checks, usage metering, multi-user accounts, RBAC, tenant/project isolation, admin, audit, uploads, exports
- **CerebrumDev.ai Reasoning Layer:** domain intent, rules, formula selection, workflow selection, approval requirement, evidence requirement, RAG-layer selection, execution planning, refusal and explanation
- **Domain kit:** finance formulas, construction workflows, insurance rules, retail calculations, domain terminology and knowledge

## 8. The final model

```text
FREE  — Domain intelligence for one user (+ launch trial: tiny upload, tiny RAG)
PRO   — Domain intelligence + private organisation intelligence for one user
TEAM  — Private organisation intelligence + people + roles + approvals + governance
```

## 9. What must never happen

- Charging for registration or for basic domain intelligence
- Customer-facing use of "Phase 1" / "Phase 2"
- A trial index becoming persistent silently — it expires, honestly, with a visible countdown
- An uploaded document overriding domain authority
- Cross-tenant RAG leakage — Layer 2 is isolated or it does not ship

---

*Free understands the domain. Paid understands your organisation.*
