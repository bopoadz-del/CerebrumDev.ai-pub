# Free-Trial Readiness — Final Report

Engagement across **CerebrumDev.ai** and **Cerebrum-Blocks**: Phases 1–5,
the Phase-4 decisions, and a Render deployment fix. Method throughout:
failing test first (failing output before the fix), one green step per
commit, one repo finished before the other, and every claim reported from
the pushed remote — never a working tree.

> Companion honesty docs: `KNOWN_LIMITATIONS.md`, `docs/GUARDRAILS.md`,
> and `docs/decisions/phase1-dead-controls.md` (per repo).

## The core finding held

The brief's thesis was correct: the strong engineering already existed and
was not on live traffic. Across five phases this was overwhelmingly **wiring
and deleting false claims, not building from scratch** — the contract
kernel, grounding gate, block signing, and provenance verification were all
real and are now connected to the request path. The only genuinely new
builds were the trial-quota layer and the scope-refusal mechanism, both
small.

## Merge state

| Phase | CerebrumDev.ai | Cerebrum-Blocks |
|---|---|---|
| 1 — Fail honestly | merged (#121) | merged (#53) |
| 2 — Connect what exists | merged (#122) | merged (#54) |
| 2.5 — Trial readiness | merged (#123) | merged (#55) |
| 3 — Honesty artefacts | merged (#124) | merged (#56) |
| 4 — Decisions built | merged (#125) | merged (#57) |
| 5 — Hardening | pushed, **not merged** (2 commits) | pushed, **not merged** (7 commits) |

Phases 1–4 are on `master`/`main`. **Phase 5 plus the Render disk fix are
pushed and awaiting merge** on branch
`claude/cerebrum-free-trial-readiness-4vwzmn` in both repos.

## Phase 1 — all 11 blockers closed

- **1.1 Network claim — deleted.** `assert_host_allowed` asserted on a host
  string before spawning a subprocess; no enforcement is possible without a
  netns/egress proxy/firewall. Claim removed from code and docs.
- **1.2 Illegal Kimi argv — removed** in both repos (`--prompt` implies auto
  mode; `--yolo/--auto` are mutually exclusive and the CLI rejects them).
- **1.3 Success envelopes — fixed.** CLI failures now raise and fail the
  session as 502 carrying `kimi_error`/`cli_returncode`, never `ok:True`
  over a silent stub.
- **1.4 Nine dead controls — each wired or deleted**, recorded in
  `docs/decisions/phase1-dead-controls.md` per repo. `verify_token` is
  flagged as store inventory pending an owner ruling.
- **1.5 Output validation fails closed** — which immediately caught 6 real
  contract violations, fixed by making blocks match their contracts.
- **1.6 Decorative tests — 6 found and fixed.** A mutation sweep (delete the
  control, run its tests) exposed tests that passed with the safety property
  removed.
- **1.7 Stubs labeled in-product** — `android_drive` stopped returning
  `status: success` with fabricated device paths.

## Phase 2 — connected

- **2.1 Trust-scope enforcement on `/v1/execute`** (the highest-value fix):
  caller-supplied tenant/permission scope is stripped and replaced with the
  server-derived scope from the validated API key.
- **2.2 Grounding is a mandatory platform stage** on every answer path
  (CDev chat + steward runtime; Blocks execute + aviation). The
  `aviation_chat_server` caller-supplied-steps bypass, the raw ungrounded
  fallback, and the fabricated verdict are all gone; blocked answers are
  null; every verdict persists.
- **2.3 Health reports evaluated capability** — probes the Kimi CLI the way
  storage/redis are probed; `/ready` no longer counts `KIMI_MOCK` as a
  configured LLM.

## Phase 2.5 — the trial is publishable

Server-side per-account quotas (generation / daily chat / export), enforced
at every entry point; the trial boundary; a one-paragraph in-product "what
this is not yet" notice. A **live stranger walk** (register → first
prototype in 4 API calls, 4th generation refused with 429) found and fixed
a silent domain-default bug on session create.

## Phase 3 — honesty artefacts

- **Retrieval numbers:** golden hit@5 = **1.00 (47/47)**, blind hit@5 =
  **0.25 (3/12)**. The blind set was authored and committed *before* the
  eval runner existed, so blindness is verifiable in history.
- `KNOWN_LIMITATIONS.md` and `docs/GUARDRAILS.md` per repo, worst honest
  number first.
- The Blocks README was rewritten — it cited **seven** nonexistent paths
  (not the four in the audit) and claimed Ed25519-signed blocks while zero
  were signed.

## Phase 4 — decisions, per ruling (build #1–#3, #4 out of scope)

- **Source precedence** — equal-relevance results resolve by credibility
  tier; the steward combined search discloses `estate_overrides_platform`.
- **Revision currency** — `superseded_by` entries are down-ranked and warned;
  matches disclose `as_of` (effective date).
- **Scope refusal** — questions in the refusal categories (medication
  dosing, structural sign-off, legal filing strategy, live emergency) are
  never attempted, checked *before* the LLM; the steward kit ships its own
  copy so every generated product inherits it.
- **Live-state awareness — declared out of scope** (requires real
  integrations); the as-of-date disclosure is the mitigation.

## Phase 5 — hardening (pending merge)

- **Block signing now operating: 105/105 registry blocks signed and
  verifying** (was 79 verifying / 1 drifted / 25 unsigned). The private key
  is written for the owner's secrets manager;
  `scripts/rotate_publisher_key.py` refuses to write key material inside the
  repo and re-signs + verifies 100% in one command.
- **Concurrent two-tenant isolation test — found a real leak.** Stateful
  blocks keyed storage on a caller-supplied namespace; `_namespace` is now
  server-derived, so one tenant cannot reach another's data.
- **Git credential scrubbing** on the deploy subprocess — the access token
  was leaking via git's stderr into logs and API responses.
- **Dependency pinning + lockfiles** in both repos.
- **Dockerfile/render.yaml parity** — fixed real drift (missing
  `poppler-utils`, Python-version mismatch), enforced by a parity test.
- **Reversible `usage_counters` migration** (revision 0002).
- Bonus: the long-red cross-line-ending signing test is genuinely fixed
  (signatures are LF-canonical by construction).

## Render

Added a persistent disk + `STORAGE_PATH` to the Blocks `render.yaml` (the
grounding audit log was on ephemeral disk). Done as infrastructure-as-code —
no Render MCP is connected, and a live account is not driven off a pasted
key. Requires a dashboard apply / blueprint sync to take effect.

## Test state (from the pushed branches)

- **CerebrumDev.ai:** 657 passed. Remaining failures: 2 pre-existing + 6
  environment-dependent (documented in `KNOWN_LIMITATIONS.md`).
- **Cerebrum-Blocks:** 799 passed. Remaining failures: 4 environment-
  dependent (offline embedder ×2, absent corpus, Redis). The line-endings
  failure is fixed.

## What needs the owner

1. **Merge the two Phase-5 branches** to complete the brief.
2. **Rotate the Render API key** that was pasted into the working session.
3. **Copy the signing private key** out of the ephemeral scratchpad into a
   secrets manager (only the public half is committed).
4. **Apply the Blocks disk** in the Render dashboard or via blueprint sync.
5. **Watch the first build** of each repo after merge — the new `==` pins are
   the one deploy risk not validatable in the build sandbox.

## Verification honesty

Everything is **mock-verified** (real subprocesses, real files) except the
Phase 2.5 stranger walk, which ran live against a locally booted backend. No
production deployment was exercised, and the real Kimi CLI never ran (its
flag-rejection is taken from documented contract). Honestly open by design:
kit-*bundle* Ed25519 signing (registry blocks are signed; kit bundles remain
sha256-provenance) and live-state awareness (out of scope). One outstanding
ruling remains: the `verify_token` identity-kit disposition from Phase 1.
