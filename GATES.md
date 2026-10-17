# GATES.md — Platform Build Gates (canonical)

The gate checklist every platform build must pass before it is pushed,
and the rule that decides whether a gate may fail a build at all.

**Canonical home**: this file, in the Factory (`bopoadz-del/CerebrumDev.ai`).
**Binding on**: every writer (human or AI) building a platform product
from Cerebrum-Blocks — including in external product repos.
**Enforcement**: `AGENTS.md` mandates reading this file before any
platform build or review; `scripts/check_platform_gates.py` verifies a
product repo carries the gate artifacts and fails otherwise.

---

## G0 — Grounding (store-first)

- [ ] Pin the Cerebrum-Blocks commit; vendor the kit byte-exact under `blocks/vendor/store`.
- [ ] `VENDOR.lock` with per-file sha256 (no `__pycache__`); `.gitattributes -text` on the vendored tree so hashes match on every OS.
- [ ] CI fails on any in-place edit under the vendored tree.
- [ ] Kit provenance (commit, manifest version) recorded.

## G1 — Domain truth

- [ ] Every surfaced answer carries `evidence_class` + `source`; Class B (expert recall) is never Class A.
- [ ] Verdicts are three-valued (PASS / FAIL / UNPROVABLE) — never bare booleans.
- [ ] Constraints are computed, not hardcoded (critical path, pacing constraint, cascade) — a test proves a graph change moves the answer.
- [ ] Refusal-class responses for the known traps: generic PPM tables, unsupported markets, construction-PM sources, raw media past the edge.

## G2 — Security

- [ ] Fail-closed auth on **every** route: reviewer (read) / operator (mutate); only health endpoints anonymous.
- [ ] Constant-time token compare (`hmac.compare_digest`), never `==`.
- [ ] CORS is an explicit allowlist; production never defaults to `*`.
- [ ] Audit journal is append-only, digest-only, never secrets or plaintext payloads.
- [ ] No secrets in the repo; `.env.example` + `secrets_guide.md` only.

## G3 — Honesty

- [ ] No fabricated data: connectors without a live system declare `mock_unavailable` and fail closed.
- [ ] Normalisers are real, tested code even when the adapter is a mock.
- [ ] Every placeholder is named, self-documenting, and off the critical path.
- [ ] Agents are deterministic-first; LLM output is keyed-optional and never silently replaces grounded answers.

## G4 — Tests

- [ ] Unit tests cover every reasoning rule and the three-valued logic.
- [ ] API tests include a per-route 401 matrix and role separation.
- [ ] Mutation probes: each probe breaks one safety property; all must go red.
- [ ] CI runs: pytest + vendor lock check + mutation probes. Red CI blocks the merge.

## G5 — Retrieval

- [ ] Uploaded documents are actually indexed and retrievable (round-trip test) with citations.
- [ ] Uploaded docs carry `A_sourced_document`; domain-sheet chunks carry Class B — never conflated.
- [ ] Embeddings (when used) carry a fingerprint guard (model + dim mismatch refuses).

## G6 — Deployment

- [ ] Sovereign compose profile + secrets guide; edge/vision contract is metadata-only.
- [ ] Raw media fails closed at the edge boundary (tested).
- [ ] Cloud fallback is optional and labelled non-sovereign.

## G7 — Delivery

- [ ] CI green on push — the only gate that counts at handoff.
- [ ] README states markets, honesty posture, run steps; ACCEPTANCE lists criteria with the proving test; RUNLOG records findings.
- [ ] Every new verdict/refusal is a named reason string.

---

## A gate exists only if a brief can turn it on

A gate failure the brief never defined means the GATE is wrong, not the
product: the gate goes advisory immediately, with the reason in the ledger,
and no patch is written. A gate exists only if a brief can turn it on.

Enforcement: the build runner classifies every round's failures as
brief-defined or factory-invented BEFORE any writer dispatch. A
factory-invented failure is recorded as an advisory ledger event (check id +
reason) and never reaches the writer; a round whose failures are all
factory-invented ends with no writer round spent.

### Run history (latest first)

- **2026-09-14 — HotelOps comparison test**: a fresh scaffold lost to a mature
  first PR on security and retrieval. Added to G2: per-route 401 matrix,
  `compare_digest`, CORS allowlist. Added to G5: uploaded-doc round-trip with
  A-class citations (decorative "uploaded docs" is a tie-breaker loser).
