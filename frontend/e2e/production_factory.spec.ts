import { expect, test, type Page } from '@playwright/test'

/**
 * Live Factory UI against PLAYWRIGHT_BASE_URL / BASE_URL (production:
 * https://www.cerebrum-dev.com). These are browser tests, not API smoke.
 * Skip unless pointed at a remote host — local Vite has no production SMTP.
 */

const baseURL =
  process.env.PLAYWRIGHT_BASE_URL || process.env.BASE_URL || 'http://127.0.0.1:5173'
const isLive = /^(https?:\/\/)(?!127\.0\.0\.1|localhost)/i.test(baseURL)

test.describe.configure({ mode: 'serial' })
test.skip(!isLive, 'live Factory UI only (set PLAYWRIGHT_BASE_URL)')

async function assertAuthGate(page: Page) {
  await expect(page.getByRole('heading', { name: 'CerebrumDev.ai' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Enter the factory' })).toBeVisible()
  await expect(page.getByPlaceholder('you@company.com')).toBeVisible()
}

test('auth gate loads on the live UI', async ({ page }) => {
  await page.goto('/')
  await assertAuthGate(page)
  await expect(page.getByText('One account. Tell the factory. Receive your platform.')).toBeVisible()
})

test('register, forgot-password, and verify screens are reachable', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Create an account' }).click()
  await expect(page.getByRole('heading', { name: 'Create your account' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Create your account' })).toBeVisible()

  await page.getByRole('button', { name: 'Forgot password?' }).click()
  await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Send reset token' })).toBeVisible()

  await page.getByRole('button', { name: 'Have a verification token?' }).click()
  await expect(page.getByRole('heading', { name: 'Verify your email' })).toBeVisible()
  await expect(page.getByPlaceholder('verification token')).toBeVisible()

  await page.getByRole('button', { name: 'Sign in' }).click()
  await assertAuthGate(page)
})

test('invalid login hits the live API and surfaces an error', async ({ page }) => {
  await page.goto('/')
  await page.getByPlaceholder('you@company.com').fill(`no-such-user-${Date.now()}@example.com`)
  await page.getByPlaceholder('password (8+ characters)').fill('definitely-wrong-password')
  await page.getByRole('button', { name: 'Enter the factory' }).click()
  await expect(page.locator('.error-box')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('register a throwaway account and land on verify-email', async ({ page }) => {
  const email = `pw.prod.${Date.now()}@example.com`
  const password = `Pw${Date.now()}Aa1`

  await page.goto('/')
  await page.getByRole('button', { name: 'Create an account' }).click()
  await page.getByPlaceholder('you@company.com').fill(email)
  await page.getByPlaceholder('password (8+ characters)').fill(password)
  await page.getByRole('button', { name: 'Create your account' }).click()

  const verifyHeading = page.getByRole('heading', { name: 'Verify your email' })
  const factoryUnreachable = page.getByRole('heading', { name: 'Factory unreachable' })
  const floorHeading = page.getByRole('heading', { name: 'Factory Floor' })

  await expect(verifyHeading).toBeVisible({ timeout: 45_000 })
  await expect(factoryUnreachable).toHaveCount(0)
  await expect(floorHeading).toHaveCount(0)
  await expect(page.getByPlaceholder('verification token')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Verify email' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Resend verification email' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible()
  await expect(page.getByText(/check your inbox|verification link|paste the token/i)).toBeVisible()
})

const liveEmail = process.env.PLAYWRIGHT_LIVE_EMAIL || ''
const livePassword = process.env.PLAYWRIGHT_LIVE_PASSWORD || ''
const liveToken = process.env.PLAYWRIGHT_LIVE_TOKEN || ''
const hasLiveAccount = Boolean(liveToken || (liveEmail && livePassword))

const SMALL_BRIEF = 'build me a vineyard operations desk for a family winery'

async function signInLive(page: Page) {
  if (liveToken) {
    await page.context().addCookies([
      {
        name: 'cdt',
        value: liveToken,
        domain: 'api.cerebrum-dev.com',
        path: '/',
        secure: true,
        httpOnly: true,
        sameSite: 'Lax',
      },
    ])
    if (liveEmail) {
      await page.addInitScript((email) => {
        localStorage.setItem('cerebrum.factory.email', email)
        localStorage.removeItem('cerebrum.factory.token')
      }, liveEmail)
    }
    await page.goto('/')
  } else {
    await page.goto('/')
    await page.getByPlaceholder('you@company.com').fill(liveEmail)
    await page.getByPlaceholder('password (8+ characters)').fill(livePassword)
    await page.getByRole('button', { name: 'Enter the factory' }).click()
  }
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({
    timeout: 45_000,
  })
  await expect(page.getByRole('heading', { name: 'Factory unreachable' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Verify your email' })).toHaveCount(0)
}

test('verified account: Floor brief, feature list, live Approve, billing honesty', async ({
  page,
}) => {
  test.skip(!hasLiveAccount, 'set PLAYWRIGHT_LIVE_EMAIL+PASSWORD or PLAYWRIGHT_LIVE_TOKEN')
  test.setTimeout(240_000)

  const draftHits: string[] = []
  page.on('request', (req) => {
    const url = req.url()
    if (req.method() === 'POST' && /\/v1\/sessions\/[^/]+\/(chat|product\/draft)/.test(url)) {
      draftHits.push(`${req.method()} ${url}`)
    }
  })

  await signInLive(page)
  const body = await page.locator('body').innerText()
  expect(body).not.toMatch(/workbench is on|resident engineer is on|build mode is on/i)
  expect(body).not.toMatch(/Kimi workbench/i)

  await page.getByRole('button', { name: 'Account' }).click()
  await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible()
  await expect(page.getByText('Email verified')).toBeVisible()
  await expect(page.getByText('Yes')).toBeVisible()
  if (liveEmail) await expect(page.getByText(liveEmail)).toBeVisible()
  // Visible only — do not click: live forgot-password must not fire for this account.
  await expect(page.getByRole('button', { name: 'Send password reset' })).toBeVisible()

  await page.getByRole('button', { name: 'Subscription' }).click()
  await expect(page.getByRole('heading', { name: 'Subscription' })).toBeVisible()
  const upgrade = page.getByRole('button', { name: 'Upgrade' })
  const manage = page.getByRole('button', { name: 'Manage billing' })
  // Trial still offers Upgrade; current Factory / Active must not.
  if (await upgrade.isVisible().catch(() => false)) {
    await expect(upgrade).toBeEnabled()
    await upgrade.click()
  } else {
    await expect(manage).toBeVisible()
    await expect(upgrade).toHaveCount(0)
    await expect(page.getByText(/Upgrade still says so/i)).toHaveCount(0)
  }
  await expect(
    page.getByText(/Payments are not connected on this deployment yet|stripe/i).first(),
  ).toBeVisible({ timeout: 20_000 })

  await page.getByRole('button', { name: 'Factory Floor' }).click()
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible()

  const approve = page.getByRole('button', { name: /Approve & build/ })
  const takeover = page.getByRole('heading', { name: 'Coding agent has taken over' }).or(
    page.getByText(/coding agent has taken over/i),
  )
  const composer = page.getByPlaceholder(/Try:|coding agent has taken over/i)

  await expect(takeover.or(approve).or(composer)).toBeVisible({ timeout: 45_000 })
  await page.waitForTimeout(1500)

  const composerLocked =
    (await composer.isVisible().catch(() => false)) &&
    !(await composer.isEnabled().catch(() => true))

  if (composerLocked || (await takeover.isVisible().catch(() => false))) {
    await expect(page.getByText('COLLECTOR')).toBeVisible()
    await expect(page.getByText('STORE_MANAGER')).toBeVisible()
    await expect(page.getByText('TESTER')).toBeVisible()
    return
  }

  if (!(await approve.isVisible().catch(() => false))) {
    await expect(composer).toBeEnabled({ timeout: 15_000 })
    await composer.fill(SMALL_BRIEF)
    await page.getByRole('button', { name: 'Send' }).click()
    await expect(approve.or(takeover)).toBeVisible({ timeout: 90_000 })
    expect(draftHits.length).toBeGreaterThan(0)
  }

  if (await takeover.isVisible().catch(() => false)) {
    await expect(page.getByText('COLLECTOR')).toBeVisible()
    return
  }

  await expect(approve).toBeEnabled()
  await expect(page.getByText(/architect LLM|Capabilities/i).first()).toBeVisible()
  // The build waits for the user's typed level (no default): choose it on
  // the intake line, as a user would, before Approve.
  await page.getByTestId('intake-change').click()
  await page.getByTestId('intake-build-level').selectOption('prototype')
  // Change only proposes; Confirm stores it.
  await page.getByTestId('intake-confirm').click()
  await expect(page.getByText(/Confirmed: build level prototype/).first()).toBeVisible({
    timeout: 30_000,
  })
  await approve.click()
  await expect(takeover).toBeVisible({ timeout: 90_000 })
  await expect(page.getByText('COLLECTOR')).toBeVisible()
  await expect(page.getByText('STORE_MANAGER')).toBeVisible()
  await expect(page.getByPlaceholder(/coding agent has taken over/i)).toBeDisabled()
})
