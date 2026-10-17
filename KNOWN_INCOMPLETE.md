# KNOWN_INCOMPLETE — CerebrumDev.ai

Honest register of functions `scripts/audit_stubs.py` flags as hollow in the
shipping `backend/app/` tree. Every entry below is either a `Protocol`
interface declaration (structural typing — a `...` body is correct; the real
implementation lives in the same module) or a benign, guarded fallback. There
are **no** unimplemented functions on a user/demo path; the Phase 2 ingestion
scaffold stubs below sit behind 501 routes and land in Phase 2 §2/§3.

Format: `- <path> :: <name>  — <reason>`

## Protocol interface declarations (`...` is correct; impl is real)
The estate dual-RAG layer IS implemented — `RagIndexStore` (JSONL persistence)
and `HashEmbedder` / FastEmbed provide the real bodies. These entries are the
`Protocol` method signatures used for structural typing.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: read_manifest  — RagIndexStoreProtocol declaration; impl at RagIndexStore.read_manifest.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: read_records  — RagIndexStoreProtocol declaration; impl at RagIndexStore.read_records.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: write_index  — RagIndexStoreProtocol declaration; impl at RagIndexStore.write_index.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: upsert_document  — RagIndexStoreProtocol declaration; impl at RagIndexStore.upsert_document.
- backend/app/factory/kits/private_estate_operations/rag/store.py :: stats  — RagIndexStoreProtocol declaration; impl at RagIndexStore.stats.
- backend/app/factory/kits/private_estate_operations/rag/embeddings.py :: embed  — Embedder Protocol declaration; impl at HashEmbedder.embed (+ FastEmbed provider).
- backend/app/factory/build/gates.py :: __call__  — Gate Protocol declaration (structural typing for a phase check); the real bodies are the module-level gate functions gate_gaps_enumerated / gate_blocks_import_offline / gate_workspace_compiles / gate_suite_green / gate_store_ops_authorised, wired in the GATES mapping.

## Benign guarded fallback
- backend/app/resident_engineer/router.py :: _resolve_principal  — the `else` (non-estate, no steward-auth module) branch returns None; every state-changing resident route fails closed (401) when the principal is None, so a None here authorizes nothing.

## Phase 2 ingestion scaffold — §2/§3 land later (501-gated)
- backend/app/cerebrum_product_kernel/ingestion/drive_connector.py :: begin_oauth  — Phase 2 §2 scaffold; contract in ingestion/__init__.py; the mounted router returns 501 until the Drive connector lands.
- backend/app/cerebrum_product_kernel/ingestion/drive_connector.py :: oauth_callback  — Phase 2 §2 scaffold; 501-gated.
- backend/app/cerebrum_product_kernel/ingestion/drive_connector.py :: list_files  — Phase 2 §2 scaffold; 501-gated.
- backend/app/cerebrum_product_kernel/ingestion/drive_connector.py :: sync_file  — Phase 2 §2 scaffold; 501-gated.
- backend/app/cerebrum_product_kernel/ingestion/formula_intake.py :: submit_overlay  — Phase 2 §3 scaffold; 501-gated.
- backend/app/cerebrum_product_kernel/ingestion/formula_intake.py :: list_overlays  — Phase 2 §3 scaffold; 501-gated.
## Deliberate no-op by policy
- backend/alembic/versions/0001_baseline.py :: downgrade  — the baseline migration's reverse would be `DROP TABLE` on every accounts table; destroying user/account data is never an acceptable automated rollback. Schema rollback below the baseline is a manual, operator-decided act (restore from backup).
- backend/app/factory/build/roles_handlers.py :: _coder_route_body  — U12/U5: returning None keeps capability HTTP on kernel execute_action; an LLM-authored body would displace the kernel. The public import remains ``app.factory.build.roles``.
- backend/app/factory/build/harvest.py :: _store_write_authorized  — U6: harvest write is unauthorized; fail-closed so STORE_MANAGER cannot pretend to push Factory fixes to Cerebrum-Blocks.

---

## Open work — registered, not fixed

Not hollow functions. These are known-incomplete *systems*, listed so nobody
reads a green suite as "finished". `scripts/audit_stubs.py` ignores this
section (it only parses `- ` lines containing `::`).

### 1. Role runner — BUILT; cutover and the LLM writer are NOT
`app/factory/build/runner.py` binds the three kernels and drives a real
build: phase order via `assert_phase_order`, a lane-restricted `RoleWorkspace`
per role, the phase's gate after each role, the WRITER↔TESTER rework loop, a
budget whose exhaustion is a recorded failure, and resume from the ledger.
It runs end to end and produces a platform that invokes vendored blocks
locally instead of calling the store over HTTP.

Three things are genuinely still open:

**(a) Cutover — DONE 2026-08-17.** The runner is the production default; see
1b for what it trades. The template path survives behind
`FACTORY_BUILD_ENGINE=template` as the documented revert.

**(b) The WRITER calls the LLM coder — DONE.** Kimi writes each handler,
model spec and route against the block's harvested contract; the
deterministic body remains the recorded fallback when no key is configured
(which is how CI exercises this path). Measured: `field_ops` build 16,
RUN_SUCCEEDED with all five handlers agent-written and zero coder failures.

**(c) STORE_MANAGER is half built.** The **read-only registrar is
implemented** (`app/factory/build/registrar.py`, `cli.py store registry`): it
scans build ledgers and answers which platform cloned which block at which
revision, which clones have drifted from a given Store head, and where the
estate has diverged — two platforms running different code behind one block
name. It executes no `StoreOp`. `store_manager.py`
defines 15 ops with approval gates and every consumer still only prints the
manifest. The write half stays deferred deliberately: executing store ops
means writing to the Cerebrum-Blocks repo (publish, version, deprecate) with
a human-approval path for MAJOR and DELETE. That is its own milestone, and a partial
implementation that writes to the Store without the approval path is worse
than none.

The factory rebuild is **cut over and live**; the remaining open piece is
the STORE_MANAGER write half (c) and the artifact-parity port in 1b.

**S5 residual (2026-08-23):** Cerebrum-Blocks harvest remains unbuilt. Factory
CLONER now emits Store-unwired adapter contracts (`offline_adapters`) into
`vendor/**` and seals that tree after CLONER. The next product will re-ship
those Factory-owned corrections unless harvest lands upstream. This does not
pretend Blocks was fixed. No Blocks write was performed; none is authorized
from this repo. `prepare_pilot_workspace` is gone from the shipping runner.

**S13 harvest (2026-08-23):** `app.factory.build.harvest.evaluate_harvest` is
the policy no-op. It records BLOCKED, copies nothing, and states the
consequence (products will not receive Factory pipeline fixes from
Cerebrum-Blocks until an authorized harvest exists). A green S13 suite is not
a harvest.

### 1b. Cutover — DONE 2026-08-17, and what it traded
**The live test decided it.** A product downloaded from the running platform
was audited: six capability handlers that were ONE template differing only in
a `BLOCK_IDS` list, each `httpx.post(store_url + "/v1/execute")` back to the
operator's store, `vendor/blocks/` present but unreachable (no dispatch
runtime, no lockfile, no Store runtime slice), and zero coding-agent
authorship anywhere in the manifest. Everything the factory campaign fixed
lived behind a door production never opened.

`product_architect.generate_product` — the single door all four production
callers use (session generate, chat flow, `/v1/factory/generate`, workbench
promote) — now routes to the role runner. `FACTORY_BUILD_ENGINE=template`
reverts to the old path, which keeps its own contract tests.

Because a real build is minutes rather than the template's seconds, it runs
on a background thread and **the build ledger is the job record**: status is
a read of the artifact (`GET /v1/sessions/{id}/product/build-status`), so it
survives a worker restart. The download refuses a build that is still running
(409) or that failed its gates (409) — shipping either would hand the
customer a torn or gate-rejected artifact.

**What the runner artifact gains:** agent-written handlers/models/routes
against each block's real contract, in-process dispatch over blocks vendored
WITH the Store runtime slice they need, sqlite persistence, its own suite that
runs with the network blocked, deploy scaffold, `blocks.lock.json` provenance,
`scripts/release_gate.py` (clone-and-test) and `docs/build_provenance.json`
recording which artifacts the agent wrote.

**What it still does not emit (the honest trade):** hats/agent manifests,
workflows.json, the universal console, connectors, edge profile,
certification scaffold, Product DNA and the Resident Engineer — roughly the
template path's other ~60 files. Their contract tests are pinned to
`FACTORY_BUILD_ENGINE=template` rather than deleted. Porting the ones that
carry real value (Resident Engineer, Product DNA) to the runner is the next
piece of work; nothing about them is lost, but a runner-built product does
not carry them today.

### 1h. A runner build REQUIRES a coder key — MEASURED
With `FACTORY_CODER_ENABLED=0` (or no LLM key) the WRITER falls back to the
deterministic contract template, and a build against the **real Store** then
fails its own TESTER gate: the template calls each block with its declared
default action and a bare payload, and real blocks reject that
("Input validation failed", "channels required", "No steps defined"). Only
the agent writes handlers that construct each block's required input from the
capability's own fields.

Consequences, stated plainly:
- **Production must have a coder key.** `CEREBRUM_LLM_API_KEY` is set on the
  live service, so this holds today — but a keyless deploy will not produce a
  downloadable product; it will produce honest gate failures.
- **CI passes keyless** because it builds against the vendor mirror, whose
  stubs accept any payload. That is a weaker exercise than production and is
  the reason `test_d3_runner_is_the_production_artifact` deliberately does not
  pin an engine.
- The failure is loud and specific (status `failed` with findings), never a
  silently degraded artifact — and the download refuses it.

### 1i. The factory model and the coder timeout are load-bearing config — MEASURED
The first production build after cutover sat in the WRITER for **51 minutes**
against a 45-minute wall clock and never finished. Three compounding causes,
all now fixed in code, but the operational lesson is the config one:

1. **Production was configured with a model nobody had validated.**
   `CEREBRUM_FACTORY_LLM_MODEL` was `kimi-k2.7-code`; every measured build in
   the campaign used `kimi-k2.7-code-highspeed`. After the swap, handlers were
   written in ~30-45s each and progress was continuous. **Check the live env
   against the model the evidence covers — a code default does not change a
   service that already has the variable set.**
2. `ReadTimeout` was retried (3 attempts x 2 model legs x 180s = up to 18
   minutes for ONE artifact). Only connection failures retry now, and the
   per-call ceiling is `FACTORY_CODER_TIMEOUT_S` (default 120, production
   150).
3. The wall clock is only checked BETWEEN phases, so nothing bounded a slow
   WRITER. Roles now carry the build deadline and the coder yields to the
   deterministic template when a call cannot finish inside it, recording the
   skip by name.

Also fixed, and the reason this took an hour to diagnose: **nothing configured
logging**, so every application log line was dropped in production while
uvicorn's access logs printed, and the ledger recorded only phase boundaries —
a 51-minute WRITER reported a frozen "2/5". Builds now emit per-artifact
progress (`build_status.activity`) and app logs reach stdout. A build whose
thread dies reports `stalled` instead of `building` forever.

### 1j. Trial generations are consumed by OUR failures — product decision needed
Measured 2026-08-17: the test account hit
`trial_limit_reached (counter=generation, limit=3, scope=lifetime)` after
three builds, **all three of which failed for build-environment reasons we
caused** (unvalidated model, retry amplification, no pytest in the image).

Server-side metering is working correctly; the question is what it should
count. As it stands a customer whose builds fail because of a factory defect
exhausts their free trial and receives nothing — the worst possible first
impression, and invisible to us because the counter looks healthy.

Recommendation: decrement on a build that reaches `RUN_SUCCEEDED`, not on
`generate` being called. A gate-failed or stalled build should be free.
Deliberately NOT changed here: it is a commercial policy decision, not a bug.

### 1k. The chat config also drives code generation
`get_llm_config()` (the "chat" config, `CEREBRUM_CHAT_LLM_MODEL`, currently
`moonshot-v1-8k`) is consumed by `chain_generator.py`, `rule_parser.py`,
`packager.py` and `platform_packager.py` — not only conversation.
`moonshot-v1-8k` is the model this campaign measured as unable to write
working code (routes returning `None`, no convergence in three rework
rounds). For conversation it is a fine, cheap choice; for chain and rule
generation it is the same trap the factory coder was in before cutover.
Not changed mid-verification: it affects cost and conversational behaviour
and deserves its own measured comparison.

### 1l. First build after any deploy pays for a Store clone
The service has no persistent disk, so `resolve_blocks_root()` re-clones
Cerebrum-Blocks after every deploy. Measured: a `product/plan` call that
triggered the clone exceeded a 10-minute client timeout, while the same call
took 0.8s once the checkout existed. Not a defect, but it means the first
build after a deploy is slow and a client with a short timeout will appear to
hang. A persistent disk or a warm-up call at boot would remove it.

### 1c. Agent output quality is model-bound — MEASURED, and the first reading was wrong
`kimi-k2.7-code` builds the smoke blueprint end to end: **SUCCESS, rework 0,
7 of 10 artifacts agent-written, no coder failures**, passing the strict route
test first time. The agent path works.

`moonshot-v1-8k` does not — it writes a route returning `None` and cannot fix
it across three rework rounds, ending `FAILED_BUDGET_SPENT`. The runner
behaved correctly in refusing to ship it.

**Scope that result honestly: it holds for a 2-capability blueprint.** A
realistic 5-capability build does not converge even on `kimi-k2.7-code` — see
1d. "The agent path works" is true of the smoke case and not yet of a client-
grade one.

**The original conclusion here — "K2-class models are required but unavailable"
— was wrong, and the cause is worth remembering.** `coder.py` hardcoded
`temperature: 0.2`; every kimi-k2.x/k3 model answers `400 invalid temperature:
only 1 is allowed for this model`. The coder caught the HTTPStatusError,
recorded a CoderError and shipped templates, so builds reported SUCCESS with
`agent_written: 0` and the symptom read as a weak or unavailable model. The
request never reached the model. Fixed: the temperature is now sent only when
`LLM_TEMPERATURE` is configured, matching what `llm_config._llm_temperature()`
already documented. Regression-tested in `tests/test_coder_temperature.py`.

Also fixed: the factory fallback default was `kimi-k2.5-code`, which returns
`404 Not found the model` — the retry leg had never been real.

### 1d. The agent path did not converge on a realistic blueprint — FIXED in layers
First fixed for route/spec drift (constraints in the spec, `_sample_payload`
honouring them, the route prompt enforcing nothing the spec does not declare).
Live builds against the real Store then exposed the rest of the convergence
stack, each fixed with a new-shape test:

- findings must NAME what failed (dispatch error envelopes carry block and
  action; route/smoke tests collect every failing capability, not the first);
- a rework round is a **ratchet** (only implicated capabilities regenerate;
  green specs/handlers/routes are reused, so fixing red cannot regress green);
- a rework is an **edit** (the coder sees its own previous body — verbatim
  regeneration was measured converging to the same wrong code five rounds
  running);
- a statically rejected body earns one repair retry with the gate's reason;
- the block contract carries what blocks enforce at runtime (block.json
  actions, `input_schema` required fields, and the requirement strings blocks
  answer in their own error literals).

Measured end state: `field_ops.yaml` on `kimi-k2.7-code-highspeed` reaches
RUN_SUCCEEDED with rework 2, all five handlers coder-written, zero coder
failures.

### 1f. Clones are recorded unpinned — FIXED
Every `CLONE` event used to carry `source_commit: "unpinned"`, which made
staleness unanswerable and unrecoverably so: once a platform ships, the commit
a block came from cannot be reconstructed, because the vendored files look
identical whether they are current or a year behind.

`run_cloner` now pins each block. A real Store checkout records
`git rev-parse HEAD`; the vendor mirror is not a git repository, so it records
a content digest that moves when the block does. A checkout that is not a git
repo falls back to content rather than recording `"unknown"`, which would be a
revision that means nothing. Both land in `blocks.lock.json` and in the
ledger. Covered by `tests/factory/test_clone_pinning.py`.

### 1g. Real Store blocks are NOT standalone — FIXED, and proven live
**Was:** 83 of 106 blocks in the real Cerebrum-Blocks registry are shims that
import `app.*` and only run inside the Blocks platform. The first build against
the real checkout failed its own CLONER gate on all six blocks with
`ModuleNotFoundError: No module named 'app'`, and every earlier
offline-standalone result had been measured against mirror stubs.

**Fix (the "vendor the runtime slice" option):** the CLONER vendors the
transitive `app.blocks`/`app.core` slice under `vendor/cerebrum/` with imports
mechanically rewritten (the platform's own package is already named `app`), a
generated registry listing only vendored blocks, and the slice pinned in
`blocks.lock.json`. Module-level imports of unvendorable Store packages fail
the clone loudly; function-local ones are recorded in the lockfile. Covered by
`tests/factory/test_cloner_runtime_slice.py`.

**Proven, not asserted:** `field_ops.yaml` (5 capabilities, 6 real blocks)
builds RUN_SUCCEEDED against the real Store, boots in a clean venv, and
answers every capability route over HTTP with records persisted — with the
generated suite *blocking outbound network* and scanning nested results, so
the green cannot be a wrapper around failed block calls. Two Store-side
defects found by these builds were fixed upstream in Cerebrum-Blocks
(#66 workflow failed-step crash, #67 MCP channel standalone), which is the
Store-Manager harvest loop working end to end.

**Still true and registered:** blocks whose features need external services
(notification email/webhook/slack, anything with lazy `app.dependencies`-class
imports) carry those limits into the platform; they are listed per-clone in
`blocks.lock.json` under `lazy_foreign_imports`, and delivery-style features
need operator config in production. The offline guarantee covers what the
suite enforces: local execution, no store, no network.

### 1e. A phase killed mid-write leaves a torn workspace — FIXED
The runner records verdicts per *phase*. A phase killed part-way through
leaves a state no verdict describes, and the agent picks different entity
names on each call, so two partial passes do not compose.

Observed on the `field_ops` run: the process was killed during a WRITER pass.
`models.py`/`store.py` came from that pass (table `defect`) while
`routes.py`/`main.py` survived from an earlier one (`store.save("field_defect")`).
The platform booted and then answered
`sqlite3.OperationalError: no such table: field_defect`.

Worse for resume: `completed_roles()` reads WRITER's last terminal event as
`GATE_PASSED` from the previous round, so `resume_point()` returns TESTER and
would test a half-written `app/`. `test_resume_picks_up_where_the_kill_happened`
only ever kills *between* phases, so it does not cover this.

**Fixed.** The WRITER now writes to a staging directory and its output is
copied into the destination only after the pass returns successfully, so a
hard kill leaves the previous complete attempt rather than a splice.
Exception-rollback would not have been enough — a kill runs no Python, so the
protection has to be that the destination was never touched.
`completed_roles()` now treats a role whose last event is `PHASE_STARTED` as
running rather than complete, so a stale `GATE_PASSED` from an earlier attempt
can no longer mask an interrupted pass, and `interrupted_role()` names it.
Covered by `tests/factory/test_interrupted_phase.py`, including an assertion
that every table `routes.py` references is one `store.py` actually creates.

### 2. CEREBRUM_LLM_API_KEY missing on the Render backend — BLOCKS PRODUCTION
**Owner: Owner. Dashboard secret. Not fixable in code — do not work around it.**

The Render service `cerebrumdev-backend` (`srv-d9ta2pad0e5s738lllpg`) has no
LLM key set. Factory drafting there falls back to keyword mode and the coder
cannot run at all, so a production factory run cannot produce any
agent-written capability — every artifact would silently take the template
path. Local development is unaffected (`backend/.env` carries a key).

Note `/ready`'s `llm_configured` field is true only when a real LLM
credential is set (`KIMI_API_KEY`, `CEREBRUM_LLM_API_KEY`, chat/factory
variants, or `ANTHROPIC_API_KEY`). `LLM_PROVIDER` alone is not evidence —
that was a lie this register used to warn about. Verify a live factory run
with the `coder_failures` field of a build, or `POST /v1/sessions/{id}/product/draft`
and check `drafting_mode`.

### 3. Steward blocks dual-register against the factory's own mirror
`estate_registry` and `portfolio_rollup` do not exist in the real
Cerebrum-Blocks repo (109 blocks, neither present).
`dual_registry.load_blocks_registry` merges `app/factory/vendor_blocks_mirror`
into the registry unconditionally, so those two satisfy the dual-registration
gate against a copy the factory ships to itself. Pointing `blocks_root` at a
real Store checkout changes nothing. Pinned by
`test_steward_blocks_come_from_the_mirror_not_the_store`, which is designed to
go red when they land upstream — that is the cue to drop them from the mirror.

### 4. Generation is not byte-reproducible when the coder runs — RESOLVED for resume
Same blueprint, two builds, different tree hash — because an LLM does not emit
the same bytes twice. This is irreducible, not a bug to fix.

It no longer blocks resume. `ProductGenerator`'s `inputs_hash` is misnamed: it
is `hash_tree` of the *generated output* (`generator.py`), so it moves with any
LLM-written file and could never have been a resume key anyway — the value is
unknown until the build it is meant to authorise has already run. The runner
uses `runner.blueprint_hash()` instead: canonical sorted-key JSON of the
blueprint, hashing the build's actual inputs. Stable by construction, proven
by `test_blueprint_hash_is_the_resume_key_and_is_stable`.

Standing constraint: never add a provenance or resume check that hashes the
generated tree. The scaffold's own reproducibility is asserted separately (see
"Running the tests" in README.md).
