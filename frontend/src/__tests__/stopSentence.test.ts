/**
 * PR-12: words when it breaks, and the running line.
 *
 * The stop sentence is written by the page from the server's stop record
 * (spec section 4.5): kind, step, steps done, fix rounds, time. These tests
 * pin the worked examples, scan every sentence for wire ids, check that each
 * sentence's primary action is one the action function offers for the same
 * state, and that the whole-build bar never moves backwards.
 */
import { describe, expect, it } from 'vitest'
import type { BuildStatus, StopRecord } from '../api/factory'
import { buildFace } from '../buildCard'
import { buildActions } from '../buildActions'
import {
  EMPTY_RUNNING_MEMO,
  foldRunning,
  runningLine,
  runningTimers,
  wholeBuildFraction,
  type RunningMemo,
} from '../buildProgress'
import { WIRE_IDS } from '../stepNames'
import { OUR_SIDE, stopWords } from '../stopSentence'

const PHASES = ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']

function record(kind: string, extra: Partial<StopRecord> = {}): StopRecord {
  return {
    kind,
    plain: '',
    step: 'WRITER',
    step_index: 3,
    steps_total: 5,
    steps_done: 2,
    fix_rounds: 0,
    elapsed_s: null,
    technical: {
      code: 'worker_timed_out',
      reason_chain: ['codewhale_worker_failed', 'worker_timed_out'],
      message: 'codewhale_worker_failed: worker_timed_out: headless job exceeded its 2289s wall',
      reference: '7461cb639b',
    },
    ...extra,
  }
}

// Example 1 (the owner's build): a worker timeout after one fix round.
const TIME_RAN_OUT = record('time_ran_out', {
  plain: 'This step ran out of time before it finished.',
  fix_rounds: 1,
  elapsed_s: 2289,
})
// Example 2: a check that used its fix round and has its own sentence.
const CHECK_FAILED = record('check_failed', {
  plain: 'A country code was typed into the code instead of read from the money settings.',
  check_plain: 'A country code was typed into the code instead of read from the money settings.',
  fix_rounds: 1,
  technical: { code: 'FAILED_GATE', reason_chain: ['writer_contract'], message: 'FAILED(WRITER, writer_contract, x)', reference: 'ab12' },
})
// Example 3: our build service restarted while the code was written.
const SERVICE = record('service_interrupted', {
  plain: 'Our build service restarted while the code was being written.',
  restarted: true,
  technical: { code: 'FACTORY_CODE_CLI_ORPHANED', reason_chain: [], message: 'orphaned', reference: 'cd34' },
})

function stopped(stop: StopRecord, extra: Partial<BuildStatus> = {}): BuildStatus {
  return {
    state: 'failed',
    phases: PHASES,
    completed: PHASES.slice(0, stop.steps_done ?? 0),
    phases_done: stop.steps_done,
    stop,
    ...extra,
  }
}

describe('stopWords: the worked examples of section 4.5', () => {
  it('1. time ran out', () => {
    const w = stopWords(TIME_RAN_OUT)
    expect(`${w.word} at ${w.where}`).toBe('Stopped at step 3 of 5')
    expect(w.sentence).toBe(
      'Writing the code ran out of time after about 38 minutes. This is on our side, not in your brief. Steps 1 and 2 are done and kept.',
    )
    const plan = buildActions({ build: stopped(TIME_RAN_OUT) })
    expect(plan.bar[0]).toMatchObject({ label: 'Try again', sub: 'Picks up at step 3. Finished steps are kept.' })
  })

  it('2. a quality check did not pass: the check\'s own sentence comes from the server', () => {
    const w = stopWords(CHECK_FAILED)
    expect(`${w.word} at ${w.where}`).toBe('Stopped at step 3 of 5')
    expect(w.sentence).toBe(
      'A quality check did not pass after 1 fix round. A country code was typed into the code instead of read from the money settings. Steps 1 and 2 are done and kept.',
    )
    const plan = buildActions({ build: stopped(CHECK_FAILED) })
    expect(plan.bar.map((a) => [a.label, a.sub])).toEqual([
      ['Try again', 'One more attempt at step 3.'],
      ['Change answers', 'Change the brief, then resume this build.'],
    ])
  })

  it('2b. a check with no sentence of its own says only that a quality check did not pass', () => {
    const generic = record('check_failed', { plain: 'A quality check did not pass.', check_plain: null, fix_rounds: 0 })
    expect(stopWords(generic).sentence).toBe('A quality check did not pass. Steps 1 and 2 are done and kept.')
  })

  it.each([
    // The server's typed field decides, never the sentence's first words.
    ['A quality check of the money settings found a typed country code.', 'A quality check of the money settings found a typed country code. '],
    ['', ''],
  ])('2c. the check sentence is the typed check_plain only (%j)', (own, said) => {
    const w = stopWords(record('check_failed', { plain: 'Something else entirely.', check_plain: own, fix_rounds: 0 }))
    expect(w.sentence).toBe(`A quality check did not pass. ${said}Steps 1 and 2 are done and kept.`)
  })

  it('3. our build service restarted: our side, the step by its running name', () => {
    const w = stopWords(SERVICE)
    expect(w.sentence).toBe(
      `Our build service restarted while writing the code. ${OUR_SIDE} Steps 1 and 2 are done and kept.`,
    )
    expect(buildActions({ build: stopped(SERVICE) }).bar[0].sub).toBe('Picks up at step 3. Finished steps are kept.')
  })

  it.each([
    [true, 'restarted'],
    [false, 'was interrupted'],
  ])('3b. restarted=%s says the service %s', (restarted, verb) => {
    const w = stopWords({ ...SERVICE, restarted })
    expect(w.sentence.startsWith(`Our build service ${verb} while writing the code.`)).toBe(true)
  })

  it('other states use the same pattern', () => {
    expect(stopWords(record('user_stopped')).sentence).toBe('You stopped this build at step 3. Steps 1 and 2 are done and kept.')
    expect(stopWords(record('no_progress'), { quietS: 2520 }).sentence).toBe(
      'No update from the build for 42 minutes. Steps 1 and 2 are done and kept.',
    )
    const gone = stopWords({ kind: 'not_found', step: '', step_index: null, steps_total: null, steps_done: 0 })
    expect(gone.word).toBe("We can't find this build")
    expect(gone.sentence).toBe("We can't find this build on the server. Your brief and feature list are safe.")
  })

  it('the sentence follows the typed kind, never the raw message', () => {
    // The same raw message under two kinds gives two different sentences.
    const a = stopWords(record('time_ran_out', { elapsed_s: 600 })).sentence
    const b = stopWords(record('user_stopped')).sentence
    expect(a).not.toBe(b)
    expect(a).toContain('ran out of time')
  })
})

const ALL_KINDS: StopRecord[] = [
  TIME_RAN_OUT,
  CHECK_FAILED,
  SERVICE,
  record('nothing_produced', { plain: 'This step finished without producing any code.' }),
  record('no_progress'),
  record('user_stopped'),
  record('time_ran_out', { step: 'SOME_FUTURE_STEP', step_index: null, steps_total: null }),
  { kind: 'not_found', step: '', step_index: null, steps_total: null, steps_done: 0 },
]

describe('stopWords: no wire id, short sentences', () => {
  const FORBIDDEN = [
    ...WIRE_IDS,
    'worker_timed_out',
    'codewhale_worker_failed',
    'FAILED_GATE',
    'FAILED(',
    'writer_contract',
    'REWORK',
    '7461cb639b',
    'SOME_FUTURE_STEP',
    'refused',
  ]
  it.each(ALL_KINDS.map((s) => [s.kind, s] as const))('%s carries no wire id or code', (_kind, stop) => {
    const w = stopWords(stop, { quietS: 600 })
    const text = `${w.word} ${w.where ?? ''} ${w.sentence}`
    for (const id of FORBIDDEN) expect(text).not.toContain(id)
    for (const sentence of w.sentence.split(/(?<=\.)\s+/)) {
      expect(sentence.split(/\s+/).length).toBeLessThanOrEqual(20)
    }
  })
})

describe("each sentence's button is one the action function returns for that state", () => {
  it.each(ALL_KINDS.map((s) => [s.kind, s] as const))('%s', (_kind, stop) => {
    const build: BuildStatus =
      stop.kind === 'not_found' ? { state: 'not_found', stop } : stopped(stop, { last_event_age_s: 600 })
    const words = stopWords(stop)
    const plan = buildActions({ build })
    const primary = plan.bar.filter((a) => a.style === 'primary')
    expect(primary).toHaveLength(1)
    expect(primary[0].id).toBe(words.primary)
    expect(primary[0].label).toBe(words.primary === 'check_again' ? 'Check again' : 'Try again')
    // The card says the same words the function wrote.
    const face = buildFace(build)
    expect(face.sentence).toBe(stopWords(stop, { quietS: 600 }).sentence)
  })

  it('a stop you asked for is its own neutral card', () => {
    expect(buildFace(stopped(record('user_stopped'))).tone).toBe('neutral')
    expect(buildFace(stopped(TIME_RAN_OUT)).tone).toBe('warning')
    expect(buildFace(stopped(CHECK_FAILED)).tone).toBe('danger')
  })

  it('a build the server cannot find offers Check again and Start from scratch, nothing else', () => {
    const plan = buildActions({ build: { state: 'not_found' } })
    expect(plan.bar.map((a) => a.label)).toEqual(['Check again', 'Start from scratch'])
    expect(plan.bar[1].confirm?.keep).toBe('Cancel')
    expect(plan.more).toEqual([])
  })
})

/* ------------------------------ the running line ------------------------------ */

const RUNNING: BuildStatus = {
  state: 'building',
  phases: PHASES,
  phases_done: 2,
  phase_index: 3,
  phase_total: 5,
  current_phase: { id: 'WRITER', label: 'Platform manufacturer' } as BuildStatus['current_phase'],
  phase_progress: { done: 2, total: 4, fraction: 0.5, stage: 'handlers' },
  last_event_age_s: 30,
  deadline: { anchor: 'RUN_STARTED', ceiling_s: 5400, deadline_in_s: 3600 },
}

describe('runningLine', () => {
  it('is one line: step, the step by its running name, the share of features', () => {
    expect(runningLine(RUNNING)).toBe('Step 3 of 5 — Writing the code — 2 of 4 features')
  })
  it('names no wire id, even for a unit or a step the table does not know', () => {
    const odd = { ...RUNNING, current_phase: { id: 'NEW_STEP' }, phase_progress: { done: 1, total: 3, stage: 'widgets' } } as BuildStatus
    const line = runningLine(odd)
    expect(line).toBe('Step 3 of 5 — Step 3 — 1 of 3 parts')
    for (const id of [...WIRE_IDS, 'NEW_STEP', 'widgets', 'handlers']) expect(line).not.toContain(id)
  })
})

describe('the whole-build bar never moves backwards', () => {
  // A run that moves on, enters a fix round (the step's own count drops), is
  // restarted (the record starts over at step 1), and moves on again.
  const SEQUENCE: BuildStatus[] = [
    { ...RUNNING, phases_done: 0, phase_index: 1, current_phase: { id: 'COLLECTOR' } as BuildStatus['current_phase'], phase_progress: undefined },
    { ...RUNNING, phases_done: 2, phase_index: 3, phase_progress: { done: 1, total: 4, stage: 'handlers' } },
    { ...RUNNING, phases_done: 2, phase_index: 3, phase_progress: { done: 3, total: 4, stage: 'handlers' } },
    // fix round: the step's count drops back
    { ...RUNNING, phases_done: 2, phase_index: 3, phase_progress: { done: 1, total: 4, stage: 'handlers' } },
    // restart: the record starts over
    { ...RUNNING, phases_done: 0, phase_index: 1, current_phase: { id: 'COLLECTOR' } as BuildStatus['current_phase'], phase_progress: undefined },
    { ...RUNNING, phases_done: 3, phase_index: 4, current_phase: { id: 'TESTER' } as BuildStatus['current_phase'], phase_progress: undefined },
  ]

  it('across a sequence of status reads that includes a fix round and a restart', () => {
    let memo: RunningMemo = EMPTY_RUNNING_MEMO
    const values: number[] = []
    SEQUENCE.forEach((build, i) => {
      memo = foldRunning(memo, build, 1_000_000 + i * 4000)
      values.push(memo.bar ?? 0)
    })
    for (let i = 1; i < values.length; i++) expect(values[i]).toBeGreaterThanOrEqual(values[i - 1])
    // The raw value does drop, so the memo is what holds it.
    const raw = SEQUENCE.map((b) => wholeBuildFraction(b) ?? 0)
    expect(raw.some((v, i) => i > 0 && v < raw[i - 1])).toBe(true)
    expect(values[values.length - 1]).toBeCloseTo(0.6, 5)
  })

  it('the same status object read twice does not move anything', () => {
    const once = foldRunning(EMPTY_RUNNING_MEMO, RUNNING, 1000)
    expect(foldRunning(once, RUNNING, 99_000)).toBe(once)
  })
})

describe('the three timers', () => {
  it('started, last update and expected end, from the server\'s own deadline', () => {
    const now = Date.UTC(2026, 10, 24, 12, 0, 0)
    const memo = foldRunning(EMPTY_RUNNING_MEMO, RUNNING, now)
    expect(memo.endMs).toBe(now + 3600 * 1000)
    expect(memo.startedMs).toBe(now + 3600 * 1000 - 5400 * 1000)
    const timers = runningTimers(RUNNING, memo, now)
    expect(timers.map((t) => t.key)).toEqual(['started', 'updated', 'expected'])
    expect(timers[1].text).toBe('Last update just now')
    expect(timers[0].text).toMatch(/^Started \d/)
    expect(timers[2].text).toMatch(/^Expected end \d/)
  })

  it('a start is not guessed when the deadline is measured from a later event, but a known one is kept', () => {
    const reworked = { ...RUNNING, deadline: { anchor: 'REWORK', ceiling_s: 5400, deadline_in_s: 4000 } }
    const unknown = foldRunning(EMPTY_RUNNING_MEMO, reworked, 1000)
    expect(unknown.startedMs).toBeNull()
    const known = foldRunning(foldRunning(EMPTY_RUNNING_MEMO, RUNNING, 1000), reworked, 5000)
    expect(known.startedMs).toBe(1000 + 3600 * 1000 - 5400 * 1000)
    expect(known.endMs).toBe(5000 + 4000 * 1000)
  })

  it('no deadline in the record: no expected end, and the past is said as past', () => {
    const none = foldRunning(EMPTY_RUNNING_MEMO, { ...RUNNING, deadline: null }, 1000)
    expect(runningTimers(RUNNING, none, 1000).map((t) => t.key)).toEqual(['updated'])
    const memo = foldRunning(EMPTY_RUNNING_MEMO, RUNNING, 0)
    expect(runningTimers(RUNNING, memo, 3601 * 1000).find((t) => t.key === 'expected')?.text).toMatch(/^Past its expected end/)
  })
})
