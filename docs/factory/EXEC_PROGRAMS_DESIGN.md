# Executive Programs — build design note (item 4, spec prep)

Status: **draft spec**. Reconciles to `SPEC_EXEC_PROGRAMS_PRODUCT_v1.md` (owner, 4 Nov). The
production run starts only after the FACTORY lane reports a 7/7 pilot inside budget
(ORDERS_08NOV_0525Z_FACTORY_exec_programs_production_run.md §3). No build until then.

## 1. Inputs

### 1.1 Source document → track YAMLs
- `exec-programs.html` — 68,624 bytes, Drive id `1ZXFeueB4RKmGKHlVfUxmMlWNpkCL5uAq`
  (owner upload 08 Nov 05:20Z). Replaces the 06 Nov 41,401-byte copy.
- Becomes the fixture `backend/tests/fixtures/executive_programs/exec-programs.html`.
- **Before the build**, re-pin and diff against the 41,401-byte copy (tracks, weeks, MODES,
  RUBRIC, SESSIONS, prompts, tabs). Diff summary quoted in `REPORT_FACTORY_<HHMM>Z_exec_programs_diff.md`.
- **SOURCE RULE (spec §Data model + acceptance 10)**: a track is one YAML validated against
  `track.v1.schema.json`; the two 16-week week-tables in the spec are the DATA for `tracks/*.yaml`,
  never strings in code. Every figure/framework/company/case name is supplied by the YAML at run
  time. The no-hardwiring grep of product code for any track name, week theme, book title or case
  name returns nothing outside `tracks/` and `tests/fixtures/`.

### 1.2 Program engine (Store block) — the actual contract
`program_engine` v1.0.0 (registry `block_registry/program_engine/block.json`, Store #44).
- **Block envelope**: `input` = one JSON object, "Engine operation payload (operation + arguments)";
  `layer 3`, `filesystem: true`, `network: false`; every op returns `{"status": "success"|"error", "result": …}`.
- **Operations**: `load_track(path)`, `session_prompt(track, week, session)`, `defense_prompt(track, week)`,
  `jury_prompt(track)`, `markdown(track, state)`, `restore(text)`.
- **Engine mirrors the factory kernel** (`schema/calendar/modes/rubric/prompts/export`). The product
  routes ALL program math through these — it never re-implements a calendar, rubric band, or prompt.

### 1.3 `track.v1` schema (closed, `extra="forbid"`)
`Track{schema_version="track.v1", id, name, audience, length_weeks≥4, hours_per_week, rubric, weights,
terms[]}`; `Rubric{points_each=20, bands{A,B,C,rewrite_below}, criteria = exactly 5}`;
`Weights{memos+defenses+capstone == 100}`; `Term{number, name, outcome, defense_minutes=45, weeks[]}`;
`Week{number, theme, readings[], case, deliverable}`; `Reading{title, author, chapters?}`;
`Case{title, decision_question, public_sources[]}`; `Deliverable{title, brief, pages≥1}`.
Validation errors raise `TrackError` with the dotted field path.

### 1.4 Engine surface the learner app must call
- `calendar`: `Session{week, kind, offset, minutes, date, name}`, `sessions(start, length_weeks)`,
  `week_start`, `current_week`.
- `modes`: `SessionMode(str, Enum)`, `mode_for_kind(kind)`.
- `rubric`: `total(scores)`, `band(total_score, rubric)`, `weighted_average(…)`, `graduated(…)`.
- `export`: `State{track, start, data: dict[str, dict[int, WeekState]]}`; `WeekState{memo, scores[5],
  verdicts[5], defense, jury}`; `markdown`, `backup`, `restore` (raises `RestoreError`).
- `prompts`: `session_prompt(track, week_no, session)`, `defense_prompt(track, week_no)`, `jury_prompt(track)`.

## 2. Product contract (spec §Product, §Data model, §User flows)

- **Users**: Learner (one per enrolment), Faculty (AI), Grader (AI, separate), Reviewer (human jury
  member, invited per defense), Admin (track author).
- **Cadence**: week = Thursday→Wednesday, six sessions (Framework, Case prep, Case class, Lab 1, Lab 2,
  Exam), Sunday off; start date chosen at enrolment, every session date computed from it.
- **Grading**: memo 5 criteria × 20 → A ≥ 90, B 80–89, C 70–79, < 70 = rewrite; term defense pass/fail;
  capstone accepted by a jury of three (all three accept).
- **Weights**: memos 60%, defenses 20%, capstone 20%; graduation = B average + 4 passes + accepted capstone.
- **LLM**: the Factory's configured provider through the single egress, on the kernels — no new provider.
- **Build level**: pilot; delivery `github_repo` to a `cerebrum-exec-programs` branch on cerebrum-builds-pub;
  also exportable as zip.
- **Out of scope v1**: payments, multi-tenant orgs, video, mobile app, live human faculty scheduling, LMS integrations.
- **Data model entities** (all persisted): Track, Term, Week, Reading, Case, Deliverable, Session, Rubric,
  Enrolment (version-pinned), Submission (versions kept, one rewrite under C), Grade (provenance = kernel
  block + version + rubric version), Defense (45 min, transcript stored), Capstone (jury[3], verdicts[3]),
  Transcript (exportable PDF).

## 3. Eight capabilities (spec §Capabilities) — replace the generic contract rows

The Factory grades **at least five** of these plus the UI before handover.

| # | Capability | Store blocks | Done means |
|---|------------|--------------|------------|
| 1 | track_loader | validation, knowledge | Any schema-valid YAML loads; an invalid one refused with the field path; two tracks ship |
| 2 | enrolment_and_calendar | workflow, database, notification | Start date in, 16 weeks and 96 sessions out, correct for any timezone; week-1 reading email sent |
| 3 | faculty_session | knowledge, memory, audit | Six session modes; transcript stored; mode and week visible in the audit log |
| 4 | memo_grading | formula_executor, evidence_verifier, audit | Rubric scoring with per-criterion lines; grader run id + provenance stored; a rewrite path under 70 |
| 5 | gradebook_and_transcript | analytics, dashboard, document_engine | Live average, weights applied, transcript PDF with every grade |
| 6 | defense_and_capstone | workflow, team, notification | Term defenses on schedule, re-sit rule, jury invites by email, three verdicts, acceptance rule |
| 7 | track_admin | validation, audit, storage | Upload, validate, diff, version pinning per enrolment |
| 8 | reading_and_case_library | knowledge, storage | Per-week readings and cases listed with public sources; faculty withholds outcomes until case class |

## 4. AI faculty and grader (spec §AI faculty and grader)

Two separate agents on the same provider; the **grader never sees the faculty transcript of the week it
grades**, and the **faculty never writes a deliverable** for the learner.

| Session kind | Faculty mode | What the mode does |
|---|---|---|
| framework | lecture | Teaches the week's framework from the reading, then has the learner restate it for their own company |
| case_prep | brief | Hands over the case + decision question; refuses to discuss the outcome |
| case_class | socratic | Cold-calls, changes facts mid-way, forces a committed decision with numbers, then reveals the outcome |
| lab | sparring | Challenges/cuts the learner's draft; never drafts the deliverable itself |
| exam | oral_quiz | 20-minute quiz on the week after the grade is in |
| defense | examiner | Attacks the weakest number/assumption of the term paper, records pass/fail |

Binding: every mode is a kernel skill with the track's week data as its only content input; **prompt text
carries no track, company or case names** — those come from the YAML at run time. The grader applies the
rubric as a formula (5 × 20) and writes one verdict line per criterion; its run id, kernel block version and
rubric version are stored on the grade.

## 5. Acceptance (spec §Acceptance, verbatim)

- Both track YAMLs load; a deliberately broken YAML (missing rubric) is refused with the field path
- Enrol on a Thursday and on a Monday, two timezones: all 96 session dates correct, week 1 reading email sent
- Each of the six session modes answers in character on a synthetic week; transcripts stored and listed
- Grade three synthetic memos (strong, weak, empty): bands A, rewrite, rewrite; verdict lines present; provenance resolves to a kernel block and rubric version
- Gradebook average and weights verified against a hand calculation; transcript PDF opens and lists every entry
- Term defense pass and fail paths, re-sit scheduled within 7 days; capstone with two reviewer invites and three verdicts; graduation rule fires only when met
- Track admin: upload a third track (10-week sample), validate, diff, enrol; existing enrolments unaffected
- UI walk-through of enrol → today → session → submit → gradebook on desktop and phone width, zero dead buttons
- Store acceptance floor 21/21 green; Factory grades ≥ 5 capabilities and the UI before handover
- No hardwiring: grep of product code for any track name, week theme, book title or case name returns nothing outside tracks/ and tests/fixtures/

## 6. User-flow decisions the note now pins (from the spec)

- Missed session: a session not opened by the next week is flagged missed and must be made up before the
  following week unlocks.
- Rewrite: a result under 70 opens exactly one rewrite under C; versions are kept.
- Defense: 45 min on the term's last Wednesday; pass/fail; a fail schedules a re-sit within 7 days.
- Capstone: learner uploads plan + deck and invites two reviewers by email; each records a verdict; the app
  records the AI examiner's verdict as the third.
- Track admin: upload/edit a track YAML; validate; show the diff of weeks that changed; live enrolments keep
  the version they started on (version pinning per enrolment).

## 7. Out of scope for this note
- Pinning/diffing `exec-programs.html` and the actual run are item 3 (queue), gated on the 7/7 pilot; this
  note only prepares the spec.
