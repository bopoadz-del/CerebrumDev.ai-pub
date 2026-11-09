# Executive Programs — build design note (item 4, spec prep)

Status: **draft spec**. The production run starts only after the FACTORY lane
reports a 7/7 pilot inside budget (ORDERS_08NOV_0525Z_FACTORY_exec_programs_production_run.md §3).
This note records inputs, contract, and the two-input test plan so the build can
start the minute the pilot lands. It does **not** hardwire any track content.

## 1. Inputs

### 1.1 Source document
- `exec-programs.html` — 68,624 bytes, Drive id `1ZXFeueB4RKmGKHlVfUxmMlWNpkCL5uAq`
  (owner upload 08 Nov 05:20Z). Replaces the 06 Nov 41,401-byte copy.
- Becomes the fixture `backend/tests/fixtures/executive_programs/exec-programs.html`.
- **Before the build**, re-pin and diff against the 41,401-byte copy: tracks,
  weeks, MODES, RUBRIC, SESSIONS, prompts, and any new tabs/features. The diff
  summary is quoted in `REPORT_FACTORY_<HHMM>Z_exec_programs_diff.md` (FACTORY lane).
- **NO HARDWIRING**: every figure/framework the app teaches is derived from the
  source at build time, never retyped into factory or emitter code.

### 1.2 Program engine (Store block)
- `program_engine` v1.0.0, registered in the Store registry (Store #44).
- Pure engine: all track content comes from the caller's `track.v1` document.
- Operations: `load_track`, `session_prompt`, `defense_prompt`, `jury_prompt`,
  `markdown`, `restore`.
- `track.v1` schema is **closed** (`extra="forbid"`), so unknown fields fail
  loudly with a dotted field path:
  - `Track`: `id`, `name`, `audience`, `length_weeks`, `hours_per_week`,
    `rubric`, `weights`, `terms[]`.
  - `Rubric`: `points_each=20`, `bands{A,B,C,rewrite_below}`, exactly 5 criteria.
  - `Weights`: `memos + defenses + capstone == 100`.
  - `Term`: `number`, `name`, `outcome`, `defense_minutes`, `weeks[]`.
  - `Week`: `number`, `theme`, `readings[]`, `case`, `deliverable`.

### 1.3 Domain packs
- `coo.v1`, `ceo.v1`, plus any new track the 68,624-byte source adds — re-derived
  from `exec-programs.html` (not authored by hand).

### 1.4 Build path (unchanged)
Floor session → upload `exec-programs.html` → brief drafted from the upload →
owner-approvable draft → 5-phase build → STORE gate → graded → export zip →
deployed product. If the build fails, fix the Factory mechanism, never the
product's files.

## 2. Contract (production-grade, ORDERS_08NOV_0525Z §2)

Each §2 line maps to the Factory mechanism that satisfies it and the grader
evidence that proves it. A §2 line that fails → Factory mechanism fix, then re-run.

| # | Requirement | Factory mechanism | Grader evidence |
|---|-------------|-------------------|-----------------|
| 2a | Multi-user sign-up/sign-in; per-learner progress, memos, grades stored server-side; coach/admin cohort view | session-scoped user store + auth routes; `program_engine.State` persisted per user; coach role queries the cohort | two distinct learners' progress is isolated server-side; coach sees both, learner sees only own |
| 2b | Program engine: tracks, 16-week calendars, sessions/modes/prompts, deliverables, 5-criterion rubric+bands, weighted grades, defense/jury, export/backup/restore — driven by `program_engine` + kernel, not re-implemented | route all engine math through `program_engine` block operations; no duplicate calendar/rubric logic in product | engine ops exercised end-to-end; a rubric band is produced by `rubric.band`, not by a hand-rolled branch |
| 2c | Learner-facing UI (program bar, tabs, session view, memo editor, grading view, progress), mobile width, light+dark, accessible basics | `ui_modules` render the learner surface; the brief compiles the UI, not a JSON operator console | Playwright renders learner views at mobile width in both themes |
| 2d | Content honesty: every figure/framework carries its source; improvised content labelled | formula-label gate (#163) applies to every emitted claim | each taught figure has a source citation; improvised text is labelled |
| 2e | Security baseline: per-deploy random token (no literal), auth on every API route, input validation, no secrets in client | per-deploy token from env, never a literal; auth middleware on all routes | two deploys produce distinct non-literal tokens; unauthenticated route returns 401 |
| 2f | Ops: deploy target asked (not assumed); health/ready endpoints; grader verdict recorded | deploy target from the Floor session; `/health` + `/ready`; verdict persisted | deploy URL asked-and-answered; endpoints green; verdict quoted |

## 3. Two-input test plan

Every decision below is exercised by a `@pytest.mark.parametrize` test with
**≥ 2 distinct inputs** (the repo's two-input rule), plus a Playwright check where
a UI path is the decision.

### 3.1 Program engine — track validation
- Two distinct `track.v1` documents (`coo.v1`, `ceo.v1`) each load cleanly via
  `program_engine.load_track`.
- Two malformed inputs (weights not summing 100; `length_weeks` ≠ sum of weeks)
  each raise `TrackError` carrying the dotted field path.

### 3.2 Calendar
- Two distinct start dates → each yields a 16-week schedule with sessions mapped
  to the correct days/modes; schedules differ only where the dates differ.

### 3.3 Rubric + weights
- Two distinct score vectors → correct weighted band (`A` vs `B`) via
  `rubric.weighted_average` / `rubric.band`; `graduated` returns the same verdict
  for both `A`/`B`, and the rewrite boundary for the failing vector.

### 3.4 Multi-user state isolation
- Two learners on the same track → `program_engine.markdown` exports differ per
  learner; `restore(backup(state))` round-trips for both.

### 3.5 Domain pack derivation
- Two tracks (`coo.v1`, `ceo.v1`) → distinct `session_prompt` / `jury_prompt`
  outputs; the engine never fabricates a session that is not in the track.

### 3.6 Security token
- Two deploy runs → distinct, non-literal tokens; neither equals any known literal.

### 3.7 Playwright (UI)
- Two uploaded documents render two chips and the reply names both (Floor attach,
  already merged #179).
- Two tracks render two distinct learner program bars; memo editor persists per
  user; grading view shows the rubric band.

## 4. Out of scope for this note
- Pinning/diffing `exec-programs.html` and the actual run are item 3 (queue) and
  gated on the 7/7 pilot; this note only prepares the spec.
