# Cerebrum Universal Kernel — Extraction & Certification Mission

**Status:** GOLD — awaiting fire order
**Author:** Master of the Factory (delegated by the Founder)
**Date:** 2026-07-22
**Doctrine:** The Fork = golden donor · Cerebrum-Blocks = kernel home · CerebrumDev.ai = generator · Projecttwo = second validation product · neutral proof platform = certification evidence

---

## 1. Mission

Extract the generic platform capabilities hiding inside The Fork, harden them with the factory's battle-tested layers, certify them as versioned kernel kits in Cerebrum-Blocks, and wire the Factory to generate every future product **on the kernel** — so platform production is measured in hours, not weeks.

> **The Universal Kernel is already hiding inside The Fork. Our job is extraction and certification — not reinvention.**

The Kernel is the ~80% every Cerebrum product shares. The Domain Pack is the ~20% that differs. Neither may leak into the other.

---

## 2. The One-Hour Standard (generation target)

**Mission target: one hour from configuration to a functioning platform baseline.** Not one hour to a certified enterprise pilot — the hour buys the baseline; domain certification remains the serious work.

### 2.1 The generation hour

```text
0–15 min   Enter platform/domain configuration
15–30 min  Factory generates repo, backend, React UI, DB, Docker and CI
30–45 min  Install Universal Kernel + selected domain kit
45–60 min  Seed demo data, boot and run the standard acceptance journey
```

At minute 60 the platform already has:

- Login and real roles
- Tenant/project isolation
- React product shell
- PostgreSQL and pgvector
- Upload and durable processing
- Approvals and audit evidence
- Hybrid RAG and LLM provider
- XLSX/PDF exports
- Docker and GitHub Actions
- A deployed or deployment-ready demo
- Domain modules appearing from configuration

### 2.2 What still takes additional time (honest list)

The domain's **special intelligence** cannot be fabricated in one hour:

- Domain calculations and rules
- Exact approval matrix
- Domain workflows
- Specialist dashboards
- Connector mappings
- Real client data
- Legal, financial, medical or engineering validation
- Live deployment verification and security testing

### 2.3 The realistic time table

| Result | Target time |
|---|---:|
| Generated functioning platform | **1 hour** |
| Strong domain demo using an existing mature kit | **3–6 hours** |
| New domain requiring new blocks | **1–3 days** |
| Proper pilot validation and deployment | **Several days**, depending on integrations |

For a domain where the Store kit already exists and is certified — a future second Finance platform, another construction product — **one hour for a working product is realistic**.

### 2.4 The key acceptance rule

> **After the kernel project, no developer should rebuild authentication, RAG, audit, uploads, exports, workers, Docker, CI or the React shell again.**

Cross-check against the capability manifest (§4) — the rule has **no gaps**:

| Rule item | Served by |
|---|---|
| authentication | #1 `identity` · #2 `authorization_policy` · #22 `rate_limit_guard` |
| RAG | #10 `embedding_provider` · #11 `vector_store` · #12 `hybrid_retrieval` · #13 `llm_provider` · #14 `grounded_answer` |
| audit | #5 `audit_evidence` · #17 `json_audit_export` · #24 `provenance_verification` |
| uploads | #6 `secure_ingestion` · #7 `document_parsing` |
| exports | #15 `xlsx_export` · #16 `pdf_export` |
| workers | #8 `durable_jobs` |
| Docker, CI, React shell | **Factory generator templates** — the generator's half of the hour, not Store kits |

The manifest covers the rule completely — and adds #21 `billing_entitlement`, #23 `notification_mailer`, and the approval/monitoring capabilities the rule doesn't even demand yet.

### 2.5 The product configuration contract

The hour's input is a short YAML — the only thing a human should hand the Factory:

```yaml
product:
  name: LegalOps
  domain_kit: legal_ops

roles:
  - legal_admin
  - counsel
  - reviewer
  - executive_viewer

modules:
  - matter_dashboard
  - contract_review
  - obligations
  - risk
  - assistant

connectors:
  - sharepoint
  - google_drive
  - docusign
```

Then the Factory produces the application. This configuration is the seed of the domain pack + platform block fed into the Product Delivery Standard render (§9): roles, modules and connectors map to domain-pack fields; the rest is generated.

**The one-hour standard is the Factory's headline SLA.** Once Wave 4 lands, a timed generation run — configuration in, acceptance journey green, 60 minutes — becomes a standing acceptance test for the Factory itself.

---

## 3. Roles (fixed by doctrine)

| Repo | Role | Rule |
|---|---|---|
| **The Fork** | Golden donor | Extract generic capabilities. Neutralize. Construction/domain material **stays behind** — never crosses into the kernel. |
| **Cerebrum-Blocks** | Kernel home | `block_store/kits/universal_kernel/` — every capability lands here as a versioned, hash-pinned, test-certified kit. Blocks flow STORE → PRODUCT only. |
| **CerebrumDev.ai (Factory)** | Generator | Consumes kernel kits + domain pack → emits product shell, CI, Docker, infra, `.env` contract. Owns the 15–30 min generation segment of the hour. Never hand-writes what a kit provides. |
| **Projecttwo** | Second validation product | Proof the kernel generalizes beyond construction. If the kernel can't serve FinanceOps cleanly, the extraction was wrong. |
| **Neutral proof platform** | Certification evidence | Fresh sample product, non-construction AND non-finance, proving the full chain end-to-end (§7). |

---

## 4. Kernel capability manifest — 24 capabilities

Provenance legend: **F** = extracted from The Fork · **D** = factory-hardened (CerebrumDev.ai) · **N** = new build (does not exist anywhere yet)

| # | Capability | Prov. | Source of truth | Neutralization / work needed |
|---|---|---|---|---|
| 1 | `identity` | F+D | Fork JWT mechanics **merged with** factory `accounts.py` + `accounts_store` | Register/login/verify-email/password-reset; single-purpose hashed token family (`cdt_`/`cdk_`/`cdv_`/`cdr_`); PBKDF2; dual sqlite/Postgres backend behind one contract |
| 2 | `authorization_policy` | F→N | Fork RBAC as seed | Fork's admin/user model is too narrow — extend to user → tenant → project → role → permission → action → approval chain |
| 3 | `scope_guard` | D+F | Factory ownership doctrine | **404-not-403**: cross-tenant access never leaks existence. Factory-hardened; Fork's version needs the same doctrine applied |
| 4 | `approval_action` | **N** | — | The missing piece. propose → digest → approve → execute-once → replay prevention. Seed pattern: P4 runtime envelope (declared writes, guarded execution) |
| 5 | `audit_evidence` | F→N | Fork JSONL as seed | JSONL append-only is not evidence. DB records + per-record digests + hash chaining + exportable evidence bundle. Factory `cerebrum_product_kernel/audit.py` is the embryo |
| 6 | `secure_ingestion` | F | Fork ingestion foundation | Strip construction document types; keep the pipeline |
| 7 | `document_parsing` | F | Fork | Neutralize parsers; keep format support generic |
| 8 | `durable_jobs` | F | Fork | Job queue survives restart; domain job types stay out |
| 9 | `block_runner` | **N** | — | Unsolved in The Fork. Isolated execution of blocks with resource limits. Seed pattern: P4 runtime allowlisted commands + envelope guards |
| 10 | `embedding_provider` | F | Fork | Keep embedding-dimension validation — that bug class is already solved there |
| 11 | `vector_store` | F | Fork pgvector (HNSW/GIN) | Neutralize schema; keep index strategy |
| 12 | `hybrid_retrieval` | F | Fork BM25/RRF | **138 RAG tests become the certification base** — the kit ships with its proof |
| 13 | `llm_provider` | F+D | Fork cloud/Ollama/llama.cpp/offline **+** factory `ENGINE_PROFILE` | Fork's provider layer hardened with P4's profile-resolution discipline (cloud_api \| ollama_cloud \| local_sovereign, OpenAI-compatible, one retry then fail closed) |
| 14 | `grounded_answer` | F | Fork | Citation-required answering; neutralize prompt construction material |
| 15 | `xlsx_export` | F | Fork real exports | Keep; the "no fake exports" law is already embodied |
| 16 | `pdf_export` | F | Fork | Keep |
| 17 | `json_audit_export` | F | Fork | Merge with #5 audit_evidence upgrade |
| 18 | `health` | F+D | Fork **+** factory `/health` `/ready` | Production-hardened probes from the factory's deployment fixes |
| 19 | `monitoring` | F | Fork | Keep |
| 20 | `structured_outcomes` | F+D | Fork **+** P4 `Halt(condition, evidence, resume_input)` | Every failure is structured, resumable, and reported — even on halt (Article 6 report pattern) |
| 21 | `billing_entitlement` | **D** | Factory P3 — **The Fork has nothing here** | Stripe checkout/portal/webhooks → entitlement sync; 3-day trial lifecycle; `require_entitled` 402 gate. The monetization kernel every paid product needs |
| 22 | `rate_limit_guard` | **D** | Factory P0 | Sliding-window 429 on the auth surface. Abuse protection is platform infrastructure, not per-product afterthought |
| 23 | `notification_mailer` | **D** | Factory P0 | SMTP with **honest labeled dev-mode fallback** (no silent mock). Verification + reset flows depend on it |
| 24 | `provenance_verification` | **D** | Factory P4 `dna.py` + `cerebrum_product_kernel/provenance.py` | Checksum manifest verified **before anything else runs**; hash-pin immutability pattern (same mechanism that pins the delivery standard) |

**Honest arithmetic:** 20 capabilities conceptually present in The Fork (~65–75% of the kernel), of which ~40–50% is reusable with limited neutralization. 4 capabilities are factory-born. 2 capabilities (`approval_action`, `block_runner`) are genuine new builds — and both already have their seed patterns in the P4 runtime.

---

## 5. Certification contract — what makes a kit a *kernel* kit

A capability is not a kernel kit until it carries all five:

1. **Code module** — generic, domain-free, importable by any product
2. **Test suite** — the kit ships with its own proof (Fork's 138 RAG tests are the model)
3. **`kernel_manifest.json`** — `{name, version, sha256, dependencies, provenance: fork|factory|new, acceptance_test}` — hash-pinned exactly like the Product Delivery Standard (`STANDARD_SHA256` pattern); any byte change is a new version
4. **Fail-closed behavior** — missing config, missing dependency, or integrity mismatch halts loudly; honesty labels where a fallback exists; no silent mocks
5. **Product pin** — generated products pin kit versions in their DNA manifest; the P4 runtime's verify-before-anything rule applies to kits too

---

## 6. Extraction rules (the law of the mission)

1. **Extract generic, leave domain behind.** Construction material stays in The Fork. Finance material stays in FinanceOps. If a line of code names a domain concept, it does not cross.
2. **One capability = one PR = merge-when-green.** No batch extractions. CI babysitting doctrine applies.
3. **Neutralization is a rewrite, not a rename.** Renamed construction code is construction code.
4. **The kit's tests must pass against a neutral fixture** — no Fork data, no FinanceOps data.
5. **Fork stays untouched.** Extraction is read-only on the donor. The Fork remains the proven reference.

---

## 7. The neutral proof chain (mission acceptance)

The mission is complete when a **fresh sample platform — non-construction, non-finance** — is generated by the Factory from kernel kits + one domain pack, and demonstrates in CI and in the browser:

**login → roles → tenant isolation → upload → durable processing → approval → audit → hybrid RAG → LLM answer with citations → XLSX + PDF export → PostgreSQL CI → Docker build → browser E2E**

Every arrow in that chain must be served by a certified kit, not by hand-written product code. The proof platform's DNA manifest names the kit versions it consumed.

---

## 8. Sequencing — four waves

| Wave | Spine | Capabilities | Why first / last |
|---|---|---|---|
| **1 — Trust** | Everything stands on it | identity, authorization_policy, scope_guard, rate_limit_guard, audit_evidence, provenance_verification | No product ships without the trust spine; mostly factory-born, so lowest extraction risk |
| **2 — Intelligence** | The RAG core | secure_ingestion, document_parsing, durable_jobs, embedding_provider, vector_store, hybrid_retrieval, llm_provider, grounded_answer | Heaviest Fork extraction; rides on Wave 1's identity + audit |
| **3 — Operations** | Ship and bill | xlsx_export, pdf_export, json_audit_export, health, monitoring, structured_outcomes, billing_entitlement, notification_mailer | Turns a working product into an operable, monetizable one |
| **4 — New builds + proof** | The frontier | approval_action, block_runner, **neutral proof platform** | The two capabilities nobody has, then the certification that the whole kernel is real — closed by the timed one-hour generation run (§2) |

---

## 9. How this fires through the Factory (dogfooding)

This mission is itself a factory run:

1. Write a **kernel-extraction domain pack** (domain: platform infrastructure; authoritative calculations: none — acceptance is the §7 chain; high-impact actions: Store writes, kit certification).
2. Render it through the **Product Delivery Standard** (`/v1/factory/delivery-standard/render`) — the same immutable standard every product uses.
3. Hand the rendered brief to **Kimi Code — the factory's only coder** — one wave at a time, one capability per PR.
4. The factory audits each kit PR against the certification contract (§5) before merge.

The factory builds its own kernel using its own standard. If the standard can't produce the kernel, the standard gets fixed — that's the deepest dogfood we have.

---

## 10. Standing risks (honest list)

- **Fork RBAC extension (#2)** is the largest single design task — the tenant/project/permission chain touches everything above it.
- **approval_action (#4) and block_runner (#9)** are new builds; estimates on them are guesses until Wave 1 lands.
- **FinanceOps validation** may reveal kernel gaps FinanceOps papered over with hand-written code — treat every discovery as a kernel defect, not a product defect.
- **Kit versioning** across products will eventually need a compatibility policy; deferred until a second product actually pins kits.
- **The one-hour SLA** holds only while domain kits are certified and mature; an uncertified kit silently turns the hour back into days — the time table (§2.3) must stay attached to every sales and planning conversation.

---

*Extraction and certification — not reinvention. One hour to the baseline; the domain's special intelligence remains the serious work. The kernel is already hiding in The Fork, hardened in the Factory, and waiting for its home in the Store.*
