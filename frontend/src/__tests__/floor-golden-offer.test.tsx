/**
 * F-4b (H1 Floor UI): a golden match is OFFERED on the drafted blueprint,
 * never swapped in. The Floor shows the offer on the blueprint card and the
 * user resolves it: Accept calls the accept-golden route and re-renders the
 * card with the returned blueprint; Decline calls decline-golden and keeps
 * the user's draft. Values come from the stream / route responses, not from
 * constants in the component, so two different offers render two results.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { BlueprintCard, Floor } from '../App'

const chatStreamMock = vi.fn()
const getMock = vi.fn()
const getHealthMock = vi.fn()
const sessionStateMock = vi.fn()
const acceptGoldenMock = vi.fn()
const declineGoldenMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    chatStream: (...args: unknown[]) => chatStreamMock(...args),
    getHealth: (...args: unknown[]) => getHealthMock(...args),
    product: {
      ...actual.product,
      get: (...args: unknown[]) => getMock(...args),
      acceptGolden: (...args: unknown[]) => acceptGoldenMock(...args),
      declineGolden: (...args: unknown[]) => declineGoldenMock(...args),
    },
    sessions: {
      ...actual.sessions,
      state: (...args: unknown[]) => sessionStateMock(...args),
    },
  }
})

function draftWithOffer(name: string, goldenId: string, goldenName: string, overlap: number) {
  return {
    product_name: name,
    vertical: 'v_' + goldenId,
    summary: 'User draft for ' + name,
    drafting_mode: 'architect_llm',
    capabilities: [{ id: 'cap_' + goldenId, description: 'Cap', strategy_hint: 'GENERATE' }],
    golden_suggestion: {
      golden_id: goldenId,
      golden_name: goldenName,
      overlap,
      drafting_mode: 'golden',
      label: 'Matching golden offered',
    },
  }
}

async function renderWithDraft(sid: string, draft: ReturnType<typeof draftWithOffer>) {
  chatStreamMock.mockImplementation(
    async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({ event: 'blueprint', data: { summary: 'Blueprint drafted.', blueprint: draft } })
    },
  )
  render(<Floor sessionId={sid} goPlatforms={() => {}} />)
  fireEvent.change(screen.getByPlaceholderText(/Try:/), { target: { value: 'Build me a platform' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))
  return screen.findByTestId('bp-golden-offer')
}

describe('Floor golden offer (F-4b)', () => {
  beforeEach(() => {
    for (const m of [chatStreamMock, getMock, getHealthMock, sessionStateMock, acceptGoldenMock, declineGoldenMock]) {
      m.mockReset()
    }
    sessionStateMock.mockResolvedValue({ chat_history: [] })
    getHealthMock.mockResolvedValue({ factory_code_cli: { available: true, credentials_file_present: true } })
    getMock.mockResolvedValue({})
  })

  it('shows the offered golden from the stream (two offers, two renders)', async () => {
    const offer = await renderWithDraft('sess_a', draftWithOffer('Alpha Ops', 'g_alpha', 'Alpha Golden', 0.8))
    expect(offer).toHaveTextContent('Alpha Golden')
    expect(offer).toHaveTextContent('80% overlap')
    expect(offer).toHaveTextContent('Matching golden offered')
  })

  it('a different offer renders different text', async () => {
    const offer = await renderWithDraft('sess_b', draftWithOffer('Beta Ops', 'g_beta', 'Beta Golden', 0.65))
    expect(offer).toHaveTextContent('Beta Golden')
    expect(offer).toHaveTextContent('65% overlap')
    expect(offer).not.toHaveTextContent('Alpha Golden')
  })

  it('Accept calls accept-golden for this session and renders the returned blueprint', async () => {
    acceptGoldenMock.mockResolvedValue({
      ok: true,
      source: 'golden',
      blueprint: { product_name: 'Alpha Golden Platform', vertical: 'v_g_alpha', capabilities: [], golden_suggestion: null },
    })
    await renderWithDraft('sess_acc', draftWithOffer('Alpha Ops', 'g_alpha', 'Alpha Golden', 0.8))
    fireEvent.click(screen.getByTestId('bp-accept-golden'))
    await waitFor(() => expect(acceptGoldenMock).toHaveBeenCalledWith('sess_acc'))
    expect(declineGoldenMock).not.toHaveBeenCalled()
    expect(await screen.findByText(/Golden accepted/)).toBeInTheDocument()
    expect(await screen.findByText(/Alpha Golden Platform/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByTestId('bp-golden-offer')).not.toBeInTheDocument())
  })

  it('Decline calls decline-golden for this session and keeps the user draft', async () => {
    declineGoldenMock.mockResolvedValue({
      ok: true,
      source: 'drafted',
      blueprint: { product_name: 'Beta Ops', vertical: 'v_g_beta', capabilities: [], golden_suggestion: null },
    })
    await renderWithDraft('sess_dec', draftWithOffer('Beta Ops', 'g_beta', 'Beta Golden', 0.65))
    fireEvent.click(screen.getByTestId('bp-decline-golden'))
    await waitFor(() => expect(declineGoldenMock).toHaveBeenCalledWith('sess_dec'))
    expect(acceptGoldenMock).not.toHaveBeenCalled()
    expect(await screen.findByText(/Golden declined/)).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByTestId('bp-golden-offer')).not.toBeInTheDocument())
    expect(screen.queryByText(/Golden accepted/)).not.toBeInTheDocument()
  })

  it('a failed resolve surfaces the error and keeps the offer', async () => {
    acceptGoldenMock.mockRejectedValue(new Error('409 nothing to accept'))
    await renderWithDraft('sess_err', draftWithOffer('Alpha Ops', 'g_alpha', 'Alpha Golden', 0.8))
    fireEvent.click(screen.getByTestId('bp-accept-golden'))
    expect(await screen.findByText(/409 nothing to accept/)).toBeInTheDocument()
    // second session id -> second call argument (not a constant)
    expect(acceptGoldenMock).toHaveBeenCalledWith('sess_err')
    expect(screen.getByTestId('bp-golden-offer')).toBeInTheDocument()
  })
})

describe('BlueprintCard golden offer visibility', () => {
  const base = { product_name: 'P', capabilities: [{ id: 'c', description: 'C', strategy_hint: 'GENERATE' }] }
  const sug = { golden_id: 'g', golden_name: 'G', overlap: 0.9, drafting_mode: 'golden', label: 'Offered' }
  const noop = () => {}

  it('no offer card without a golden_suggestion', () => {
    render(<BlueprintCard blueprint={base} busy={false} onApprove={noop} onRefine={noop} onAcceptGolden={noop} onDeclineGolden={noop} />)
    expect(screen.queryByTestId('bp-golden-offer')).not.toBeInTheDocument()
  })

  it('no offer card while access is paused', () => {
    render(<BlueprintCard blueprint={{ ...base, golden_suggestion: sug }} busy={false} onApprove={noop} onRefine={noop} onAcceptGolden={noop} onDeclineGolden={noop} accessPaused />)
    expect(screen.queryByTestId('bp-golden-offer')).not.toBeInTheDocument()
  })

  it('buttons are disabled while busy', () => {
    render(<BlueprintCard blueprint={{ ...base, golden_suggestion: sug }} busy onApprove={noop} onRefine={noop} onAcceptGolden={noop} onDeclineGolden={noop} />)
    expect(screen.getByTestId('bp-accept-golden')).toBeDisabled()
    expect(screen.getByTestId('bp-decline-golden')).toBeDisabled()
  })
})
