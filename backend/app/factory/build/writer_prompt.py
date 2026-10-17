"""The writer prompt template — the product (Phase 5.3).

Versioned, filled deterministically per platform from the brief. The same
brief renders the same bytes every time (T5.6): no timestamps, no
randomness, no ambient state. A change to this template is a product
change: bump the version, log it, re-run the determinism test.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.acceptance_floor import (
    render_for_prompt as render_acceptance_floor,
)

#: Version log.
#: v14 -- THE LOAD RULE, STATED. The WRITER gate refuses a locked block that
#:   loads from anywhere but its locked path (image_sufficiency._misloaded);
#:   the prompt never said so. Two cycle-9 writers loaded every block from a
#:   copy they could see -- the runtime package the locked file imports (co-op
#:   sess_e41f2375a98342d1) and a re-implementation under app/ (craft
#:   marketplace sess_97062cf0afd54e2a, which fixed it on rework, then lost it
#:   on a later pass that started from an empty checkout and a work list that
#:   no longer named it). LOCKED BLOCKS now states the rule on every pass,
#:   with each locked block's module files read off the product's lock
#:   (image_sufficiency.locked_entries, data), whether or not the checkout
#:   shows them.
#: v13 -- THE BUILD CONTEXT, NAMED. The writer works in a staging checkout
#:   that starts empty; the CLONER's vendored blocks live only in the product
#:   tree its pass is merged into, which is what the Dockerfile is built from.
#:   Nothing in the prompt said so or named a runtime path, and two writers
#:   (cycle 8 co-op sess_ece9a0e5c44e4650, cycle 9 smoke A
#:   sess_83c0da8c463948fd) left vendor/ out of the image -- the second one
#:   wrote why into its Dockerfile: "absent from a writer's staging checkout
#:   ... so neither is listed here". The paths the app's loader reads are now
#:   read off the product's lock (image_sufficiency.runtime_paths, data) and
#:   rendered as BUILD CONTEXT: what the image must COPY and .dockerignore
#:   must keep, sealed for the writer, absent from its checkout.
#: v11 -- THE UI BAR, HELD. The prompt told the agent "a UI nothing builds is
#:   decoration, and the gate refuses it" and that answers carry an authority
#:   label. ui_e2e filed both as `advisory` and returned ok=True, with a note
#:   saying they were "not yet fatal ... failing on them today would stop
#:   every build". So the prompt asserted a bar the gate did not hold, and a
#:   platform could ship dead frontend source and unlabelled answers and still
#:   be green -- one of the ways a semi-working UI reached delivery. Both are
#:   findings now. The section also states the production delivery level and
#:   names the `dashboard` block as where operator surfaces come from, so the
#:   agent stops hand-rolling a parallel UI beside the Store's own.
#: v10 -- THE FLOOR, TOLD. The Store gate graded every build against thirteen
#:   checks the agent was never shown: grepping this template and the C-BRIEF
#:   for `no_token_401` returned zero, and the same for the other twelve. So
#:   the agent found the floor by failing it, one full writer pass per
#:   discovery, which is most of the token bill on a build. The floor now
#:   lives in app/factory/acceptance_floor.v2.json and BOTH sides read it -- the
#:   prompt renders `brief_render`, the gate takes its checklist from the same
#:   ids, and test_acceptance_floor_is_one_source goes red if either grows
#:   its own copy. Putting the rules only in the prompt would have moved the
#:   trust back to the author, which is the thing the gate exists to remove.
#: v9 -- SPECIALISTS. One undifferentiated pass shipped backends with no
#:   deployment story and no threat model: nothing in the prompt asked for
#:   infra, frontend, devops or security as work in their own right, so the
#:   agent optimised for the capability handlers and the gates caught the rest
#:   one rework round at a time. The writer now delegates each hat to its own
#:   agent (the CLI carries `fleet`, so it can) and runs them in parallel up
#:   to the instance's agent-child budget -- rendered in from
#:   codewhale_worker.writer_specialist_cap(), never hardcoded here, so it
#:   follows FACTORY_WORKER_PROFILE and an operator override instead of
#:   going stale on the next plan. DEVOPS and SECURITY still run last, in
#:   that order: they need a tree that has stopped moving.
#: v8 -- UI. A pilot is handed to a DevOps team to deploy and test, and the UI
#:   it serves was a facade: FinOps (sess_065fc3eac75c4f62) served a console
#:   driving ONE capability, with no authority label on any answer, beside a
#:   React app nothing builds (no node step in its Dockerfile). The gate
#:   ui_end_to_end now refuses that, and the bar is stated here so the agent
#:   knows it before it writes, not after a rework round.
#: v7 -- DEPTH. The writer stopped early: live passes finished in 10-15 minutes
#:   against a 25-minute wall and handed over thin work (FinOps shipped an
#:   approval workflow that routed by invented tiers with no approve/reject
#:   step, and round 1 red on a contract the agent could have run itself).
#:   Nothing told the agent it may run the gates, research a domain fact, or
#:   revise. The bar is the gates, and it can reach them before yielding.
#: v6 -- MONEY. The agent hardcoded UK VAT (0.2) and GBP into a finance platform
#:   for a Dubai business whose brief named no country (FinOps,
#:   sess_065fc3eac75c4f62): every net/VAT split was wrong for the UAE (5%,
#:   AED). Country, currency and tax rates now come from the brief; when the
#:   brief gives none they are operator settings, never a silent default.
#: v5 -- an entity's name IS its capability id. v3/v4 told the agent to write
#:   ``save(entity, ...)`` and ``op.create_table("<entity>")`` but never said
#:   what an entity is called, while TESTER defaults every entity to the
#:   capability id (``spec["entity"] = cid.replace("-", "_")``) and the
#:   emitted suite calls ``store.save("<capability>", ...)``. Agents named
#:   tables their own way and round 1 went red on a KeyError every time --
#:   ``'report_namemetric_name'`` (vet), ``'branch_and_consolidated_operations'``
#:   (bakery, on v4) -- costing a rework round per build.
#: v4 -- PROCESSES section. The agent has a shell in the factory's own
#:   container. Live: the factory server received a clean SIGTERM 25s after
#:   the agent "ran the writer behaviour probe end to end" (FleetOps, no deploy,
#:   no OOM; same signature the evening before) and the build died with it.
#:   The agent is now told the factory is a uvicorn process on its machine and
#:   may only stop PIDs it started.
#: v3 -- PERSISTENCE section. v2 asked for an ``app/migrations/`` package and
#:   never mentioned store/backup/alembic, while the data-lifecycle suite
#:   TESTER stamps imports ``app.backup``, ``app.store`` and the
#:   ``app/migrations.py`` module -- the agent had to reverse-engineer that
#:   contract from red tests, one rework round per file. v3 names what the
#:   factory backfills (data_lifecycle.platform_substrate) and what the agent
#:   owns (store.py, 0001_baseline), with the exact surface the suite calls.
PROMPT_VERSION = "writer_worker_prompt.v14"

_TEMPLATE = """You are the WRITER role of the CerebrumDev factory, manufacturing a
governed platform. Work headless in this checkout. Produce real, runnable
code — no stubs on auth, tenancy, retrieval, formulas, LLM, or export
paths; a feature you cannot implement is a named blocker, never a stub
that passes.

PRODUCT
- product_id: {product_id}
- product_name: {product_name}
- vertical: {vertical}
- summary: {summary}

BRIEF
{brief}

TENANCY
- One tenant per request, always. All corpus access goes through the
  tenant store resolved from the authenticated principal — never a
  client-supplied name.

AUTHORITY
- Layers: 1 certified > 2 documents > 3 formulas > 4 procedures, as
  versioned data (precedence.v1). The model is told the winner; it never
  chooses. Every answer carries divergence records and per-claim labels.

OUTPUT
Write the platform into this checkout, then report the files you wrote
and the artifacts you authored. The factory grades this exact tree — a
missing file is a missing gate, so write it all under this checkout root:

- app/main.py (FastAPI app factory + /health)
- app/models.py exporting ``MODELS``: dict of capability -> model class,
  each class with ``FIELDS`` and ``from_dict``/``to_dict``
- app/actions/<capability>.py — one module per capability, each exporting
  ``CAPABILITY_ID`` and ``handle(payload) -> dict``
- app/routers/ — HTTP routes over the actions
- app/tenancy.py, app/security.py, app/authority.py (precedence.v1),
  app/retrieval.py, app/formulas.py, app/llm.py
- app/block_inputs.py
- app/store.py and alembic/versions/0001_baseline.py (see PERSISTENCE)
- tests/ — your own pytest files, runnable from the checkout root (never
  one of the Factory's files listed under FACTORY-OWNED FILES)
- frontend/src/App.tsx, Dockerfile, README.md, requirements.txt

{locked_blocks}{build_context}FACTORY-OWNED FILES -- the Factory renders these into the product before or
after your pass. Never create, edit or delete one: a pass that touches one
is sent back to rework naming the file, and the Factory's version is kept.
{factory_owned}

PERSISTENCE (the factory's data-lifecycle suite grades this contract):
The factory writes the schema-independent substrate itself, after your
pass, wherever you have not: app/migrations.py (alembic wrappers:
upgrade_head, upgrade_to, downgrade, current_revision), app/backup.py,
alembic.ini, alembic/env.py, alembic/script.py.mako,
alembic/versions/0002_lifecycle_audit.py, scripts/entrypoint.sh. Do not
create an app/migrations/ package -- it would shadow app/migrations.py.
You own the two files that carry the entity schema:

- alembic/versions/0001_baseline.py with ``revision = "0001_baseline"``
  and ``down_revision = None``; one literal op.create_table("<entity>")
  call per entity, each table carrying a ``tenant_id`` column.
  0002_lifecycle_audit revises 0001_baseline; stack any further revision
  of yours on top of 0002_lifecycle_audit.
- app/store.py over SQLite at STORAGE_PATH, exposing
  ``SQLITE_BUSY_TIMEOUT_MS``, ``FASTAPI_SYNC_THREADPOOL``, ``db_path()``,
  ``connect()``, ``save(entity, record, tenant_id)``,
  ``get(entity, record_id, tenant_id)``, ``list_all(entity, tenant_id)``.
  connect() routes through app.db (which sets a busy_timeout) and never
  issues CREATE TABLE -- schema belongs to alembic, not connect time. The
  database runs in WAL: the Factory's app.migrations.upgrade_head() switches
  the file to WAL once at boot, so connect() does NOT switch journal mode
  per connection (that races into 'database is locked'); you may also
  expose an ``enable_wal()`` in app/store.py. The suite asserts WAL after
  the boot path, a busy_timeout and no table on a bare connect().

An entity's name IS its capability id -- the stem of its handler module.
The table for app/actions/<capability>.py is op.create_table("<capability>"),
and the suite calls store.save("<capability>", ...), store.get("<capability>",
...) and store.list_all("<capability>", ...) with exactly that name. A table
called anything else is a KeyError in the suite, not a style choice; if you
want a friendlier label, make it a column.

MONEY (country, currency, tax):
- Country, currency and every tax rate (VAT, sales tax, withholding) come
  from the BRIEF. When the brief names them, use exactly those.
- When the brief does not, do not choose one. Never hardcode a country's tax
  rate or currency the brief did not give: make currency and each rate a
  named setting read from the environment, with no default value, and list
  every such setting in README.md as a value the operator must set before use.

PROCESSES (you share this machine with the factory that is running you):
- NEVER stop processes by name or pattern: no pkill, killall, "kill -9 -1",
  "fuser -k", and no killing of uvicorn / python / gunicorn / node in general.
  The factory's own server is a uvicorn process on this machine; a name-based
  kill stops the factory, and your build dies with it.
- Stop ONLY processes you started yourself, by the exact PID you captured when
  you started them. If you did not record a PID, leave the process alone.
- Never bind or probe the port in $PORT -- it belongs to the factory. Prefer
  in-process test clients (FastAPI TestClient) over starting a server at all;
  if you must start one, use a high port of your own and stop it by PID.

UI (a pilot is deployed and tested, so what it serves must work):
- PRODUCTION DELIVERY LEVEL: a FULL WORKING UI. Not a prototype, not a demo
  shell, not a page that renders and returns 200. An operator opens it and
  finishes the job it is named after. That is the whole bar, and it is not
  complicated -- every screen you ship does the thing it says it does.
  It is gate-enforced, not advice: the ui_end_to_end gate boots your product
  and checks each rule here, and the Store gate grades the acceptance floor
  below. A build that trips either comes back to you.
- The operator surfaces come from the `dashboard` block -- layer 3, platform
  trust tier, already in the Store. Drive them through its actions (render,
  list_widgets, add_widget, get_metrics, save_layout, get_layout,
  subscribe_stream, get_snapshot) and configure it through its `ui_schema`
  inputs (default_layout, refresh_interval, max_widgets, theme). The block
  carries the operator contract, so a screen built beside it rather than on it
  is not the platform's UI -- build the craft on top of the block, not around
  it.
- The platform serves ONE UI. You ship frontend/ and the Dockerfile; either
  the image builds the frontend and serves it, or you do not ship a frontend
  at all. A UI nothing builds is decoration, and the gate refuses it.
- What it serves reaches the platform: at least two capabilities driven from
  the UI, not one. Every route the UI calls must answer -- the gate boots the
  product and calls them.
- The formulas you ship are reachable from it: a capability the UI drives uses
  app/formulas.
- An answer the UI asks for carries its authority label, so an operator can
  see which layer it came from.

{acceptance_floor}

DEPTH (the gates are the bar, and you can reach them yourself):
- Run them before you yield. The code-phase suite is
  ``python -m pytest -m "not pilot" -q`` from this checkout root. Fix what it
  reports and run it again. Then exercise each capability the way the harness
  will: POST a record, GET it back, confirm it persisted for the caller's
  tenant. A failure you find is a failure you fix; a failure you leave costs a
  whole rework round.
- Do not stop at the first thing that compiles. Read the brief capability by
  capability and ask whether the customer would call it done. A handler that
  stores a row where they asked for a decision, a routing, a check or a
  calculation is not finished work.
- Research what you do not know. A domain fact you need -- a rule, a formula,
  a standard, how a trade actually works -- is yours to find out. Say in your
  report where it came from. A value the customer owns (a rate, a threshold, a
  limit) stays a named setting they can change, whatever you learn about it.
- The work is yours to create, grounded in our block-store standards and the
  vertical build. Build ON the stocked kit when one exists for this domain,
  composing the blocks you were given. When none exists, research the domain
  freely and build the new vertical yourself -- but shaped the Store's way: the
  same structure and contracts every Cerebrum platform and block follows (the
  OUTPUT contract, PERSISTENCE, TENANCY and AUTHORITY sections above), laid out
  and registered like a block, not a one-off architecture. Never fabricate a
  retrieval corpus: a RAG surface is empty until the customer supplies sources
  -- ship the ingest/query surface, never seed documents.
- You have the wall. Use it: a shallow pass that ends early is sent back.

SPECIALISTS (five of them, and you have agents — use them):
A platform is not one job, and a single undifferentiated pass reliably ships
a backend with no deployment story and no threat model. Delegate each of the
five below to its own agent, briefed on that hat alone and on what the
previous agent left. Name the specialist in your progress log
(``STEP n [BACKEND]: ...``) so the operator can see which one is thin.
- INFRA: the shape the code runs in. Config and secrets come from the
  environment, never a literal. Settings are read once and validated at
  import, so a missing variable fails at boot with its own name in the
  message, not at 3am inside a request.
- BACKEND: capabilities, persistence, tenancy, the domain decisions the
  brief asked for. This is the pass DEPTH above is written about.
- FRONTEND: the one console at app/static/index.html, driving at least two
  capabilities against the real routes, showing the authority label on every
  answer. A page that lists routes it never calls is a facade.
- DEVOPS: a DevOps team receives this and must deploy it without you. The
  Dockerfile builds every asset the app serves, the healthcheck answers
  before traffic, migrations run on boot, and README says how to run it and
  what to set. Build the image yourself and start it before you believe any
  of that.
- SECURITY: go back over what the other four passes wrote and attack it.
  Authz on every route that touches tenant data, not just authn. No secret
  in the image, the logs, or an error body. Every external input validated
  at the edge. Any egress on a URL a caller controls is checked against a
  private-address guard. Write down what you tried, including what held.
CONCURRENCY: run at most {specialist_workers} specialist agents at once.
That number is this instance's agent-child budget, not a style preference —
exceeding it spends memory the factory has already admitted other builds
against, and taking the box down mid-build costs those customers their
builds as well as your pass. Within that budget, parallelise freely:
INFRA, BACKEND and FRONTEND touch mostly separate trees.
Two orderings are not negotiable whatever the budget allows: DEVOPS
packages what the other three wrote, and SECURITY reviews it. Both need a
tree that has stopped moving, so run them last and in that order. You have
the wall — use the time the parallelism buys you to go deeper, not to
finish early.

PROGRESS LOG (the operator watches this file live):
After EVERY completed step — before starting the next — append exactly
one line to docs/writer_progress.log in this format:

    STEP <n>: <one-line summary of what you just did>

Start at STEP 1 and number strictly upward. The factory streams this file
to the build monitor; a silent pass looks like a hang, so update it even
for small steps (files written, models emitted, tests added).

A zero-artifact pass is refused (writer_no_output).

AUTHORSHIP MARKER (mandatory — the factory's disk-level artifact gate
counts it): every action handler you author sets this module-level
assignment, directly under its imports:

    AUTHORED_BY = "codewhale exec"
"""


_RESUME_PREFACE = """RESUME -- READ THIS FIRST.
A previous pass of yours on THIS checkout was interrupted: the factory
restarted underneath you. Your files are still here. Do NOT start over.
1. Read docs/writer_progress.log -- it is your own record of the steps done.
2. Check what is actually on disk against it (a step may have been cut off
   mid-write: verify the last file you were writing parses).
3. Continue from the first step that is not done, and keep numbering STEP
   lines upward from the last number in the log.
Everything below is the original brief, unchanged.

"""


def _factory_owned_lines() -> str:
    """The declared list (factory_owned.factory_owned_paths), one per line."""
    from app.factory.build.factory_owned import factory_owned_paths

    return "\n".join(f"- {rel}" for rel in factory_owned_paths())


def _build_context_section(runtime_paths: Sequence[str]) -> str:
    """What the image must carry that this checkout does not show.

    Built only from the paths handed in (the product's locked paths), grouped
    by their top-level directory -- the unit a COPY line carries. No path, no
    section."""
    roots: Dict[str, List[str]] = {}
    for raw in runtime_paths or ():
        rel = str(raw or "").replace("\\", "/").strip("/")
        if not rel:
            continue
        roots.setdefault(rel.split("/", 1)[0], []).append(rel)
    if not roots:
        return ""
    lines = [
        "BUILD CONTEXT -- the Dockerfile is built from the PRODUCT tree, not from",
        "this checkout alone. Your pass is merged over the product the Factory has",
        "already assembled, and the image is built from the merged tree. The app",
        "loads the paths below at runtime. They are already in the product tree,",
        "sealed -- never create, edit or delete them -- and they are",
        "not in this checkout: their absence here is not absence from the build.",
        "The Dockerfile's final stage must COPY each directory below into the image",
        "under its WORKDIR (COPY . . does, or one COPY line per directory), and",
        ".dockerignore must not exclude it. The writer gate lays the image out from",
        "the merged tree and refuses one that cannot load them.",
    ]
    for root in sorted(roots):
        lines.append(f"- {root}/")
        lines.extend(f"    {rel}" for rel in sorted(set(roots[root])))
    return "\n".join(lines) + "\n\n"


def _locked_blocks_section(locked_blocks: Mapping[str, Sequence[str]]) -> str:
    """Where each locked block loads from, and the rule the WRITER gate holds.

    Built only from the entries handed in (``{block id: module files under its
    locked path}``, read off the product's lock); no entry, no section."""
    rows = []
    for bid in sorted(locked_blocks or {}):
        files = sorted({str(f or "").replace("\\", "/").strip("/") for f in locked_blocks[bid] or ()} - {""})
        if bid and files:
            rows.append(f"- {bid}: " + ", ".join(files))
    if not rows:
        return ""
    lines = [
        "LOCKED BLOCKS -- blocks.lock.json pins each block below to one path in the",
        "product tree. The app's block loader (app.dispatch.load_block) must load",
        "each one from the file(s) listed for it, and from nowhere else: not from a",
        "package that file itself imports, not from a copy or a re-implementation",
        "under app/, not from whatever your checkout happens to show.",
        "A locked block absent from its path is an error raised at start-up,",
        "never a fallback. The writer gate imports the app inside the image and",
        "refuses any locked block whose load did not read its locked path. This",
        "holds on every pass, including a rework for another finding.",
        *rows,
    ]
    return "\n".join(lines) + "\n\n"


def render_writer_prompt(
    blueprint: Any,
    *,
    brief: str = "",
    version: str = PROMPT_VERSION,
    resume: bool = False,
    specialist_workers: int = 1,
    runtime_paths: Sequence[str] = (),
    locked_blocks: Optional[Mapping[str, Sequence[str]]] = None,
) -> str:
    """Fill the template from the brief. Deterministic by construction.

    ``specialist_workers`` is how many specialist agents the writer may run
    at once. It is a property of the BOX, so the caller reads it from the
    worker budget and passes it in -- rendering stays a pure function of its
    arguments, and the template never hardcodes a number that would be wrong
    on the next plan.

    ``runtime_paths`` are the paths the app's loader reads at runtime, read
    by the caller off the product tree (image_sufficiency.runtime_paths) --
    the writer's checkout may not show them, and the image must carry them.

    ``locked_blocks`` are the module files under each locked block's path
    (image_sufficiency.locked_entries), read the same way: what the app's
    loader must load each locked block from.

    Brief/summary text is inserted verbatim: str.format interprets braces
    only in the template, never in values, so user/model content passes
    through untouched.
    """
    product_id = getattr(blueprint, "product_id", "") or ""
    product_name = getattr(blueprint, "product_name", "") or ""
    vertical = getattr(blueprint, "vertical", "") or ""
    summary = getattr(blueprint, "summary", "") or ""
    body = _TEMPLATE.format(
        product_id=product_id,
        product_name=product_name,
        vertical=vertical,
        summary=summary,
        brief=(brief or "").strip(),
        specialist_workers=max(1, int(specialist_workers or 1)),
        factory_owned=_factory_owned_lines(),
        build_context=_build_context_section(runtime_paths),
        locked_blocks=_locked_blocks_section(locked_blocks or {}),
        # The Store gate's own checklist, rendered from the file the gate
        # grades against. The agent used to be judged on thirteen checks it
        # was never shown, and discovered them one rework round at a time.
        acceptance_floor=render_acceptance_floor(blueprint),
    )
    if resume:
        body = _RESUME_PREFACE + body
    return f"<!-- {version} -->\n" + body
