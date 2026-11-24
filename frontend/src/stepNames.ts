/**
 * One name per thing (spec section 4.4).
 *
 * The wire ids stay exactly as the build record carries them: every
 * build_ledger.jsonl ever written, every stored session and every gate name
 * holds 'CLONER' and 'WRITER' as literal strings, so renaming them would
 * rewrite history. What the screen shows is a label, and every client string
 * that names a step, a check or the build level reads it from this table.
 * Raw ids appear only inside Technical detail.
 */

export interface StepName {
  /** The label: "Write the code". */
  label: string
  /** The running form: "Writing the code". */
  running: string
}

/** The five build steps, keyed by their wire id, in build order. */
export const STEP_NAMES: Readonly<Record<string, StepName>> = {
  COLLECTOR: { label: 'Plan the parts', running: 'Planning the parts' },
  CLONER: { label: 'Gather building blocks', running: 'Gathering building blocks' },
  WRITER: { label: 'Write the code', running: 'Writing the code' },
  TESTER: { label: 'Test', running: 'Testing' },
  STORE_MANAGER: { label: 'Package', running: 'Packaging' },
}

/** The run's step order when the record does not carry its own list. */
export const STEP_ORDER: readonly string[] = Object.keys(STEP_NAMES)

/**
 * The review inside step 3. Its verdict travels in the ledger under this gate
 * name (backend ``app.factory.build.grader.GRADER_GATE``); a decision from any
 * other gate is not a review, whatever step it sent back.
 */
export const REVIEW_GATE = 'grader'
export const REVIEW_NAME = 'Quality check'

/**
 * What a step counts while it works, as the screen names it. The record's
 * progress carries a unit (``handlers`` while the code is written); the
 * screen says "features". A unit the table does not know reads as "parts".
 */
const UNIT_NAMES: Readonly<Record<string, string>> = {
  handlers: 'features',
  capabilities: 'features',
  features: 'features',
  blocks: 'building blocks',
}

export function unitName(unit: string | null | undefined): string {
  return UNIT_NAMES[String(unit ?? '').trim().toLowerCase()] ?? 'parts'
}

/** The three kinds of check, keyed by their wire id, in the order they run. */
export const CHECK_NAMES: Readonly<Record<string, { label: string; phrase: string }>> = {
  CODE: { label: 'Code checks', phrase: 'the code checks' },
  PRODUCT: { label: 'Pilot tests', phrase: 'the pilot tests' },
  STORE: { label: 'Store check', phrase: 'the Store check' },
}

export const CHECK_ORDER: readonly string[] = Object.keys(CHECK_NAMES)

/** The verdict words a check can carry, as the screen says them. */
const VERDICT_WORDS: Readonly<Record<string, string>> = {
  PASS: 'passed',
  FAIL: 'failed',
  NOT_RUN: 'not run',
}

/**
 * The label of a step. An id the table does not know reads as its position
 * ("Step 4") or "This step" -- never as the raw id.
 */
export function stepLabel(id: string | null | undefined, position?: number | null): string {
  const known = id ? STEP_NAMES[id] : undefined
  if (known) return known.label
  return position ? `Step ${position}` : 'This step'
}

/** The running form of a step, with the same fallback as ``stepLabel``. */
export function stepRunning(id: string | null | undefined, position?: number | null): string {
  const known = id ? STEP_NAMES[id] : undefined
  if (known) return known.running
  return position ? `Step ${position}` : 'This step'
}

/** "Code checks", or null for a check id the table does not know. */
export function checkLabel(id: string): string | null {
  return CHECK_NAMES[id]?.label ?? null
}

/** "passed" / "failed" / "not run"; null for a verdict the table does not know. */
export function verdictWord(verdict: string | null | undefined): string | null {
  const key = String(verdict ?? '').trim().replace(/\s+/g, '_').toUpperCase()
  return VERDICT_WORDS[key] ?? null
}

/**
 * The build level as the user chose it: the server's own word, capitalised
 * ("prototype" -> "Prototype"). Never a grade name, never a stop gate.
 */
export function levelWord(level: string | null | undefined): string | null {
  const word = String(level ?? '').trim().replace(/[_-]+/g, ' ')
  if (!word) return null
  return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()
}

/**
 * The checks a build level includes, in run order: every check up to and
 * including the gate the level stops at. A level that names no stop gate, or
 * one the table does not know, includes all three.
 */
export function checksIncluded(stopGate: string | null | undefined): string[] {
  const at = CHECK_ORDER.indexOf(String(stopGate ?? '').toUpperCase())
  return at >= 0 ? CHECK_ORDER.slice(0, at + 1) : [...CHECK_ORDER]
}

/** The checks a level leaves out, in run order. */
export function checksSkipped(stopGate: string | null | undefined): string[] {
  const included = new Set(checksIncluded(stopGate))
  return CHECK_ORDER.filter((id) => !included.has(id))
}

/** "a", "a or b", "a, b or c". */
export function orList(items: string[]): string {
  if (items.length <= 1) return items.join('')
  return `${items.slice(0, -1).join(', ')} or ${items[items.length - 1]}`
}

/**
 * Every wire id this table renames. A user-visible string outside Technical
 * detail must contain none of them (the scan test reads this list).
 */
export const WIRE_IDS: readonly string[] = [
  ...STEP_ORDER,
  REVIEW_GATE.toUpperCase(),
  ...CHECK_ORDER,
]
