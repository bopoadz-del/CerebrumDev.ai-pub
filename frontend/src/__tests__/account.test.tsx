/**
 * Account must not render the opposite verified boolean while /me settles,
 * and the Account id row stays in the layout before and after settle.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Account } from '../App'
import type { AccountInfo } from '../api/factory'

const meMock = vi.fn()
const resendMock = vi.fn()
const forgotPasswordMock = vi.fn()
const resetPasswordMock = vi.fn()
const changePasswordMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    getEmail: () => 'owner@factory.dev',
    auth: {
      ...actual.auth,
      me: (...args: unknown[]) => meMock(...args),
      resendVerification: (...args: unknown[]) => resendMock(...args),
      forgotPassword: (...args: unknown[]) => forgotPasswordMock(...args),
      resetPassword: (...args: unknown[]) => resetPasswordMock(...args),
      changePassword: (...args: unknown[]) => changePasswordMock(...args),
    },
  }
})

describe('Account verified settling', () => {
  beforeEach(() => {
    meMock.mockReset()
    resendMock.mockReset()
    forgotPasswordMock.mockReset()
    resetPasswordMock.mockReset()
    changePasswordMock.mockReset()
  })

  it('does not render Yes or No until /me settles, and keeps the Account row', async () => {
    let resolveMe!: (value: AccountInfo) => void
    meMock.mockImplementation(
      () =>
        new Promise<AccountInfo>((resolve) => {
          resolveMe = resolve
        }),
    )
    render(<Account onLogout={() => {}} />)
    expect(screen.getByRole('heading', { name: 'Account' })).toBeInTheDocument()
    expect(screen.getByText('Email verified')).toBeInTheDocument()
    expect(screen.queryByText('Yes')).not.toBeInTheDocument()
    expect(screen.queryByText('No')).not.toBeInTheDocument()
    const placeholders = screen.getAllByText('—')
    expect(placeholders.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('Account', { selector: 'dt' })).toBeInTheDocument()

    resolveMe({
      email: 'owner@factory.dev',
      email_verified: true,
      account_id: 'acct_settled',
    })
    await waitFor(() => expect(screen.getByText('Yes')).toBeInTheDocument())
    expect(screen.queryByText('No')).not.toBeInTheDocument()
    expect(screen.getByText('acct_settled')).toBeInTheDocument()
  })

  it('renders the boot /me value on first paint so Verified does not flash', () => {
    meMock.mockImplementation(() => new Promise(() => {}))
    render(
      <Account
        onLogout={() => {}}
        initialMe={{
          email: 'owner@factory.dev',
          email_verified: true,
          account_id: 'acct_boot',
        }}
      />,
    )
    expect(screen.getByText('Yes')).toBeInTheDocument()
    expect(screen.queryByText('No')).not.toBeInTheDocument()
    expect(screen.getByText('acct_boot')).toBeInTheDocument()
  })

  it('can still show No after settle when the account is unverified', async () => {
    meMock.mockResolvedValue({
      email: 'owner@factory.dev',
      email_verified: false,
      account_id: 'acct_unverified',
    })
    render(<Account onLogout={() => {}} />)
    expect(await screen.findByText('No')).toBeInTheDocument()
    expect(screen.queryByText('Yes')).not.toBeInTheDocument()
    expect(screen.getByText('acct_unverified')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Resend verification email' })).toBeInTheDocument()
  })

  it('resend verification posts to the existing API and fills a dev token', async () => {
    meMock.mockResolvedValue({
      email: 'owner@factory.dev',
      email_verified: false,
      account_id: 'acct_unverified',
    })
    resendMock.mockResolvedValue({
      ok: true,
      already_verified: false,
      verification: {
        mode: 'dev_token',
        email_sent: false,
        note: 'SMTP not configured',
        dev_verification_token: 'cdv_from_account',
      },
    })
    render(<Account onLogout={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Resend verification email' }))
    await waitFor(() => expect(resendMock).toHaveBeenCalled())
    expect(screen.getByPlaceholderText('verification token')).toHaveValue('cdv_from_account')
    expect(screen.getByText('SMTP not configured')).toBeInTheDocument()
  })
})

describe('Account password reset', () => {
  beforeEach(() => {
    meMock.mockReset()
    forgotPasswordMock.mockReset()
    resetPasswordMock.mockReset()
    changePasswordMock.mockReset()
    meMock.mockResolvedValue({
      email: 'owner@factory.dev',
      email_verified: true,
      account_id: 'acct_settled',
    })
  })

  it('offers Send password reset for the signed-in email', async () => {
    render(<Account onLogout={() => {}} />)
    expect(await screen.findByText('owner@factory.dev')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send password reset' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('posts forgot-password for the current email and shows success without resetting', async () => {
    forgotPasswordMock.mockResolvedValue({
      ok: true,
      message: 'If the email is registered, a reset link follows.',
    })
    render(<Account onLogout={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Send password reset' }))
    await waitFor(() => expect(forgotPasswordMock).toHaveBeenCalledWith('owner@factory.dev'))
    expect(screen.getByText('If the email is registered, a reset link follows.')).toBeInTheDocument()
    expect(resetPasswordMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.getByText('owner@factory.dev')).toBeInTheDocument()
  })

  it('surfaces a forgot-password API error without changing the session', async () => {
    forgotPasswordMock.mockRejectedValue(new Error('rate limited'))
    render(<Account onLogout={() => {}} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Send password reset' }))
    expect(await screen.findByText('rate limited')).toBeInTheDocument()
    expect(resetPasswordMock).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })
})

describe('Account change password', () => {
  beforeEach(() => {
    meMock.mockReset()
    changePasswordMock.mockReset()
    forgotPasswordMock.mockReset()
    resetPasswordMock.mockReset()
    meMock.mockResolvedValue({
      email: 'owner@factory.dev',
      email_verified: true,
      account_id: 'acct_settled',
    })
  })

  it('posts current and new password and keeps Send password reset', async () => {
    changePasswordMock.mockResolvedValue({
      ok: true,
      message: 'Password updated. Other sessions were signed out.',
    })
    render(<Account onLogout={() => {}} />)
    expect(await screen.findByRole('heading', { name: 'Change password' })).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('current password'), {
      target: { value: 'old-pass-123' },
    })
    fireEvent.change(screen.getByPlaceholderText('new password (8+ characters)'), {
      target: { value: 'new-pass-456' },
    })
    fireEvent.change(screen.getByPlaceholderText('confirm new password'), {
      target: { value: 'new-pass-456' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
    await waitFor(() =>
      expect(changePasswordMock).toHaveBeenCalledWith('old-pass-123', 'new-pass-456'),
    )
    expect(
      screen.getByText('Password updated. Other sessions were signed out.'),
    ).toBeInTheDocument()
    expect(screen.getByPlaceholderText('current password')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Send password reset' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(resetPasswordMock).not.toHaveBeenCalled()
  })

  it('rejects a confirmation mismatch without calling the API', async () => {
    render(<Account onLogout={() => {}} />)
    await screen.findByRole('heading', { name: 'Change password' })
    fireEvent.change(screen.getByPlaceholderText('current password'), {
      target: { value: 'old-pass-123' },
    })
    fireEvent.change(screen.getByPlaceholderText('new password (8+ characters)'), {
      target: { value: 'new-pass-456' },
    })
    fireEvent.change(screen.getByPlaceholderText('confirm new password'), {
      target: { value: 'mismatch-789' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('New password and confirmation do not match.')).toBeInTheDocument()
    expect(changePasswordMock).not.toHaveBeenCalled()
  })

  it('surfaces a wrong-password API error without signing out', async () => {
    changePasswordMock.mockRejectedValue(new Error('Current password is incorrect'))
    render(<Account onLogout={() => {}} />)
    await screen.findByRole('heading', { name: 'Change password' })
    fireEvent.change(screen.getByPlaceholderText('current password'), {
      target: { value: 'wrong-old' },
    })
    fireEvent.change(screen.getByPlaceholderText('new password (8+ characters)'), {
      target: { value: 'new-pass-456' },
    })
    fireEvent.change(screen.getByPlaceholderText('confirm new password'), {
      target: { value: 'new-pass-456' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Current password is incorrect')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.getByText('owner@factory.dev')).toBeInTheDocument()
  })
})
