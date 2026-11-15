/**
 * SEC-2 exam: a typed password never reaches browser storage, logs or an
 * error event, and every password field takes its autocomplete hint from
 * the one purpose rule.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Account, AuthGate } from '../App'
import { scrubEvent } from '../eventScrub'
import {
  SECRET_AUTOCOMPLETE,
  isStoredSecretKey,
  purgeStoredSecrets,
  secretFieldProps,
  type SecretPurpose,
} from '../secretFields'

const loginMock = vi.fn()
const registerMock = vi.fn()
const resetMock = vi.fn()
const changeMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    auth: {
      ...actual.auth,
      me: vi.fn().mockResolvedValue({ email: 'owner@factory.dev', email_verified: true }),
      login: (...a: unknown[]) => loginMock(...a),
      register: (...a: unknown[]) => registerMock(...a),
      resetPassword: (...a: unknown[]) => resetMock(...a),
      changePassword: (...a: unknown[]) => changeMock(...a),
    },
  }
})

// Two distinct secrets per run so a hit can never be a coincidence of one value.
const SECRETS = ['Qx7!vault-horse-91', 'm3ad0w#Lantern_42']

function storedValues(): string[] {
  const out: string[] = []
  for (const store of [localStorage, sessionStorage]) {
    for (let i = 0; i < store.length; i += 1) {
      const k = store.key(i)
      if (k !== null) out.push(k, store.getItem(k) ?? '')
    }
  }
  return out
}

function leaks(haystack: string[]): string[] {
  return SECRETS.filter((s) => haystack.some((h) => h.includes(s)))
}

const consoleCalls: string[] = []

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  consoleCalls.length = 0
  window.history.pushState(null, '', '/')
  for (const m of ['log', 'info', 'warn', 'error', 'debug'] as const) {
    vi.spyOn(console, m).mockImplementation((...args: unknown[]) => {
      consoleCalls.push(args.map((a) => (typeof a === 'string' ? a : JSON.stringify(a))).join(' '))
    })
  }
  loginMock.mockReset().mockResolvedValue({ login_token: 'cdt_test' })
  registerMock.mockReset().mockResolvedValue({ login_token: 'cdt_test' })
  resetMock.mockReset().mockResolvedValue({ ok: true, message: 'done' })
  changeMock.mockReset().mockResolvedValue({ ok: true, message: 'changed' })
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('one autocomplete rule by purpose', () => {
  it.each([
    ['current', 'current-password'],
    ['new', 'new-password'],
    ['one-time', 'one-time-code'],
  ] as [SecretPurpose, string][])('%s -> %s, always a masked field', (purpose, hint) => {
    expect(secretFieldProps(purpose)).toEqual({
      type: 'password',
      autoComplete: hint,
      autoCapitalize: 'off',
      autoCorrect: 'off',
      spellCheck: false,
    })
  })

  it('the three purposes never share a hint', () => {
    const hints = Object.values(SECRET_AUTOCOMPLETE)
    expect(new Set(hints).size).toBe(hints.length)
  })

  it.each([
    ['/', 'current-password'],
    ['/register', 'new-password'],
    ['/reset-password', 'new-password'],
  ])('sign-in screen at %s marks its password %s', (path, hint) => {
    window.history.pushState(null, '', path)
    const { container } = render(<AuthGate onAuthed={() => {}} />)
    const fields = container.querySelectorAll('input[type="password"]')
    expect(fields).toHaveLength(1)
    expect(fields[0].getAttribute('autocomplete')).toBe(hint)
  })

  it('every account password field carries the hint of its purpose', async () => {
    const { container } = render(<Account onLogout={() => {}} />)
    await screen.findByRole('heading', { name: 'Change password' })
    const hints = Array.from(container.querySelectorAll('input[type="password"]')).map((el) =>
      el.getAttribute('autocomplete'),
    )
    expect(hints.sort()).toEqual(
      ['current-password', 'new-password', 'new-password', 'one-time-code'].sort(),
    )
  })
})

describe('no password in browser storage', () => {
  it('sign in with Remember me stores the email only, and the form forgets the password', async () => {
    const onAuthed = vi.fn()
    render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'owner@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: SECRETS[0] },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Remember me' }))
    fireEvent.click(screen.getByRole('button', { name: 'Enter the factory' }))
    await waitFor(() => expect(onAuthed).toHaveBeenCalled())
    expect(loginMock).toHaveBeenCalledWith('owner@factory.dev', SECRETS[0])
    expect(leaks(storedValues())).toEqual([])
    expect(screen.getByPlaceholderText('password (8+ characters)')).toHaveValue('')
  })

  it('register and reset store no password', async () => {
    window.history.pushState(null, '', '/register')
    const onAuthed = vi.fn()
    const first = render(<AuthGate onAuthed={onAuthed} />)
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'owner@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: SECRETS[0] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create your account' }))
    await waitFor(() => expect(onAuthed).toHaveBeenCalled())
    first.unmount()

    window.history.pushState(null, '', '/reset-password?token=rst_abc')
    render(<AuthGate onAuthed={() => {}} />)
    fireEvent.change(await screen.findByPlaceholderText('new password (8+ characters)'), {
      target: { value: SECRETS[1] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Update password' }))
    await waitFor(() => expect(resetMock).toHaveBeenCalledWith('rst_abc', SECRETS[1]))
    expect(leaks(storedValues())).toEqual([])
  })

  it('change password and the admin key store nothing', async () => {
    render(<Account onLogout={() => {}} />)
    await screen.findByRole('heading', { name: 'Change password' })
    fireEvent.change(screen.getByPlaceholderText('admin key'), { target: { value: SECRETS[1] } })
    fireEvent.change(screen.getByPlaceholderText('current password'), {
      target: { value: SECRETS[1] },
    })
    for (const ph of ['new password (8+ characters)', 'confirm new password']) {
      fireEvent.change(screen.getByPlaceholderText(ph), { target: { value: SECRETS[0] } })
    }
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }))
    await waitFor(() => expect(changeMock).toHaveBeenCalledWith(SECRETS[1], SECRETS[0]))
    await screen.findByText('changed')
    expect(leaks(storedValues())).toEqual([])
  })

  it('a failed sign-in logs no password', async () => {
    loginMock.mockRejectedValue(new Error('invalid credentials'))
    render(<AuthGate onAuthed={() => {}} />)
    fireEvent.change(screen.getByPlaceholderText('you@company.com'), {
      target: { value: 'owner@factory.dev' },
    })
    fireEvent.change(screen.getByPlaceholderText('password (8+ characters)'), {
      target: { value: SECRETS[0] },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Enter the factory' }))
    await screen.findByText('invalid credentials')
    expect(leaks(consoleCalls)).toEqual([])
    expect(leaks(storedValues())).toEqual([])
  })
})

describe('purge of secrets an older build stored', () => {
  it('removes password and key entries by kind and keeps the session', () => {
    localStorage.setItem('cerebrum.factory.password', SECRETS[0])
    localStorage.setItem('some_admin_key', SECRETS[1])
    sessionStorage.setItem('Draft-Passphrase', SECRETS[0])
    localStorage.setItem('cerebrum.factory.email', 'owner@factory.dev')
    localStorage.setItem('cerebrum.factory.token', 'cdt_keep')
    localStorage.setItem('cerebrum.factory.remember', '1')
    const removed = purgeStoredSecrets()
    expect(removed.sort()).toEqual(
      ['Draft-Passphrase', 'cerebrum.factory.password', 'some_admin_key'].sort(),
    )
    expect(leaks(storedValues())).toEqual([])
    expect(localStorage.getItem('cerebrum.factory.email')).toBe('owner@factory.dev')
    expect(localStorage.getItem('cerebrum.factory.token')).toBe('cdt_keep')
    expect(localStorage.getItem('cerebrum.factory.remember')).toBe('1')
  })

  it.each([
    ['userPassword', true],
    ['x.passwd', true],
    ['PWD', true],
    ['ops-api-key', true],
    ['locale', false],
    ['theme', false],
    ['monkey_island', false],
  ])('isStoredSecretKey(%s) is %s', (name, expected) => {
    expect(isStoredSecretKey(name)).toBe(expected)
  })
})

describe('no password in an error event', () => {
  it('an event carrying the typed password in each place comes out without it', () => {
    const event = {
      message: `login failed password=${SECRETS[0]}`,
      request: {
        data: { email: 'owner@factory.dev', password: SECRETS[0] },
        headers: { Authorization: `Bearer ${SECRETS[1]}` },
      },
      extra: { password: SECRETS[0], newPassword: SECRETS[1], current_password: SECRETS[1] },
      breadcrumbs: [{ category: 'ui.input', data: { value: SECRETS[0] } }],
      exception: {
        values: [{ type: 'Error', stacktrace: { frames: [{ vars: { pwd: SECRETS[1] } }] } }],
      },
    }
    const out = scrubEvent(event)
    expect(out).not.toBeNull()
    expect(leaks([JSON.stringify(out)])).toEqual([])
  })

  it('an ordinary error still reports', () => {
    const out = scrubEvent({ message: 'build failed at step 3', level: 'error' })
    expect(out).toEqual({ message: 'build failed at step 3', level: 'error' })
  })
})
