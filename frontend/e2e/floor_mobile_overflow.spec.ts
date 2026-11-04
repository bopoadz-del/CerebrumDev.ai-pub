/**
 * J1 (ORDERS_04NOV_2000Z): the Floor must not scroll sideways at phone width.
 * The owner saw a build-failure line run off the right edge mid-word on a phone,
 * because .coder-failure-line did not wrap. This loads the Floor at 390x844,
 * injects a failure line carrying a long unbroken serialised payload (the exact
 * shape that overflowed), and asserts there is no horizontal page scroll.
 */
import { expect, test, type Page } from '@playwright/test'

const SID = 'sess_e2e_mobile'

async function mockFloor(page: Page) {
  await page.addInitScript(() => {
    localStorage.removeItem('cerebrum.factory.token')
    localStorage.setItem('cerebrum.factory.email', 'e2e.mobile@factory.dev')
  })
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  await page.route('**/v1/auth/me', (r) =>
    r.fulfill(json({ email: 'e2e.mobile@factory.dev', email_verified: true, account_id: 'acct_e2e' })),
  )
  await page.route(/\/v1\/sessions\/?$/, (r) =>
    r.fulfill(json(r.request().method() === 'GET' ? { sessions: [{ session_id: SID }] } : { session_id: SID })),
  )
  await page.route(/\/v1\/domains(\/|$)/, (r) => r.fulfill(json({ domains: [] })))
  await page.route(`**/v1/sessions/${SID}/product`, (r) => r.fulfill(json({ session_id: SID })))
  await page.route('**/v1/billing/status', (r) =>
    r.fulfill(json({ ok: true, billing: { plan: 'trial', subscription_status: 'trialing', trial_days_left: 3, entitled: true, checkout_available: false } })),
  )
  await page.route('**/health', async (r) => {
    if (new URL(r.request().url()).pathname !== '/health') return r.fallback()
    return r.fulfill(json({ status: 'ok', factory_code_cli: { available: true, credentials_file_present: true, requires_kimi_credentials: true } }))
  })
}

test('the Floor does not scroll sideways at phone width, even with a long failure line', async ({ page }) => {
  await mockFloor(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  // The signed-in shell is present once the rail renders.
  await expect(page.locator('.shell')).toBeVisible()

  // Inject the failure line shape that ran off the edge on the owner's phone.
  await page.evaluate(() => {
    const host = document.querySelector('main') || document.body
    const el = document.createElement('p')
    el.className = 'coder-failure-line'
    el.textContent =
      'WRITER — ' + 'Z'.repeat(400) +
      ' {"error":"block refused: workflow: RuntimeError: Unknown action: None"}'
    host.appendChild(el)
  })

  const overflow = await page.evaluate(() => {
    const se = document.scrollingElement as Element
    return se.scrollWidth - se.clientWidth
  })
  expect(overflow).toBeLessThanOrEqual(1) // no horizontal page scroll at 390px
})
