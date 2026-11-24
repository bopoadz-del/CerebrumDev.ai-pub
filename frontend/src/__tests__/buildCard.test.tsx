/**
 * PR-07: the BUILD card -- state line, one sentence, actions, steps, and a
 * closed Technical detail that is the only home of machine text.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { BuildStatus } from '../api/factory'
import { BuildCard, buildFace, errorReport, keptSentence } from '../buildCard'

const PHASES = ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']
const TIMEOUT =
  'codewhale_worker_failed: worker_timed_out: headless job exceeded its 2289s wall (dispatched with 1800.0s)'
const REWORK = {
  class: 'REWORK',
  gate: 'WRITER',
  round_gate: 1,
  gate_budget: 1,
  round_build: 1,
  build_ceiling: 6,
  check: 'writer_contract',
  finding: 'app/actions/contract_settings.py:47: the declared code is a literal',
}

/** The owner's stopped build of 21 Nov, as the build record carries it. */
const OWNER_STOP: BuildStatus = {
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
  next_continue: 'Continue will resume at WRITER: ' + TIMEOUT,
  failure_narrative: { text: 'Where it stopped: WRITER. Why: ' + TIMEOUT, source: 'ledger' },
  decisions: [REWORK],
  activity_log: [{ role: 'writer', text: 'wrote handler contract_settings' }],
}

const WIRE = [
  ...PHASES,
  'RESEARCHER',
  'WORKERS',
  'GRADER',
  'codewhale_worker_failed',
  'worker_timed_out',
  'writer_contract',
  'REWORK',
  '7461cb639b',
]

/** The card's text with Technical detail removed: what the face says. */
function faceText(): string {
  const card = screen.getByTestId('floor-coder-takeover').cloneNode(true) as HTMLElement
  card.querySelector('[data-testid="floor-tech-detail"]')?.remove()
  return card.textContent ?? ''
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('BuildCard with the owner\'s stopped build', () => {
  it('keeps every wire id, reason code, path and FAILED( text off the face', () => {
    render(
      <BuildCard
        build={OWNER_STOP}
        sessionId="sess_owner"
        productTitle="Contracts Hub"
        actions={<button type="button">Take a copy</button>}
      />,
    )
    const face = faceText()
    for (const id of WIRE) expect(face).not.toContain(id)
    expect(face).not.toContain('FAILED(')
    expect(face).not.toMatch(/[\w-]+\/[\w./-]+\.(py|ts|tsx|json)/)
    expect(face).not.toMatch(/\d+s\b/)
  })

  it('says where it stopped, what is kept, and the fix rounds used, once', () => {
    render(<BuildCard build={OWNER_STOP} sessionId="sess_owner" />)
    expect(screen.getByTestId('floor-build-state')).toHaveTextContent('Stopped at step 3 of 5')
    expect(screen.getByTestId('floor-build-sentence')).toHaveTextContent(
      'The build stopped before it finished. Steps 1 and 2 are done and kept.',
    )
    expect(screen.getByTestId('floor-fix-rounds')).toHaveTextContent('1 fix round used (of 1)')
    expect(screen.getByTestId('floor-coder-takeover')).toHaveAttribute('data-tone', 'danger')
  })

  it('shows the raw message exactly once, inside a closed Technical detail', () => {
    render(<BuildCard build={OWNER_STOP} sessionId="sess_owner" />)
    const detail = screen.getByTestId('floor-tech-detail') as HTMLDetailsElement
    expect(detail.tagName).toBe('DETAILS')
    expect(detail.open).toBe(false)
    expect(detail.querySelector('summary')).toHaveTextContent('Technical detail')
    const text = detail.textContent ?? ''
    expect(text.split(TIMEOUT).length - 1).toBe(1)
    // The history, the reference and the activity log are there, once each.
    expect(text.split('7461cb639b').length - 1).toBe(1)
    expect(screen.getByTestId('floor-decisions')).toHaveTextContent('writer_contract')
    expect(screen.getByTestId('floor-activity-log')).toHaveTextContent('wrote handler contract_settings')
    // The grey paragraphs are not repeated anywhere on the card.
    const card = screen.getByTestId('floor-coder-takeover').textContent ?? ''
    expect(card).not.toContain('Continue will resume')
    expect(card).not.toContain('Where it stopped')
  })

  it('"Copy error report" copies the report and shows "Copied"', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(<BuildCard build={OWNER_STOP} sessionId="sess_owner" productTitle="Contracts Hub" />)
    expect(screen.getByTestId('floor-copy-result')).toHaveTextContent('')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy error report' }))
    })
    expect(screen.getByTestId('floor-copy-result')).toHaveTextContent('Copied')
    const report = writeText.mock.calls[0][0] as string
    expect(report).toContain('Session: sess_owner')
    expect(report).toContain('Product: Contracts Hub')
    expect(report).toContain('Reference: 7461cb639b')
    expect(report).toContain('Technical message: FAILED(WRITER')
  })

  it('says so when the clipboard refuses', async () => {
    Object.assign(navigator, { clipboard: { writeText: vi.fn().mockRejectedValue(new Error('denied')) } })
    render(<BuildCard build={OWNER_STOP} sessionId="sess_owner" />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy error report' }))
    })
    expect(screen.getByTestId('floor-copy-result')).toHaveTextContent('Could not copy')
  })

  it('has no inline copy chip and no status pill on the face', () => {
    render(<BuildCard build={OWNER_STOP} sessionId="sess_owner" />)
    expect(document.querySelector('.coder-failure-copy')).toBeNull()
    expect(document.querySelector('.status-pill')).toBeNull()
    expect(screen.queryByRole('button', { name: 'copy' })).toBeNull()
  })
})

describe('buildFace', () => {
  const stalled: BuildStatus = { ...OWNER_STOP, state: 'stalled', last_event_age_s: 2520 }
  const byUser: BuildStatus = { ...OWNER_STOP, coder_control: 'stop' }
  const running: BuildStatus = {
    state: 'building',
    phases: PHASES,
    completed: ['COLLECTOR', 'CLONER'],
    phase_index: 3,
    phase_total: 5,
    current_phase: { id: 'WRITER' },
  }

  it.each([
    [stalled, 'No update from the build for 42 minutes. Steps 1 and 2 are done and kept.'],
    [byUser, 'You stopped this build at step 3. Steps 1 and 2 are done and kept.'],
    [OWNER_STOP, 'The build stopped before it finished. Steps 1 and 2 are done and kept.'],
  ])('a stop writes its sentence from typed facts (%#)', (build, sentence) => {
    const face = buildFace(build)
    expect(face.word).toBe('Stopped')
    expect(face.tone).toBe('danger')
    expect(face.sentence).toBe(sentence)
  })

  // The stop classes the server types (failure_record.STOP_TEXT): the face
  // names the class, and a stop no check judged is amber, never red.
  it.each([
    ['out_of_time', 'ran out of time', 'The time allowance ended before this step finished. No check refused the work.', 'warning'],
    ['could_not_run', 'could not finish', 'This step stopped before it finished. No check judged the work.', 'warning'],
    ['check_refused', 'did not pass its check', 'A check looked at the work and did not pass it.', 'danger'],
    ['rounds_spent', 'used up its fix rounds', 'A check still did not pass the work.', 'danger'],
  ])('a classified stop (%s) is named by its class and toned by it', (kind, title, summary, tone) => {
    const build: BuildStatus = { ...OWNER_STOP, failure: { ...OWNER_STOP.failure, kind, title, summary } }
    const face = buildFace(build)
    expect(face.tone).toBe(tone)
    expect(face.sentence).toBe(`This step ${title}. Steps 1 and 2 are done and kept.`)
    render(<BuildCard build={build} sessionId="sess_owner" />)
    const text = faceText()
    for (const id of WIRE) expect(text).not.toContain(id)
    // Said once: the longer summary lives only in the closed Technical detail.
    expect(text).not.toContain(summary)
    expect(screen.getByTestId('floor-coder-takeover').textContent?.split(summary).length).toBe(2)
    expect(screen.getByTestId('floor-failure-line')).toHaveTextContent(`The "Write the code" step ${title} — ${summary}`)
    expect(screen.getByTestId('floor-failure-line').closest('details')).not.toHaveAttribute('open')
  })

  it('the copy action sits outside the capped, scrolling detail body', () => {
    render(<BuildCard build={OWNER_STOP} sessionId="sess_owner" />)
    const copy = screen.getByTestId('floor-copy-error-report')
    expect(copy.closest('.tech-body')).toBeNull()
    expect(copy.closest('details')).toBe(screen.getByTestId('floor-tech-detail'))
  })

  it('a stop without a class keeps the plain sentence and the red tone', () => {
    const build: BuildStatus = { ...OWNER_STOP, failure: { ...OWNER_STOP.failure, kind: 'out_of_time' } }
    expect(buildFace(build)).toMatchObject({
      tone: 'danger',
      sentence: 'The build stopped before it finished. Steps 1 and 2 are done and kept.',
    })
  })

  it.each([
    [0, 'No step was finished yet.'],
    [1, 'Step 1 is done and kept.'],
    [2, 'Steps 1 and 2 are done and kept.'],
    [4, 'Steps 1 to 4 are done and kept.'],
  ])('kept sentence for %i steps', (n, text) => {
    expect(keptSentence(n)).toBe(text)
  })

  it('a running build is info-toned with its step and no fix-round line', () => {
    const face = buildFace(running)
    expect(face).toMatchObject({ tone: 'info', word: 'Building', where: 'step 3 of 5', fixRoundNow: null })
  })

  it.each([
    [{ ...REWORK, round_gate: 1, gate_budget: 1 }, true],
    [{ ...REWORK, round_gate: 1, gate_budget: 3 }, false],
  ])('a fix round in progress is one amber line (%#)', (decision, last) => {
    const face = buildFace({
      ...running,
      failure: { location: 'WRITER', code: 'writer_contract' },
      decisions: [decision],
    })
    expect(face.tone).toBe('warning')
    expect(face.fixRoundNow).toContain('A quality check sent work back. The Factory is fixing it now.')
    expect(face.fixRoundNow?.includes('This is the last retry.')).toBe(last)
    expect(face.fixRoundNow).not.toContain('writer_contract')
  })

  it.each([
    [null, 'Building'],
    [{ state: 'succeeded' } as BuildStatus, 'Ready'],
  ])('other states get a state word (%#)', (build, word) => {
    expect(buildFace(build).word).toBe(word)
  })

  it('the error report never invents a field the record does not carry', () => {
    const report = errorReport({ state: 'failed' }, { sessionId: 's1' })
    expect(report).toContain('Session: s1')
    expect(report).not.toContain('Reference:')
    expect(report).not.toContain('Product:')
  })
})

describe('a writer pass the runner continued (GROKGRADE F297)', () => {
  const PASS = {
    continuing: true,
    pass: 2,
    max_passes: 3,
    writer_minutes: 60,
    ceiling_minutes: 120,
    text: 'continuing, pass 2 of 3: the writer was still working when its time ran out',
    ts: '2026-11-24T07:24:30+00:00',
  }
  const building: BuildStatus = {
    state: 'building',
    phases: PHASES,
    completed: ['COLLECTOR', 'CLONER'],
    activity_log: Array.from({ length: 15 }, (_, i) => ({ ts: '', role: 'WRITER', text: `heartbeat ${i}` })),
    writer_pass: PASS,
  }

  it.each([
    [{ ...building }, 'Continuing in pass 2 of 3', 'Writing time so far: 60 min of 120 min allowed.'],
    [{ ...building, writer_pass: { ...PASS, pass: 3, writer_minutes: 120, ceiling_minutes: null } }, 'pass 3 of 3', '120 min.'],
  ])('is said on the card face, not only in Technical detail (%#)', (build, pass, time) => {
    render(<BuildCard build={build as BuildStatus} sessionId="s1" />)
    const line = screen.getByTestId('floor-writer-pass')
    expect(line.textContent).toContain(pass)
    expect(line.textContent).toContain(time)
    expect(line.closest('details')).toBeNull()
  })

  it.each([
    [{ ...building, writer_pass: { ...PASS, continuing: false, text: 'writer not continued: x' } }],
    [{ ...building, state: 'failed' as const }],
    [{ ...building, writer_pass: null }],
  ])('is absent when no pass is continuing or the build stopped (%#)', (build) => {
    render(<BuildCard build={build as BuildStatus} sessionId="s1" />)
    expect(screen.queryByTestId('floor-writer-pass')).toBeNull()
  })
})
