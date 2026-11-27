/**
 * PR-10: one name per thing. Every wire id maps to exactly one display name,
 * and nothing the table returns is itself a wire id.
 */
import { describe, expect, it } from 'vitest'
import {
  CHECK_NAMES,
  CHECK_ORDER,
  REVIEW_NAME,
  STEP_NAMES,
  STEP_ORDER,
  WIRE_IDS,
  checkLabel,
  checksIncluded,
  checksSkipped,
  levelWord,
  orList,
  stepLabel,
  stepRunning,
  verdictWord,
} from '../stepNames'

describe('the step name table', () => {
  it('maps each wire id to exactly one display name, and no two ids share one', () => {
    const labels = STEP_ORDER.map((id) => stepLabel(id))
    const running = STEP_ORDER.map((id) => stepRunning(id))
    expect(new Set(labels).size).toBe(STEP_ORDER.length)
    expect(new Set(running).size).toBe(STEP_ORDER.length)
    for (const id of STEP_ORDER) {
      expect(stepLabel(id)).toBe(STEP_NAMES[id].label)
      expect(stepRunning(id)).toBe(STEP_NAMES[id].running)
    }
    const checks = CHECK_ORDER.map((id) => checkLabel(id))
    expect(new Set(checks).size).toBe(CHECK_ORDER.length)
    expect(labels).not.toContain(REVIEW_NAME)
  })

  it.each([
    ['label', (id: string) => stepLabel(id), ['Collector', 'Researcher', 'Workers', 'Tester', 'Store Manager']],
    [
      'running form',
      (id: string) => stepRunning(id),
      [
        'Collector (finding the parts)',
        'Researcher (gathering building blocks)',
        'Workers (writing the code)',
        'Tester (testing)',
        'Store Manager (packaging)',
      ],
    ],
  ])('names the five stages in build order (%s)', (_kind, name, expected) => {
    expect(STEP_ORDER).toEqual(['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER'])
    expect(STEP_ORDER.map(name)).toEqual(expected)
  })

  it('never returns a wire id as a name', () => {
    const names = [
      ...STEP_ORDER.flatMap((id) => [stepLabel(id), stepRunning(id)]),
      ...CHECK_ORDER.map((id) => CHECK_NAMES[id].label),
      ...CHECK_ORDER.map((id) => CHECK_NAMES[id].phrase),
      REVIEW_NAME,
    ]
    for (const name of names) {
      for (const wire of WIRE_IDS) expect(name).not.toContain(wire)
    }
  })

  it('an unknown id reads as its position, never as the raw id', () => {
    expect(stepLabel('SOME_NEW_ROLE', 6)).toBe('Step 6')
    expect(stepLabel('SOME_NEW_ROLE')).toBe('This step')
    expect(stepRunning(null, 2)).toBe('Step 2')
  })
})

describe('build level and checks', () => {
  it("echoes the user's word for the level", () => {
    expect(levelWord('prototype')).toBe('Prototype')
    expect(levelWord('PILOT')).toBe('Pilot')
    expect(levelWord('')).toBeNull()
    expect(levelWord(undefined)).toBeNull()
  })

  it('a level includes every check up to its stop gate, and only those', () => {
    expect(checksIncluded('CODE')).toEqual(['CODE'])
    expect(checksSkipped('CODE')).toEqual(['PRODUCT', 'STORE'])
    expect(checksIncluded('PRODUCT')).toEqual(['CODE', 'PRODUCT'])
    expect(checksIncluded('STORE')).toEqual(['CODE', 'PRODUCT', 'STORE'])
    expect(checksSkipped('STORE')).toEqual([])
    // No stop gate, or one the table does not know: all three are listed.
    expect(checksIncluded(undefined)).toEqual(['CODE', 'PRODUCT', 'STORE'])
    expect(checksIncluded('SOMETHING_ELSE')).toEqual(['CODE', 'PRODUCT', 'STORE'])
  })

  it('says verdicts in words', () => {
    expect(verdictWord('PASS')).toBe('passed')
    expect(verdictWord('FAIL')).toBe('failed')
    expect(verdictWord('NOT RUN')).toBe('not run')
    expect(verdictWord('NOT_RUN')).toBe('not run')
    expect(verdictWord('UNKNOWN')).toBeNull()
  })

  it('joins a list in plain words', () => {
    expect(orList(['a'])).toBe('a')
    expect(orList(['a', 'b'])).toBe('a or b')
    expect(orList(['a', 'b', 'c'])).toBe('a, b or c')
  })
})
