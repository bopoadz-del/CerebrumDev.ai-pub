# Where the agent sits in the build kernels — and where it could go next

Written after the first live manufacturing campaign against the real
Cerebrum-Blocks store (2026-08-13, builds 1–14 of `field_ops.yaml`). Records
the division-of-labour decision as built, the measured reasons for it, and the
two expansions worth doing — so the next session extends this deliberately
instead of re-deriving or accidentally reversing it.

## The decision as built

Five roles, one agent. Each kernel has a job title, an agent seat, and a
distinctive HTTP surface on the delivered platform (`GET /v1/jobs` is the
roster). CLONER and STORE_MANAGER stay deterministic kernels (exact answers,
provenance). The coding agent is consulted from three kernels:

| Kernel | Job | Agent | HTTP |
|---|---|---|---|
| **COLLECTOR** | Binding surveyor | consult | `GET /v1/catalog` |
| **CLONER** | Block stocker | none | `GET /v1/inventory` |
| **WRITER** | Platform manufacturer | manufacture | `GET /v1/capabilities`, `POST/GET /v1/{capability}`, `GET /v1/{capability}/{id}` |
| **TESTER** | Acceptance inspector | consult | `GET /v1/gates` (describes coverage; does not run tests) |
| **STORE_MANAGER** | Store registrar | none | `GET /v1/provenance` |

- **COLLECTOR** — report-only binding review. The kernel still resolves
  `block_ids` verbatim; the agent endorses or flags a mismatch. Mismatches
  are notes, never a plan mutation and never a weaker gate.
- **WRITER** — manufactures handlers, model specs, routes, README, inside
  lanes it cannot escape (`build/authority.py`, `build/gates.py`).
- **TESTER** — extra domain cases under TESTER authority (`tests/**` only).
  Cases are admitted only as mutations of spec-derived payloads; they cannot
  replace the kernel suite. The WRITER still cannot see or edit `tests/`.

Why the mechanical roles stay agent-free:

- **The mechanical roles have exact answers.** Vendoring six blocks plus the
  runtime slice takes ~2 s and is byte-identical every run. Provenance
  (commit pins, lockfile, ledger) must be exact or the Store Manager's
  staleness question becomes unanswerable. An agent adds latency, token cost
  and new failure modes to work that needs no judgment.
- **The inspector must not be the builder.** The TESTER may *add* cases; it
  must not let the WRITER write the tests that judge it. Every fake green in
  the campaign was caught by deterministic test machinery the agent cannot
  edit (WRITER lane excludes `tests/`; TESTER lane excludes `app/`).
- **"Defined jobs" as executable code beats defined jobs as prompt text.**
  A prompt instruction is advice; `assert_write_allowed` is a wall.

## What made the single-agent WRITER converge (the campaign's yield)

Each of these exists because a live build failed without it. They are the
convergence stack, in `build/roles.py` and `factory/coder.py`:

1. Findings name what failed (dispatch error envelopes carry block+action;
   generated tests collect every failing capability, not just the first).
2. Rework is a **ratchet** — only implicated capabilities regenerate; green
   specs/handlers/routes are reused verbatim.
3. Rework is an **edit** — the coder sees its own previous body next to the
   findings. Blind regeneration converged to identical wrong code five
   rounds running.
4. A statically rejected body earns one repair retry carrying the gate's
   reason.
5. The block contract carries everything discoverable at build time:
   block.json actions and defaults, `input_schema` required fields, and the
   requirement strings blocks answer in their own error literals.
6. Offline is enforced, not asserted: the generated conftest blocks outbound
   sockets, and the nested-results scan fails any green wrapping a failed
   block call.

## Future work: more agent, inside fixed kernels

The COLLECTOR review and TESTER extra-cases expansions below shipped. What
remains is tightening them from live campaigns, not re-deriving the split.

### 1. A judging COLLECTOR (semantic block choice) — shipped
The COLLECTOR still trusts the blueprint's `block_ids` for the plan. The
coding agent now reviews each binding against harvested `block.json`
contracts and records endorse/mismatch notes. Lane: report-only (the
COLLECTOR stays read-only); mismatches do not invent gaps and do not
weaken the gate.

### 2. An augmenting TESTER (agent-written domain cases) — shipped
The TESTER's kernel suite is still templated from the spec. The coding
agent may propose *additional* domain cases; the kernel admits them only
as mutations of spec-derived payloads (same keys, at least one value
changed, no new keys). They land in `tests/test_agent_domain.py` under
TESTER authority. The kernel suite remains the floor.

### Explicitly rejected (and why)
- Agent-driven CLONER/collector file operations: no judgment content, exact
  answers required, provenance must be deterministic.
- Agent-authored replacements for kernel tests: the inspector-is-not-the-
  builder invariant is the reason this factory's greens are believable.
- Letting the WRITER see or edit `tests/` "to understand failures": findings
  already carry the full failing assertion; the lane exclusion is what makes
  the rework loop honest.
