/**
 * PR-10: the stepper, the review rule and the level strip -- and a scan that
 * no wire id reaches a user-visible string outside Technical detail.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import type { BuildStatus } from '../api/factory'
import { BuildCard } from '../buildCard'
import { LevelGradeDetail, LevelGradeStrip } from '../levelGradeView'
import { Stepper, stepViews } from '../stepper'
import { WIRE_IDS } from '../stepNames'

const PHASES = ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']
const TIMEOUT =
  'codewhale_worker_failed: worker_timed_out: headless job exceeded its 2289s wall (dispatched with 1800.0s)'
const CONTRACT_SEND_BACK = {
  class: 'REWORK',
  gate: 'WRITER',
  gate_name: 'writer_contract',
  round_gate: 1,
  gate_budget: 1,
  round_build: 1,
  build_ceiling: 6,
  check: 'writer_contract',
  finding: 'app/actions/contract_settings.py:47: the declared code is a literal',
}

/** A worker timeout after an earlier contract send-back (the owner's build of 21 Nov). */
const TIMEOUT_STOP: BuildStatus = {
  state: 'failed',
  cycle: 'code',
  outcome: 'FAILED',
  detail: 'WRITER failed: ' + TIMEOUT,
  phases: PHASES,
  completed: ['COLLECTOR', 'CLONER'],
  failure: {
    phase: 'WRITER',
    location: 'WRITER',
    outcome: 'failed',
    reason: 'codewhale_worker_failed',
    code: 'codewhale_worker_failed',
    summary: 'WRITER was refused during the build; the full detail is in the build record.',
    ref: '7461cb639b',
  },
  failed: { gate: 'WRITER', check: 'codewhale_worker_failed', finding: TIMEOUT },
  failed_label: `FAILED(WRITER, codewhale_worker_failed, ${TIMEOUT})`,
  decisions: [CONTRACT_SEND_BACK],
  level_grade: { level: 'SCAFFOLD', three_gate: { CODE: 'FAIL', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' } },
  build_level: { build_level: 'prototype', stop_gate: 'CODE' },
}

const BUILDING_FIX_ROUND: BuildStatus = {
  state: 'building',
  phases: PHASES,
  completed: ['COLLECTOR', 'CLONER'],
  current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
  phase_index: 3,
  phase_total: 5,
  failure: { phase: 'WRITER', location: 'WRITER', reason: 'writer_contract' },
  decisions: [CONTRACT_SEND_BACK],
}

const FINISHED_PROTOTYPE: BuildStatus = {
  state: 'succeeded',
  outcome: 'SUCCESS',
  cycle: 'code',
  phases: PHASES,
  completed: PHASES,
  level_grade: { level: 'CODE_GREEN', three_gate: { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' } },
  build_level: { build_level: 'prototype', stop_gate: 'CODE' },
}

/** Words that left the screen in spec 4.4, on top of the table's own wire ids. */
const OLD_NAMES = [
  'RESEARCHER',
  'WORKERS',
  'REWORK',
  'writer_contract',
  'codewhale_worker_failed',
  'Built to',
  'stops at',
  'Code-green',
  'Store-green',
  'Scaffold',
  'NOT RUN',
  'rejected',
  'Binding surveyor',
  'Platform manufacturer',
]

function renderCard(build: BuildStatus) {
  return render(
    <BuildCard
      build={build}
      sessionId="sess_scan"
      steps={
        <>
          <LevelGradeStrip build={build} testIdPrefix="floor" />
          <Stepper build={build} />
        </>
      }
      detail={<LevelGradeDetail build={build} testIdPrefix="floor" />}
    />,
  )
}

/** What the card says with Technical detail removed, including screen-reader-only text. */
function faceText(): string {
  const card = screen.getByTestId('floor-coder-takeover').cloneNode(true) as HTMLElement
  card.querySelector('[data-testid="floor-tech-detail"]')?.remove()
  return card.textContent ?? ''
}

afterEach(cleanup)

describe('no wire id outside Technical detail', () => {
  for (const [name, build] of [
    ['a timeout after a contract send-back', TIMEOUT_STOP],
    ['a build in a fix round', BUILDING_FIX_ROUND],
    ['a finished prototype', FINISHED_PROTOTYPE],
  ] as const) {
    it(`${name}: the face holds no wire id or old name`, () => {
      renderCard(build)
      const face = faceText()
      for (const id of [...WIRE_IDS, ...OLD_NAMES]) expect(face).not.toContain(id)
      // ...and the ids are still there for support, inside Technical detail.
      expect(screen.getByTestId('floor-tech-detail').textContent).toMatch(/WRITER|CODE/)
    })
  }
})

describe('the stepper', () => {
  it('is one row of five steps with exactly one current step', () => {
    render(<Stepper build={BUILDING_FIX_ROUND} />)
    const row = screen.getByTestId('floor-stepper-row')
    const steps = within(row).getAllByRole('listitem')
    expect(steps).toHaveLength(5)
    const atNow = steps.filter((li) => ['current', 'fixing', 'stopped'].includes(li.dataset.state ?? ''))
    expect(atNow).toHaveLength(1)
    expect(atNow[0]).toHaveAttribute('data-step', 'WRITER')
    expect(screen.getByTestId('floor-stepper-caption')).toHaveTextContent('Write the code')
  })

  it('says each state in words, not colour alone', () => {
    render(<Stepper build={TIMEOUT_STOP} />)
    const text = screen.getByTestId('floor-stepper-row').textContent ?? ''
    expect(text).toContain('Step 1, Plan the parts: done')
    expect(text).toContain('Step 3, Write the code: stopped')
    expect(text).toContain('Step 4, Test: not reached')
  })

  it('a timeout that carries a contract send-back does not produce a "rejected" review', () => {
    render(<Stepper build={TIMEOUT_STOP} />)
    expect(screen.queryByTestId('floor-phase-grader-failed')).not.toBeInTheDocument()
    expect(screen.queryByTestId('floor-phase-grader-rework')).not.toBeInTheDocument()
    expect(screen.getByTestId('floor-stepper')).not.toHaveTextContent(/Quality check|rejected/)
    expect(screen.getByTestId('floor-phase-failed-WRITER')).toBeInTheDocument()
  })

  it("a stop on the grader's own reason is \"Quality check did not pass\" on step 3", () => {
    render(
      <Stepper
        build={{
          ...TIMEOUT_STOP,
          failure: { phase: 'WRITER', location: 'WRITER', code: 'grader_not_certified', reason: 'grader_not_certified' },
          failed: { gate: 'WRITER', check: 'grader_not_certified', finding: 'GRADER: NOT_CERTIFIED' },
        }}
      />,
    )
    const mark = screen.getByTestId('floor-phase-grader-failed')
    expect(mark.closest('li')).toHaveAttribute('data-step', 'WRITER')
    expect(screen.getByTestId('floor-stepper-caption')).toHaveTextContent(
      'Write the code — Quality check did not pass',
    )
  })

  it('a finished build shows every step done and an outcome line', () => {
    render(<Stepper build={FINISHED_PROTOTYPE} />)
    expect(stepViews(FINISHED_PROTOTYPE).every((s) => s.state === 'done')).toBe(true)
    expect(screen.getByTestId('floor-stepper-caption')).toHaveTextContent('5 of 5 steps done')
    expect(screen.queryByTestId('floor-phase-grader-failed')).not.toBeInTheDocument()
  })
})

describe('the level strip', () => {
  it('reads "Build level: Prototype" and lists only the checks the level includes', () => {
    render(<LevelGradeStrip build={FINISHED_PROTOTYPE} testIdPrefix="floor" />)
    expect(screen.getByTestId('floor-build-level')).toHaveTextContent('Build level: Prototype')
    expect(screen.getByTestId('floor-gate-code')).toHaveTextContent('Code checks passed')
    expect(screen.queryByTestId('floor-gate-product')).not.toBeInTheDocument()
    expect(screen.queryByTestId('floor-gate-store')).not.toBeInTheDocument()
  })

  it('shows no pill text of its own when the record carries no level and no checks', () => {
    const { container } = render(
      <LevelGradeStrip build={{ state: 'succeeded', outcome: 'SUCCESS' }} testIdPrefix="floor" />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})
