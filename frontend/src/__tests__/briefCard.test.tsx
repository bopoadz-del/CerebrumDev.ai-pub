/**
 * PR-04: the "Your brief" card, pinned at the top of the Floor in every state.
 * All values below are invented test data; the card reads only what it is given.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { BriefCard, briefStatus, nameWithCode, type BriefStatus } from '../briefCard'
import type { IntakeState } from '../api/factory'

const getMock = vi.fn()
const verticalsMock = vi.fn()
const sessionStateMock = vi.fn()
const watchBuildMock = vi.fn()
const getHealthMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    watchBuildStatus: (...args: unknown[]) => watchBuildMock(...args),
    getHealth: (...args: unknown[]) => getHealthMock(...args),
    product: {
      ...actual.product,
      get: (...args: unknown[]) => getMock(...args),
      verticals: (...args: unknown[]) => verticalsMock(...args),
    },
    sessions: {
      ...actual.sessions,
      state: (...args: unknown[]) => sessionStateMock(...args),
    },
  }
})

const DECLARED = { vertical: 'shipyards', country: 'NO', currency: 'NOK', build_level: 'light' }

function brief(intake: IntakeState, status: BriefStatus, features: string[] | null = null, approved = false) {
  return render(
    <BriefCard
      intake={intake}
      status={status}
      features={features}
      featuresApproved={approved}
      productName="Dock Ledger"
      delivery="zip"
      firstAsk="A ledger for dock bookings."
      onTyped={vi.fn()}
      onEdit={vi.fn()}
    />,
  )
}

describe('briefStatus: one word for the state of the brief', () => {
  const declared: IntakeState = { declared: DECLARED, proposal: null }
  it.each([
    [{ intake: { declared: {}, proposal: null }, approved: false }, 'draft'],
    [{ intake: { declared: {}, proposal: { country: 'NO' } }, approved: false }, 'suggested by chat'],
    [{ intake: declared, approved: true, buildState: 'building' }, 'locked'],
    [{ intake: declared, approved: true, buildState: 'failed' }, 'stopped'],
    [{ intake: declared, approved: true, buildState: 'stalled' }, 'stopped'],
    [{ intake: declared, approved: true, buildState: 'succeeded' }, 'built'],
    [{ intake: { ...declared, reopened: true }, approved: true, buildState: 'failed' }, 'draft'],
  ] as const)('%o -> %s', (input, want) => {
    expect(briefStatus(input as Parameters<typeof briefStatus>[0])).toBe(want)
  })
})

describe('the brief card', () => {
  it.each(['draft', 'suggested by chat', 'locked', 'stopped', 'built'] as BriefStatus[])(
    'renders in the %s state with its status word and values, never greyed',
    (status) => {
      brief({ declared: DECLARED, proposal: null }, status, ['dock_bookings'], status !== 'draft')
      const card = screen.getByTestId('brief-card')
      expect(screen.getByTestId('brief-status')).toHaveTextContent(status)
      expect(screen.getByTestId('brief-values')).toHaveTextContent('Shipyards')
      expect(screen.getByTestId('brief-values')).toHaveTextContent('NOK')
      expect(screen.getByTestId('brief-values')).toHaveTextContent('Light')
      expect(card.className).not.toMatch(/\bdim\b/)
      const editable = status === 'draft' || status === 'suggested by chat'
      expect(screen.queryByTestId('intake-change') !== null).toBe(editable)
    },
  )

  it('names appear with codes, from the browser data', () => {
    brief({ declared: DECLARED, proposal: null }, 'locked', ['dock_bookings'], true)
    fireEvent.click(screen.getByTestId('brief-expand'))
    expect(screen.getByTestId('brief-country')).toHaveTextContent(nameWithCode('region', 'NO'))
    expect(screen.getByTestId('brief-country')).toHaveTextContent('(NO)')
    expect(screen.getByTestId('brief-currency')).toHaveTextContent('(NOK)')
    // The browser knows these codes, so a name sits beside each code.
    expect(nameWithCode('region', 'NO')).not.toBe('NO')
    expect(nameWithCode('currency', 'NOK')).not.toBe('NOK')
  })

  it('a missing required answer reads "Not set"', () => {
    brief({ declared: { vertical: null, country: 'NO', currency: null, build_level: null }, proposal: null }, 'draft')
    expect(screen.getByTestId('brief-missing')).toHaveTextContent('Not set: currency, build level')
    fireEvent.click(screen.getByTestId('brief-expand'))
    expect(screen.getByTestId('brief-currency')).toHaveTextContent('Not set')
    expect(screen.getByTestId('brief-build_level')).toHaveTextContent('Not set')
    expect(screen.getByTestId('brief-vertical')).toHaveTextContent('General')
  })

  it('a suggestion is its own strip and does not replace the confirmed values', () => {
    brief({ declared: DECLARED, proposal: { country: 'IS' } }, 'suggested by chat')
    expect(screen.getByTestId('intake-proposal')).toHaveTextContent('(IS)')
    expect(screen.getByTestId('brief-values')).not.toHaveTextContent('IS')
    expect(screen.getByRole('button', { name: 'Confirm these' })).toBeEnabled()
  })

  it('lists the features by name with the count, and what the user asked for', () => {
    brief({ declared: DECLARED, proposal: null }, 'locked', ['dock_bookings', 'berth_plan'], true)
    expect(screen.getByTestId('brief-feature-count')).toHaveTextContent('2 features')
    fireEvent.click(screen.getByTestId('brief-expand'))
    const list = screen.getByTestId('brief-features')
    expect(within(list).getByText('Dock bookings')).toBeInTheDocument()
    expect(within(list).getByText('Berth plan')).toBeInTheDocument()
    expect(screen.getByTestId('brief-details')).toHaveTextContent('A ledger for dock bookings.')
  })
})

describe('the Floor restores the approved features from GET /product', () => {
  beforeEach(() => {
    getMock.mockReset()
    verticalsMock.mockReset()
    sessionStateMock.mockReset()
    watchBuildMock.mockReset()
    getHealthMock.mockReset()
    getHealthMock.mockResolvedValue({ factory_code_cli: { available: true, credentials_file_present: true } })
    sessionStateMock.mockResolvedValue({ chat_history: [{ role: 'user', content: 'A ledger for dock bookings.' }] })
    verticalsMock.mockResolvedValue({
      verticals: [],
      chosen: DECLARED.vertical,
      default: '',
      intake: { declared: DECLARED, proposal: null },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({ state: 'building', phases_done: 2, phases_total: 5 })
    })
    getMock.mockResolvedValue({
      blueprint: {
        product_name: 'Dock Ledger',
        capabilities: [{ id: 'dock_bookings' }, { id: 'berth_plan' }, { id: 'audit' }],
      },
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'dock-ledger', triggered_by: 'approve' },
    })
  })

  it('after a reload the brief is locked and lists the saved feature list', async () => {
    const { Floor } = await import('../App')
    render(<Floor sessionId="sess_brief" goPlatforms={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('brief-feature-count')).toHaveTextContent('3 features'))
    expect(screen.getByTestId('brief-status')).toHaveTextContent('locked')
    expect(screen.getByTestId('brief-values')).toHaveTextContent('NOK')
    expect(screen.queryByTestId('intake-change')).toBeNull()
    expect(screen.getByTestId('brief-lock')).toBeInTheDocument()
    fireEvent.click(screen.getByTestId('brief-expand'))
    const list = screen.getByTestId('brief-features')
    expect(within(list).getByText('Berth plan')).toBeInTheDocument()
    expect(within(list).getByText('Audit')).toBeInTheDocument()
  })
})
