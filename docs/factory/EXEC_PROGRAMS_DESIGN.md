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

### 1.2 Program engine (Store block) — the actual contract
`program_engine` v1.0.0 (registry `block_registry/program_engine/block.json`, Store #44).

- **Block envelope**: `input` = one JSON object, "Engine operation payload (operation +
  arguments)"; `layer 3`, `filesystem: true`, `network: false`; every op returns
  `{"status": "success"|"error", "result": …}` (errors carry the reason in `error`).
- **Operations** (`app/blocks/program_engine/block.py`):
  - `load_track(path)` → `Track.model_dump(mode="json")`
  - `session_prompt(track, week, session)` → prompt string
  - `defense_prompt(track, week)` → prompt string
  - `jury_prompt(track)` → prompt string
  - `markdown(track, state)` → export markdown
  - `restore(text)` → `State.model_dump()`
- **Engine mirrors the factory kernel** (byte-identical `schema/calendar/modes/rubric/prompts/export`).
  The product routes all program math through these ops — it never re-implements a
  calendar, rubric band, or prompt.

### 1.3 `track.v1` schema (closed, `extra="forbid"`)
`Track{schema_version="track.v1", id, name, audience, length_weeks≥4, hours_per_week,
rubric, weights, terms[]}`; `Rubric{points_each=20, bands{A,B,C,rewrite_below}, criteria
= exactly 5}`; `Weights{memos+defenses+capstone == 100}`; `Term{number, name, outcome,
defense_minutes=45, weeks[]}`; `Week{number, theme, readings[], case, deliverable}`;
`Reading{title, author, chapters?}`; `Case{title, decision_question, public_sources[]}`;
`Deliverable{title, brief, pages≥1}`. Validation errors raise `TrackError` carrying the
dotted field path.

### 1.4 Engine surface the learner app must call
- `calendar`: `Session{week, kind, offset, minutes, date, name}`, `sessions(start, length_weeks)`,
  `week_start`, `current_week`.
- `modes`: `SessionMode(str, Enum)`, `mode_for_kind(kind)`.
- `rubric`: `total(scores)`, `band(total_score, rubric)`, `weighted_average(…)`, `graduated(…)`.
- `export`: `State{track, start, data: dict[str, dict[int, WeekState]]}`; `WeekState{memo,
  scores[5], verdicts[5], defense, jury}`; `markdown`, `backup`, `restore` (raises `RestoreError`).
- `prompts`: `session_prompt(track, week_no, session)`, `defense_prompt(track, week_no)`,
  `jury_prompt(track)`.

### 1.5 Domain packs
`coo.v1`, `ceo.v1`, plus any new track the 68,624-byte source adds — re-derived from
`exec-programs.html` into `track.v1` documents (not authored by hand).

### 1.6 Build path (unchanged)
Floor session → upload `exec-programs.html` → brief drafted from the upload →
owner-approvable draft → 5-phase build → STORE gate → graded → export zip →
deployed product. If the build fails, fix the Factory mechanism, never the product's files.

## 2. Contract (production-grade, ORDERS_08NOV_0525Z §2)

| # | Requirement | Factory mechanism (derived from the actual build) | Grader evidence |
|---|-------------|----------------------------------------------------|-----------------|
| 2a | Multi-user sign-up/sign-in; per-learner progress, memos, grades server-side; coach/admin cohort view | per-user `program_engine.State` (`data` keyed by learner) persisted server-side; auth routes; coach role reads the cohort | two learners' `State` isolated server-side; coach sees both, learner sees own |
| 2b | Program engine: tracks, 16-week calendars, sessions/modes/prompts, deliverables, 5-criterion rubric+bands, weighted grades, defense/jury, export/backup/restore | route ALL math through `program_engine` ops (`load_track`, `session_prompt`, `defense_prompt`, `jury_prompt`, `markdown`, `restore`) and the `calendar`/`modes`/`rubric`/`prompts` helpers | a band comes from `rubric.band`, a session from `calendar.sessions` — never a hand-rolled branch |
| 2c | Learner-facing UI (program bar, tabs, session view, memo editor, grading view, progress), mobile, light+dark, accessible | `ui_modules` render the learner surface; the brief compiles the UI, not a JSON operator console | Playwright renders learner views at mobile width in both themes |
| 2d | Content honesty: every figure/framework carries its source; improvised content labelled | formula-label gate (#163) applies to every emitted claim | each taught figure has a source citation; improvised text is labelled |
| 2e | Security baseline: per-deploy random token (no literal), auth on every API route, input validation, no secrets in client | per-deploy token from env, never a literal; auth middleware on all routes | two deploys produce distinct non-literal tokens; unauthenticated route returns 401 |
| 2f | Ops: deploy target asked (not assumed); health/ready endpoints; grader verdict recorded | deploy target from the Floor session; `/health` + `/ready`; verdict persisted | deploy URL asked-and-answered; endpoints green; verdict quoted |

## 3. Two-input test plan (derived from the actual engine surface)

### 3.1 Track validation (`load_track`)
- Two distinct `track.v1` documents (`coo.v1`, `ceo.v1`) each load cleanly.
- Two malformed inputs (weights ≠ 100; `length_weeks` ≠ sum of week counts) each raise
  `TrackError` with the dotted field path.

### 3.2 Calendar (`calendar.sessions`)
- Two distinct start dates → 16-week schedules whose `Session{week, date, kind}` differ
  only where the dates differ; `current_week` returns the right week for each.

### 3.3 Rubric + weights (`rubric.band` / `weighted_average` / `graduated`)
- Two distinct score vectors → correct band (`A` vs `B`) and weighted average; `graduated`
  returns the same verdict for `A`/`B` and the rewrite boundary for the failing vector.

### 3.4 Multi-user state isolation (`export.State` / `backup` / `restore`)
- Two learners on one track → `markdown(track, state)` differs per learner;
  `restore(backup(state))` round-trips for both; a torn backup raises `RestoreError`.

### 3.5 Prompts (`session_prompt` / `jury_prompt`)
- Two tracks (`coo.v1`, `ceo.v1`) → distinct `session_prompt`/`jury_prompt` outputs; the
  engine never fabricates a session absent from the track.

### 3.6 Security token
- Two deploy runs → distinct, non-literal tokens; neither equals any known literal.

### 3.7 Playwright (UI)
- Two uploaded documents render two chips and the reply names both (Floor attach, #179).
- Two tracks render two distinct learner program bars; memo editor persists per user;
  grading view shows the rubric band.

## 4. Out of scope for this note
- Pinning/diffing `exec-programs.html` and the actual run are item 3 (queue), gated on the
  7/7 pilot; this note only prepares the spec.
