/**
 * PR-06: the edit-brief sheet. One Save sends all four answers in one
 * request; Cancel sends nothing; an unknown code and half a pair each show an
 * error; the card shows only what the server returned. All values below are
 * invented test data.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { BriefSheet, checkDraft, draftFrom } from '../briefSheet'
import type { CodeOption } from '../localePicker'

const getMock = vi.fn()
const verticalsMock = vi.fn()
const sessionStateMock = vi.fn()
const getHealthMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
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

const KITS = [{ vertical: 'zorblat_yards', kit: 'zorblat', build_ready: true }]
const REGIONS: CodeOption[] = [
  { code: 'ZQ', name: 'Zorblat Isles' },
  { code: 'ZR', name: 'Quux Reach' },
]
const CURRENCIES: CodeOption[] = [{ code: 'ZQX', name: 'Zorblat mark' }]

function sheet(props: Partial<Parameters<typeof BriefSheet>[0]> = {}) {
  const onSave = vi.fn()
  const onClose = vi.fn()
  render(
    <BriefSheet
      initial={{ vertical: 'zorblat_yards', country: 'NO', currency: 'NOK', build_level: 'light' }}
      verticals={KITS}
      onSave={onSave}
      onClose={onClose}
      {...props}
    />,
  )
  return { onSave, onClose }
}

describe('BriefSheet', () => {
  it('puts every label above its field: level cards, industry chips, country, currency', () => {
    sheet()
    const dialog = screen.getByRole('dialog', { name: 'Your brief' })
    expect(within(dialog).getAllByRole('radio')).toHaveLength(4)
    expect(within(dialog).getByRole('radio', { name: /Light/ })).toBeChecked()
    expect(within(dialog).getByLabelText('Country')).toHaveValue('Norway (NO)')
    expect(within(dialog).getByLabelText('Currency')).toHaveValue('Norwegian Krone (NOK)')
    expect(within(dialog).getByRole('button', { name: 'Zorblat yards' })).toHaveAttribute('aria-pressed', 'true')
  })

  it.each([
    ['prototype', 'SE', 'SEK'],
    ['production', 'JP', 'JPY'],
  ])('Save sends one request with all four values (%s)', (level, country, currency) => {
    const { onSave } = sheet()
    fireEvent.click(screen.getByRole('radio', { name: new RegExp(level, 'i') }))
    fireEvent.change(screen.getByLabelText('Country'), { target: { value: country.toLowerCase() } })
    fireEvent.change(screen.getByLabelText('Currency'), { target: { value: currency } })
    fireEvent.click(screen.getByRole('button', { name: 'General' }))
    fireEvent.click(screen.getByTestId('brief-save'))
    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith({ vertical: '', country, currency, build_level: level })
  })

  it.each(['Cancel', 'Close'])('%s sends nothing and closes', (name) => {
    const { onSave, onClose } = sheet()
    fireEvent.change(screen.getByLabelText('Country'), { target: { value: 'SE' } })
    fireEvent.click(screen.getByRole('button', { name }))
    expect(onSave).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('Escape closes the sheet and sends nothing', () => {
    const { onSave, onClose } = sheet()
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onSave).not.toHaveBeenCalled()
  })

  it.each([
    ['Country', 'QQ', /Not a country this browser knows/],
    ['Currency', 'QQQ', /Not a currency this browser knows/],
  ])('an unknown %s code shows an error and Save waits', (label, code, message) => {
    const { onSave } = sheet()
    fireEvent.change(screen.getByLabelText(label), { target: { value: code } })
    expect(screen.getByText(message)).toBeInTheDocument()
    expect(screen.getByTestId('brief-save')).toBeDisabled()
    expect(screen.getByTestId('brief-save-reason')).toHaveTextContent('Fix the marked answer to save.')
    fireEvent.click(screen.getByTestId('brief-save'))
    expect(onSave).not.toHaveBeenCalled()
  })

  it.each([
    ['Country', 'Currency'],
    ['Currency', 'Country'],
  ])('half a pair (only %s) shows an error', (kept, cleared) => {
    sheet({ initial: {} })
    fireEvent.change(screen.getByLabelText(kept), { target: { value: kept === 'Country' ? 'SE' : 'SEK' } })
    expect(screen.getByLabelText(cleared)).toHaveValue('')
    expect(screen.getByTestId('pair-error')).toHaveTextContent('Set both a country and a currency, or neither.')
    expect(screen.getByTestId('brief-save')).toBeDisabled()
  })

  it.each([
    [false, 'Save', 'Nothing changed yet.'],
    [true, 'Save and try again', 'Nothing changed yet.'],
  ])('with nothing changed Save waits and says why (resume %s)', (resume, label, reason) => {
    sheet({ resume })
    expect(screen.getByTestId('brief-save')).toHaveTextContent(label)
    expect(screen.getByTestId('brief-save')).toBeDisabled()
    expect(screen.getByTestId('brief-save-reason')).toHaveTextContent(reason)
  })

  it('shows the server refusal inside the sheet', () => {
    sheet({ error: 'The blueprint is approved: nothing was changed.' })
    expect(screen.getByTestId('sheet-refused')).toHaveTextContent('nothing was changed')
  })
})

describe('checkDraft and draftFrom', () => {
  it.each([
    [{ country: 'ZQ', currency: 'ZQX' }, 'Zorblat Isles (ZQ)', 'Zorblat mark (ZQX)'],
    [{ country: 'zr', currency: null }, 'Quux Reach (ZR)', ''],
  ])('opens on the server answers by name and code: %j', (initial, countryText, currencyText) => {
    const draft = draftFrom(initial, REGIONS, CURRENCIES)
    expect([draft.countryText, draft.currencyText]).toEqual([countryText, currencyText])
  })

  it.each([
    [{ countryText: 'Zorblat Isles (ZQ)', currencyText: 'zqx' }, false],
    [{ countryText: 'Quux Reach', currencyText: 'ZQX' }, true],
  ])('reads changed against the server answers: %j', (texts, changed) => {
    const initial = { country: 'ZQ', currency: 'ZQX', build_level: 'pilot' }
    const draft = { ...draftFrom(initial, REGIONS, CURRENCIES), ...texts }
    const result = checkDraft(draft, initial, REGIONS, CURRENCIES)
    expect(result.changed).toBe(changed)
    expect(result.countryError ?? result.currencyError ?? result.pairError).toBeNull()
  })
})

describe('the Floor: suggest, change, save', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    getMock.mockReset()
    verticalsMock.mockReset()
    sessionStateMock.mockReset()
    getHealthMock.mockReset()
    fetchMock.mockReset()
    getHealthMock.mockResolvedValue({ factory_code_cli: { available: true, credentials_file_present: true } })
    getMock.mockResolvedValue({ session_id: 'sess_sheet' })
    sessionStateMock.mockResolvedValue({ chat_history: [{ role: 'user', content: 'A ledger for dock bookings.' }] })
    verticalsMock.mockResolvedValue({
      verticals: KITS,
      chosen: null,
      default: '',
      intake: { declared: {}, proposal: { country: 'NO', currency: 'NOK', build_level: 'light' } },
    })
  })

  it.each([
    ['SE', 'SEK', 'Sweden'],
    ['JP', 'JPY', 'Japan'],
  ])('one request carries all four answers and Confirm; the card shows the server reply (%s)', async (country, currency, name) => {
    fetchMock.mockImplementation(async (_url: string, init?: RequestInit) => {
      const sent = JSON.parse(String(init?.body))
      const intake = {
        declared: {
          vertical: sent.vertical || null,
          country: sent.country,
          currency: sent.currency,
          build_level: sent.build_level,
        },
        proposal: null,
      }
      const body =
        `event: intake\ndata: ${JSON.stringify(JSON.stringify(intake))}\n\n` +
        `event: info\ndata: ${JSON.stringify(JSON.stringify({ ok: true, summary: 'Confirmed.' }))}\n\n` +
        'event: done\ndata: \n\n'
      return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
    })
    vi.stubGlobal('fetch', fetchMock)
    const { Floor } = await import('../App')
    render(<Floor sessionId="sess_sheet" goPlatforms={() => {}} />)
    await waitFor(() => expect(screen.getByTestId('intake-proposal')).toBeInTheDocument())
    fireEvent.click(screen.getByTestId('intake-change'))
    const dialog = screen.getByRole('dialog', { name: 'Your brief' })
    // Pre-filled with the suggestion.
    expect(within(dialog).getByLabelText('Country')).toHaveValue('Norway (NO)')
    fireEvent.change(within(dialog).getByLabelText('Country'), { target: { value: country } })
    fireEvent.change(within(dialog).getByLabelText('Currency'), { target: { value: currency } })
    fireEvent.click(within(dialog).getByTestId('brief-save'))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    const chats = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/chat'))
    expect(chats).toHaveLength(1)
    expect(JSON.parse(String(chats[0][1]?.body))).toEqual({
      message: '',
      action: 'confirm_intake',
      vertical: '',
      country,
      currency,
      build_level: 'light',
    })
    expect(screen.getByTestId('brief-values')).toHaveTextContent(name)
    expect(screen.getByTestId('brief-values')).toHaveTextContent(currency)
    vi.unstubAllGlobals()
  })
})
