/**
 * PR-09: every press answers where the finger is. A busy label, then a
 * one-line result in the action bar, then the next step the server names.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Floor } from '../App'
import { ApiError } from '../api/factory'
import { actionOutcome, humanError, messageFromBody, nextActionFor } from '../actionResult'
import { buildActions } from '../buildActions'

const chatStreamMock = vi.fn()
const watchBuildMock = vi.fn()
const getMock = vi.fn()
const downloadMock = vi.fn()
const sessionStateMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    chatStream: (...args: unknown[]) => chatStreamMock(...args),
    watchBuildStatus: (...args: unknown[]) => watchBuildMock(...args),
    downloadProductPackage: (...args: unknown[]) => downloadMock(...args),
    getHealth: () => Promise.resolve({ factory_code_cli: { available: true, credentials_file_present: true } }),
    product: {
      ...actual.product,
      get: (...args: unknown[]) => getMock(...args),
      verticals: () => Promise.resolve({ verticals: [], intake: { declared: {}, proposal: null } }),
    },
    sessions: { ...actual.sessions, state: (...args: unknown[]) => sessionStateMock(...args) },
  }
})

type OnEvent = (ev: { event: string; data: unknown }) => void

const FAILED_BUILD = {
  state: 'failed',
  stopped: true,
  cycle: 'code',
  pilot_ready: false,
  certified: false,
  phases_done: 2,
  phases_total: 5,
  failed: { gate: 'TESTER', check: 'suite_green', finding: 'a test failed' },
}

function stoppedSession(sid: string) {
  watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
    onProgress(FAILED_BUILD)
  })
  getMock.mockResolvedValue({
    blueprint: { product_name: 'Tank Log', capabilities: [{ id: 'tanks', description: 'Tanks' }] },
    blueprint_approved: true,
    generation: { engine: 'runner', product_id: 'tank_log' },
  })
  render(<Floor sessionId={sid} goPlatforms={() => {}} />)
}

describe('action results in the action bar', () => {
  beforeEach(() => {
    chatStreamMock.mockReset()
    watchBuildMock.mockReset()
    getMock.mockReset()
    downloadMock.mockReset()
    sessionStateMock.mockReset()
    sessionStateMock.mockResolvedValue({ chat_history: [] })
  })

  it('an info event with refused renders its summary in the action bar, with the named next step', async () => {
    chatStreamMock.mockImplementation(async (_s: string, _m: string, onEvent: OnEvent) => {
      onEvent({
        event: 'info',
        data: {
          ok: false,
          refused: 'nothing_to_resume',
          summary: 'This workspace is not on this host.',
          awaiting_action: 'start_over',
        },
      })
    })
    stoppedSession('sess_refused')
    fireEvent.click(await screen.findByTestId('floor-try-again'))
    const result = await screen.findByTestId('floor-action-result')
    expect(result).toHaveAttribute('data-tone', 'refused')
    expect(screen.getByTestId('floor-action-result-text')).toHaveTextContent(
      'Refused: This workspace is not on this host.',
    )
    // The bar sits above the composer: the answer is where the finger was.
    expect(screen.getByTestId('floor-action-bar')).toContainElement(result)
    // The next step is the bar's own action, and it still asks first.
    fireEvent.click(screen.getByTestId('floor-action-result-next'))
    expect(await screen.findByTestId('floor-confirm-sheet')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('floor-action-result-close'))
    expect(screen.queryByTestId('floor-action-result')).not.toBeInTheDocument()
  })

  it('the pressed button shows its busy label while the request runs', async () => {
    let finish: () => void = () => {}
    chatStreamMock.mockImplementation(
      () => new Promise<void>((resolve) => {
        finish = resolve
      }),
    )
    stoppedSession('sess_busy')
    fireEvent.click(await screen.findByTestId('floor-try-again'))
    await waitFor(() => expect(screen.getByTestId('floor-try-again')).toHaveTextContent('Starting…'))
    expect(screen.getByTestId('floor-try-again')).toBeDisabled()
    finish()
    await waitFor(() => expect(screen.getByTestId('floor-action-result-text')).toHaveTextContent('Started again.'))
  })

  it('a refused download shows the server message only, never the raw body', async () => {
    downloadMock.mockRejectedValue(
      new ApiError(429, JSON.stringify({ detail: { error: 'trial_limit_reached', message: 'Free-trial limit reached for export.' } })),
    )
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({ state: 'stalled', phases_done: 2, phases_total: 5 })
    })
    getMock.mockResolvedValue({ blueprint_approved: true, generation: { engine: 'runner', product_id: 'p' } })
    render(<Floor sessionId="sess_dl" goPlatforms={() => {}} />)
    fireEvent.click(await screen.findByTestId('floor-take-copy'))
    const text = await screen.findByTestId('floor-action-result-text')
    expect(text.textContent).toBe('Free-trial limit reached for export.')
    expect(text.textContent).not.toContain('{')
    expect(text.textContent).not.toContain('trial_limit_reached')
  })

  it('Change answers opens the brief sheet in resume mode when the server reopens the intake', async () => {
    chatStreamMock.mockImplementation(async (_s: string, _m: string, onEvent: OnEvent) => {
      onEvent({
        event: 'intake',
        data: { declared: { vertical: 'winery', country: 'NZ', currency: 'NZD' }, proposal: { vertical: 'winery' }, reopened: true },
      })
      onEvent({ event: 'info', data: { ok: true, summary: 'Change any answer.' } })
    })
    stoppedSession('sess_change')
    fireEvent.click(await screen.findByTestId('floor-continue-with-intake'))
    expect(await screen.findByTestId('brief-sheet')).toBeInTheDocument()
    expect(screen.getByTestId('brief-save')).toHaveTextContent('Save and try again')
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument()
  })

  it('Change answers without a reopened intake opens no sheet and says so', async () => {
    chatStreamMock.mockImplementation(async (_s: string, _m: string, onEvent: OnEvent) => {
      onEvent({ event: 'info', data: { ok: true, summary: 'ok' } })
    })
    stoppedSession('sess_noreopen')
    fireEvent.click(await screen.findByTestId('floor-continue-with-intake'))
    await screen.findByTestId('floor-action-result')
    expect(screen.queryByTestId('brief-sheet')).not.toBeInTheDocument()
    expect(screen.getByTestId('floor-action-result')).toHaveAttribute('data-tone', 'refused')
  })

  it('a failed send restores the composer text and offers Try again on the bubble', async () => {
    getMock.mockResolvedValue({})
    chatStreamMock.mockRejectedValueOnce(new ApiError(503, '<html>Service Unavailable</html>'))
    render(<Floor sessionId="sess_send" goPlatforms={() => {}} />)
    const input = screen.getByPlaceholderText(/Try:/)
    fireEvent.change(input, { target: { value: 'A tank log for a winery' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(input).toHaveValue('A tank log for a winery'))
    expect(screen.getByText('The Factory did not answer. Try again in a moment.')).toBeInTheDocument()
    expect(screen.queryByText(/Service Unavailable/)).not.toBeInTheDocument()
    chatStreamMock.mockImplementationOnce(async (_s: string, _m: string, onEvent: OnEvent) => {
      onEvent({ event: 'delta', data: 'Drafting it now.' })
    })
    fireEvent.click(screen.getByTestId('chat-error-retry'))
    await waitFor(() => expect(chatStreamMock).toHaveBeenCalledTimes(2))
    expect(chatStreamMock.mock.calls[1][1]).toBe('A tank log for a winery')
    await waitFor(() => expect(input).toHaveValue(''))
  })

  it('an info reply whose words are not streamed is never an empty bubble', async () => {
    getMock.mockResolvedValue({})
    chatStreamMock.mockImplementation(async (_s: string, _m: string, onEvent: OnEvent) => {
      onEvent({ event: 'info', data: { ok: true, summary: 'Your brief is saved.', stream_delta: false } })
    })
    render(<Floor sessionId="sess_info" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), { target: { value: 'hello there' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Your brief is saved.')).toBeInTheDocument()
  })

  it('a reply with no words and no card leaves no empty bubble', async () => {
    getMock.mockResolvedValue({})
    chatStreamMock.mockImplementation(async () => {})
    const { container } = render(<Floor sessionId="sess_empty" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), { target: { value: 'hello there' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() => expect(chatStreamMock).toHaveBeenCalled())
    await waitFor(() => {
      const empty = Array.from(container.querySelectorAll('.bubble.factory')).filter(
        (b) => !b.textContent?.trim(),
      )
      expect(empty).toHaveLength(0)
    })
  })
})

describe('the result contract (pure)', () => {
  it('an error body with detail.message renders that message only', () => {
    const body = JSON.stringify({ detail: { error: 'trial_limit_reached', counter: 'export', message: 'Free-trial limit reached.' } })
    expect(messageFromBody(body)).toBe('Free-trial limit reached.')
    expect(humanError(new ApiError(429, body), 'fallback')).toBe('Free-trial limit reached.')
  })

  it('a body with only a code or a route falls back to a sentence for the status', () => {
    expect(humanError(new ApiError(409, JSON.stringify({ detail: 'a build is already in progress — poll /product/build-status' })), 'The download could not be made.')).toBe(
      'The download could not be made.',
    )
    expect(humanError(new ApiError(400, JSON.stringify({ detail: 'no_blueprint' })), 'Could not do that.')).toBe('Could not do that.')
    expect(humanError(new ApiError(502, 'Bad Gateway'), 'x')).toBe('The Factory did not answer. Try again in a moment.')
    expect(humanError(new ApiError(429, ''), 'x')).toBe('Too many requests right now. Wait a minute, then try again.')
    expect(humanError(new TypeError('Failed to fetch'), 'x')).toBe('No connection to the Factory. Check your connection, then try again.')
  })

  it('refused says why and names the next step only from awaiting_action', () => {
    expect(actionOutcome('try_again', { ok: false, summary: 'Choose a build level first.', awaiting_action: 'set_build_level' })).toEqual({
      tone: 'refused',
      text: 'Refused: Choose a build level first.',
      next: null,
    })
    expect(actionOutcome('try_again', { ok: false, summary: 'Gone.', awaiting_action: 'start_over' }).next).toBe('start_over')
    expect(actionOutcome('try_again', { ok: false }).text).toBe('Refused by the Factory. Nothing was changed.')
    expect(actionOutcome('start_over', { ok: true, summary: 'Press Start over again.' })).toEqual({
      tone: 'done',
      text: 'Started from scratch.',
      next: null,
    })
    expect(nextActionFor('continue')).toBe('try_again')
    expect(nextActionFor('toString')).toBeNull()
    expect(nextActionFor(undefined)).toBeNull()
  })

  it('a pending action shows its busy label and is disabled; the others keep theirs', () => {
    const plan = buildActions({ build: FAILED_BUILD as never, pending: 'try_again', busy: true })
    const retry = plan.bar.find((a) => a.id === 'try_again')!
    expect(retry.label).toBe('Starting…')
    expect(retry.disabled).toBe('Starting…')
    const change = plan.bar.find((a) => a.id === 'change_answers')!
    expect(change.label).toBe('Change answers')
  })
})
