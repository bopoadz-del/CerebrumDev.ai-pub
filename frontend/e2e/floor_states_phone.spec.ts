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
 * (Check 2, the brief card, starts with PR-04.)
 */
import { expect, test, type Page } from '@playwright/test'
import { BUILD_STATES, FLOOR_STATES, SID, mockFloorStates, type FloorState } from './fixtures/floorStates'

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
          const vh = window.innerHeight
          const vw = window.innerWidth
          const stream = document.querySelector('[data-testid="floor-stream"]') as HTMLElement
          const s = stream.getBoundingClientRect()
          const streamOnScreen = s.top >= 0 && s.bottom <= vh + 1 && s.height >= 160
          const shown = (el: Element) => {
            const r = el.getBoundingClientRect()
            const cs = getComputedStyle(el)
            return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'
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
