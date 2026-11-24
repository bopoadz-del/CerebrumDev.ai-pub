/**
 * PR-08: one pure function from a build's state to its ordered actions
 * (the button matrix). Labels, order, one primary at most, never a primary
 * on a destructive action, and a confirm on every destructive one.
 */
import { describe, expect, it } from 'vitest'
import type { BuildStatus } from '../api/factory'
import { buildActions, stopKind, type ActionInput, type ActionPlan } from '../buildActions'

const PHASES = ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']
const STOP_DECISION = {
  class: 'STOP',
  gate: 'WRITER',
  round_gate: 1,
  gate_budget: 1,
  round_build: 2,
  build_ceiling: 6,
  check: 'writer_contract',
  finding: 'budget used',
}

const base = { phases: PHASES, completed: ['COLLECTOR', 'CLONER'] }
const failure = { phase: 'WRITER', location: 'WRITER', outcome: 'failed', reason: 'x', code: 'x' }

const BUILDS: Record<string, BuildStatus> = {
  building: { state: 'building', ...base, current_phase: { id: 'WRITER' } as BuildStatus['current_phase'] },
  starting: { state: 'waiting' },
  stopping: { state: 'building', ...base, coder_control: 'stop' },
  timedOut: { state: 'failed', ...base, failure },
  checkFailed: { state: 'failed', ...base, failure, stopped: STOP_DECISION },
  byYou: { state: 'failed', ...base, failure, coder_control: 'stop' },
  stalled: { state: 'stalled', ...base, current_phase: { id: 'WRITER' } as BuildStatus['current_phase'], last_event_age_s: 900 },
  prototype: { state: 'succeeded', cycle: 'code', outcome: 'SUCCESS', build_level: { build_level: 'pilot', stop_gate: 'STORE' } },
  prototypeCodeOnly: { state: 'succeeded', cycle: 'code', outcome: 'SUCCESS', build_level: { build_level: 'prototype', stop_gate: 'CODE' } },
  github: {
    state: 'succeeded',
    cycle: 'code',
    outcome: 'SUCCESS',
    build_level: { build_level: 'prototype', stop_gate: 'CODE' },
    delivery_format: 'github_repo',
    repo_url: 'https://example.test/org/repo',
  },
}

function plan(build: BuildStatus, extra: Partial<ActionInput> = {}): ActionPlan {
  return buildActions({ build, ...extra })
}
const labels = (p: ActionPlan) => p.bar.map((a) => a.label)
const moreLabels = (p: ActionPlan) => p.more.map((a) => a.label)
const all = (p: ActionPlan) => [...p.bar, ...p.more]

describe('buildActions: labels and order per state', () => {
  it.each([
    ['building', ['Stop build'], []],
    ['starting', ['Stop build'], []],
    ['stopping', ['Stopping…'], []],
    ['timedOut', ['Try again', 'Download copy'], ['Change answers', 'Start from scratch', 'Copy error report']],
    ['checkFailed', ['Try again', 'Change answers'], ['Download copy', 'Start from scratch', 'Copy error report']],
    ['byYou', ['Try again', 'Download copy'], ['Start from scratch', 'Copy error report']],
    ['stalled', ['Try again', 'Download copy'], ['Start from scratch', 'Copy error report']],
    ['prototype', ['Download prototype (.zip)', 'Run pilot checks'], []],
    ['prototypeCodeOnly', ['Download prototype (.zip)'], []],
    ['github', ['Open repository', 'Download prototype (.zip)'], []],
  ])('%s', (name, bar, more) => {
    const p = plan(BUILDS[name])
    expect(labels(p)).toEqual(bar)
    expect(moreLabels(p)).toEqual(more)
  })

  it('never offers Pause or Resume', () => {
    for (const b of Object.values(BUILDS)) {
      expect(all(plan(b)).map((a) => a.label)).not.toEqual(expect.arrayContaining(['Pause']))
      expect(all(plan(b)).map((a) => a.label)).not.toEqual(expect.arrayContaining(['Resume']))
    }
  })
})

describe('buildActions: the rules that hold in every state', () => {
  it.each(Object.keys(BUILDS))('%s has at most one primary and no primary destructive action', (name) => {
    for (const extra of [{}, { accessPaused: true }, { busy: true }, { downloading: true }]) {
      const p = plan(BUILDS[name], extra)
      expect(all(p).filter((a) => a.style === 'primary').length).toBeLessThanOrEqual(1)
      for (const a of all(p)) {
        if (a.id === 'start_over' || a.id === 'stop') expect(a.style).toBe('danger')
      }
    }
  })

  it.each(Object.keys(BUILDS))('%s: every action has one visible line', (name) => {
    for (const a of all(plan(BUILDS[name]))) expect(a.sub.trim().length).toBeGreaterThan(0)
  })

  it('Start from scratch always needs a confirm whose default is Cancel', () => {
    for (const b of Object.values(BUILDS)) {
      for (const a of all(plan(b)).filter((x) => x.id === 'start_over')) {
        expect(a.confirm).toBeDefined()
        expect(a.confirm?.keep).toBe('Cancel')
        expect(a.confirm?.go).toBe('Start from scratch')
      }
    }
    expect(all(plan(BUILDS.timedOut)).some((a) => a.id === 'start_over')).toBe(true)
  })

  it('Stop build asks first, with Keep building as the safe choice', () => {
    const stop = plan(BUILDS.building).bar[0]
    expect(stop.confirm?.keep).toBe('Keep building')
    expect(stop.confirm?.go).toBe('Stop build')
    expect(stop.disabled).toBeNull()
  })

  it('a pressed Stop shows Stopping… and cannot be pressed again', () => {
    const stop = plan(BUILDS.building, { stopping: true }).bar[0]
    expect(stop.label).toBe('Stopping…')
    expect(stop.disabled).toBeTruthy()
    expect(stop.confirm).toBeUndefined()
  })
})

describe('buildActions: Try again', () => {
  it('is the primary first action of every stopped state, the stall included', () => {
    for (const name of ['timedOut', 'checkFailed', 'byYou', 'stalled']) {
      const first = plan(BUILDS[name]).bar[0]
      expect(first.id).toBe('try_again')
      expect(first.style).toBe('primary')
    }
  })

  it('names the step it picks up at, from the run\'s own phase list', () => {
    expect(plan(BUILDS.timedOut).bar[0].sub).toBe('Picks up at step 3. Finished steps are kept.')
    expect(plan(BUILDS.checkFailed).bar[0].sub).toBe('One more attempt at step 3.')
    expect(plan(BUILDS.byYou).bar[0].sub).toBe('Resumes at step 3, where you stopped it.')
  })
})

describe('buildActions: access paused and busy', () => {
  it('disables what starts work, says why, and keeps downloads enabled', () => {
    const p = plan(BUILDS.timedOut, { accessPaused: true })
    expect(p.accessPaused).toBe(true)
    const by = Object.fromEntries(all(p).map((a) => [a.id, a]))
    expect(by.try_again.disabled).toBe('Access is paused.')
    expect(by.change_answers.disabled).toBe('Access is paused.')
    expect(by.start_over.disabled).toBe('Access is paused.')
    expect(by.download_copy.disabled).toBeNull()
    expect(by.copy_report.disabled).toBeNull()
    const fin = plan(BUILDS.prototype, { accessPaused: true })
    expect(fin.bar[0].disabled).toBeNull()
    expect(fin.bar[1].disabled).toBe('Access is paused.')
  })

  it('a running request disables what starts work, not the downloads', () => {
    const by = Object.fromEntries(all(plan(BUILDS.timedOut, { busy: true })).map((a) => [a.id, a]))
    expect(by.try_again.disabled).toBe('Wait for the current request to finish.')
    expect(by.download_copy.disabled).toBeNull()
  })

  it('a download being packed disables only the downloads', () => {
    const by = Object.fromEntries(all(plan(BUILDS.timedOut, { downloading: true })).map((a) => [a.id, a]))
    expect(by.download_copy.disabled).toBe('Packing…')
    expect(by.try_again.disabled).toBeNull()
  })
})

describe('buildActions: finished builds', () => {
  it('opens the repository whenever a link exists, and only an http link', () => {
    expect(plan(BUILDS.github).bar[0].href).toBe('https://example.test/org/repo')
    const notALink = plan({ ...BUILDS.github, repo_url: 'javascript:alert(1)' })
    expect(labels(notALink)).not.toContain('Open repository')
  })

  it('a refused certified download offers the next step and a copy, with the reason', () => {
    const p = plan({ state: 'succeeded', outcome: 'FAILED' } as BuildStatus)
    expect(labels(p)[1]).toBe('Download copy')
    expect(p.bar[0].style).toBe('primary')
    expect(p.bar[0].sub).toMatch(/did not pass its checks/)
  })
})

describe('stopKind', () => {
  it('reads typed facts only', () => {
    expect(stopKind(BUILDS.building)).toBeNull()
    expect(stopKind(BUILDS.byYou)).toBe('by_you')
    expect(stopKind(BUILDS.stalled)).toBe('our_side')
    expect(stopKind(BUILDS.checkFailed)).toBe('check')
    expect(stopKind(BUILDS.timedOut)).toBe('our_side')
  })
})

/**
 * The server's allowed actions for each stop state (floor_actions.
 * allowed_build_actions). The page offers exactly that set: every button it
 * draws is one the server accepts, and every action the server accepts is on
 * screen. "Copy error report" sends nothing and is the page's own.
 */
describe('buildActions: the server action list decides', () => {
  const STOPPED = ['try_again', 'download_copy', 'change_answers', 'start_over']
  const MATRIX: [string, Partial<BuildStatus>, string[]][] = [
    ['time ran out', { state: 'failed', stop: { kind: 'time_ran_out' } }, STOPPED],
    ['check failed', { state: 'failed', stop: { kind: 'check_failed' } }, ['try_again', 'change_answers', 'download_copy', 'start_over']],
    ['user stopped', { state: 'failed', stop: { kind: 'user_stopped' } }, STOPPED],
    ['service interrupted', { state: 'failed', stop: { kind: 'service_interrupted' } }, STOPPED],
    ['nothing produced', { state: 'failed', stop: { kind: 'nothing_produced' } }, STOPPED],
    ['stall', { state: 'stalled', stop: { kind: 'no_progress' } }, ['try_again', 'start_over']],
    ['stall seen as failed by awaitBuild', { state: 'failed', stop: { kind: 'no_progress' } }, ['try_again', 'start_over']],
    ['not found', { state: 'not_found', stop: { kind: 'not_found' } }, ['check_again', 'start_over']],
    ['running', { state: 'building' }, ['stop']],
    ['waiting on the owner', { state: 'building', n3_waiting: true } as Partial<BuildStatus>, ['try_again']],
  ]

  it.each(MATRIX)('%s: offered == allowed', (_name, build, allowed) => {
    const plan = buildActions({ build: { ...build, actions: allowed } as BuildStatus })
    const offered = [...plan.bar, ...plan.more].map((a) => a.id).filter((id) => id !== 'copy_report')
    expect(new Set(offered)).toEqual(new Set(allowed))
    // One primary whenever a safe action is offered; never on a destructive one.
    const safe = [...plan.bar, ...plan.more].some((a) => a.style !== 'danger' && a.id !== 'copy_report')
    expect(plan.bar.filter((a) => a.style === 'primary')).toHaveLength(safe ? 1 : 0)
  })

  it.each([
    // The server refuses Download copy on a stall: it is not drawn.
    [{ state: 'stalled', stop: { kind: 'no_progress' } }, ['try_again', 'start_over'], 'download_copy'],
    // The server refuses Try again on a finished build: it is not drawn.
    [{ state: 'succeeded', pilot_ready: true }, ['download_platform'], 'try_again'],
  ])('an action the server refuses is never drawn (%#)', (build, allowed, refused) => {
    const plan = buildActions({ build: { ...build, actions: allowed } as BuildStatus })
    expect([...plan.bar, ...plan.more].map((a) => a.id)).not.toContain(refused)
  })

  it.each([
    [null, true],
    [['try_again'], false],
  ])('with no server list (before the first read) the layout stands (%j)', (actions, hasCopy) => {
    const build = { state: 'stalled', stop: { kind: 'no_progress' }, actions } as BuildStatus
    const ids = [...buildActions({ build }).bar, ...buildActions({ build }).more].map((a) => a.id)
    expect(ids.includes('download_copy')).toBe(hasCopy)
  })
})
