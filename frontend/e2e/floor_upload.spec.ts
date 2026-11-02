/**
 * F-10b e2e (mocked, per the repo's route-fulfill harness — no live backend):
 * the Floor "+" picker's accept-set is DERIVED from GET /upload/allowed; a pick
 * uploads and then lists the indexed result; a backend 415 surfaces as an error.
 */
import { expect, test, type Page } from '@playwright/test'

const SID = 'sess_e2e_upload'
const ALLOWED = {
  extensions: ['.docx', '.pdf', '.xlsx'],
  accept: '.docx,.pdf,.xlsx',
  max_files: 10,
  max_file_bytes: 20 * 1024 * 1024,
}

async function mockFloor(page: Page) {
  await page.addInitScript(() => {
    localStorage.removeItem('cerebrum.factory.token')
    localStorage.setItem('cerebrum.factory.email', 'e2e.upload@factory.dev')
  })
  const json = (body: unknown, status = 200) => ({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
  await page.route('**/v1/auth/me', (r) =>
    r.fulfill(json({ email: 'e2e.upload@factory.dev', email_verified: true, account_id: 'acct_e2e' })),
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
  // The accept-set the "+" derives from (never hardwired in the frontend).
  await page.route(`**/v1/sessions/${SID}/upload/allowed`, (r) => r.fulfill(json(ALLOWED)))
}

test('Floor "+" derives its accept-set from the backend and lists the indexed result', async ({ page }) => {
  await mockFloor(page)
  await page.route(`**/v1/sessions/${SID}/upload/status`, (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'parsed' }) }),
  )
  await page.route(`**/v1/sessions/${SID}/upload/result`, (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'completed', total_chunks: 3, failed_files: [] }) }),
  )
  await page.route(`**/v1/sessions/${SID}/upload`, (r) =>
    r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ files_received: 1, job_id: 'j1' }) }),
  )

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })

  // Accept-set came from the backend, not a frontend literal.
  const input = page.getByTestId('floor-upload-input')
  await expect(input).toHaveAttribute('accept', '.docx,.pdf,.xlsx')
  await expect(page.getByTestId('floor-upload-add')).toBeEnabled()

  await input.setInputFiles({ name: 'spec.pdf', mimeType: 'application/pdf', buffer: Buffer.from('hello') })

  const result = page.getByTestId('floor-upload-result')
  await expect(result).toContainText('spec.pdf')
  await expect(result).toContainText('3 chunks indexed')
})

test('Floor "+" surfaces a backend 415 and shows no result', async ({ page }) => {
  await mockFloor(page)
  await page.route(`**/v1/sessions/${SID}/upload`, (r) =>
    r.fulfill({ status: 415, contentType: 'application/json', body: JSON.stringify({ detail: "File type '.exe' is not accepted" }) }),
  )

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Factory Floor' })).toBeVisible({ timeout: 20_000 })
  const input = page.getByTestId('floor-upload-input')
  await expect(page.getByTestId('floor-upload-add')).toBeEnabled()

  await input.setInputFiles({ name: 'malware.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') })

  await expect(page.getByTestId('floor-upload-error')).toContainText('not accepted')
  await expect(page.getByTestId('floor-upload-result')).toHaveCount(0)
})
