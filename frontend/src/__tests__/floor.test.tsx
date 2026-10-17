/**
 * Factory Floor contract: the architect LLM drafts, the user approves
 * the feature list, the coding agent takes over and manufactures it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Floor } from '../App'

const chatStreamMock = vi.fn()
const awaitBuildMock = vi.fn()
const watchBuildMock = vi.fn()
const getMock = vi.fn()
const downloadMock = vi.fn()
const coderControlMock = vi.fn()
const getHealthMock = vi.fn()
const approveMock = vi.fn()
const sessionStateMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    chatStream: (...args: unknown[]) => chatStreamMock(...args),
    awaitBuild: (...args: unknown[]) => awaitBuildMock(...args),
    watchBuildStatus: (...args: unknown[]) => watchBuildMock(...args),
    downloadProductPackage: (...args: unknown[]) => downloadMock(...args),
    getHealth: (...args: unknown[]) => getHealthMock(...args),
    product: {
      ...actual.product,
      get: (...args: unknown[]) => getMock(...args),
      coderControl: (...args: unknown[]) => coderControlMock(...args),
      approve: (...args: unknown[]) => approveMock(...args),
    },
    sessions: {
      ...actual.sessions,
      state: (...args: unknown[]) => sessionStateMock(...args),
    },
  }
})

const LLM_BLUEPRINT = {
  product_name: 'Vineyard Platform',
  vertical: 'winery',
  summary: 'Tank, barrel and club operations for a family winery.',
  drafting_mode: 'architect_llm',
  capabilities: [
    { id: 'fermentation_tanks', description: 'Track tanks', strategy_hint: 'GENERATE' },
    { id: 'audit', description: 'Audit trails', strategy_hint: 'REUSE', block_ids: ['audit'] },
  ],
}

describe('Factory Floor — architect LLM then coding agent', () => {
  beforeEach(() => {
    chatStreamMock.mockReset()
    awaitBuildMock.mockReset()
    watchBuildMock.mockReset()
    getMock.mockReset()
    downloadMock.mockReset()
    coderControlMock.mockReset()
    approveMock.mockReset()
    approveMock.mockResolvedValue({ ok: true, blueprint_approved: false })
    coderControlMock.mockResolvedValue({ ok: true, control: { action: 'pause' } })
    sessionStateMock.mockReset()
    sessionStateMock.mockResolvedValue({ chat_history: [] })
    getHealthMock.mockReset()
    getHealthMock.mockResolvedValue({
      factory_code_cli: { available: true, credentials_file_present: true },
    })
    getMock.mockResolvedValue({})
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        activity: 'wrote handler payments',
        last_event: 'wrote handler payments',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        phases_done: 2,
        phases_total: 5,
        next_phase: { id: 'TESTER', label: 'Acceptance inspector' },
        phase_progress: { done: 7, total: 7, fraction: 1, stage: 'handlers' },
        last_event_age_s: 8,
        stale: false,
        coder_log: 'dispatching compiled brief via FACTORY_CODE_CLI\nSTEP 0 inventory ok\n',
        coder_log_present: true,
        coder_control: 'run',
      })
    })
  })

  it('drafts with the architect LLM and labels the blueprint', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: {
          summary: 'Blueprint drafted: Vineyard Platform (winery). Drafted by the architect LLM.',
          blueprint: LLM_BLUEPRINT,
        },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform for a family winery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('architect LLM')).toBeInTheDocument()
    expect(screen.getByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
    expect(chatStreamMock).toHaveBeenCalledWith(
      'sess_ui',
      'Build me a vineyard management platform for a family winery',
      expect.any(Function),
    )
  })

  it('approve starts a coding-agent runner build', async () => {
    const goPlatforms = vi.fn()
    chatStreamMock.mockImplementation(async (_sid: string, _message: string, onEvent: (ev: { event: string; data: unknown }) => void, _v?: unknown, _l?: unknown, typed?: { action: string }) => {
      if (typed?.action === 'approve') {
        onEvent({
          event: 'generation',
          data: {
            summary:
              'The chat LLM started the coding agent. Build started for vineyard: the coding agent has taken over the floor and is writing 2 capability(ies).',
            triggered_by: 'chat_llm',
            generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
          },
        })
        return
      }
      onEvent({
        event: 'blueprint',
        data: { summary: 'Blueprint drafted.', blueprint: LLM_BLUEPRINT },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={goPlatforms} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))
    expect(await screen.findByText('coding agent')).toBeInTheDocument()
    expect(screen.getByText('chat LLM')).toBeInTheDocument()
    expect(await screen.findByRole('status')).toHaveTextContent(/Coding agent has taken over/)
    expect(await screen.findByText('COLLECTOR')).toBeInTheDocument()
    expect(screen.getByText('Binding surveyor')).toBeInTheDocument()
    expect(screen.getByText('WORKERS')).toBeInTheDocument()
    expect(screen.getByText('Platform manufacturer')).toBeInTheDocument()
    expect(screen.getByText('TESTER')).toBeInTheDocument()
    expect(screen.getByText('Acceptance inspector')).toBeInTheDocument()
    expect(screen.getByText('Block stocker')).toBeInTheDocument()
    expect(screen.getByText('Store registrar')).toBeInTheDocument()
    expect(await screen.findByText(/Writing your platform/)).toBeInTheDocument()
    expect(screen.getByText('WORKERS 3/5')).toBeInTheDocument()
    expect(screen.getByText(/then TESTER/)).toBeInTheDocument()
    expect(screen.getByText('7/7 handlers')).toBeInTheDocument()
    expect(screen.getByText(/Last: wrote handler payments/)).toBeInTheDocument()
    expect(screen.getByText(/still working/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/coding agent has taken over/i)).toBeDisabled()
    // The build is still running (WRITER 3/5), so the Floor must not offer the
    // platforms CTA yet — there is no platform to open until the agent lands.
    expect(
      screen.queryByRole('button', { name: 'Open Your Platforms' }),
    ).not.toBeInTheDocument()
    expect(goPlatforms).not.toHaveBeenCalled()
    expect(chatStreamMock).toHaveBeenCalledWith('sess_ui', '', expect.any(Function), undefined, undefined, { action: 'approve' })
    expect(watchBuildMock).toHaveBeenCalled()
  })

  it('labels keyword fallback when the architect LLM did not draft', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: {
          summary: 'Drafted by deterministic templates (no LLM).',
          blueprint: {
            ...LLM_BLUEPRINT,
            drafting_mode: 'keyword_fallback',
            drafting_note: 'LLM drafting disabled',
          },
        },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/template fallback — no LLM/)).toBeInTheDocument()
    expect(screen.getByText(/The architect LLM did not draft this blueprint/)).toBeInTheDocument()
    // F4: the fallback is a visible warning before approval, not a dim aside.
    const warning = screen.getByTestId('bp-drafting-fallback-warning')
    expect(warning).toHaveTextContent(/The architect LLM did not draft/)
    expect(warning).toHaveTextContent(/LLM drafting disabled/)
    expect(warning).toHaveAttribute('role', 'alert')
  })

  it('labels a golden lettings draft as a golden blueprint, not template fallback', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: {
          summary: 'Drafted from the golden residential-lettings blueprint.',
          blueprint: {
            product_name: 'Residential Lettings Platform',
            vertical: 'residential_lettings',
            summary: 'Factory golden for a residential-lettings platform.',
            drafting_mode: 'golden',
            capabilities: [
              { id: 'viewing_management', description: 'Record a viewing', strategy_hint: 'COMPOSE' },
            ],
          },
        },
      })
    })
    render(<Floor sessionId="sess_lettings" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build a platform for residential lettings' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/golden blueprint/)).toBeInTheDocument()
    expect(screen.queryByText(/template fallback — no LLM/)).not.toBeInTheDocument()
    expect(screen.queryByText(/The architect LLM did not draft this blueprint/)).not.toBeInTheDocument()
  })

  it('restores the feature list and coder takeover from the session product', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_hydrate" goPlatforms={() => {}} />)
    expect(await screen.findByText('coding agent')).toBeInTheDocument()
    expect(screen.getByText('chat LLM')).toBeInTheDocument()
    expect(await screen.findByRole('status')).toHaveTextContent(/Coding agent has taken over/)
    expect(screen.getByPlaceholderText(/coding agent has taken over/i)).toBeDisabled()
  })

  it('the pre-build conversation survives a reload', async () => {
    // Live, 2026-09-29 (MEP Construction Platform): after takeover the
    // Floor re-hydrated only two synthetic bubbles -- greeting + takeover
    // card -- and the owner's brief and the whole drafting exchange
    // vanished from the page. The backend kept chat_history the whole
    // time; the Floor just never asked for it.
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    sessionStateMock.mockResolvedValue({
      chat_history: [
        { role: 'user', content: 'Build me a vineyard management platform' },
        { role: 'assistant', content: 'Blueprint drafted: Vineyard Platform (winery).' },
        { role: 'assistant', content: 'The coding agent has taken over the floor.' },
      ],
    })
    render(<Floor sessionId="sess_history" goPlatforms={() => {}} />)
    expect(await screen.findByRole('status')).toHaveTextContent(/Coding agent has taken over/)
    // The owner's brief and the drafting exchange are still on the floor...
    expect(screen.getByText('Build me a vineyard management platform')).toBeInTheDocument()
    expect(screen.getByText(/Blueprint drafted: Vineyard Platform/)).toBeInTheDocument()
    // ...and the takeover line renders once (the card), never twice.
    expect(screen.getAllByText('The coding agent has taken over the floor.')).toHaveLength(1)
  })

  it('restores a pending blueprint after remount so Approve & build is still there', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: false,
    })
    render(<Floor sessionId="sess_pending" goPlatforms={() => {}} />)
    expect(await screen.findByText('architect LLM')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
  })

  it('does not show coder takeover when hydrating a pending draft over leftover generation', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: false,
      generation: { engine: 'runner', product_id: 'old-winery', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_stale_runner" goPlatforms={() => {}} />)
    expect(await screen.findByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('SUCCESS takeover heading is finished only when pilot-ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
        acceptance: { passed: 12, total: 12, ok: true },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'automotive-retail', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_done" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Coding agent finished/ })).toBeInTheDocument()
    expect(screen.getByTestId('floor-product-title')).toHaveTextContent('Vineyard Platform')
    expect(screen.getByTestId('floor-acceptance-score')).toHaveTextContent('12/12')
    expect(screen.getByText('Finished — 22 artifacts; 6 templated. Download ready.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.queryByText(/22 of 28/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent has taken over' })).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('finished heading prefers blueprint.product_name over a generic product_id slug', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 10, agent_written: 6, templated: 4 },
        acceptance: { passed: 12, total: 12, ok: true },
      })
    })
    getMock.mockResolvedValue({
      blueprint: { product_name: 'FinanceOps', vertical: 'finance', drafting_mode: 'architect_llm' },
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'product', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_48a72e0d1cac44ae" goPlatforms={() => {}} />)
    expect(await screen.findByTestId('floor-product-title')).toHaveTextContent('FinanceOps')
    expect(screen.getByTestId('floor-acceptance-score')).toHaveTextContent('12/12')
    const title = screen.getByRole('heading', { name: /FinanceOps/ })
    expect(title).toHaveTextContent('FinanceOps')
    expect(title).toHaveTextContent('12/12')
    expect(title).not.toHaveTextContent(/^product\s+12\/12/i)
    expect(screen.queryByText('product 12/12')).not.toBeInTheDocument()
  })

  it('code-cycle SUCCESS is a prototype, not Finished / Download ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        authorship: { artifacts: 24, agent_written: 11, templated: 13 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_proto" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Code-cycle prototype ready/ })).toBeInTheDocument()
    expect(
      screen.getByText(/Code-cycle prototype — 11 artifacts; 13 templated. Not yet pilot-ready/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Coding agent finished/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Continue to pilot' })).toBeEnabled()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('surfaces CODE_GREEN level_grade — never Finished / founding / Download ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        authorship: { artifacts: 24, agent_written: 11, templated: 13 },
        level_grade: {
          level: 'CODE_GREEN',
          pilot_ready: false,
          founding_customer_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' },
          blockers: ['pilot_ready is false'],
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_code_green" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Code-cycle prototype ready/ })).toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.getByTestId('floor-gate-code')).toHaveTextContent('CODE PASS')
    expect(screen.getByTestId('floor-gate-product')).toHaveTextContent('PRODUCT NOT RUN')
    expect(screen.getByTestId('floor-gate-store')).toHaveTextContent('STORE NOT RUN')
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
  })

  it('sess_cec9a 1449 photograph: thin VetCare never claims Finished / Store-green / Download', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        outcome: 'SUCCESS',
        cycle: 'pilot',
        pilot_ready: false,
        authorship: {
          artifacts: 24,
          agent_written: 6,
          templated: 18,
          action_py: 3,
          agent_artifacts: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
          cli_authored_ids: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
        },
        level_grade: {
          level: 'CODE_GREEN',
          founding_customer_ready: false,
          pilot_ready: false,
          full_pilot: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: {
        product_name: 'VetCare Hub',
        vertical: 'veterinary-care',
        drafting_mode: 'architect_llm',
      },
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_cec9a1345b2049bb" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Code-cycle prototype ready/ })).toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.getByText(/Code-cycle prototype — 6 artifacts; 18 templated/)).toBeInTheDocument()
    expect(screen.getByText(/Not yet pilot-ready/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Coding agent finished/ })).not.toBeInTheDocument()
    expect(screen.queryByText(/Store-green/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Export (.zip) — below full-pilot authorship floor' }),
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Continue to pilot' })).toBeEnabled()
  })

  it('surfaces FOUNDING_CUSTOMER_READY and keeps the gold export enabled', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          founding_customer_ready: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          blockers: [],
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_founding" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Coding agent finished/ })).toBeInTheDocument()
    expect(screen.getByTestId('floor-pilot-ready-pill')).toHaveTextContent('Founding-customer-ready')
    expect(screen.getByTestId('floor-gate-product')).toHaveTextContent('PRODUCT PASS')
    expect(screen.getByTestId('floor-gate-store')).toHaveTextContent('STORE PASS')
    expect(screen.getByText(/Founding-customer-ready. Download ready./)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Continue to pilot' })).not.toBeInTheDocument()
  })

  it('does not gild a STORE_GREEN zip as founding-customer-ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
        level_grade: {
          level: 'STORE_GREEN',
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          founding_customer_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          blockers: ['handlers call the store over HTTP: viewing_management.py'],
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_store" goPlatforms={() => {}} />)
    expect(await screen.findByTestId('floor-pilot-ready-pill')).toHaveTextContent('Store-green')
    expect(screen.getByText(/Store-green zip ready — not founding-customer-ready/)).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
  })

  it('refuses Floor Store-green Download when CLI-failed keep-path is below the authorship floor', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        outcome: 'SUCCESS',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 24, agent_written: 1, templated: 23, action_py: 1 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
        coder_receipt: {
          ok: false,
          blocker: 'FACTORY_CODE_CLI_FAILED',
          detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_c220986f67914681" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Code-cycle prototype ready/ })).toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.queryByText(/Founding-customer-ready\. Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Export (.zip) — below full-pilot authorship floor' }),
    ).toBeDisabled()
  })

  it('refuses Floor Download when CLI-failed card is not actually pilot-ready', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'pilot',
        authorship: { artifacts: 24, agent_written: 1, templated: 23 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
          pilot_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'FAIL', STORE: 'NOT_RUN' },
        },
        coder_receipt: {
          ok: false,
          blocker: 'FACTORY_CODE_CLI_FAILED',
          detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_45729bb662cf4a5d" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(screen.getByText(/FACTORY_CODE_CLI_FAILED: CLI exited 1/)).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    // F5: a gate-failed build offers exactly the three typed choices -- an
    // honest as-is copy, Continue with new answers, Start over.
    expect(screen.getByTestId('floor-take-copy')).toBeEnabled()
    expect(screen.getByTestId('floor-continue-with-intake')).toBeEnabled()
    expect(screen.getByTestId('floor-start-over')).toBeEnabled()
    expect(screen.queryByTestId('floor-rerun-writer')).not.toBeInTheDocument()
  })

  it('sess_45729bb 0639 photograph: thin Store-green Floor never claims founding', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        outcome: 'SUCCESS',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 24, agent_written: 8, templated: 16 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_45729bb662cf4a5d" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Coding agent finished/ })).toBeInTheDocument()
    expect(screen.getByTestId('floor-pilot-ready-pill')).toHaveTextContent('Store-green')
    expect(screen.getByTestId('floor-pilot-ready-pill')).not.toHaveTextContent(
      'Founding-customer-ready',
    )
    expect(screen.getByText(/Pilot-ready — 8 artifacts; 16 templated/)).toBeInTheDocument()
    expect(screen.getByText(/Store-green zip ready — not founding-customer-ready/)).toBeInTheDocument()
    expect(screen.queryByText('Founding-customer-ready', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready\. Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
  })

  it('honesty-locks a founding claim when pilot_ready is false', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        authorship: { artifacts: 10, agent_written: 4, templated: 6 },
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          pilot_ready: false,
          founding_customer_ready: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_lock" goPlatforms={() => {}} />)
    expect(
      await screen.findByRole('heading', { name: /Acceptance 0\/12 — not pilot-ready/ }),
    ).toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.getByText(/Acceptance is 0\/12/)).toBeInTheDocument()
    expect(screen.getByText(/not a failed build/)).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
  })

  it('live sess_4591d5cc 1551 Floor photograph is acceptance-pending, not a failed build', async () => {
    // launching-ready 1551 Floor golden: CODE-CYCLE PROTOTYPE READY heading,
    // CODE/PRODUCT/STORE PASS, Export — acceptance 0/12. That is not a crash.
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        outcome: 'SUCCESS',
        cycle: 'pilot',
        auto_pilot: true,
        pilot_ready: true,
        acceptance: { passed: 0, total: 12, ok: false, missing: true },
        authorship: { artifacts: 27, agent_written: 6, templated: 21 },
        level_grade: {
          level: 'CODE_GREEN',
          founding_customer_ready: false,
          pilot_ready: false,
          full_pilot: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: {
        product_name: 'Residential Lettings Platform',
        vertical: 'residential-lettings',
        drafting_mode: 'architect_llm',
      },
      blueprint_approved: true,
      generation: {
        engine: 'runner',
        product_id: 'residential-lettings',
        triggered_by: 'chat_llm',
      },
    })
    render(<Floor sessionId="sess_4591d5cc45d04fe1" goPlatforms={() => {}} />)
    expect(
      await screen.findByRole('heading', { name: /Acceptance 0\/12 — not pilot-ready/ }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Code-cycle prototype ready/ })).not.toBeInTheDocument()
    expect(screen.getByTestId('floor-prototype-pill')).toHaveTextContent('Code-green (prototype)')
    expect(screen.getByTestId('floor-gate-code')).toHaveTextContent('CODE PASS')
    expect(screen.getByTestId('floor-gate-product')).toHaveTextContent('PRODUCT PASS')
    expect(screen.getByTestId('floor-gate-store')).toHaveTextContent('STORE PASS')
    expect(screen.getByText(/Acceptance is 0\/12/)).toBeInTheDocument()
    expect(screen.getByText(/not a failed build/)).toBeInTheDocument()
    expect(screen.queryByText(/build failed/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export (.zip) — acceptance 0/12' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Continue to pilot' })).toBeEnabled()
  })

  it('Continue to pilot sends continue when auto-pilot is blocked', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'generation',
        data: {
          summary: 'Opening pilot cycle for residential-lettings on the same workspace/hash.',
          triggered_by: 'chat_llm',
          generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
        },
      })
    })
    watchBuildMock
      .mockImplementationOnce(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          auto_pilot: false,
          authorship: { artifacts: 24, agent_written: 11, templated: 13 },
        })
      })
      .mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          current_phase: { id: 'TESTER', label: 'Acceptance inspector' },
          phase_index: 4,
          phase_total: 5,
        })
      })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_cta" goPlatforms={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to pilot' }))
    await waitFor(() => expect(chatStreamMock).toHaveBeenCalledWith('sess_cta', '', expect.any(Function), undefined, undefined, { action: 'run_pilot' }))
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Coding agent finished/ })).not.toBeInTheDocument()
  })

  it('downloads the zip from the Floor after the coding agent finishes', async () => {
    downloadMock.mockResolvedValue(undefined)
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 19, agent_written: 13, templated: 6 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_dl" goPlatforms={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Download platform export (.zip)' }))
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith('sess_dl'))
  })

  it('replaces FINISHED with TAKEN OVER when a pilot generation event arrives', async () => {
    watchBuildMock
      .mockImplementationOnce(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          authorship: { artifacts: 30, agent_written: 11, templated: 19 },
        })
      })
      .mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
          phase_index: 3,
          phase_total: 5,
          last_event: 'wrote handler tenancy_application_pipeline',
          last_event_age_s: 12,
        })
      })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'generation',
        data: {
          summary: 'Opening pilot cycle for residential-lettings on the same workspace/hash.',
          triggered_by: 'chat_llm',
          generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
        },
      })
    })
    render(<Floor sessionId="sess_pilot" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Code-cycle prototype ready/ })).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: '(a) continue the existing lettings hub into its pilot cycle' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Coding agent finished/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByText(/Writing your platform/)).toBeInTheDocument()
  })

  it('keeps only one Open Your Platforms CTA after a second generation card', async () => {
    watchBuildMock
      .mockImplementationOnce(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'succeeded',
          pilot_ready: false,
          cycle: 'code',
          authorship: { artifacts: 30, agent_written: 11, templated: 19 },
        })
      })
      .mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
        onProgress({
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
          phase_index: 3,
          phase_total: 5,
        })
      })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'generation',
        data: {
          summary: 'Opening pilot cycle.',
          triggered_by: 'chat_llm',
          generation: { engine: 'runner', triggered_by: 'chat_llm' },
        },
      })
    })
    render(<Floor sessionId="sess_dup" goPlatforms={() => {}} />)
    // Wait for the succeeded snapshot — otherwise coderActive+null build
    // briefly disables the composer (pilot reopen race).
    expect(await screen.findByRole('heading', { name: /Code-cycle prototype ready/ })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Open Your Platforms' })).toHaveLength(1)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: '(a) continue the existing lettings hub into its pilot cycle' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await screen.findByText('Opening pilot cycle.')
    // The second card is mid-build, so the CTA is hidden rather than
    // duplicated — never more than one, and none while building.
    expect(screen.queryAllByRole('button', { name: 'Open Your Platforms' })).toHaveLength(0)
  })

  it('offers New session on the Floor header', async () => {
    const onNewSession = vi.fn()
    render(
      <Floor sessionId="sess_ui" goPlatforms={() => {}} onNewSession={onNewSession} />,
    )
    const btn = await screen.findByRole('button', { name: 'New session' })
    expect(btn).toBeEnabled()
    fireEvent.click(btn)
    await waitFor(() => expect(onNewSession).toHaveBeenCalledTimes(1))
  })

  it('does not show New session when the control is not provided', async () => {
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'New session' })).not.toBeInTheDocument()
    expect(screen.queryByTestId('floor-factory-cli-status')).not.toBeInTheDocument()
  })

  it('shows Coding agent stopped — not finished Download — when TESTER is red', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        cycle: 'pilot',
        outcome: 'FAILED_BUDGET_SPENT',
        pilot_ready: false,
        detail:
          'rework budget of 3 exhausted; TESTER gate still failing: PRODUCT (pilot-marked suite): suite is red',
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'residential-lettings', triggered_by: 'chat_llm' },
    })
    const onNewSession = vi.fn()
    render(
      <Floor sessionId="sess_red" goPlatforms={() => {}} onNewSession={onNewSession} />,
    )
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(screen.getByText(/rework budget of 3 exhausted/)).toBeInTheDocument()
    expect(screen.queryByText(/taken over the floor/i)).not.toBeInTheDocument()
    expect(screen.queryByText('coding agent')).not.toBeInTheDocument()
    expect(screen.queryByText('chat LLM')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    const choices = screen.getByTestId('floor-failure-choices')
    // Exactly three choices on a FAILED build (the status pill is not one).
    expect(choices.querySelectorAll('button')).toHaveLength(3)
    expect(screen.getByTestId('floor-take-copy')).toBeEnabled()
    expect(screen.getByTestId('floor-continue-with-intake')).toBeEnabled()
    expect(screen.getByTestId('floor-start-over')).toBeEnabled()
    expect(screen.queryByRole('heading', { name: /Coding agent finished/ })).not.toBeInTheDocument()
    // A new product is the header's New session, not a failure choice.
    const startNew = screen.getByRole('button', { name: 'New session' })
    fireEvent.click(startNew)
    await waitFor(() => expect(onNewSession).toHaveBeenCalledTimes(1))
  })

  it('names the FAILED gate, says what Continue will fix, and offers Start over', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        cycle: 'code',
        pilot_ready: false,
        certified: false,
        detail: 'FAILED(STORE_MANAGER, metrics_served, GET /metrics is 404)',
        failed: { gate: 'STORE_MANAGER', check: 'metrics_served', finding: 'GET /metrics is 404' },
        failed_label: 'FAILED(STORE_MANAGER, metrics_served, GET /metrics is 404)',
        next_continue:
          'Continue will resume this platform\'s branch at STORE_MANAGER and send the writer the failing check metrics_served: GET /metrics is 404',
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_failed_platform" goPlatforms={() => {}} />)
    expect(await screen.findByTestId('floor-failed-label')).toHaveTextContent(
      'FAILED(STORE_MANAGER, metrics_served, GET /metrics is 404)',
    )
    expect(screen.getByTestId('floor-next-continue')).toHaveTextContent(/Continue will resume/)
    // Never green: the only export is the labelled as-is copy.
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByTestId('floor-take-copy')).toBeEnabled()
    const startOver = screen.getByTestId('floor-start-over')
    fireEvent.click(startOver)
    await waitFor(() =>
      expect(chatStreamMock).toHaveBeenCalledWith(
        'sess_failed_platform', '', expect.any(Function), undefined, undefined, { action: 'start_over' },
      ),
    )
  })

  it('F5: a FAILED build explains itself and offers three typed choices', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        cycle: 'code',
        pilot_ready: false,
        certified: false,
        detail: 'FAILED(TESTER, product_gate, tide_relay refused its own schema)',
        failed: { gate: 'TESTER', check: 'product_gate', finding: 'tide_relay refused its own schema' },
        failed_label: 'FAILED(TESTER, product_gate, tide_relay refused its own schema)',
        failure_narrative: {
          text: 'What was tried: the run passed COLLECTOR, CLONER and WRITER. Where it stopped: TESTER (product_gate).',
          source: 'ledger',
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'zorblat', triggered_by: 'chat_llm' },
    })
    downloadMock.mockResolvedValue(undefined)
    render(<Floor sessionId="sess_f5" goPlatforms={() => {}} />)
    expect(await screen.findByTestId('floor-failure-narrative')).toHaveTextContent(
      'Where it stopped: TESTER (product_gate).',
    )
    fireEvent.click(screen.getByTestId('floor-take-copy'))
    await waitFor(() =>
      expect(chatStreamMock).toHaveBeenCalledWith(
        'sess_f5', '', expect.any(Function), undefined, undefined, { action: 'take_copy' },
      ),
    )
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith('sess_f5', { asIs: true }))
    fireEvent.click(screen.getByTestId('floor-continue-with-intake'))
    await waitFor(() =>
      expect(chatStreamMock).toHaveBeenCalledWith(
        'sess_f5', '', expect.any(Function), undefined, undefined, { action: 'continue_with_intake' },
      ),
    )
  })

  it('names missing coding-agent credentials from /health on the Floor', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: false,
        blocker: 'FACTORY_CODE_CLI_CREDENTIALS_MISSING',
      },
    })
    render(<Floor sessionId="sess_creds" goPlatforms={() => {}} />)
    const banner = await screen.findByTestId('floor-factory-cli-status')
    expect(banner).toHaveTextContent('Coding agent credentials missing')
    expect(banner).toHaveTextContent('FACTORY_CODE_CLI_CREDENTIALS_MISSING')
    expect(banner).toHaveTextContent('DEEPSEEK_API_KEY')
    expect(banner).toHaveTextContent('FACTORY_CODEWHALE_WRITER=1')
    expect(screen.queryByTestId('floor-factory-cli-status')).toHaveTextContent(/Coding agent credentials/)
  })

  it('does not name Kimi CLI credentials when Cursor BA is the executor', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: false,
        requires_kimi_credentials: false,
        requires_cli: false,
        cursor_ba_available: true,
      },
    })
    render(<Floor sessionId="sess_cursor_ba" goPlatforms={() => {}} />)
    await waitFor(() => expect(getHealthMock).toHaveBeenCalled())
    expect(screen.queryByTestId('floor-factory-cli-status')).not.toBeInTheDocument()
    expect(screen.queryByText(/FACTORY_CODE_CLI_CREDENTIALS_MISSING/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Coding agent credentials missing/)).not.toBeInTheDocument()
  })

  it('names missing coding-agent default_model from /health on the Floor', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: true,
        default_model_configured: false,
        blocker: 'FACTORY_CODE_CLI_NO_MODEL',
      },
    })
    render(<Floor sessionId="sess_no_model" goPlatforms={() => {}} />)
    const banner = await screen.findByTestId('floor-factory-cli-status')
    expect(banner).toHaveTextContent('Coding agent has no model')
    expect(banner).toHaveTextContent('FACTORY_CODE_CLI_NO_MODEL')
    expect(banner).toHaveTextContent('default_model')
    expect(banner).toHaveTextContent('DEEPSEEK_API_KEY')
    expect(screen.queryByText(/CODING AGENT HAS TAKEN OVER/i)).not.toBeInTheDocument()
  })

  it('does not offer a ready Download when the coding agent stalled', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'stalled',
        detail: 'no build activity for 31 min — the build process is gone (restart or redeploy); generate again',
        pilot_ready: false,
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_stall" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent stalled' })).toBeInTheDocument()
    expect(screen.getByTestId('floor-stalled-pill')).toHaveTextContent('Build stalled')
    expect(
      screen.queryByRole('button', { name: 'Download platform export (.zip)' }),
    ).not.toBeInTheDocument()
    // A stalled build is a gate-failed artifact too: retry it, or take the
    // tree honestly labeled as-is — never a "ready" download.
    const exportBtn = screen.getByRole('button', { name: 'Download as-is (failed gates)' })
    expect(exportBtn).toBeEnabled()
    expect(exportBtn).toHaveClass('ghost')
    expect(screen.getByTestId('floor-rerun-writer')).toBeEnabled()
  })

  it('stops the takeover chrome when the ledger is unreadable', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'unknown',
        detail: "LEDGER_UNREADABLE: build_ledger.jsonl:4561 is not a readable ledger event: 'seq'",
        pilot_ready: false,
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'veterinary-care', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_ledger" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    expect(screen.queryByText(/Writing your platform/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Coding agent has taken over' })).not.toBeInTheDocument()
    expect(screen.getByTestId('floor-take-copy')).toBeEnabled()
    expect(screen.getByTestId('floor-continue-with-intake')).toBeEnabled()
  })

  it('keeps coding chrome after Approve while generation SSE and status poll are pending', async () => {
    let releaseApprove: (() => void) | undefined
    const approveGate = new Promise<void>((resolve) => {
      releaseApprove = resolve
    })
    chatStreamMock.mockImplementation(
      async (_sid: string, _message: string, onEvent: (ev: { event: string; data: unknown }) => void, _v?: unknown, _l?: unknown, typed?: { action: string }) => {
        if (typed?.action === 'approve') {
          await approveGate
          onEvent({
            event: 'generation',
            data: {
              summary: 'Build started for residential-lettings.',
              triggered_by: 'chat_llm',
              generation: {
                engine: 'runner',
                product_id: 'residential-lettings',
                triggered_by: 'chat_llm',
              },
            },
          })
          return
        }
        onEvent({
          event: 'blueprint',
          data: {
            summary: 'Drafted from the golden residential-lettings blueprint.',
            blueprint: {
              product_name: 'Residential Lettings Platform',
              vertical: 'residential_lettings',
              summary: 'Factory golden for a residential-lettings platform.',
              drafting_mode: 'golden',
              capabilities: [
                { id: 'viewing_management', description: 'Record a viewing', strategy_hint: 'COMPOSE' },
              ],
            },
          },
        })
      },
    )
    watchBuildMock.mockImplementation(async () => new Promise(() => {}))
    getMock.mockResolvedValue({
      blueprint: {
        product_name: 'Residential Lettings Platform',
        vertical: 'residential_lettings',
        drafting_mode: 'golden',
        capabilities: [
          { id: 'viewing_management', description: 'Record a viewing', strategy_hint: 'COMPOSE' },
        ],
      },
      blueprint_approved: false,
    })
    render(<Floor sessionId="sess_hold" goPlatforms={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Approve & build' }))
    expect(await screen.findByTestId('floor-coder-takeover')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    releaseApprove?.()
    expect(await screen.findByText('coding agent')).toBeInTheDocument()
    // The generation event re-renders the takeover panel; a synchronous query
    // here raced that re-render (CI flake on #697). Await the same element.
    expect(await screen.findByTestId('floor-coder-takeover')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Coding agent finished/ })).not.toBeInTheDocument()
  })

  it('does not offer Floor download while the coding agent is still writing', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _message: string, onEvent: (ev: { event: string; data: unknown }) => void, _v?: unknown, _l?: unknown, typed?: { action: string }) => {
      if (typed?.action === 'approve') {
        onEvent({
          event: 'generation',
          data: {
            summary: 'Build started.',
            triggered_by: 'chat_llm',
            generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
          },
        })
        return
      }
      onEvent({
        event: 'blueprint',
        data: { summary: 'Blueprint drafted.', blueprint: LLM_BLUEPRINT },
      })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
  })

  it('does not gold-label Store-green or founding while COLLECTOR is still running', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        cycle: 'code',
        pilot_ready: false,
        current_phase: { id: 'COLLECTOR', label: 'Binding surveyor' },
        phase_index: 1,
        phase_total: 5,
        last_event: 'collecting capabilities',
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'property-management', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_northbridge" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent has taken over' })).toBeInTheDocument()
    // Watch snapshot paints one tick after takeover chrome; wait for it
    // the same way the WRITER takeover test waits for "Writing your platform".
    expect(await screen.findByText(/Writing your platform/)).toBeInTheDocument()
    expect(screen.getByText('COLLECTOR 1/5')).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Store-green/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('floor-pilot-ready-pill')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Download code-cycle prototype (.zip)' })).not.toBeInTheDocument()
  })

  it('clears coder takeover when a new blueprint is drafted after a runner', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 19, agent_written: 13, templated: 6 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({
        event: 'blueprint',
        data: { summary: 'Blueprint drafted: new tasting room.', blueprint: LLM_BLUEPRINT },
      })
    })
    render(<Floor sessionId="sess_redraft" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: /Coding agent finished/ })).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'build me a tasting room for a family winery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('does not wipe a streamed Floor reply when a leftover chain event arrives', async () => {
    chatStreamMock.mockImplementation(async (_sid: string, _msg: string, onEvent: (ev: { event: string; data: unknown }) => void) => {
      onEvent({ event: 'delta', data: 'Drafting a tasting-room platform for the winery. ' })
      onEvent({ event: 'chain', data: { chain: ['audit'] } })
    })
    render(<Floor sessionId="sess_ui" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'build me a tasting room for a family winery' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByText(/Drafting a tasting-room platform/)).toBeInTheDocument()
    expect(screen.queryByText(/That sounds like kit configuration/)).not.toBeInTheDocument()
  })

  it('renders a one-line already-signed-in notice when provided', async () => {
    render(
      <Floor sessionId="sess_ui" goPlatforms={() => {}} notice="Already signed in." />,
    )
    expect(await screen.findByRole('heading', { name: 'Factory Floor' })).toBeInTheDocument()
    expect(screen.getByText('Already signed in.')).toBeInTheDocument()
    expect(screen.getByPlaceholderText(/Try:/)).toBeEnabled()
  })

  it('hard-disables Send when Factory access is paused — typing does not unlock it', async () => {
    render(<Floor sessionId="sess_paused" goPlatforms={() => {}} accessPaused />)
    const send = await screen.findByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()
    const composer = screen.getByPlaceholderText('Factory access is paused')
    expect(composer).toBeDisabled()
    fireEvent.change(composer, { target: { value: 'Invoice Tracker for a small studio' } })
    expect(send).toBeDisabled()
    fireEvent.click(send)
    expect(chatStreamMock).not.toHaveBeenCalled()
    expect(screen.getByText('Factory access is paused.')).toBeInTheDocument()
  })

  it('does not offer Approve & build on a hydrated draft when access is paused', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: false,
    })
    render(<Floor sessionId="sess_paused_draft" goPlatforms={() => {}} accessPaused />)
    expect(await screen.findByText('Vineyard Platform')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve & build/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByText('Factory access is paused.')).toBeInTheDocument()
  })

  it('streams the coder session log and Pause/Stop call coder-control', async () => {
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_monitor" goPlatforms={() => {}} />)
    expect(await screen.findByTestId('floor-coder-log')).toBeInTheDocument()
    expect(screen.getByTestId('floor-coder-log')).toHaveTextContent('STEP 0 inventory ok')
    expect(screen.getByTestId('floor-coder-pause')).toBeEnabled()
    expect(screen.getByTestId('floor-coder-stop')).toBeEnabled()
    fireEvent.click(screen.getByTestId('floor-coder-pause'))
    await waitFor(() =>
      expect(coderControlMock).toHaveBeenCalledWith('sess_monitor', 'pause'),
    )
    fireEvent.click(screen.getByTestId('floor-coder-stop'))
    await waitFor(() =>
      expect(coderControlMock).toHaveBeenCalledWith('sess_monitor', 'stop'),
    )
  })

  it('names the failed stage and its reason when build-status carries a failure', async () => {
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        detail: 'writer_no_output: zero agent-authored artifacts',
        failure: {
          phase: 'WRITER',
          location: 'WRITER',
          reason: 'writer_no_output',
          detail: 'zero agent-authored artifacts in the workspace',
        },
        phase_trail: [
          { phase: 'COLLECTOR', outcome: 'passed' },
          { phase: 'CLONER', outcome: 'passed' },
          {
            phase: 'WRITER',
            outcome: 'failed',
            reason: 'writer_no_output',
            location: 'WRITER',
            detail: 'zero agent-authored artifacts',
          },
          { phase: 'TESTER', outcome: 'not_reached' },
          { phase: 'STORE_MANAGER', outcome: 'not_reached' },
        ],
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'retal', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_f4_failure" goPlatforms={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Coding agent stopped' })).toBeInTheDocument()
    // The red stage is the one the user was watching.
    expect(screen.getByTestId('floor-phase-failed-WRITER')).toBeInTheDocument()
    // The pill names the phase, not a generic "pilot failed".
    expect(screen.getByTestId('floor-failed-pill')).toHaveTextContent('WRITER failed')
    // The reason token is rendered, with the gate's own words.
    expect(screen.getByTestId('floor-failure-line')).toHaveTextContent(
      'WRITER failed — writer_no_output: zero agent-authored artifacts in the workspace',
    )
  })

  it('streams the coding agent narration as a live log, not one sentence', async () => {
    // One replaced sentence cannot tell a working agent from a wedged one.
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        last_event: 'writer: STEP 25: final pass on the staged tree',
        last_event_age_s: 9,
        stale: false,
        activity_log: [
          { ts: '2026-09-19T10:00:00Z', role: 'WRITER', text: 'writer: STEP 0: inventory read' },
          { ts: '2026-09-19T10:04:00Z', role: 'WRITER', text: 'writer: STEP 10: rework round 1' },
          { ts: '2026-09-19T10:09:00Z', role: 'WRITER', text: 'writer: STEP 25: final pass on the staged tree' },
        ],
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_activity" goPlatforms={() => {}} />)

    const log = await screen.findByTestId('floor-activity-log')
    // The whole pass is visible, not just the newest line.
    expect(log).toHaveTextContent('STEP 0: inventory read')
    expect(log).toHaveTextContent('STEP 10: rework round 1')
    expect(log).toHaveTextContent('STEP 25: final pass on the staged tree')
    // The single-line fallback is replaced, not doubled up.
    expect(screen.queryByText(/^Last: /)).not.toBeInTheDocument()
  })

  it('shows a queued writer its place for a build slot', async () => {
    // Owner, 2026-10-06: a full build slot queues, never fails -- and the
    // Floor says where the build stands in the queue.
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        last_event: 'waiting for a build slot -- position 2 (1 ahead)',
        last_event_age_s: 4,
        stale: false,
        queued: { position: 2, ahead: 1, since: '2026-10-06T10:00:00Z', waited_s: 30 },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_queued" goPlatforms={() => {}} />)

    const queued = await screen.findByText(/Waiting for a build slot — position 2/)
    expect(queued).toHaveTextContent('Waiting for a build slot — position 2 (1 ahead)')
  })

  it('shows a TESTER mid-fail as an orange rework, red only when the run stops', async () => {
    // Live automotive build: TESTER went suite_red, the writer took the work
    // back (WORKERS 3/5 still moving) -- and the Floor painted TESTER the same
    // red as a dead build. A rework round is the machine working, not a stop:
    // orange while the run is alive, red only when state is failed/stalled.
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'building',
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        completed: ['COLLECTOR', 'CLONER'],
        failure: {
          phase: 'TESTER',
          location: 'TESTER',
          reason: 'suite_red',
          detail: 'acceptance suite is red: 3 checks failing',
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'automotive', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_rework_orange" goPlatforms={() => {}} />)

    // Orange rework chip, not the red failed chip.
    const chip = await screen.findByTestId('floor-phase-rework-TESTER')
    expect(chip.className).toContain('rework')
    expect(screen.queryByTestId('floor-phase-failed-TESTER')).not.toBeInTheDocument()
    // The note says the work went back to the coder; no red alert.
    const note = screen.getByTestId('floor-rework-line')
    expect(note).toHaveTextContent('TESTER sent it back to the coder')
    expect(note).toHaveTextContent('suite_red')
    expect(screen.queryByTestId('floor-failure-line')).not.toBeInTheDocument()
  })

  it('shows a failure the run recovered from as history, not a red alert', async () => {
    // sess_617f60024df24a4e: a 13/13 build showed "TESTER failed -- KeyError"
    // in red for a KeyError the agent had already fixed in rework.
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        cycle: 'pilot',
        pilot_ready: true,
        completed: ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER'],
        acceptance: { passed: 13, total: 13, ok: true },
        failure: null,
        recovered_failure: {
          phase: 'TESTER',
          location: 'TESTER',
          reason: 'suite_red',
          detail: "suite is red: KeyError: 'branch_and_consolidated_operations'",
        },
      })
    })
    getMock.mockResolvedValue({
      blueprint: LLM_BLUEPRINT,
      blueprint_approved: true,
      generation: { engine: 'runner', product_id: 'bakery', triggered_by: 'chat_llm' },
    })
    render(<Floor sessionId="sess_recovered" goPlatforms={() => {}} />)

    const note = await screen.findByTestId('floor-recovered-line')
    expect(note).toHaveTextContent('Recovered in rework')
    expect(note).toHaveTextContent('TESTER')
    // No red alert, no red phase chip, on a build that succeeded.
    expect(screen.queryByTestId('floor-failure-line')).not.toBeInTheDocument()
    expect(screen.queryByTestId('floor-phase-failed-TESTER')).not.toBeInTheDocument()
  })

  it('keeps the drafted name on the build header after approve', async () => {
    // The name is shown in the finished header ("<name> 13/13 -- ...").
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        cycle: 'pilot',
        pilot_ready: true,
        completed: ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER'],
        acceptance: { passed: 13, total: 13, ok: true },
      })
    })
    // bakery-operations: a session started on the Floor loads its design on
    // mount (empty), and after approve the newest card is the generation
    // card, which carries no blueprint -- the header read "Untitled platform".
    chatStreamMock.mockImplementation(async (_sid: string, _message: string, onEvent: (ev: { event: string; data: unknown }) => void, _v?: unknown, _l?: unknown, typed?: { action: string }) => {
      if (typed?.action === 'approve') {
        onEvent({
          event: 'generation',
          data: {
            summary: 'Build started for vineyard.',
            triggered_by: 'chat_llm',
            generation: { engine: 'runner', product_id: 'vineyard', triggered_by: 'chat_llm' },
          },
        })
        return
      }
      onEvent({ event: 'blueprint', data: { summary: 'Blueprint drafted.', blueprint: LLM_BLUEPRINT } })
    })
    render(<Floor sessionId="sess_title" goPlatforms={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText(/Try:/), {
      target: { value: 'Build me a vineyard management platform' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled(),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))

    const title = await screen.findByTestId('floor-product-title')
    expect(title).toHaveTextContent('Vineyard Platform')
    expect(title).not.toHaveTextContent('Untitled platform')
  })
})
