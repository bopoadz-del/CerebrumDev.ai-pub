/**
 * Scrolling rules for the Floor's one stream (spec section 4.2, "Rules for
 * scrolling"). The stream is the only scroller; the page never scrolls itself
 * except in the two cases below.
 *
 * 1. Follow new content only when a NEW MESSAGE arrives and the reader is
 *    already within FOLLOW_WITHIN_PX of the bottom. A status update (a build
 *    poll, a streamed word) never moves the reader.
 * 2. When the build first reaches a stopped or finished state, bring the top
 *    of the build card into view once.
 */

/** How close to the bottom the reader must be for a new message to be followed. */
export const FOLLOW_WITHIN_PX = 80

export type StreamEvent = 'new-message' | 'status-update'

/** Distance in px between the visible bottom of a scroller and its content end. */
export function distanceFromBottom(el: {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}): number {
  return Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight)
}

/** True only for a new message while the reader is within FOLLOW_WITHIN_PX of the bottom. */
export function shouldFollow(event: StreamEvent, distancePx: number): boolean {
  if (event !== 'new-message') return false
  return distancePx <= FOLLOW_WITHIN_PX
}

/** Build states that end a run: the card is brought into view once on reaching one. */
const SETTLED_STATES = new Set(['failed', 'stalled', 'succeeded', 'not_found'])

/**
 * The key of a settled build state, or null while the build is still moving.
 * The card is scrolled into view when this key changes to a non-null value.
 */
export function settledKey(state: string | null | undefined): string | null {
  if (!state) return null
  return SETTLED_STATES.has(state) ? state : null
}

/** Smooth scrolling unless the reader asked the system for reduced motion. */
export function scrollBehavior(
  matchMedia: ((query: string) => { matches: boolean }) | undefined = typeof window !== 'undefined'
    ? window.matchMedia?.bind(window)
    : undefined,
): ScrollBehavior {
  if (!matchMedia) return 'auto'
  return matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
}
