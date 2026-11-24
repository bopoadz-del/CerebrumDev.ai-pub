import type { StopRecord } from './api/factory'
import { stepRunning } from './stepNames'

/**
 * Words when it breaks (spec section 4.5).
 *
 * The page writes the stop sentence from the server's stop record (PR-11):
 * the kind of stop (a closed set), the step, the steps done, the fix rounds
 * used and the time. It never reads error text. Four parts, in this order:
 *
 *   1. where it stopped   -- the state line: "Stopped at step 3 of 5"
 *   2. why, in plain words -- one sentence, and whose side it is on
 *   3. what is kept        -- "Steps 1 and 2 are done and kept."
 *   4. what you can do now -- the primary action, by its exact label
 *
 * Machine text (codes, the reason chain, the raw message, the reference)
 * stays under ``stop.technical`` and is rendered only in Technical detail.
 */

/** Said when the cause is on the Factory's side, never in the user's brief. */
export const OUR_SIDE = 'This is on our side, not in your brief.'

/** The stop kinds whose cause is the Factory's own. */
const OUR_SIDE_KINDS = new Set(['time_ran_out', 'service_interrupted', 'nothing_produced'])

export type StopPrimary = 'try_again' | 'check_again'

export interface StopWords {
  /** The state words: "Stopped", or the not-found state's own words. */
  word: string
  /** "step 3 of 5", or null when the record does not say. */
  where: string | null
  /** Why, whose side, what is kept: the sentences after the state line. */
  sentence: string
  /** The state's primary action. Its label and line come from the action bar. */
  primary: StopPrimary
}

export function keptSentence(n: number): string {
  if (n <= 0) return 'No step was finished yet.'
  if (n === 1) return 'Step 1 is done and kept.'
  if (n === 2) return 'Steps 1 and 2 are done and kept.'
  return `Steps 1 to ${n} are done and kept.`
}

/** Whole minutes, at least 1: the one time figure a sentence carries. */
export function minutesOf(seconds: number): number {
  return Math.max(1, Math.round(seconds / 60))
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1)
}

function plural(n: number, one: string): string {
  return `${n} ${one}${n === 1 ? '' : 's'}`
}

/** The check's own plain sentence: the server's typed field, never a guess. */
function checkOwnSentence(stop: StopRecord): string | null {
  const text = (stop.check_plain ?? '').trim()
  return text || null
}

/**
 * The words for a stopped build, from its stop record.
 *
 * ``quietS`` is how long the build has sent no update (for "no progress"); it
 * comes from the status's own last-event age, not from the stop record.
 */
export function stopWords(stop: StopRecord, opts: { quietS?: number | null } = {}): StopWords {
  const at = typeof stop.step_index === 'number' && stop.step_index > 0 ? stop.step_index : null
  const total = typeof stop.steps_total === 'number' && stop.steps_total > 0 ? stop.steps_total : null
  const where = at && total ? `step ${at} of ${total}` : null
  const knownStep = Boolean(stop.step) || at != null
  const running = knownStep ? stepRunning(stop.step, at) : null
  // The step it stopped at is not done: the kept steps end before it, so the
  // sentence never says step 3 is kept while it reads "Stopped at step 3".
  const done = Math.max(0, stop.steps_done ?? 0)
  const kept = keptSentence(at ? Math.min(done, at - 1) : done)
  const elapsed = typeof stop.elapsed_s === 'number' && stop.elapsed_s > 0 ? stop.elapsed_s : null

  if (stop.kind === 'not_found') {
    return {
      word: "We can't find this build",
      where: null,
      sentence: "We can't find this build on the server. Your brief and feature list are safe.",
      primary: 'check_again',
    }
  }

  let why: string
  switch (stop.kind) {
    case 'time_ran_out':
      why = `${running ?? 'The build'} ran out of time${elapsed ? ` after about ${minutesOf(elapsed)} minutes` : ''}.`
      break
    case 'service_interrupted': {
      // The server says whether its service restarted or failed in place.
      const what = stop.restarted ? 'restarted' : 'was interrupted'
      why = running
        ? `Our build service ${what} while ${lowerFirst(running)}.`
        : `Our build service ${what} during the build.`
      break
    }
    case 'nothing_produced':
      why = `${running ?? 'The build'} stopped without producing anything.`
      break
    case 'no_progress': {
      const quiet = typeof opts.quietS === 'number' && opts.quietS > 0 ? opts.quietS : null
      why = quiet
        ? `No update from the build for ${minutesOf(quiet)} minutes.`
        : 'No update from the build for a long time.'
      break
    }
    case 'user_stopped':
      why = at ? `You stopped this build at step ${at}.` : 'You stopped this build.'
      break
    case 'check_failed': {
      const rounds = stop.fix_rounds ?? 0
      const lead = rounds > 0 ? `A quality check did not pass after ${plural(rounds, 'fix round')}.` : 'A quality check did not pass.'
      const own = checkOwnSentence(stop)
      why = own ? `${lead} ${own}` : lead
      break
    }
    default:
      // A kind this page does not know yet: the server's own sentence.
      why = (stop.plain ?? '').trim() || 'The build stopped before it finished.'
  }
  const side = OUR_SIDE_KINDS.has(stop.kind) ? ` ${OUR_SIDE}` : ''
  return { word: 'Stopped', where, sentence: `${why}${side} ${kept}`, primary: 'try_again' }
}
