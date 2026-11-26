import { ApiError } from './api/factory'
import type { ActionId } from './buildActions'

/**
 * One contract for what a press says back, where the finger is:
 *   busy label -> a one-line result in the action bar -> the next step as a
 *   button when the server names one.
 *
 * The result is read from the server's typed reply (`ok`, `summary`,
 * `refused`, `awaiting_action`), never from its wording. A failed request
 * shows the server's own human message when it sent one, otherwise a plain
 * sentence chosen by the HTTP status; never a raw body.
 */

/** A typed info reply from the chat route (the `info` SSE event). */
export interface InfoReply {
  ok?: boolean
  summary?: string
  refused?: string
  awaiting_action?: string
  stream_delta?: boolean
  /** The build was already running: the press started nothing new. */
  already_running?: boolean
}

export type OutcomeTone = 'done' | 'refused' | 'error'

export interface ActionOutcome {
  tone: OutcomeTone
  text: string
  /** The next step the server named, as one of the bar's own actions. */
  next: ActionId | null
}

/** The typed Floor actions on the wire, as the action bar's own actions. */
const WIRE_ACTION: Record<string, ActionId> = {
  continue: 'try_again',
  take_copy: 'download_copy',
  continue_with_intake: 'change_answers',
  start_over: 'start_over',
  run_pilot: 'run_pilot',
}

/** The bar action for a wire action the server is waiting on; null when it has none. */
/**
 * Hold the build card where the reader pressed: scroll the stream by however
 * far the card's top has moved from `anchor`. No anchor (no press in flight)
 * or a move under one pixel leaves the stream alone. Returns the distance
 * scrolled.
 */
export function holdCardTop(stream: { scrollTop: number }, cardTop: number, anchor: number | null): number {
  if (anchor === null) return 0
  const delta = cardTop - anchor
  if (Math.abs(delta) < 1) return 0
  stream.scrollTop += delta
  return delta
}

export function nextActionFor(awaiting: string | null | undefined): ActionId | null {
  if (!awaiting) return null
  return Object.prototype.hasOwnProperty.call(WIRE_ACTION, awaiting) ? WIRE_ACTION[awaiting] : null
}

/** Said when a press found the build already running. */
export const ALREADY_RUNNING = 'Nothing new was started: this build is still running.'

/** What a press that went through says, per action. */
const DONE_TEXT: Record<ActionId, string> = {
  stop: 'Stop requested. Work so far is kept.',
  try_again: 'Started again.',
  download_copy: 'Copy downloading.',
  change_answers: 'Your brief is open to change. Edit brief changes it.',
  start_over: 'Started from scratch.',
  copy_report: 'Copied',
  download_platform: 'Download started.',
  run_pilot: 'Pilot checks started.',
  open_repo: 'Repository opened.',
  check_again: 'Checked again.',
}

export function doneText(id: ActionId): string {
  return DONE_TEXT[id]
}

/**
 * The one-line result of a typed press: refused and why when the server said
 * no (`ok: false`), otherwise done. The next step comes only from the
 * server's `awaiting_action`, never from the summary's words.
 */
export function actionOutcome(id: ActionId, info: InfoReply | null | undefined): ActionOutcome {
  if (info && info.ok === false) {
    const why = (info.summary || '').trim()
    return {
      tone: 'refused',
      text: why ? `Refused: ${why}` : 'Refused by the Factory. Nothing was changed.',
      next: nextActionFor(info.awaiting_action),
    }
  }
  // The server's typed answer that a run is already going: nothing started.
  if (info?.already_running) return { tone: 'refused', text: ALREADY_RUNNING, next: null }
  return { tone: 'done', text: doneText(id), next: null }
}

/** A sentence a person can read: words, no envelope, no code, no path. */
function isPlainSentence(text: string): boolean {
  const s = text.trim()
  if (!s || s.length > 280 || !/\s/.test(s)) return false
  if (/[{}<>[\]]/.test(s)) return false
  // A route or file path ("poll /product/build-status") is not for people.
  if (/(^|\s)\/\w/.test(s)) return false
  return true
}

function messageIn(value: unknown): string | null {
  if (typeof value === 'string') return isPlainSentence(value) ? value.trim() : null
  if (value && typeof value === 'object') {
    const v = value as { message?: unknown; detail?: unknown }
    return messageIn(v.message) ?? messageIn(v.detail)
  }
  return null
}

/** The human message inside an error body (`detail.message`, `detail`, `message`). */
export function messageFromBody(raw: string): string | null {
  const text = raw.trim()
  if (!text) return null
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    return null
  }
  if (typeof data === 'string') return messageIn(data)
  if (data && typeof data === 'object') {
    const d = data as { detail?: unknown; message?: unknown }
    return messageIn(d.detail) ?? messageIn(d.message)
  }
  return null
}

/**
 * What went wrong, for a person. The server's own message when it sent one;
 * otherwise a sentence for the status; otherwise the caller's fallback.
 */
export function humanError(e: unknown, fallback: string): string {
  const raw = e instanceof Error ? e.message : typeof e === 'string' ? e : ''
  const fromBody = messageFromBody(raw)
  if (fromBody) return fromBody
  const status = e instanceof ApiError ? e.status : null
  if (status === 401) return 'You are signed out. Sign in again, then try again.'
  if (status === 429) return 'Too many requests right now. Wait a minute, then try again.'
  if (status !== null && status >= 500) return 'The Factory did not answer. Try again in a moment.'
  if (e instanceof TypeError) return 'No connection to the Factory. Check your connection, then try again.'
  // A sentence the client wrote itself (not a body) is shown as it is.
  if (status === null && isPlainSentence(raw) && !/^\s*[{[]/.test(raw)) return raw.trim()
  return fallback
}
