import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  clearRememberedPassword,
  clearSession,
  factoryAccessPaused,
  forgetRememberedLogin,
  getEmail,
  getRememberedPassword,
  ApiError,
  isTransientBootError,
  isTransientNetworkError,
  rememberLogin,
  setSession,
  subscriptionDisplay,
} from '../api/factory'

describe('factory auth storage', () => {
  afterEach(() => {
    localStorage.clear()
  })

  it('stores a cdt_ login token when provided (Bearer backup)', () => {
    setSession('owner@factory.dev', 'cdt_login_token')
    expect(localStorage.getItem('cerebrum.factory.token')).toBe('cdt_login_token')
    expect(getEmail()).toBe('owner@factory.dev')
  })

  it('setSession without token leaves an existing Bearer backup in place', () => {
    localStorage.setItem('cerebrum.factory.token', 'cdt_leftover')
    setSession('owner@factory.dev')
    expect(localStorage.getItem('cerebrum.factory.token')).toBe('cdt_leftover')
    expect(getEmail()).toBe('owner@factory.dev')
  })

  it('clearSession drops leftover tokens and the email hint', () => {
    localStorage.setItem('cerebrum.factory.token', 'cdt_leftover')
    setSession('owner@factory.dev', 'cdt_leftover')
    clearSession()
    expect(localStorage.getItem('cerebrum.factory.token')).toBeNull()
    expect(getEmail()).toBeNull()
  })

  it('rememberLogin stores email and password under cerebrum.factory.*', () => {
    rememberLogin('owner@factory.dev', 'supersecret1')
    expect(getEmail()).toBe('owner@factory.dev')
    expect(getRememberedPassword()).toBe('supersecret1')
    expect(localStorage.getItem('cerebrum.factory.email')).toBe('owner@factory.dev')
    expect(localStorage.getItem('cerebrum.factory.password')).toBe('supersecret1')
    expect(document.cookie).not.toContain('supersecret1')
  })

  it('forgetRememberedLogin and clearRememberedPassword drop saved fields', () => {
    rememberLogin('owner@factory.dev', 'supersecret1')
    clearRememberedPassword()
    expect(getRememberedPassword()).toBeNull()
    expect(getEmail()).toBe('owner@factory.dev')
    forgetRememberedLogin()
    expect(getEmail()).toBeNull()
    expect(getRememberedPassword()).toBeNull()
  })

  it('clearSession keeps remembered email+password for the next visit', () => {
    rememberLogin('owner@factory.dev', 'supersecret1')
    clearSession()
    expect(getEmail()).toBe('owner@factory.dev')
    expect(getRememberedPassword()).toBe('supersecret1')
  })

  it('sends credentials: include and Authorization Bearer when token present', async () => {
    localStorage.setItem('cerebrum.factory.token', 'cdt_backup')
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ email: 'owner@factory.dev' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await auth.me()
    expect(fetchMock).toHaveBeenCalled()
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('include')
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBe('Bearer cdt_backup')
    vi.unstubAllGlobals()
  })

  it('omits Authorization when no token is stored (cookie-only path)', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ email: 'owner@factory.dev' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await auth.me()
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect(init.credentials).toBe('include')
    const headers = init.headers as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
    vi.unstubAllGlobals()
  })
})

describe('factoryAccessPaused', () => {
  it('matches Subscription Factory access: paused only when entitled is false', () => {
    expect(factoryAccessPaused({ entitled: false })).toBe(true)
    expect(factoryAccessPaused({ entitled: true })).toBe(false)
    expect(factoryAccessPaused({})).toBe(false)
    expect(factoryAccessPaused(null)).toBe(false)
    expect(factoryAccessPaused(undefined)).toBe(false)
  })

  it('never pauses while enforcement is off, even for an expired trial', () => {
    // assert_entitled() returns early when BILLING_ENFORCEMENT is off, so the
    // API serves these accounts. Pausing the composer anyway locked the user
    // out of a UI whose backend was open.
    expect(factoryAccessPaused({ entitled: false, enforcement: false })).toBe(false)
    expect(factoryAccessPaused({ entitled: true, enforcement: false })).toBe(false)
  })

  it('still pauses an unentitled account while enforcement is on', () => {
    expect(factoryAccessPaused({ entitled: false, enforcement: true })).toBe(true)
    expect(factoryAccessPaused({ entitled: true, enforcement: true })).toBe(false)
  })

  it('falls back to the entitled flag when enforcement is absent', () => {
    // Older backend, or a status call that failed and resolved to null.
    expect(factoryAccessPaused({ entitled: false })).toBe(true)
    expect(factoryAccessPaused({ entitled: false, enforcement: undefined })).toBe(true)
    expect(factoryAccessPaused(null)).toBe(false)
  })
})

describe('isTransientNetworkError', () => {
  it('treats Failed to fetch as a race, not an API status', () => {
    expect(isTransientNetworkError(new TypeError('Failed to fetch'))).toBe(true)
    expect(isTransientNetworkError(new Error('NetworkError when attempting to fetch resource.'))).toBe(
      true,
    )
    expect(isTransientNetworkError(new Error('backend down'))).toBe(false)
    expect(isTransientNetworkError({ name: 'AbortError', message: 'aborted' })).toBe(false)
  })

  it('treats gateway 5xx as a boot race so /register is not unreachable', () => {
    expect(isTransientBootError(new TypeError('Failed to fetch'))).toBe(true)
    expect(isTransientBootError(new ApiError(502, 'Bad Gateway'))).toBe(true)
    expect(isTransientBootError(new ApiError(503, 'backend down'))).toBe(true)
    expect(isTransientBootError(new ApiError(401, 'unauthorized'))).toBe(false)
    expect(isTransientBootError(new ApiError(403, 'email_not_verified'))).toBe(false)
  })
})

describe('subscriptionDisplay', () => {
  it('keeps a live trial internally consistent', () => {
    const view = subscriptionDisplay({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 3,
      entitled: true,
    })
    expect(view).toMatchObject({
      planLabel: 'trial',
      statusLabel: 'trialing',
      showTrialDays: true,
      trialDaysLeft: 3,
      accessLabel: 'Active',
      currentPlan: 'trial',
    })
  })

  it('does not paint expired trial as still trialing with 0 days and Paused', () => {
    const view = subscriptionDisplay({
      plan: 'trial',
      subscription_status: 'trialing',
      trial_days_left: 0,
      entitled: false,
    })
    expect(view.statusLabel).toBe('expired')
    expect(view.accessLabel).toBe('Paused')
    expect(view.showTrialDays).toBe(false)
    expect(view.planLabel).toBe('trial')
    expect(view.currentPlan).toBe('trial')
    expect(view.statusLabel).not.toBe('trialing')
  })

  it('labels an active paid subscription as factory + Active', () => {
    const view = subscriptionDisplay({
      subscription_status: 'active',
      entitled: true,
    })
    expect(view.planLabel).toBe('factory')
    expect(view.statusLabel).toBe('active')
    expect(view.accessLabel).toBe('Active')
    expect(view.showTrialDays).toBe(false)
    expect(view.currentPlan).toBe('factory')
  })
})

describe('req retry', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('retries Failed to fetch then succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ email: 'owner@factory.dev' }),
      })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await expect(auth.me()).resolves.toEqual({ email: 'owner@factory.dev' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not retry 401', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      text: async () => JSON.stringify({ detail: 'unauthorized' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await expect(auth.me()).rejects.toMatchObject({ status: 401 })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('retries GET 503 then succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        text: async () => JSON.stringify({ detail: 'backend down' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () => JSON.stringify({ email: 'owner@factory.dev' }),
      })
    vi.stubGlobal('fetch', fetchMock)
    const { auth } = await import('../api/factory')
    await expect(auth.me()).resolves.toEqual({ email: 'owner@factory.dev' })
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
