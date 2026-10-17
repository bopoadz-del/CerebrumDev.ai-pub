/**
 * Your Platforms contract: a runner build is a background coding-agent job.
 * The page must show that work, name who wrote the artifacts, and refuse
 * to download a failed or in-flight tree.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Platforms } from '../App'
import { platformCardTitle } from '../accountViews'

const getMock = vi.fn()
const buildStatusMock = vi.fn()
const generateMock = vi.fn()
const awaitBuildMock = vi.fn()
const watchBuildMock = vi.fn()
const downloadMock = vi.fn()
const getHealthMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    product: {
      ...actual.product,
      get: (...args: unknown[]) => getMock(...args),
      buildStatus: (...args: unknown[]) => buildStatusMock(...args),
      generate: (...args: unknown[]) => generateMock(...args),
    },
    awaitBuild: (...args: unknown[]) => awaitBuildMock(...args),
    watchBuildStatus: (...args: unknown[]) => watchBuildMock(...args),
    downloadProductPackage: (...args: unknown[]) => downloadMock(...args),
    getHealth: (...args: unknown[]) => getHealthMock(...args),
  }
})

const GENERATION = {
  product_id: 'vineyard',
  engine: 'runner',
  inputs_hash: 'abc123',
  output_dir: '/tmp/vineyard',
}

describe('Your Platforms — coding-agent build', () => {
  beforeEach(() => {
    getMock.mockReset()
    buildStatusMock.mockReset()
    generateMock.mockReset()
    awaitBuildMock.mockReset()
    watchBuildMock.mockReset()
    downloadMock.mockReset()
    getHealthMock.mockReset()
    getHealthMock.mockResolvedValue({
      factory_code_cli: { available: true, credentials_file_present: true },
    })
    buildStatusMock.mockResolvedValue({ ok: true, build: { state: 'not_started' } })
    watchBuildMock.mockImplementation(async () => {})
  })

  it('shows a loading skeleton — never empty-state — while the first fetch is in flight', async () => {
    let resolveGet: (v: object) => void = () => {}
    getMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveGet = resolve as (v: object) => void
        }),
    )
    render(<Platforms sessionId="sess_ui" />)
    expect(screen.getByTestId('loading-skeleton')).toBeInTheDocument()
    expect(screen.queryByText('No platform built yet')).not.toBeInTheDocument()
    resolveGet({ blueprint: { product_name: 'Draft', vertical: 'winery' } })
    expect(await screen.findByText('No platform built yet')).toBeInTheDocument()
    expect(screen.queryByTestId('loading-skeleton')).not.toBeInTheDocument()
    expect(watchBuildMock).not.toHaveBeenCalled()
  })

  it('empty state when nothing has been generated', async () => {
    getMock.mockResolvedValue({ blueprint: { product_name: 'Draft', vertical: 'winery' } })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('No platform built yet')).toBeInTheDocument()
    expect(watchBuildMock).not.toHaveBeenCalled()
  })

  it('empty state names a pending golden residential_lettings draft — never a gold Download', async () => {
    getMock.mockResolvedValue({
      blueprint: {
        product_name: 'Residential Lettings Platform',
        vertical: 'residential_lettings',
        drafting_mode: 'golden',
      },
      blueprint_approved: false,
    })
    render(<Platforms sessionId="sess_lettings" />)
    expect(await screen.findByTestId('platforms-empty-state')).toBeInTheDocument()
    expect(screen.getByText('No platform built yet')).toBeInTheDocument()
    expect(screen.getByTestId('platforms-draft-hint')).toHaveTextContent(
      'Draft: Residential Lettings Platform (residential_lettings)',
    )
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Building…' })).not.toBeInTheDocument()
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
    expect(watchBuildMock).not.toHaveBeenCalled()
  })

  it('auto-polls and shows the coding agent at work', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      const building = {
        state: 'building',
        phases_done: 2,
        phases_total: 5,
        current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
        phase_index: 3,
        phase_total: 5,
        activity: 'wrote handler inventory_management',
        last_event: 'wrote handler inventory_management',
        phase_progress: { done: 2, total: 4, fraction: 0.5, stage: 'handlers' },
        last_event_age_s: 20,
        stale: false,
        completed: ['COLLECTOR', 'CLONER'],
      }
      onProgress(building)
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByTestId('platforms-product-title')).toHaveTextContent(
      'Vineyard Platform',
    )
    expect(screen.getByText('runner')).toBeInTheDocument()
    expect(await screen.findByText(/Coding agent at work — WORKERS 3\/5/)).toBeInTheDocument()
    expect(screen.getByText(/2\/4 handlers/)).toBeInTheDocument()
    const building = screen.getByRole('button', { name: 'Building…' })
    expect(building).toBeDisabled()
    expect(building).toHaveClass('ghost')
  })

  it('does not gold-label founding while the coding agent is still at COLLECTOR', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Northbridge Lettings Desk', vertical: 'property-management' },
    })
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
          level: 'STORE_GREEN',
          founding_customer_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText(/Coding agent at work — COLLECTOR 1\/5/)).toBeInTheDocument()
    expect(screen.queryByText(/Store-green/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('platforms-pilot-ready-pill')).not.toBeInTheDocument()
    const building = screen.getByRole('button', { name: 'Building…' })
    expect(building).toBeDisabled()
    expect(building).toHaveClass('ghost')
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
  })

  it('refresh shows Refreshing… then re-fetches product + build-status', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Dealership Platform', vertical: 'auto' },
    })
    const atTwo = {
      state: 'building',
      phases_done: 2,
      phases_total: 5,
      activity: 'WRITER handler 2/4',
      completed: ['cloner'],
    }
    const atThree = {
      state: 'building',
      phases_done: 3,
      phases_total: 5,
      activity: 'TESTER gate',
      completed: ['cloner', 'writer'],
    }
    // Refresh is the only buildStatus caller; watcher seeds the first snapshot.
    buildStatusMock.mockResolvedValueOnce({ ok: true, build: atThree })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress(atTwo)
      return new Promise(() => {})
    })

    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('runner')).toBeInTheDocument()
    expect(
      await screen.findByText(/Coding agent at work — 3\/5 · last: WRITER handler 2\/4 · still working/),
    ).toBeInTheDocument()
    expect(getMock).toHaveBeenCalledTimes(1)
    expect(buildStatusMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(screen.getByRole('button', { name: 'Refreshing…' })).toBeDisabled()

    await waitFor(() => {
      expect(getMock).toHaveBeenCalledTimes(2)
      expect(buildStatusMock).toHaveBeenCalledTimes(1)
    })
    expect(getMock).toHaveBeenNthCalledWith(2, 'sess_ui')
    expect(buildStatusMock).toHaveBeenNthCalledWith(1, 'sess_ui')
    expect(
      await screen.findByText(/Coding agent at work — 4\/5 · last: TESTER gate · still working/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Building…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled()
    expect(generateMock).not.toHaveBeenCalled()
  })

  it('names how many artifacts the coding agent wrote', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 10, agent_written: 6, templated: 4 },
        acceptance: { passed: 12, total: 12, ok: true },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText('Finished — 6 artifacts; 4 templated')).toBeInTheDocument()
    expect(screen.queryByText(/6 of 10/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.getByTestId('platforms-pilot-ready-pill')).toHaveTextContent('Pilot-ready')
    expect(screen.getByTestId('platforms-acceptance-score')).toHaveTextContent('12/12')
    expect(screen.getByTestId('platforms-product-title')).toHaveTextContent('Vineyard Platform')
    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('Vineyard Platform')
    expect(screen.getByRole('heading', { level: 3 })).toHaveTextContent('12/12')
    expect(screen.getByRole('heading', { level: 3 })).not.toHaveTextContent(/^product\b/i)
  })

  it('card title prefers blueprint product_name over generic product_id', async () => {
    getMock.mockResolvedValue({
      generation: { ...GENERATION, product_id: 'product' },
      blueprint: { product_name: 'FinanceOps', vertical: 'finance' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 10, agent_written: 6, templated: 4 },
        acceptance: { passed: 12, total: 12, ok: true },
      })
    })
    render(<Platforms sessionId="sess_finance" />)
    const title = await screen.findByTestId('platforms-product-title')
    expect(title).toHaveTextContent('FinanceOps')
    expect(title.textContent).not.toMatch(/\bproduct\b/i)
    await waitFor(() => {
      expect(screen.getByTestId('platforms-acceptance-score')).toHaveTextContent('12/12')
    })
    expect(title).toHaveTextContent('FinanceOps')
    expect(title).toHaveTextContent('12/12')
  })

  it('card title humanizes product_id when product_name is missing', async () => {
    expect(platformCardTitle(undefined, 'product')).toBe('Product')
    expect(platformCardTitle('  ', 'residential-lettings')).toBe('Residential Lettings')
    expect(platformCardTitle('FinanceOps', 'product')).toBe('FinanceOps')
    getMock.mockResolvedValue({
      generation: { ...GENERATION, product_id: 'product' },
      blueprint: { vertical: 'finance' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        acceptance: { passed: 12, total: 12, ok: true },
      })
    })
    render(<Platforms sessionId="sess_slug" />)
    const title = await screen.findByTestId('platforms-product-title')
    expect(title).toHaveTextContent('Product')
    expect(title.textContent).not.toMatch(/\bproduct\b/)
  })

  it('shows k/12 and refuses Export when authorship is green but acceptance is not k/k', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        cycle: 'pilot',
        authorship: { artifacts: 10, agent_written: 6, templated: 4 },
        acceptance: { passed: 5, total: 12, ok: false },
        level_grade: {
          level: 'STORE_GREEN',
          founding_customer_ready: false,
          pilot_ready: true,
          full_pilot: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    await waitFor(() => {
      expect(screen.getByTestId('platforms-acceptance-score')).toHaveTextContent('5/12')
    })
    expect(screen.getByRole('button', { name: 'Export (.zip) — acceptance 5/12' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Acceptance is 5\/12/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/build failed/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
  })

  it('live sess_4591d5cc 0/12 CODE-green card does not say build failed', async () => {
    // launching-ready 1551 Platforms photograph: numeric 0/12, CODE/PRODUCT/STORE
    // PASS, Export — acceptance 0/12, lead must not call that a failed build.
    getMock.mockResolvedValue({
      generation: {
        ...GENERATION,
        product_id: 'residential-lettings',
        output_dir: '/app/storage/factory_outputs/sessions/sess_4591d5cc45d04fe1/residential-lettings',
      },
      blueprint: { product_name: 'Residential Lettings Platform', vertical: 'residential-lettings' },
    })
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
    render(<Platforms sessionId="sess_4591d5cc45d04fe1" />)
    expect(await screen.findByTestId('platforms-prototype-pill')).toHaveTextContent(
      'Code-green (prototype)',
    )
    expect(screen.getByTestId('platforms-acceptance-score')).toHaveTextContent('0/12')
    expect(screen.getByTestId('platforms-gate-code')).toHaveTextContent('CODE PASS')
    expect(screen.getByTestId('platforms-gate-product')).toHaveTextContent('PRODUCT PASS')
    expect(screen.getByTestId('platforms-gate-store')).toHaveTextContent('STORE PASS')
    expect(screen.getByRole('button', { name: 'Export (.zip) — acceptance 0/12' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Acceptance is 0\/12/)
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/not a failed build/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/build failed/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
    expect(screen.getByText(/Acceptance 0\/12 — not pilot-ready/)).toBeInTheDocument()
  })

  it('surfaces CODE_GREEN vs FOUNDING_CUSTOMER_READY vs failed on Platforms', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Lettings Hub', vertical: 'lettings' },
    })
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
        },
      })
    })
    const { unmount } = render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByTestId('platforms-prototype-pill')).toHaveTextContent(
      'Code-green (prototype)',
    )
    expect(screen.getByTestId('platforms-gate-product')).toHaveTextContent('PRODUCT NOT RUN')
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('platforms-pilot-ready-pill')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
    unmount()

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
        },
      })
    })
    const second = render(<Platforms sessionId="sess_founding" />)
    expect(await screen.findByTestId('platforms-pilot-ready-pill')).toHaveTextContent(
      'Founding-customer-ready',
    )
    expect(screen.getByText(/Founding-customer-ready — PRODUCT and STORE gates passed/)).toBeInTheDocument()
    expect(screen.getByText('Finished — 22 artifacts; 6 templated')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    second.unmount()

    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        cycle: 'pilot',
        pilot_ready: false,
        detail: 'PRODUCT (pilot-marked suite): suite is red',
        level_grade: {
          level: 'SCAFFOLD',
          founding_customer_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'FAIL', STORE: 'NOT_RUN' },
        },
      })
    })
    render(<Platforms sessionId="sess_failed" />)
    expect(await screen.findByTestId('platforms-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(screen.getByTestId('platforms-gate-product')).toHaveTextContent('PRODUCT FAIL')
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    const refused = screen.getByRole('button', { name: 'Download as-is (failed gates)' })
    expect(refused).toBeEnabled()
    expect(refused).toHaveClass('ghost')
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Build failed — no certified download/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
  })

  it('says so when the coding agent wrote nothing', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: {
          artifacts: 8,
          agent_written: 0,
          templated: 8,
          coder_failures: { audit: 'Factory architect requires KIMI_API_KEY' },
        },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(
      await screen.findByText(/Coding agent wrote 0 artifacts — this platform is templated/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Coder skip: Factory architect requires KIMI_API_KEY/)).toBeInTheDocument()
  })

  it('labels a code-cycle SUCCESS as a prototype download', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        authorship: { artifacts: 24, agent_written: 11, templated: 13 },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(
      await screen.findByText('Code-cycle prototype — 11 artifacts; 13 templated. Not yet pilot-ready'),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download code-cycle prototype (.zip)' })).toBeEnabled()
    expect(screen.queryByText(/Download ready/)).not.toBeInTheDocument()
    expect(screen.queryByTestId('platforms-pilot-ready-pill')).not.toBeInTheDocument()
    expect(screen.getByTestId('platforms-prototype-pill')).toHaveTextContent('Code-cycle prototype')
  })

  it('offers Continue to pilot on Factory Floor when auto-pilot is blocked', async () => {
    const goFloor = vi.fn()
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform', vertical: 'winery' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: false,
        cycle: 'code',
        auto_pilot: false,
        authorship: { artifacts: 24, agent_written: 11, templated: 13 },
      })
    })
    render(<Platforms sessionId="sess_ui" goFloor={goFloor} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to pilot on Factory Floor' }))
    expect(goFloor).toHaveBeenCalled()
  })

  it('downloads only after the build succeeds', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        pilot_ready: true,
        acceptance: { passed: 12, total: 12, ok: true },
        cycle: 'pilot',
        authorship: { artifacts: 6, agent_written: 6, templated: 0, action_py: 6 },
      })
    })
    downloadMock.mockResolvedValue(undefined)
    render(<Platforms sessionId="sess_ui" />)
    // Wait for the succeeded snapshot — Download stays "Building…" until then.
    expect(await screen.findByText('Finished — 6 artifacts; 0 templated')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Download platform export (.zip)' }))
    await waitFor(() => expect(downloadMock).toHaveBeenCalledWith('sess_ui'))
  })

  it('does not present a red pilot suite as a successful Download', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Cerebrum Residential Lettings Hub' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        cycle: 'pilot',
        outcome: 'FAILED_BUDGET_SPENT',
        pilot_ready: false,
        detail:
          'rework budget of 3 exhausted; TESTER gate still failing: PRODUCT (pilot-marked suite): suite is red',
        findings: ['FAILED tests/test_smoke.py::test_every_capability_executes_end_to_end'],
      })
    })
    awaitBuildMock.mockResolvedValue({
      state: 'failed',
      detail:
        'rework budget of 3 exhausted; TESTER gate still failing: PRODUCT (pilot-marked suite): suite is red',
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByTestId('platforms-failed-badge')).toHaveTextContent(/Build failed/)
    expect(screen.getByTestId('platforms-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(
      screen.queryByRole('button', { name: 'Download platform export (.zip)' }),
    ).not.toBeInTheDocument()
    const exportBtn = screen.getByRole('button', { name: 'Download as-is (failed gates)' })
    expect(exportBtn).toHaveClass('ghost')
    expect(exportBtn).toBeEnabled()
    fireEvent.click(exportBtn)
    // A red pilot suite is still never a SUCCESSFUL download: the only call
    // permitted is the explicitly labeled as-is form.
    await waitFor(() =>
      expect(downloadMock).toHaveBeenCalledWith(expect.any(String), { asIs: true }),
    )
    expect(downloadMock).not.toHaveBeenCalledWith(expect.any(String))
    expect(awaitBuildMock).not.toHaveBeenCalled()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Build failed — no certified download/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
    expect(screen.queryByText(/Download the export and launch it anywhere/i)).not.toBeInTheDocument()
  })

  it('refuses Export when a founding SUCCESS card is below the authorship floor', async () => {
    getMock.mockResolvedValue({
      generation: {
        ...GENERATION,
        product_id: 'veterinary-care',
      },
      blueprint: { product_name: 'VetCare Hub', vertical: 'veterinary-care' },
    })
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
    render(<Platforms sessionId="sess_c220986f67914681" />)
    expect(await screen.findByTestId('platforms-prototype-pill')).toHaveTextContent(
      'Code-green (prototype)',
    )
    expect(screen.queryByRole('button', { name: 'Download platform export (.zip)' })).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Export (.zip) — below full-pilot authorship floor' }),
    ).toBeDisabled()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/full-pilot floor/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/)
  })

  it('refuses Export when a CLI-failed founding claim is not actually pilot-ready', async () => {
    getMock.mockResolvedValue({
      generation: {
        ...GENERATION,
        product_id: 'veterinary-care',
      },
      blueprint: { product_name: 'VetCare Hub', vertical: 'veterinary-care' },
    })
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
    render(<Platforms sessionId="sess_45729bb662cf4a5d" />)
    expect(await screen.findByTestId('platforms-failed-pill')).toHaveTextContent('Pilot suite failed')
    expect(await screen.findByText(/FACTORY_CODE_CLI_FAILED: CLI exited 1/)).toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Download platform export (.zip)' }),
    ).not.toBeInTheDocument()
    const refused = screen.getByRole('button', { name: 'Download as-is (failed gates)' })
    expect(refused).toBeEnabled()
    expect(refused).toHaveClass('ghost')
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Build failed — no certified download/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
  })

  it('sess_45729bb 0639 photograph: thin Store-green Platforms never claims founding', async () => {
    getMock.mockResolvedValue({
      generation: {
        ...GENERATION,
        product_id: 'veterinary-care',
      },
      blueprint: { product_name: 'VetCare Hub', vertical: 'veterinary-care' },
    })
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
    render(<Platforms sessionId="sess_45729bb662cf4a5d" />)
    expect(await screen.findByTestId('platforms-pilot-ready-pill')).toHaveTextContent(
      'Store-green',
    )
    expect(screen.getByTestId('platforms-pilot-ready-pill')).not.toHaveTextContent(
      'Founding-customer-ready',
    )
    expect(screen.getByText('Pilot-ready — 8 artifacts; 16 templated')).toBeInTheDocument()
    expect(screen.getByText(/Store-green — not founding-customer-ready/)).toBeInTheDocument()
    expect(screen.queryByText('Founding-customer-ready', { exact: true })).not.toBeInTheDocument()
    expect(screen.queryByText(/Founding-customer-ready — PRODUCT and STORE/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Finished —/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Download the export/)
  })

  it('names missing coding-agent credentials from /health on Platforms', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: false,
        requires_kimi_credentials: true,
        blocker: 'FACTORY_CODE_CLI_CREDENTIALS_MISSING',
      },
    })
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({ state: 'failed', pilot_ready: false, detail: 'CLI credentials missing' })
    })
    render(<Platforms sessionId="sess_creds" />)
    const banner = await screen.findByTestId('platforms-factory-cli-status')
    expect(banner).toHaveTextContent('Coding agent credentials missing')
    expect(banner).toHaveTextContent('FACTORY_CODE_CLI_CREDENTIALS_MISSING')
    expect(banner).toHaveTextContent('DEEPSEEK_API_KEY')
    expect(banner).toHaveTextContent('FACTORY_CODEWHALE_WRITER=1')
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
  })

  it('does not name Kimi CLI credentials on Platforms when Cursor BA is the executor', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: false,
        requires_kimi_credentials: false,
        requires_cli: false,
        cursor_ba_available: true,
      },
    })
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({ state: 'succeeded', pilot_ready: true })
    })
    render(<Platforms sessionId="sess_cursor_ba" />)
    await waitFor(() => expect(getHealthMock).toHaveBeenCalled())
    expect(screen.queryByTestId('platforms-factory-cli-status')).not.toBeInTheDocument()
    expect(screen.queryByText(/FACTORY_CODE_CLI_CREDENTIALS_MISSING/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Coding agent credentials missing/)).not.toBeInTheDocument()
  })

  it('names missing coding-agent default_model from /health on Platforms', async () => {
    getHealthMock.mockResolvedValue({
      factory_code_cli: {
        available: true,
        credentials_file_present: true,
        default_model_configured: false,
        blocker: 'FACTORY_CODE_CLI_NO_MODEL',
      },
    })
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'VetCare Hub' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({ state: 'failed', pilot_ready: false, detail: 'FACTORY_CODE_CLI_NO_MODEL' })
    })
    render(<Platforms sessionId="sess_no_model" />)
    const banner = await screen.findByTestId('platforms-factory-cli-status')
    expect(banner).toHaveTextContent('Coding agent has no model')
    expect(banner).toHaveTextContent('FACTORY_CODE_CLI_NO_MODEL')
    expect(banner).toHaveTextContent('default_model')
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download the export/i)
    // Wait for watchBuild failed — /health banner can win the race while
    // the card still says Building… (CI flake on C-BRIEF-only PRs).
    expect(
      await screen.findByRole('button', { name: 'Download as-is (failed gates)' }),
    ).toBeEnabled()
  })

  it('labels code-phase success as not pilot-ready', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'succeeded',
        cycle: 'code',
        pilot_ready: false,
        authorship: { artifacts: 11, agent_written: 11, templated: 19 },
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText(/not pilot-ready/i)).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Download code-cycle prototype (.zip)' }),
    ).toBeInTheDocument()
    expect(screen.queryByTestId('platforms-pilot-ready-pill')).not.toBeInTheDocument()
  })

  it('surfaces honest stalled UI instead of forever Building…', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'Vineyard Platform' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'stalled',
        detail: 'no build activity for 40 min — the build process may be gone; generate again',
      })
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByText(/Build stalled — no build activity for 40 min/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Building…' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Download platform export (.zip)' }),
    ).not.toBeInTheDocument()
    const exportBtn = screen.getByRole('button', { name: 'Download as-is (failed gates)' })
    expect(exportBtn).toBeEnabled()
    expect(exportBtn).toHaveClass('ghost')
    expect(screen.getByTestId('platforms-stalled-pill')).toHaveTextContent('Build stalled')
  })

  it('unreadable ledger is Build failed — never Building…', async () => {
    getMock.mockResolvedValue({
      generation: GENERATION,
      blueprint: { product_name: 'VetCare Hub' },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'unknown',
        detail: "LEDGER_UNREADABLE: build_ledger.jsonl:4561 is not a readable ledger event: 'seq'",
        pilot_ready: false,
      })
    })
    render(<Platforms sessionId="sess_ledger" />)
    expect(await screen.findByText(/Build failed — LEDGER_UNREADABLE/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Building…' })).not.toBeInTheDocument()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/unreadable ledger/)
    const exportBtn = screen.getByRole('button', { name: 'Download as-is (failed gates)' })
    expect(exportBtn).toBeEnabled()
  })

  it('sess_4591d5cc sticky need≥5 card matches live 4/4 package SUCCESS', async () => {
    const stickyNeedFive = {
      state: 'failed',
      outcome: 'FAILED_ROLE_ERROR',
      cycle: 'pilot',
      pilot_ready: false,
      detail:
        'FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the full-pilot floor (written=4, cli_authored_ids=4, need ≥5). Do not SUCCESS a Store-green pilot from thin authorship.',
      findings: [
        'FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the full-pilot floor (written=4, cli_authored_ids=4, need ≥5)',
      ],
      authorship: {
        artifacts: 25,
        agent_written: 4,
        templated: 21,
        action_py: 4,
        cli_authored_ids: [
          'unit_registry_and_vacancy_tracking',
          'viewing_management',
          'maintenance_issue_tracking',
          'tenancy_application_pipeline',
        ],
      },
      acceptance: { passed: 12, total: 12, ok: true },
    }
    getMock.mockResolvedValue({
      last_error:
        'FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the full-pilot floor (written=4, cli_authored_ids=4, need ≥5). Do not SUCCESS a Store-green pilot from thin authorship.',
      generation: {
        product_id: 'residential-lettings',
        engine: 'runner',
        inputs_hash: 'd38710daf18675a65ce51f3d4a46ab54e0df4f06c2274c6219f88a804a09bc0a',
        output_dir: '/app/storage/factory_outputs/sessions/sess_4591d5cc45d04fe1/residential-lettings',
        build: stickyNeedFive,
      },
      blueprint: {
        product_name: 'Residential Lettings Platform',
        vertical: 'residential_lettings',
        capabilities: [
          { id: 'unit_registry_and_vacancy_tracking' },
          { id: 'viewing_management' },
          { id: 'maintenance_issue_tracking' },
          { id: 'tenancy_application_pipeline' },
        ],
      },
    })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress(stickyNeedFive)
    })
    render(<Platforms sessionId="sess_4591d5cc45d04fe1" />)
    expect(await screen.findByTestId('platforms-product-title')).toHaveTextContent(
      'Residential Lettings Platform',
    )
    expect(await screen.findByTestId('platforms-pilot-ready-pill')).toHaveTextContent('Store-green')
    expect(screen.queryByTestId('platforms-failed-pill')).not.toBeInTheDocument()
    expect(screen.queryByTestId('platforms-failed-badge')).not.toBeInTheDocument()
    expect(screen.queryByText(/need\s*≥\s*5/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Pilot suite failed/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Download the export/)
    expect(screen.getByTestId('platforms-lead')).not.toHaveTextContent(/Download unavailable/)
  })

  it('does not let sticky last_error hide a live SUCCESS generation.build', async () => {
    getMock.mockResolvedValue({
      last_error:
        'FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the full-pilot floor (written=4, cli_authored_ids=4, need ≥5). Do not SUCCESS a Store-green pilot from thin authorship.',
      generation: {
        product_id: 'residential-lettings',
        engine: 'runner',
        inputs_hash: 'live-hash',
        output_dir: '/tmp/residential-lettings',
        build: {
          state: 'succeeded',
          outcome: 'SUCCESS',
          cycle: 'pilot',
          pilot_ready: true,
          acceptance: { passed: 12, total: 12, ok: true },
          authorship: {
            artifacts: 25,
            agent_written: 4,
            templated: 21,
            action_py: 4,
            n_required: 4,
            cli_authored_ids: [
              'unit_registry_and_vacancy_tracking',
              'viewing_management',
              'maintenance_issue_tracking',
              'tenancy_application_pipeline',
            ],
          },
          level_grade: {
            level: 'STORE_GREEN',
            pilot_ready: true,
            acceptance: { passed: 12, total: 12, ok: true },
            full_pilot: true,
            three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
          },
        },
      },
      blueprint: {
        product_name: 'Residential Lettings Platform',
        vertical: 'residential_lettings',
        capabilities: [
          { id: 'unit_registry_and_vacancy_tracking' },
          { id: 'viewing_management' },
          { id: 'maintenance_issue_tracking' },
          { id: 'tenancy_application_pipeline' },
        ],
      },
    })
    watchBuildMock.mockImplementation(async () => {})
    render(<Platforms sessionId="sess_4591d5cc45d04fe1" />)
    expect(await screen.findByRole('button', { name: 'Download platform export (.zip)' })).toBeEnabled()
    expect(screen.queryByTestId('platforms-failed-pill')).not.toBeInTheDocument()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Download the export/)
  })

  it('does not download a failed coding-agent build', async () => {
    getMock.mockResolvedValue({ generation: GENERATION, blueprint: { product_name: 'Vineyard Platform' } })
    watchBuildMock.mockImplementation(async (_sid: string, onProgress: (s: object) => void) => {
      onProgress({
        state: 'failed',
        detail: 'TESTER gate red',
      })
    })
    awaitBuildMock.mockResolvedValue({
      state: 'failed',
      detail: 'TESTER gate red',
    })
    render(<Platforms sessionId="sess_ui" />)
    expect(await screen.findByTestId('platforms-failed-pill')).toHaveTextContent('Pilot suite failed')
    const exportBtn = screen.getByRole('button', { name: 'Download as-is (failed gates)' })
    expect(exportBtn).toBeEnabled()
    expect(exportBtn).toHaveClass('ghost')
    fireEvent.click(exportBtn)
    await waitFor(() =>
      expect(downloadMock).toHaveBeenCalledWith(expect.any(String), { asIs: true }),
    )
    expect(downloadMock).not.toHaveBeenCalledWith(expect.any(String))
    expect(awaitBuildMock).not.toHaveBeenCalled()
    expect(screen.getByTestId('platforms-lead')).toHaveTextContent(/Build failed — no certified download/)
  })
})
