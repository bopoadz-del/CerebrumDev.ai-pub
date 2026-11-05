/**
 * The chat composer must stay on screen at phone width even when the Floor is
 * tall. The owner's stalled build filled .chat-scroll with a long rework card
 * and the input bar vanished off the bottom: .chat-scroll lacked min-height:0,
 * so it expanded to its content instead of scrolling, pushing .composer past
 * main's overflow:hidden boundary. This loads the Floor at 390x844, fills the
 * scroll region with a tall card, and asserts the composer is still visible and
 * fully within the viewport (the scroll region absorbs the overflow).
 */
import { expect, test, type Page } from '@playwright/test'

const SID = 'sess_e2e_composer'

async function mockFloor(page: Page) {
  await page.addInitScript(() => {
    localStorage.removeItem('cerebrum.factory.token')
    localStorage.setItem('cerebrum.factory.email', 'e2e.composer@factory.dev')
  })
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  await page.route('**/v1/auth/me', (r) =>
    r.fulfill(json({ email: 'e2e.composer@factory.dev', email_verified: true, account_id: 'acct_e2e' })),
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

test('the chat composer stays visible at phone width when the Floor is tall', async ({ page }) => {
  await mockFloor(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(`/floor/${SID}`)

  await expect(page.locator('.floor')).toBeVisible()
  await expect(page.locator('.composer')).toBeVisible()

  // Fill the real scroll region with a tall rework card, as a stalled build does.
  await page.evaluate(() => {
    const host = document.querySelector('.chat-scroll')
    if (!host) throw new Error('.chat-scroll not rendered')
    for (let i = 0; i < 40; i++) {
      const p = document.createElement('p')
      p.className = 'bubble factory'
      p.textContent = 'REWORK WRITER round 1/2 (build 1/6) — ' + 'returned HTTP 400 '.repeat(20)
      host.appendChild(p)
    }
  })

  // The scroll region absorbs the overflow; the composer is still fully on screen.
  const within = await page.evaluate(() => {
    const el = document.querySelector('.composer') as HTMLElement | null
    if (!el) return false
    const r = el.getBoundingClientRect()
    return r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight + 1
  })
  expect(within).toBe(true)
})
