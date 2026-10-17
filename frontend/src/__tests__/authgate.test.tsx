/**
 * Smoke test: the auth gate — every account screen must be reachable
 * from the live client (market-readiness audit, gap #1).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthGate } from '../App'

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    setSession: vi.fn(),
    clearSession: vi.fn(),
    auth: {
      ...actual.auth,
      login: vi.fn().mockResolvedValue({ login_token: 'cdt_test' }),
      resetPassword: vi.fn().mockResolvedValue({
        ok: true,
        message: 'Password updated — sign in again (all previous sessions were closed).',
      }),
      register: vi.fn().mockResolvedValue({
        login_token: 'cdt_test',
        account_id: 'acct_test',
        verification: {
          mode: 'dev_token',
          email_sent: false,
          note: 'SMTP not configured',
          dev_verification_token: 'vtk_test_123',
        },
      }),
    },
  }
})

describe('AuthGate', () => {
  beforeEach(() => {
    localStorage.clear()
    window.history.pushState(null, '', '/')
  })

  it('shows sign-in by default', () => {
    render(<AuthGate onAuthed={() => {}} />)
    expect(screen.getByRole('button', { name: 'Enter the factory' })).toBeInTheDocument()
  })

  it('opens the register form on /register instead of sign-in', () => {
    window.history.pushState(null, '', '/register')
    render(<AuthGate onAuthed={() => {}} />)
    expect(screen.getByRole('heading', { name: 'Create your account' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create your account' })).toBeInTheDocument()
  })

  it('exposes password reset and email verification entry points', () => {
    render(<AuthGate onAuthed={() => {}} />)
    expect(screen.getByRole('button', { name: 'Forgot password?' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Have a verification token?' })).toBeInTheDocument()
  })

  it('forgot-password flow asks for the token', () => {
    render(<AuthGate onAuthed={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Forgot password?' }))
    expect(screen.getByRole('button', { name: 'Send reset token' })).toBeInTheDocument()
  })

  it('register with unconfigured SMTP hands the dev token to the app', async () => {
    const onAuthed = vi.fn()
    render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'new@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: 'supersecret1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create your account' }))
    await waitFor(() =>
      expect(onAuthed).toHaveBeenCalledWith({ devVerificationToken: 'vtk_test_123' }),
    )
  })

  it('shows Remember me on login only and prefills both saved fields', () => {
    localStorage.setItem('cerebrum.factory.email', 'saved@factory.dev')
    localStorage.setItem('cerebrum.factory.password', 'supersecret1')
    render(<AuthGate onAuthed={() => {}} />)
    expect(screen.getByPlaceholderText('you@company.com')).toHaveValue('saved@factory.dev')
    expect(screen.getByPlaceholderText('password (8+ characters)')).toHaveValue('supersecret1')
    expect(screen.getByRole('checkbox', { name: 'Remember me' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    expect(screen.queryByRole('checkbox', { name: 'Remember me' })).not.toBeInTheDocument()
  })

  it('persists email and password after a remembered login', async () => {
    const onAuthed = vi.fn()
    render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'saved@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: 'supersecret1' },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Remember me' }))
    fireEvent.click(screen.getByRole('button', { name: 'Enter the factory' }))
    await waitFor(() => expect(onAuthed).toHaveBeenCalled())
    expect(localStorage.getItem('cerebrum.factory.email')).toBe('saved@factory.dev')
    expect(localStorage.getItem('cerebrum.factory.password')).toBe('supersecret1')
  })

  it('clears saved email and password when Remember me is unchecked', async () => {
    localStorage.setItem('cerebrum.factory.email', 'saved@factory.dev')
    localStorage.setItem('cerebrum.factory.password', 'supersecret1')
    const onAuthed = vi.fn()
    render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Remember me' }))
    expect(localStorage.getItem('cerebrum.factory.email')).toBeNull()
    expect(localStorage.getItem('cerebrum.factory.password')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Enter the factory' }))
    await waitFor(() => expect(onAuthed).toHaveBeenCalled())
    expect(localStorage.getItem('cerebrum.factory.password')).toBeNull()
  })

  it('SMTP register enters the app so boot can show verify-email (not a dead Factory)', async () => {
    const { auth } = await import('../api/factory')
    ;(auth.register as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      login_token: 'cdt_smtp',
      email_verified: false,
      verification: { mode: 'smtp', email_sent: true },
    })
    const onAuthed = vi.fn()
    render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.click(screen.getByRole('button', { name: 'Create an account' }))
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'smtp@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: 'supersecret1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create your account' }))
    await waitFor(() => expect(onAuthed).toHaveBeenCalled())
  })
})

describe('email deep links', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('completes verification when opened from the email link', async () => {
    const { auth } = await import('../api/factory')
    ;(auth.verifyEmail as ReturnType<typeof vi.fn>) = vi
      .fn()
      .mockResolvedValue({ ok: true })
    window.history.pushState(null, '', '/verify-email?token=vtk_from_email')

    render(<AuthGate onAuthed={() => {}} />)

    await waitFor(() =>
      expect(auth.verifyEmail).toHaveBeenCalledWith('vtk_from_email'),
    )
    await waitFor(() =>
      expect(
        screen.getByText('Email verified. Sign in to enter the factory.'),
      ).toBeInTheDocument(),
    )
    expect(window.location.pathname).toBe('/login')
  })

  it('prefills the reset form when opened from the reset link', async () => {
    window.history.pushState(null, '', '/reset-password?token=rst_from_email')

    render(<AuthGate onAuthed={() => {}} />)

    await waitFor(() =>
      expect(
        screen.getByText('Choose a new password to finish the reset.'),
      ).toBeInTheDocument(),
    )
    expect(screen.getByPlaceholderText('reset token')).toHaveValue('rst_from_email')
    expect(window.location.pathname).toBe('/reset-password')
    expect(window.location.search).toBe('')
  })

  it('reset submit lands on sign-in with a success notice', async () => {
    const { auth } = await import('../api/factory')
    ;(auth.resetPassword as ReturnType<typeof vi.fn>) = vi.fn().mockResolvedValue({
      ok: true,
      message: 'Password updated — sign in again (all previous sessions were closed).',
    })
    window.history.pushState(null, '', '/reset-password?token=rst_from_email')

    render(<AuthGate onAuthed={() => {}} />)
    await waitFor(() =>
      expect(screen.getByPlaceholderText('reset token')).toHaveValue('rst_from_email'),
    )
    fireEvent.change(screen.getByPlaceholderText('new password (8+ characters)'), {
      target: { value: 'new-pass-456' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update password' }))
    await waitFor(() =>
      expect(auth.resetPassword).toHaveBeenCalledWith('rst_from_email', 'new-pass-456'),
    )
    expect(
      await screen.findByText(
        'Password updated — sign in again (all previous sessions were closed).',
      ),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(window.location.pathname).toBe('/login')
  })
})
