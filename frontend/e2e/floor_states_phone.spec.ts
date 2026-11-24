/**
 * Standing check B (UI revamp spec, section 5): every Floor state, on a phone.
 *
 * Each state fixture (e2e/fixtures/floorStates.ts) loads at 360x780; the
 * build states also at 360x640 and 412x915. For each it asserts:
 *   1. no horizontal overflow on the page or in the stream;
 *   3. the conversation is present: a user bubble exists, and a touch swipe
 *      on the stream moves it and brings that bubble into view;
 *   4. every enabled action button and the composer are inside the viewport,
 *      or reachable by scrolling the stream.
 *   2. the "Your brief" card is inside the viewport and shows the fixture's
 *      country, currency, build level and feature count (from PR-04).
 *
 * PR-02 adds the phone shell: the bottom tab bar, the top bar, the Sessions
 * drawer and its delete control, and no sideways scroll on any tab.
 */
import { expect, test, type Page } from '@playwright/test'
import {
  BUILD_STATES,
  FLOOR_STATES,
  SID,
  SUPPORT_EMAIL,
  briefFixture,
  mockFloorStates,
  type FloorState,
} from './fixtures/floorStates'

const PHONES = [
  { width: 360, height: 640 },
  { width: 360, height: 780 },
  { width: 412, height: 915 },
] as const

function viewportsFor(state: FloorState) {
  return BUILD_STATES.includes(state) ? PHONES : PHONES.filter((v) => v.width === 360 && v.height === 780)
}

async function openState(page: Page, state: FloorState) {
  await mockFloorStates(page)
  await page.goto(`/floor/${SID[state]}`)
  await expect(page.getByTestId('floor-stream')).toBeVisible()
  if (BUILD_STATES.includes(state)) {
    await expect(page.getByTestId('floor-coder-takeover')).toBeVisible()
  }
  if (state === 'feature-list-pending') {
    await expect(page.locator('.bubble.blueprint')).toBeVisible()
  }
  if (state !== 'new-session') {
    await expect(page.locator('.floor-stream .bubble.user').first()).toBeAttached()
  }
  // Let the one-time "bring the build card into view" scroll finish.
  await page.waitForTimeout(600)
}

/**
 * A real touch swipe on the stream: touchStart, a run of touchMoves, touchEnd,
 * sent as raw touch input (what a finger sends), not a scripted scrollTo.
 * A positive distance moves the finger down, which shows earlier content.
 */
async function swipeStream(page: Page, distance: number) {
  const box = await page.getByTestId('floor-stream').boundingBox()
  if (!box) throw new Error('stream not laid out')
  const x = Math.round(box.x + box.width / 2)
  const span = Math.min(Math.abs(distance), Math.round(box.height * 0.8))
  const dir = Math.sign(distance)
  const y0 = Math.round(box.y + box.height / 2 - (dir * span) / 2)
  const cdp = await page.context().newCDPSession(page)
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x, y: y0 }] })
  const steps = 10
  for (let i = 1; i <= steps; i++) {
    await cdp.send('Input.dispatchTouchEvent', {
      type: 'touchMove',
      touchPoints: [{ x, y: Math.round(y0 + (dir * span * i) / steps) }],
    })
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
  await cdp.detach()
  await page.waitForTimeout(150)
}

for (const state of FLOOR_STATES) {
  for (const vp of viewportsFor(state)) {
    test.describe(`${state} at ${vp.width}x${vp.height}`, () => {
      test.use({ viewport: { width: vp.width, height: vp.height }, hasTouch: true })

      test('no horizontal overflow on the page or in the stream', async ({ page }) => {
        await openState(page, state)
        const over = await page.evaluate(() => {
          const se = document.scrollingElement as Element
          const stream = document.querySelector('[data-testid="floor-stream"]') as HTMLElement
          const vw = document.documentElement.clientWidth
          const outside = [...document.querySelectorAll('.floor *')]
            .filter((e) => {
              const r = e.getBoundingClientRect()
              return r.width > 0 && r.right > vw + 1
            })
            .slice(0, 8)
            .map((e) => `${(e as HTMLElement).className || e.tagName}:${Math.round(e.getBoundingClientRect().right)}`)
          return {
            page: se.scrollWidth - se.clientWidth,
            stream: stream.scrollWidth - stream.clientWidth,
            outside,
          }
        })
        expect(over.page).toBeLessThanOrEqual(1)
        expect(over.stream).toBeLessThanOrEqual(1)
        expect(over.outside).toEqual([])
      })

      test('every enabled action and the composer can be reached', async ({ page }) => {
        await openState(page, state)
        const report = await page.evaluate(() => {
          // Open every collapsed section first: a control kept inside a closed
          // <details> has a layout box but is not painted, so it must be
          // measured as the user meets it -- revealed.
          document.querySelectorAll('.floor details').forEach((d) => {
            ;(d as HTMLDetailsElement).open = true
          })
          const vh = window.innerHeight
          const vw = window.innerWidth
          const stream = document.querySelector('[data-testid="floor-stream"]') as HTMLElement
          const s = stream.getBoundingClientRect()
          const streamOnScreen = s.top >= 0 && s.bottom <= vh + 1 && s.height >= 160
          const shown = (el: Element) => {
            const r = el.getBoundingClientRect()
            const cs = getComputedStyle(el)
            // checkVisibility() is false inside a skipped subtree, e.g. the
            // body of a closed <details> (PR-07's Technical detail): those
            // controls are not rendered until the row is opened.
            const rendered = typeof el.checkVisibility === 'function' ? el.checkVisibility() : true
            return rendered && r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'
          }
          const unreachable: string[] = []
          const controls = [...document.querySelectorAll('.floor button, .floor input:not([type=file]), .floor select, .floor a')]
            .filter(shown)
            .filter((el) => !(el as HTMLButtonElement).disabled || el.closest('.composer'))
          for (const el of controls) {
            const r = el.getBoundingClientRect()
            const name = (el.textContent || el.getAttribute('placeholder') || el.tagName).trim().slice(0, 40)
            if (r.left < 0 || r.right > vw + 1) {
              unreachable.push(`${name}: off screen sideways`)
              continue
            }
            if (stream.contains(el)) {
              // Reachable by scrolling the stream: inside its scroll range and
              // no taller than the stream's visible window.
              const top = r.top - s.top + stream.scrollTop
              const ok = streamOnScreen && top >= 0 && top + r.height <= stream.scrollHeight + 1 && r.height <= stream.clientHeight
              if (!ok) unreachable.push(`${name}: not reachable in the stream`)
            } else if (r.top < 0 || r.bottom > vh + 1) {
              unreachable.push(`${name}: pinned but off screen (${Math.round(r.top)}-${Math.round(r.bottom)} of ${vh})`)
            }
          }
          const composer = document.querySelector('.composer') as HTMLElement
          const c = composer.getBoundingClientRect()
          return {
            streamOnScreen,
            streamHeight: Math.round(s.height),
            composerInView: c.height > 0 && c.top >= 0 && c.bottom <= vh + 1,
            unreachable,
          }
        })
        expect(report.streamOnScreen, `stream ${report.streamHeight}px`).toBe(true)
        expect(report.composerInView).toBe(true)
        expect(report.unreachable).toEqual([])
      })

      test('the brief card is on screen and shows the brief', async ({ page }) => {
        await openState(page, state)
        const card = page.getByTestId('brief-card')
        await expect(card).toBeVisible()
        const box = await card.boundingBox()
        expect(box, 'brief card laid out').not.toBeNull()
        expect(box!.y).toBeGreaterThanOrEqual(0)
        expect(box!.y + box!.height).toBeLessThanOrEqual(vp.height)
        expect(box!.x).toBeGreaterThanOrEqual(0)
        expect(box!.x + box!.width).toBeLessThanOrEqual(vp.width + 1)
        const want = briefFixture(state)
        if (want.fields.country) {
          // The country by name, from the browser's own data.
          const name = await page.evaluate(
            (code) => new Intl.DisplayNames(undefined, { type: 'region' }).of(code) ?? code,
            want.fields.country,
          )
          await expect(card).toContainText(name)
        } else {
          await expect(card).toContainText('Not set')
        }
        if (want.fields.currency) await expect(card).toContainText(want.fields.currency)
        if (want.fields.build_level) {
          const level = want.fields.build_level
          await expect(card).toContainText(level[0].toUpperCase() + level.slice(1))
        }
        if (want.suggested) await expect(card.getByRole('button', { name: 'Confirm these' })).toBeVisible()
        await expect(page.getByTestId('brief-feature-count')).toContainText(
          want.features === null ? 'No feature list yet' : `${want.features} features`,
        )
        // Never greyed: the values keep full-contrast ink.
        const ink = await page.getByTestId('intake-declared').evaluate((el) => getComputedStyle(el).color)
        const muted = await page.evaluate(() =>
          getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim(),
        )
        expect(ink).not.toBe(muted)
        expect(await card.evaluate((el) => getComputedStyle(el).opacity)).toBe('1')
      })

      if (state !== 'new-session') {
        test('a touch swipe scrolls the stream and brings a user bubble into view', async ({ page }) => {
          await openState(page, state)
          const stream = page.getByTestId('floor-stream')
          const bubble = page.locator('.floor-stream .bubble.user').first()
          const inView = async () =>
            page.evaluate(() => {
              const st = document.querySelector('[data-testid="floor-stream"]') as HTMLElement
              const b = st.querySelector('.bubble.user') as HTMLElement
              const s = st.getBoundingClientRect()
              const r = b.getBoundingClientRect()
              return r.bottom > s.top && r.top < s.bottom
            })
          const scrollable = await stream.evaluate((el) => el.scrollHeight > el.clientHeight + 1)
          if (scrollable) {
            // Start at the end, then swipe down (finger moves down = content
            // moves down = earlier messages come into view).
            await stream.evaluate((el) => el.scrollTo({ top: el.scrollHeight, behavior: 'instant' as ScrollBehavior }))
            const before = await stream.evaluate((el) => el.scrollTop)
            await swipeStream(page, 300)
            const after = await stream.evaluate((el) => el.scrollTop)
            expect(after, 'the swipe moved the stream').toBeLessThan(before)
            for (let i = 0; i < 40 && !(await inView()); i++) await swipeStream(page, 400)
          }
          await expect(bubble).toBeAttached()
          expect(await inView()).toBe(true)
        })
      }
    })
  }
}

/* ---------------- PR-02: phone navigation and header ---------------- */

const TABS = ['Factory Floor', 'Your Platforms', 'Subscription', 'Account'] as const

for (const vp of [
  { width: 360, height: 780 },
  { width: 412, height: 915 },
]) {
  test.describe(`phone shell at ${vp.width}x${vp.height}`, () => {
    // isMobile + hasTouch: the page matches (hover: none), as a phone does.
    test.use({ viewport: vp, hasTouch: true, isMobile: true })

    test('four bottom tabs, whole labels, each at least 72x48', async ({ page }) => {
      await openState(page, 'stopped')
      const nav = page.getByRole('navigation', { name: 'Factory navigation' })
      const tabs = await nav.evaluate((el) => {
        const vh = window.innerHeight
        return [...el.querySelectorAll('button')].map((b) => {
          const r = b.getBoundingClientRect()
          const label = b.querySelector('.nav-label-short') as HTMLElement
          return {
            name: b.getAttribute('aria-label'),
            text: label.textContent,
            w: r.width,
            h: r.height,
            atBottom: r.bottom <= vh + 1 && r.bottom >= vh - 60,
            labelShown: getComputedStyle(label).display !== 'none',
            cut: label.scrollWidth > label.clientWidth,
          }
        })
      })
      expect(tabs.map((t) => t.name)).toEqual([...TABS])
      for (const t of tabs) {
        expect(t.labelShown, `${t.text} label shown`).toBe(true)
        expect(t.cut, `${t.text} label not cut`).toBe(false)
        expect(t.w, `${t.text} width`).toBeGreaterThanOrEqual(72)
        expect(t.h, `${t.text} height`).toBeGreaterThanOrEqual(48)
        expect(t.atBottom, `${t.text} sits in the bottom bar`).toBe(true)
      }
    })

    test('a 48px top bar: Sessions, the session name, New session; no support address', async ({ page }) => {
      await openState(page, 'stopped')
      const bar = await page.locator('.rail').evaluate((el) => {
        const r = el.getBoundingClientRect()
        const box = (q: string) => {
          const e = el.querySelector(q) as HTMLElement | null
          if (!e || getComputedStyle(e).display === 'none') return null
          const b = e.getBoundingClientRect()
          return { w: b.width, h: b.height }
        }
        const mail = [...el.querySelectorAll('a[href^="mailto:"]')].filter(
          (a) => (a as HTMLElement).getBoundingClientRect().width > 0 && !a.closest('.rail-sessions'),
        )
        return {
          top: r.top,
          h: r.height,
          toggle: box('.rail-sessions-toggle'),
          title: (el.querySelector('.topbar-title') as HTMLElement).innerText,
          titleBox: box('.topbar-title'),
          newBtn: box('.topbar-new'),
          mail: mail.length,
        }
      })
      expect(bar.top).toBe(0)
      expect(bar.h).toBeGreaterThanOrEqual(47)
      expect(bar.h).toBeLessThanOrEqual(49)
      expect(bar.toggle?.w ?? 0).toBeGreaterThanOrEqual(44)
      expect(bar.toggle?.h ?? 0).toBeGreaterThanOrEqual(44)
      expect(bar.newBtn?.w ?? 0).toBeGreaterThanOrEqual(44)
      expect(bar.titleBox).not.toBeNull()
      expect(bar.title).toBe('stopped')
      expect(bar.mail).toBe(0)
      await expect(page.locator('.rail').getByText(SUPPORT_EMAIL)).toBeHidden()
      // The Floor's own title row moved into the bar: no second New session.
      await expect(page.getByTestId('floor-new-session')).toBeHidden()
    })

    test('the drawer opens right under the bar and ends with the support address', async ({ page }) => {
      await openState(page, 'stopped')
      await page.getByRole('button', { name: 'Your sessions' }).click()
      const drawer = page.getByRole('region', { name: 'Your sessions' })
      await expect(drawer).toBeVisible()
      const gap = await page.evaluate(() => {
        const bar = document.querySelector('.rail') as HTMLElement
        const d = document.querySelector('.rail-sessions') as HTMLElement
        return Math.abs(d.getBoundingClientRect().top - bar.getBoundingClientRect().bottom)
      })
      expect(gap).toBeLessThanOrEqual(1)
      await expect(drawer.getByRole('link', { name: 'Contact support' })).toHaveAttribute(
        'href',
        `mailto:${SUPPORT_EMAIL}`,
      )
    })

    test('delete shows on touch; its confirm replaces the row and names the session', async ({ page }) => {
      await openState(page, 'stopped')
      expect(await page.evaluate(() => matchMedia('(hover: none)').matches)).toBe(true)
      await page.getByRole('button', { name: 'Your sessions' }).click()
      const del = page.getByRole('button', { name: 'Delete session: building', exact: true })
      await expect(del).toBeVisible()
      const box = await del.evaluate((el) => {
        const r = el.getBoundingClientRect()
        return { w: r.width, h: r.height, opacity: Number(getComputedStyle(el).opacity) }
      })
      expect(box.opacity).toBe(1)
      expect(box.w).toBeGreaterThanOrEqual(44)
      expect(box.h).toBeGreaterThanOrEqual(44)
      await del.click()
      const confirm = page.getByRole('group', { name: 'Delete building?' })
      await expect(confirm).toBeVisible()
      await expect(confirm.locator('.rail-session-confirm-name')).toHaveText('building')
      await expect(confirm.locator('xpath=ancestor::li[1]').locator('button.rail-session')).toBeHidden()
      for (const name of ['Delete', 'Keep']) {
        const h = await confirm
          .getByRole('button', { name, exact: true })
          .evaluate((el) => el.getBoundingClientRect().height)
        expect(h, `${name} height`).toBeGreaterThanOrEqual(44)
      }
    })

    test('no sideways scroll on Floor, Platforms, Plan or Account', async ({ page }) => {
      await openState(page, 'stopped')
      for (const tab of TABS) {
        await page.getByRole('button', { name: tab, exact: true }).click()
        await expect(page.getByRole('button', { name: tab, exact: true })).toHaveAttribute('aria-current', 'page')
        await page.waitForTimeout(300)
        const over = await page.evaluate(() => {
          const se = document.scrollingElement as Element
          const vw = document.documentElement.clientWidth
          const outside = [...document.querySelectorAll('main *, .rail *')]
            .filter((e) => {
              const r = e.getBoundingClientRect()
              return r.width > 0 && r.right > vw + 1 && !e.closest('.rail-sessions')
            })
            .slice(0, 6)
            .map((e) => `${(e as HTMLElement).className || e.tagName}:${Math.round(e.getBoundingClientRect().right)}`)
          return { page: se.scrollWidth - se.clientWidth, outside }
        })
        expect(over.page, `${tab} page overflow`).toBeLessThanOrEqual(1)
        expect(over.outside, `${tab} elements off screen`).toEqual([])
      }
    })

    test('the Account page carries the support address', async ({ page }) => {
      await openState(page, 'stopped')
      await page.getByRole('button', { name: 'Account', exact: true }).click()
      await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible()
      await expect(page.locator('main').getByRole('link', { name: 'Contact support' })).toHaveAttribute(
        'href',
        `mailto:${SUPPORT_EMAIL}`,
      )
    })
  })
}

/**
 * PR-03: tokens and base controls. Every button, field and select is at
 * least 44px tall, on the Floor, the sign-in screen and the Account page;
 * an outline button keeps its own look under the pointer (it never turns
 * into a gold primary).
 */
const CONTROLS =
  'button, select, textarea, input:not([type="checkbox"]):not([type="radio"]):not([type="file"]):not([type="hidden"])'

async function shortControls(page: Page): Promise<string[]> {
  return page.evaluate((sel) => {
    return [...document.querySelectorAll(sel)]
      .filter((el) => {
        const r = el.getBoundingClientRect()
        return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'
      })
      .filter((el) => el.getBoundingClientRect().height < 44 - 0.5)
      .map((el) => {
        const label = (el.getAttribute('aria-label') || el.textContent || el.getAttribute('name') || '').trim()
        return `${el.tagName.toLowerCase()}.${(el as HTMLElement).className || '-'} "${label.slice(0, 30)}" ${Math.round(el.getBoundingClientRect().height)}px`
      })
  }, CONTROLS)
}

test.describe('base controls at 360x780', () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true })

  for (const state of ['new-session', 'feature-list-pending', 'building', 'stopped', 'finished-pilot'] as const) {
    test(`every control on the Floor (${state}) is at least 44px tall`, async ({ page }) => {
      await openState(page, state)
      expect(await shortControls(page)).toEqual([])
    })
  }

  test('every control on the Account page is at least 44px tall', async ({ page }) => {
    await openState(page, 'stopped')
    await page.getByRole('button', { name: 'Account', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Account' })).toBeVisible()
    await page.waitForTimeout(300)
    expect(await shortControls(page)).toEqual([])
  })

  test('every control on the sign-in screen is at least 44px tall', async ({ page }) => {
    await mockFloorStates(page)
    // Registered last, so it wins: nobody is signed in.
    await page.route('**/v1/auth/me', (route) =>
      route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"not signed in"}' }),
    )
    await page.goto('/')
    await expect(page.locator('.auth-panel')).toBeVisible()
    const n = await page.locator(`.auth-panel :is(${CONTROLS})`).count()
    expect(n).toBeGreaterThan(1)
    expect(await shortControls(page)).toEqual([])
  })
})

test.describe('pointer hover at 360x780', () => {
  // A pointer device: (hover: hover) matches, so the hover rules apply.
  test.use({ viewport: { width: 360, height: 780 } })

  test('hovering an outline button does not turn it gold', async ({ page }) => {
    await openState(page, 'stopped')
    // PR-08: on a phone the state's actions sit in the action bar; its
    // outline button is the secondary "Download copy".
    const ghost = page.locator('[data-testid="floor-action-bar"] button.action-secondary:not(:disabled)').first()
    await ghost.scrollIntoViewIfNeeded()
    const before = await ghost.evaluate((el) => getComputedStyle(el).backgroundColor)
    await ghost.hover()
    await page.waitForTimeout(250)
    const after = await ghost.evaluate((el) => {
      const cs = getComputedStyle(el)
      return { bg: cs.backgroundColor, fg: cs.color }
    })
    const gold = ['rgb(240, 180, 41)', 'rgb(217, 154, 18)']
    expect(gold, `hover background ${after.bg} (was ${before})`).not.toContain(after.bg)
    // The label stays in the main ink, readable on the hover fill.
    expect(after.fg).toBe('rgb(232, 236, 241)')
  })
})

/**
 * PR-06: the edit-brief sheet. At 360px every label sits above its field,
 * every control is at least 44px tall, nothing overflows sideways with the
 * sheet open, and suggest -> change -> Save shows the changed country in the
 * brief card (the card shows what the server returned).
 */
test.describe('edit-brief sheet at 360x780', () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true })

  async function openSheet(page: Page, state: FloorState) {
    await openState(page, state)
    await page.getByTestId('intake-change').click()
    const sheet = page.getByRole('dialog', { name: 'Your brief' })
    await expect(sheet).toBeVisible()
    return sheet
  }

  for (const state of ['new-session', 'suggestion-pending'] as const) {
    test(`every label sits above its field (${state})`, async ({ page }) => {
      const sheet = await openSheet(page, state)
      const pairs = await sheet.evaluate((root) =>
        [...root.querySelectorAll('.sheet-field')].map((field) => {
          const label = field.querySelector(':scope > .field-label') as HTMLElement
          const control = field.querySelector(
            ':scope > input, :scope > .chip-row, :scope > .level-card',
          ) as HTMLElement
          return {
            name: label?.textContent ?? '?',
            labelBottom: label?.getBoundingClientRect().bottom ?? Infinity,
            controlTop: control?.getBoundingClientRect().top ?? -Infinity,
          }
        }),
      )
      expect(pairs.map((p) => p.name)).toEqual(['Build level', 'Industry', 'Country', 'Currency'])
      for (const p of pairs) expect(p.labelBottom, p.name).toBeLessThanOrEqual(p.controlTop + 0.5)
    })

    test(`every control is at least 44px tall and nothing overflows sideways (${state})`, async ({ page }) => {
      const sheet = await openSheet(page, state)
      expect(await shortControls(page)).toEqual([])
      const levelCards = await sheet.locator('.level-card').evaluateAll((els) =>
        els.map((el) => el.getBoundingClientRect().height),
      )
      expect(levelCards).toHaveLength(4)
      for (const h of levelCards) expect(h).toBeGreaterThanOrEqual(44)
      const overflow = await page.evaluate(() => {
        const sheetEl = document.querySelector('[data-testid="brief-sheet"]') as HTMLElement
        const body = sheetEl.querySelector('.sheet-body') as HTMLElement
        return {
          page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          sheet: sheetEl.scrollWidth - sheetEl.clientWidth,
          body: body.scrollWidth - body.clientWidth,
          right: sheetEl.getBoundingClientRect().right - window.innerWidth,
        }
      })
      expect(overflow.page).toBeLessThanOrEqual(0)
      expect(overflow.sheet).toBeLessThanOrEqual(0)
      expect(overflow.body).toBeLessThanOrEqual(0)
      expect(overflow.right).toBeLessThanOrEqual(0.5)
      // Save and Cancel are on screen, not below the fold.
      for (const name of ['Cancel', /^Save/]) {
        const box = await sheet.getByRole('button', { name }).boundingBox()
        expect(box && box.y + box.height).toBeLessThanOrEqual(780)
      }
    })
  }

  test('suggest, change, save: the brief card shows the changed country', async ({ page }) => {
    const sheet = await openSheet(page, 'suggestion-pending')
    const country = sheet.getByLabel('Country')
    // Pre-filled with the chat's suggestion, by name and code.
    await expect(country).toHaveValue(/\(AE\)$/)
    await country.fill('Swed')
    await sheet.getByRole('option', { name: /\(SE\)$/ }).click()
    await sheet.getByLabel('Currency').fill('SEK')
    await sheet.getByTestId('brief-save').click()
    await expect(sheet).toBeHidden()
    const values = page.getByTestId('brief-values')
    await expect(values).toContainText(/Sweden/)
    await expect(values).toContainText('SEK')
    await expect(page.getByTestId('intake-proposal')).toHaveCount(0)
  })

  test('an unknown code and half a pair each show an error, and Save waits', async ({ page }) => {
    const sheet = await openSheet(page, 'new-session')
    await sheet.getByLabel('Country').fill('QQ')
    await expect(sheet.getByText(/Not a country this browser knows/)).toBeVisible()
    await expect(sheet.getByTestId('pair-error')).toBeVisible()
    await expect(sheet.getByTestId('brief-save')).toBeDisabled()
    await expect(sheet.getByTestId('brief-save-reason')).toBeVisible()
  })

  test('Cancel closes the sheet and stores nothing', async ({ page }) => {
    let chats = 0
    page.on('request', (r) => {
      if (r.url().endsWith('/chat')) chats += 1
    })
    const sheet = await openSheet(page, 'suggestion-pending')
    await sheet.getByLabel('Country').fill('Japan')
    await sheet.getByRole('button', { name: 'Cancel' }).click()
    await expect(sheet).toBeHidden()
    expect(chats).toBe(0)
    await expect(page.getByTestId('intake-proposal')).toBeVisible()
  })

  test('after approval the brief is read-only with a lock', async ({ page }) => {
    await openState(page, 'building')
    await expect(page.getByTestId('brief-lock')).toBeVisible()
    await expect(page.getByTestId('intake-change')).toHaveCount(0)
  })
})

/**
 * PR-07: the BUILD card -- state line, one sentence, actions, steps and a
 * closed Technical detail. At 360px the stopped card is at most 360px tall
 * with Technical detail closed, and a stopped build's title is not green.
 */
test.describe('build card at 360x780', () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true })

  const GREEN = ['rgb(125, 211, 160)']

  // The fixture's stopped build is a timeout (failure.kind out_of_time): a
  // stop no check judged is amber (#283); a stalled build stays red.
  const TONE = { stopped: 'warning', stalled: 'danger' } as const
  for (const state of ['stopped', 'stalled'] as const) {
    test(`${state}: the card is at most 360px tall and its title is not green`, async ({ page }) => {
      await openState(page, state)
      const card = page.getByTestId('floor-coder-takeover')
      const detail = page.getByTestId('floor-tech-detail')
      if (await detail.count()) {
        expect(await detail.evaluate((el) => (el as HTMLDetailsElement).open)).toBe(false)
      }
      const box = await card.boundingBox()
      expect(box, 'card laid out').not.toBeNull()
      expect(box!.height).toBeLessThanOrEqual(360)
      const title = page.getByTestId('floor-build-state')
      await expect(title).toContainText('Stopped')
      const colour = await title.evaluate((el) => getComputedStyle(el).color)
      expect(GREEN).not.toContain(colour)
      await expect(card).toHaveAttribute('data-tone', TONE[state])
    })
  }

  test('stopped: no wire id or FAILED( text on the face; one raw message inside Technical detail', async ({
    page,
  }) => {
    await openState(page, 'stopped')
    const face = await page.getByTestId('floor-coder-takeover').evaluate((el) => {
      const copy = el.cloneNode(true) as HTMLElement
      copy.querySelector('[data-testid="floor-tech-detail"]')?.remove()
      copy.querySelector('[data-testid="floor-build-steps"]')?.remove()
      return copy.textContent ?? ''
    })
    for (const id of ['codewhale_worker_failed', 'worker_timed_out', 'writer_contract', 'REWORK', 'FAILED(', '7461cb639b']) {
      expect(face).not.toContain(id)
    }
    const detail = page.getByTestId('floor-tech-detail')
    await detail.locator('summary').click()
    await expect(detail.getByTestId('floor-failed-label')).toBeVisible()
    const text = (await detail.textContent()) ?? ''
    expect(text.split('headless job exceeded').length - 1).toBe(1)
    await expect(detail.getByRole('button', { name: 'Copy error report' })).toBeVisible()
    // Open, Technical detail scrolls inside itself and never widens the page.
    const overflow = await page.evaluate(() => document.scrollingElement!.scrollWidth - document.scrollingElement!.clientWidth)
    expect(overflow).toBeLessThanOrEqual(1)
  })

  test('fix round: one amber line, no reason code on the face', async ({ page }) => {
    await openState(page, 'fix-round')
    const line = page.getByTestId('floor-rework-line')
    await expect(line).toBeVisible()
    await expect(line).toContainText('A quality check sent work back')
    await expect(line).not.toContainText('writer_contract')
    await expect(page.getByTestId('floor-coder-takeover')).toHaveAttribute('data-tone', 'warning')
  })
})

/*
 * PR-08: the action bar. One set of verbs; pinned above the composer on a
 * phone. "Try again" and "Download copy" are on screen without scrolling on
 * a stopped or stalled build; a destructive press asks first and its safe
 * choice sends nothing; stacked buttons are full width with room between.
 */
function watchActionRequests(page: Page): string[] {
  const sent: string[] = []
  page.on('request', (req) => {
    if (req.method() !== 'POST') return
    const url = req.url()
    if (/\/chat(\/|\?|$)|\/coder-control|\/product\/generate/.test(url)) sent.push(url)
  })
  return sent
}

for (const vp of [
  { width: 360, height: 640 },
  { width: 360, height: 780 },
]) {
  test.describe(`action bar at ${vp.width}x${vp.height}`, () => {
    test.use({ viewport: vp, hasTouch: true, isMobile: true })

    for (const state of ['stopped', 'stalled'] as const) {
      test(`${state}: Try again and Download copy are fully on screen without scrolling`, async ({ page }) => {
        await openState(page, state)
        const bar = page.getByTestId('floor-action-bar')
        await expect(bar).toHaveAttribute('data-placement', 'bar')
        for (const id of ['floor-try-again', 'floor-take-copy']) {
          const box = await page.getByTestId(id).boundingBox()
          expect(box, `${id} laid out`).not.toBeNull()
          expect(box!.y).toBeGreaterThanOrEqual(0)
          expect(box!.y + box!.height).toBeLessThanOrEqual(vp.height)
          expect(box!.x).toBeGreaterThanOrEqual(0)
          expect(box!.x + box!.width).toBeLessThanOrEqual(vp.width)
        }
        await expect(page.getByTestId('floor-try-again')).toHaveText('Try again')
        await expect(page.getByTestId('floor-try-again')).toBeEnabled()
        // Not inside the card too: the actions appear in one place.
        await expect(page.getByTestId('floor-coder-takeover').getByTestId('floor-try-again')).toHaveCount(0)
      })

      test(`${state}: stacked buttons are full width with at least 8px between them`, async ({ page }) => {
        await openState(page, state)
        const rows = await page.evaluate(() => {
          const list = document.querySelector('[data-testid="floor-action-bar"] .action-list') as HTMLElement
          const lw = list.getBoundingClientRect().width
          return [...list.querySelectorAll(':scope > .action-item > .action-button, :scope > .action-more > .action-more-toggle')].map(
            (b) => {
              const r = b.getBoundingClientRect()
              return { top: r.top, bottom: r.bottom, width: r.width, listWidth: lw }
            },
          )
        })
        expect(rows.length).toBeGreaterThanOrEqual(2)
        for (const r of rows) expect(Math.abs(r.width - r.listWidth)).toBeLessThanOrEqual(1)
        for (let i = 1; i < rows.length; i++) expect(rows[i].top - rows[i - 1].bottom).toBeGreaterThanOrEqual(8)
      })
    }

    test('stopped: Start from scratch then Cancel sends no request', async ({ page }) => {
      await openState(page, 'stopped')
      const sent = watchActionRequests(page)
      await page.getByTestId('floor-action-more').click()
      await page.getByTestId('floor-start-over').click()
      const sheet = page.getByTestId('floor-confirm-sheet')
      await expect(sheet).toBeVisible()
      await expect(page.getByTestId('floor-confirm-keep')).toHaveText('Cancel')
      await expect(page.getByTestId('floor-confirm-keep')).toBeFocused()
      const box = await sheet.boundingBox()
      expect(box!.y).toBeGreaterThanOrEqual(0)
      expect(box!.y + box!.height).toBeLessThanOrEqual(vp.height + 1)
      await page.getByTestId('floor-confirm-keep').click()
      await expect(sheet).toHaveCount(0)
      await page.waitForTimeout(300)
      expect(sent).toEqual([])
    })

    test('building: Stop build then Keep building sends no request', async ({ page }) => {
      await openState(page, 'building')
      const sent = watchActionRequests(page)
      const stop = page.getByTestId('floor-coder-stop')
      await expect(stop).toHaveText('Stop build')
      await expect(page.getByRole('button', { name: /^(Pause|Resume)$/ })).toHaveCount(0)
      await stop.click()
      await expect(page.getByTestId('floor-confirm-sheet')).toContainText('Stop this build?')
      await page.getByTestId('floor-confirm-keep').click()
      await expect(page.getByTestId('floor-confirm-sheet')).toHaveCount(0)
      await page.waitForTimeout(300)
      expect(sent).toEqual([])
    })
  })
}

/*
 * PR-09: buttons answer where you pressed. After a press the visible screen
 * changes (a result line in the action bar, or the sheet), and the reader is
 * not moved away from the buttons.
 */
test.describe('action results at 360x780', () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true })

  async function cardTop(page: Page): Promise<number> {
    const box = await page.getByTestId('floor-coder-takeover').boundingBox()
    if (!box) throw new Error('build card not laid out')
    return box.y
  }

  test('stopped: Download copy changes the screen, answers in the bar and keeps the position', async ({ page }) => {
    await openState(page, 'stopped')
    const before = await page.screenshot()
    const topBefore = await cardTop(page)
    const copy = page.getByTestId('floor-take-copy')
    const copyBox = await copy.boundingBox()
    await copy.click()
    const result = page.getByTestId('floor-action-result')
    await expect(result).toBeVisible()
    await expect(page.getByTestId('floor-action-result-text')).toHaveText('Copy downloading.')
    await expect(page.getByTestId('floor-action-bar')).toContainText('Copy downloading.')
    const box = await result.boundingBox()
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.y + box!.height).toBeLessThanOrEqual(780)
    await page.waitForTimeout(400)
    expect(Math.abs((await cardTop(page)) - topBefore)).toBeLessThanOrEqual(2)
    // The pressed button is still under the finger (the bar may grow upward
    // by the result line, never sideways or off screen).
    const after = await copy.boundingBox()
    expect(Math.abs(after!.x - copyBox!.x)).toBeLessThanOrEqual(1)
    expect(after!.y + after!.height).toBeLessThanOrEqual(780)
    expect(Buffer.compare(before, await page.screenshot())).not.toBe(0)
  })

  test('stopped: Change answers opens the sheet in the viewport with "Save and try again"', async ({ page }) => {
    await openState(page, 'stopped')
    const before = await page.screenshot()
    const topBefore = await cardTop(page)
    await page.getByTestId('floor-action-more').click()
    await page.getByTestId('floor-continue-with-intake').click()
    const sheet = page.getByTestId('brief-sheet')
    await expect(sheet).toBeVisible()
    await expect(page.getByTestId('brief-save')).toHaveText('Save and try again')
    await expect(sheet.getByRole('button', { name: 'Cancel' })).toBeVisible()
    const box = await sheet.boundingBox()
    expect(box!.y).toBeGreaterThanOrEqual(0)
    expect(box!.y + box!.height).toBeLessThanOrEqual(781)
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(360)
    expect(Math.abs((await cardTop(page)) - topBefore)).toBeLessThanOrEqual(2)
    expect(Buffer.compare(before, await page.screenshot())).not.toBe(0)
    // Cancel closes it; the result line is still where the finger was.
    await sheet.getByRole('button', { name: 'Cancel' }).click()
    await expect(sheet).toHaveCount(0)
    await expect(page.getByTestId('floor-action-result-text')).toHaveText(
      'Your brief is open to change. Edit brief changes it.',
    )
    // The pre-filled answers are not a chat suggestion: no second gold button.
    await expect(page.getByTestId('intake-confirm')).toHaveCount(0)
    await expect(page.getByTestId('intake-change')).toBeVisible()
  })

  test('a refused press says why in the bar and the close control clears it', async ({ page }) => {
    await openState(page, 'stopped')
    await page.route(/\/v1\/sessions\/[^/]+\/chat$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          `event: info\ndata: ${JSON.stringify({ ok: false, summary: 'Choose a build level first.' })}\n\n` +
          'event: done\ndata: ""\n\n',
      }),
    )
    await page.getByTestId('floor-try-again').click()
    await expect(page.getByTestId('floor-action-result')).toHaveAttribute('data-tone', 'refused')
    await expect(page.getByTestId('floor-action-result-text')).toHaveText('Refused: Choose a build level first.')
    const close = page.getByTestId('floor-action-result-close')
    const cb = await close.boundingBox()
    expect(cb!.width).toBeGreaterThanOrEqual(44)
    expect(cb!.height).toBeGreaterThanOrEqual(44)
    await close.click()
    await expect(page.getByTestId('floor-action-result')).toHaveCount(0)
  })
})

/*
 * PR-10: the stepper and one name table. At 360px the steps are one row, at
 * most 56px tall, with exactly one current step on a running or stopped
 * build; no wire id or old step name is on the card's face; a finished
 * prototype reads "Ready - Prototype", lists only the checks its level
 * includes, shows no score it did not measure, and has one gold Download.
 */
test.describe('stepper at 360x780', () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true })

  const STEP_WIRE = ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER', 'GRADER', 'RESEARCHER', 'WORKERS']

  test('not-found: no stepper, because the server knows no steps of this build', async ({ page }) => {
    await openState(page, 'not-found')
    await expect(page.getByTestId('floor-stepper-row')).toHaveCount(0)
  })

  for (const state of BUILD_STATES.filter((s) => s !== 'not-found')) {
    test(`${state}: one row, at most 56px tall, no wire id on the face`, async ({ page }) => {
      await openState(page, state)
      const row = page.getByTestId('floor-stepper-row')
      await expect(row).toBeVisible()
      const box = await row.boundingBox()
      expect(box, 'stepper laid out').not.toBeNull()
      expect(box!.height).toBeLessThanOrEqual(56)
      // One row: every step's dot sits on the same line.
      const tops = await row.locator('.stepper-dot').evaluateAll((dots) =>
        dots.map((d) => Math.round(d.getBoundingClientRect().top)),
      )
      expect(tops.length).toBe(5)
      expect(new Set(tops).size).toBe(1)
      const atNow = row.locator('li[data-state="current"], li[data-state="fixing"], li[data-state="stopped"]')
      if (state.startsWith('finished')) await expect(atNow).toHaveCount(0)
      else await expect(atNow).toHaveCount(1)
      const face = await page.getByTestId('floor-coder-takeover').evaluate((el) => {
        const copy = el.cloneNode(true) as HTMLElement
        copy.querySelector('[data-testid="floor-tech-detail"]')?.remove()
        return copy.textContent ?? ''
      })
      for (const id of STEP_WIRE) expect(face).not.toContain(id)
    })
  }

  test('finished prototype: "Ready - Prototype", only its checks, no unmeasured score, one gold Download', async ({
    page,
  }) => {
    await openState(page, 'finished-prototype')
    await expect(page.getByTestId('floor-build-state')).toContainText('Ready — Prototype')
    await expect(page.getByTestId('floor-build-level')).toHaveText('Build level: Prototype')
    await expect(page.getByTestId('floor-gate-code')).toContainText('Code checks passed')
    await expect(page.getByTestId('floor-gate-product')).toHaveCount(0)
    await expect(page.getByTestId('floor-gate-store')).toHaveCount(0)
    await expect(page.getByTestId('floor-acceptance-score')).toHaveCount(0)
    await expect(page.getByTestId('floor-build-sentence')).toHaveText(
      'Prototype does not include the pilot tests or the Store check. For a higher level, start a new session.',
    )
    await expect(page.getByTestId('floor-stepper-caption')).toHaveText('5 of 5 steps done')
    const gold = page.locator('.action-primary:visible')
    await expect(gold).toHaveCount(1)
    await expect(gold).toContainText('Download')
  })
})

/*
 * PR-12: words when it breaks, and the running line. The stop sentence is
 * written from the server's stop record; the running card is one line, one
 * whole-build bar and the timers; a stop you asked for is its own neutral
 * card; a build the server cannot find is its own state with "Check again".
 */
test.describe('stop words and the running line at 360x780', () => {
  test.use({ viewport: { width: 360, height: 780 }, hasTouch: true, isMobile: true })

  test('building: one progress line, one whole-build bar and the three timers', async ({ page }) => {
    await openState(page, 'building')
    const card = page.getByTestId('floor-coder-takeover')
    await expect(card.getByTestId('floor-running-line')).toHaveCount(1)
    await expect(card.getByTestId('floor-running-line')).toHaveText('Step 3 of 5 — Writing the code — 2 of 5 features')
    await expect(card.getByRole('progressbar')).toHaveCount(1)
    const timers = card.getByTestId('floor-running-timers')
    await expect(timers.locator('[data-timer]')).toHaveCount(3)
    await expect(timers.locator('[data-timer="started"]')).toContainText('Started')
    await expect(timers.locator('[data-timer="updated"]')).toContainText('Last update')
    await expect(timers.locator('[data-timer="expected"]')).toContainText('Expected end')
    // Everything on the running face is inside the card's width.
    const box = (await card.boundingBox())!
    for (const el of await card.locator('.running *').all()) {
      const b = await el.boundingBox()
      if (b) expect(b.x + b.width).toBeLessThanOrEqual(box.x + box.width + 1)
    }
  })

  test('stopped: the sentence is written from the stop record', async ({ page }) => {
    await openState(page, 'stopped')
    await expect(page.getByTestId('floor-build-state')).toContainText('Stopped at step 3 of 5')
    await expect(page.getByTestId('floor-build-sentence')).toHaveText(
      'Writing the code ran out of time after about 38 minutes. This is on our side, not in your brief. Steps 1 and 2 are done and kept.',
    )
    await expect(page.getByTestId('floor-try-again')).toContainText('Try again')
  })

  test('user-stopped: its own neutral card, Try again first', async ({ page }) => {
    await openState(page, 'user-stopped')
    const card = page.getByTestId('floor-coder-takeover')
    await expect(card).toHaveAttribute('data-tone', 'neutral')
    await expect(page.getByTestId('floor-build-sentence')).toHaveText(
      'You stopped this build at step 3. Steps 1 and 2 are done and kept.',
    )
    await expect(page.getByTestId('floor-try-again')).toBeInViewport()
  })

  test('user-stopped: the buttons are exactly the actions the server allows', async ({ page }) => {
    await openState(page, 'user-stopped')
    const bar = page.getByTestId('floor-action-bar')
    if (await bar.getByTestId('floor-action-more').count()) await bar.getByTestId('floor-action-more').click()
    // The fixture's build-status sends the server's list for an owner stop.
    for (const id of ['floor-try-again', 'floor-take-copy', 'floor-continue-with-intake', 'floor-start-over']) {
      await expect(bar.getByTestId(id)).toHaveCount(1)
    }
    await expect(bar.getByTestId('floor-coder-stop')).toHaveCount(0)
    await expect(bar.getByTestId('floor-check-again')).toHaveCount(0)
    await expect(bar.getByTestId('continue-to-pilot')).toHaveCount(0)
  })

  test('not-found: its own state, "Check again" asks the server once more', async ({ page }) => {
    await openState(page, 'not-found')
    await expect(page.getByTestId('floor-build-state')).toContainText("We can't find this build")
    await expect(page.getByTestId('floor-build-sentence')).toHaveText(
      "We can't find this build on the server. Your brief and feature list are safe.",
    )
    let reads = 0
    page.on('request', (req) => {
      if (req.url().includes('/product/build-status')) reads += 1
    })
    const check = page.getByTestId('floor-check-again')
    await expect(check).toBeInViewport()
    await check.click()
    await expect.poll(() => reads).toBeGreaterThanOrEqual(1)
    await expect(page.getByTestId('floor-action-result')).toContainText('Still not found')
  })
})
