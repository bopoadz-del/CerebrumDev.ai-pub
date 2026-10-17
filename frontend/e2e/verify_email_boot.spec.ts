import { expect, test, type Page } from '@playwright/test'

/**
 * After register, an unverified session must land on verify-email — never
 * Floor, never "Factory unreachable". Routes are mocked so this does not
 * depend on SMTP or a live API.
 */

async function mockUnverifiedFactory(page: Page, extra?: { devToken?: string }) {
  // Boot always probes /me before showing AuthGate. Unauthenticated callers
  // must 401; only after register should /me return email_not_verified.
  let registered = false
  await page.route('**/v1/auth/register', async (route) => {
    const posted = route.request().postDataJSON() as { email?: string }
    registered = true
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        login_token: 'cdt_e2e_unverified',
        email: posted.email ?? 'e2e@factory.dev',
        email_verified: false,
        verification: extra?.devToken
          ? {
              mode: 'dev_token',
              email_sent: false,
              note: 'SMTP not configured',
              dev_verification_token: extra.devToken,
            }
          : { mode: 'smtp', email_sent: true },
      }),
    })
  })
  await page.route('**/v1/auth/me', async (route) => {
    if (!registered) {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Invalid or missing API key' }),
      })
      return
    }
    await route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'email_not_verified' }),
    })
  })
  await page.route('**/v1/auth/logout', async (route) => {
    registered = false
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ok: true }),
    })
  })
  await page.route('**/v1/sessions/**', async (route) => {
    await route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'email_not_verified' }),
    })
  })
  await page.route('**/v1/sessions/', async (route) => {
    await route.fulfill({
      status: 403,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'email_not_verified' }),
    })
  })
  await page.route('**/v1/auth/resend-verification', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        already_verified: false,
        verification: extra?.devToken
          ? {
              mode: 'dev_token',
              email_sent: false,
              dev_verification_token: extra.devToken,
            }
          : { mode: 'smtp', email_sent: true },
      }),
    })
  })
  await page.route('**/v1/domains/**', async (route) => {
    await route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Domain store unreachable' }),
    })
  })
}

async function registerThrowaway(page: Page) {
  const email = `pw.e2e.${Date.now()}@example.com`
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible({
    timeout: 15_000,
  })
  await page.getByRole('button', { name: 'Create an account' }).click()
  await page.getByPlaceholder('you@company.com').fill(email)
  await page.getByPlaceholder('password (8+ characters)').fill(`Pw${Date.now()}Aa1`)
  await page.getByRole('button', { name: 'Create your account' }).click()
}

test('register lands on verify-email, not Floor, not Factory unreachable', async ({ page }) => {
  await mockUnverifiedFactory(page)
  await registerThrowaway(page)

  await expect(page.getByRole('heading', { name: 'Verify your email' })).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByPlaceholder('verification token')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Verify email' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Resend verification email' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.getByText(/check your inbox|verification link|paste the token/i)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Factory unreachable' })).toHaveCount(0)
})

test('register surfaces a pasteable dev_token when the API provides one', async ({ page }) => {
  await mockUnverifiedFactory(page, { devToken: 'cdv_e2e_dev_token' })
  await registerThrowaway(page)

  await expect(page.getByRole('heading', { name: 'Verify your email' })).toBeVisible({
    timeout: 15_000,
  })
  await expect(page.getByTestId('dev-verification-token')).toHaveText('cdv_e2e_dev_token')
  await expect(page.getByPlaceholder('verification token')).toHaveValue('cdv_e2e_dev_token')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Factory unreachable' })).toHaveCount(0)
})
