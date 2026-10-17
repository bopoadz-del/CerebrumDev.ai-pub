/**
 * Smoke test: the subscription view must tell the truth about billing
 * (market-readiness audit, gap #3 — no "being connected" hand-waving).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Subscription } from '../App'
import { paymentsNotConnectedNote } from '../accountViews'

const statusMock = vi.fn()
const checkoutMock = vi.fn()
const portalMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    billing: {
      status: () => statusMock(),
      checkout: () => checkoutMock(),
      portal: () => portalMock(),
    },
  }
})

describe('Subscription', () => {
  beforeEach(() => {
    statusMock.mockReset()
    checkoutMock.mockReset()
    portalMock.mockReset()
    statusMock.mockResolvedValue({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 3,
      entitled: true,
      checkout_available: false,
    })
  })

  it('renders structured plan status', async () => {
    render(<Subscription />)
    expect(await screen.findByText('Trial days left')).toBeInTheDocument()
    expect(screen.getByText('trialing')).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upgrade' })).toBeInTheDocument()
    expect(
      screen.getByText(/Payments are not connected on this deployment yet/i),
    ).toBeInTheDocument()
    const trialCard = screen.getByRole('heading', { name: 'Trial' }).closest('.plan-card')
    const factoryCard = screen.getByRole('heading', { name: 'Factory' }).closest('.plan-card')
    expect(trialCard).toHaveClass('highlight')
    expect(trialCard).toHaveTextContent('Current')
    expect(factoryCard).not.toHaveClass('highlight')
    expect(factoryCard).not.toHaveTextContent('Current')
  })

  it('labels an expired trial as expired + Paused, never still trialing', async () => {
    statusMock.mockResolvedValue({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 0,
      entitled: false,
      checkout_available: false,
    })
    render(<Subscription />)
    expect(await screen.findByText('Paused')).toBeInTheDocument()
    expect(screen.getByText('expired')).toBeInTheDocument()
    expect(screen.queryByText('trialing')).toBeNull()
    expect(screen.queryByText('Trial days left')).toBeNull()
    expect(screen.queryByText('Active')).toBeNull()
    const trialCard = screen.getByRole('heading', { name: 'Trial' }).closest('.plan-card')
    expect(trialCard).toHaveTextContent('Current')
    expect(screen.queryByText('0')).toBeNull()
  })

  it('shows a loading skeleton — never bare Loading… text — on first paint', () => {
    statusMock.mockImplementation(() => new Promise(() => {}))
    render(<Subscription />)
    expect(screen.getByTestId('loading-skeleton')).toBeInTheDocument()
    expect(screen.getByLabelText('Loading subscription')).toBeInTheDocument()
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
  })

  it('does not hang on Loading after a first-load fetch failure', async () => {
    statusMock.mockRejectedValue(new TypeError('Failed to fetch'))
    render(<Subscription />)
    expect(await screen.findByText('Failed to fetch')).toBeInTheDocument()
    expect(screen.queryByText('Loading…')).not.toBeInTheDocument()
    expect(screen.queryByTestId('loading-skeleton')).not.toBeInTheDocument()
    expect(screen.getByText('Could not load subscription status.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('Retry after a first-load billing failure paints a live trial', async () => {
    statusMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({
        plan: 'trial',
        subscription_status: 'trialing',
        trial_days_left: 3,
        entitled: true,
        checkout_available: false,
      })
    render(<Subscription />)
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Trial days left')).toBeInTheDocument()
    expect(screen.getByText('trialing')).toBeInTheDocument()
    expect(screen.queryByText('Failed to fetch')).not.toBeInTheDocument()
  })

  it('never uses hand-waving "being connected" copy', async () => {
    render(<Subscription />)
    await screen.findByText('Trial days left')
    expect(screen.queryByText(/being connected/i)).toBeNull()
  })

  it('marks only Factory as current when the plan is already Factory / Active', async () => {
    statusMock.mockResolvedValue({
      plan: 'factory',
      subscription_status: 'active',
      entitled: true,
      checkout_available: false,
    })
    render(<Subscription />)
    expect(await screen.findByText('factory', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('active', { selector: 'dd' })).toBeInTheDocument()
    expect(screen.getByText('Active')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Trial' })).toBeNull()
    const factoryCard = screen.getByRole('heading', { name: 'Factory' }).closest('.plan-card')
    expect(factoryCard).toHaveClass('highlight')
    expect(factoryCard).toHaveTextContent('Current')
    expect(screen.queryByRole('button', { name: 'Upgrade' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Manage billing' })).toBeInTheDocument()
    expect(
      screen.getByText(/Payments are not connected on this deployment yet/i),
    ).toBeInTheDocument()
  })

  it('does not offer Upgrade on the current active Factory plan', async () => {
    statusMock.mockResolvedValue({
      plan: 'factory',
      subscription_status: 'active',
      entitled: true,
      checkout_available: false,
    })
    render(<Subscription />)
    await screen.findByRole('heading', { name: 'Factory' })
    expect(screen.queryByRole('button', { name: 'Upgrade' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Upgrade when you are ready/i)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Manage billing' })).toBeEnabled()
  })

  it('does not mention Upgrade in payments honesty when Factory is already current', async () => {
    statusMock.mockResolvedValue({
      plan: 'factory',
      subscription_status: 'active',
      entitled: true,
      checkout_available: false,
    })
    render(<Subscription />)
    expect(
      await screen.findByText(/Payments are not connected on this deployment yet/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Upgrade still says so/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Upgrade' })).not.toBeInTheDocument()
    expect(screen.getByText(/Your current access is unaffected/i)).toBeInTheDocument()
  })

  it('keeps Upgrade honesty copy on trial while the Upgrade button is shown', async () => {
    render(<Subscription />)
    expect(await screen.findByRole('button', { name: 'Upgrade' })).toBeInTheDocument()
    expect(screen.getByText(/Upgrade still says so instead of opening a blank checkout/i)).toBeInTheDocument()
    expect(screen.getByText(/Payments are not connected on this deployment yet/i)).toBeInTheDocument()
  })

  it('paymentsNotConnectedNote mentions Upgrade only when that button is shown', () => {
    expect(paymentsNotConnectedNote({ showUpgrade: true })).toMatch(
      /Upgrade still says so instead of opening a blank checkout/,
    )
    expect(paymentsNotConnectedNote({ showUpgrade: false })).not.toMatch(/Upgrade/)
    expect(paymentsNotConnectedNote({ showUpgrade: false })).toMatch(
      /Payments are not connected on this deployment yet/,
    )
  })

  it('checkout failure states the truth plainly', async () => {
    checkoutMock.mockRejectedValue(new Error('stripe_not_configured'))
    render(<Subscription />)
    fireEvent.click(await screen.findByRole('button', { name: 'Upgrade' }))
    await waitFor(() =>
      expect(
        screen.getByText(/Payments are not connected on this deployment yet/i),
      ).toBeInTheDocument(),
    )
  })
})
