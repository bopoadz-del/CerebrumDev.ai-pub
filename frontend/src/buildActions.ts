import type { BuildStatus } from './api/factory'
import { stoppedAt } from './buildCard'
import { exportAffordance, isPilotZipReady } from './buildProgress'

/**
 * One pure function from a build's state to its ordered actions.
 *
 * The rules (the button matrix):
 *   - at most one primary action, and never a primary on a destructive one;
 *   - a destructive action always asks first, with the safe choice as default;
 *   - every action carries one visible line saying what it does;
 *   - a disabled action carries the reason, in words;
 *   - one label per action, the same on every state.
 *
 * Only typed facts are read: the state, the owner's own stop, the runner's
 * STOP decision, the step, the delivery link and the export affordance.
 * Nothing here reads error text.
 */

export type ActionId =
  | 'stop'
  | 'try_again'
  | 'download_copy'
  | 'change_answers'
  | 'start_over'
  | 'copy_report'
  | 'download_platform'
  | 'run_pilot'
  | 'open_repo'

export type ActionStyle = 'primary' | 'secondary' | 'quiet' | 'danger'

export interface ConfirmSpec {
  question: string
  body: string
  /** The safe choice; it is the default and sends nothing. */
  keep: string
  /** The choice that acts. */
  go: string
}

export interface BuildAction {
  id: ActionId
  label: string
  /** The one visible line under the label. */
  sub: string
  style: ActionStyle
  /** Why the action cannot be pressed now, in words. Null when it can. */
  disabled: string | null
  confirm?: ConfirmSpec
  /** A link action opens this address instead of sending anything. */
  href?: string
}

export interface ActionPlan {
  /** The state's actions, in order. */
  bar: BuildAction[]
  /** The quiet "More" menu, in order. Empty when the state has none. */
  more: BuildAction[]
  /** Access is paused: a banner with "See plans" sits on top of any state. */
  accessPaused: boolean
  /** A visible reason the state's usual action is not offered. */
  note?: string | null
}

/** Why the build stopped, from typed facts only. */
export type StopKind = 'by_you' | 'check' | 'our_side'

export function stopKind(build: BuildStatus | null | undefined): StopKind | null {
  if (!build || (build.state !== 'failed' && build.state !== 'stalled')) return null
  if (build.coder_control === 'stop') return 'by_you'
  if (build.state === 'stalled') return 'our_side'
  // The runner's own STOP decision: a check used up its fix rounds.
  if (build.stopped) return 'check'
  return 'our_side'
}

export interface ActionInput {
  build: BuildStatus | null
  accessPaused?: boolean
  /** A chat request is running. */
  busy?: boolean
  /** A download is being packed. */
  downloading?: boolean
  /** Stop was pressed and the build has not stopped yet. */
  stopping?: boolean
  /** The action whose press is in flight: it shows its busy label. */
  pending?: ActionId | null
}

/** The label a pressed action shows while its request runs. */
export const BUSY_LABEL: Record<ActionId, string> = {
  stop: 'Stopping…',
  try_again: 'Starting…',
  download_copy: 'Packing…',
  change_answers: 'Opening…',
  start_over: 'Starting…',
  copy_report: 'Copying…',
  download_platform: 'Packing…',
  run_pilot: 'Starting…',
  open_repo: 'Opening…',
}

/** The pressed action shows its busy label and cannot be pressed twice. */
function withPending<T extends Pick<ActionPlan, 'bar' | 'more'>>(plan: T, pending: ActionId | null | undefined): T {
  if (!pending) return plan
  const mark = (a: BuildAction): BuildAction =>
    a.id === pending && !a.href ? { ...a, label: BUSY_LABEL[a.id], disabled: BUSY_LABEL[a.id] } : a
  return { ...plan, bar: plan.bar.map(mark), more: plan.more.map(mark) }
}

export const STOP_CONFIRM: ConfirmSpec = {
  question: 'Stop this build?',
  body: 'Work so far is kept and you can try again.',
  keep: 'Keep building',
  go: 'Stop build',
}

export const START_OVER_CONFIRM: ConfirmSpec = {
  question: 'Start from scratch?',
  body: 'The approved feature list is built again in a fresh workspace. The current work is archived, not deleted.',
  keep: 'Cancel',
  go: 'Start from scratch',
}

const PAUSED = 'Access is paused.'
const NOT_RELEASED = 'Not available yet: this build has not passed every check its level needs.'
const WAIT = 'Wait for the current request to finish.'

function startsWork(input: ActionInput): string | null {
  if (input.accessPaused) return PAUSED
  if (input.busy) return WAIT
  return null
}

function packing(input: ActionInput): string | null {
  return input.downloading ? 'Packing…' : null
}

function tryAgain(input: ActionInput, kind: StopKind | null, step: number | null): BuildAction {
  let sub: string
  if (kind === 'check') {
    sub = step ? `One more attempt at step ${step}.` : 'One more attempt at the step that stopped.'
  } else if (kind === 'by_you') {
    sub = step ? `Resumes at step ${step}, where you stopped it.` : 'Resumes where you stopped it.'
  } else {
    sub = step ? `Picks up at step ${step}. Finished steps are kept.` : 'Picks up where it stopped. Finished steps are kept.'
  }
  return { id: 'try_again', label: 'Try again', sub, style: 'primary', disabled: startsWork(input) }
}

function downloadCopy(input: ActionInput, style: ActionStyle): BuildAction {
  return {
    id: 'download_copy',
    label: 'Download copy',
    sub: 'The unfinished code as a zip, marked not passed.',
    style,
    disabled: packing(input),
  }
}

function changeAnswers(input: ActionInput, style: ActionStyle): BuildAction {
  return {
    id: 'change_answers',
    label: 'Change answers',
    sub: 'Change the brief, then resume this build.',
    style,
    disabled: startsWork(input),
  }
}

function startOver(input: ActionInput): BuildAction {
  return {
    id: 'start_over',
    label: 'Start from scratch',
    sub: 'Builds the approved features again in a fresh workspace.',
    style: 'danger',
    disabled: startsWork(input),
    confirm: START_OVER_CONFIRM,
  }
}

function copyReport(): BuildAction {
  return {
    id: 'copy_report',
    label: 'Copy error report',
    sub: 'Copies the details to send to support.',
    style: 'quiet',
    disabled: null,
  }
}

function runPilot(input: ActionInput, style: ActionStyle): BuildAction {
  return {
    id: 'run_pilot',
    label: 'Run pilot checks',
    sub: 'Runs the pilot tests on this build.',
    style,
    disabled: startsWork(input),
  }
}

/** The level includes pilot checks and they have not run yet. */
function pilotPending(build: BuildStatus): boolean {
  return !isPilotZipReady(build) && build.build_level?.stop_gate !== 'CODE'
}

function repoLink(build: BuildStatus): string | null {
  return build.repo_url && /^https?:\/\//.test(build.repo_url) ? build.repo_url : null
}

function stoppedPlan(input: ActionInput, build: BuildStatus): Pick<ActionPlan, 'bar' | 'more'> {
  const kind = stopKind(build)
  const step = stoppedAt(build)
  const retry = tryAgain(input, kind, step)
  // A stall is not a terminal failure on the server, so it cannot reopen the
  // brief; it has the same layout and verbs otherwise.
  const canChange = build.state === 'failed' && kind !== 'by_you'
  if (kind === 'check') {
    return {
      bar: [retry, changeAnswers(input, 'secondary')],
      more: [downloadCopy(input, 'quiet'), startOver(input), copyReport()],
    }
  }
  if (kind === 'by_you') {
    return { bar: [retry, downloadCopy(input, 'secondary')], more: [startOver(input), copyReport()] }
  }
  return {
    bar: [retry, downloadCopy(input, 'secondary')],
    more: [...(canChange ? [changeAnswers(input, 'quiet')] : []), startOver(input), copyReport()],
  }
}

function finishedPlan(input: ActionInput, build: BuildStatus): Pick<ActionPlan, 'bar' | 'more' | 'note'> {
  const aff = exportAffordance(build)
  const pilot = pilotPending(build)
  if (aff.disabled) {
    // The certified download is not released yet: no download button that
    // would read as certified; the next step and the reason, in words.
    const next = pilot ? runPilot(input, 'primary') : tryAgain(input, null, null)
    return { bar: [next], more: [], note: NOT_RELEASED }
  }
  if (aff.asIs) {
    // Finished, but the certified download is refused: the next step is a
    // pilot run where the level includes one, otherwise another attempt.
    const next = pilot ? runPilot(input, 'primary') : tryAgain(input, null, null)
    return {
      bar: [
        { ...next, sub: `${next.sub} The finished build did not pass its checks, so it is not shipped as certified.` },
        { ...downloadCopy(input, 'secondary'), sub: 'The code as it stands, marked not certified.' },
      ],
      more: [],
    }
  }
  const prototype = !isPilotZipReady(build)
  const download: BuildAction = {
    id: 'download_platform',
    label: prototype ? 'Download prototype (.zip)' : 'Download platform (.zip)',
    sub: prototype ? 'The prototype code as a zip. Not pilot-tested.' : 'The finished platform as a zip.',
    style: 'primary',
    disabled: packing(input),
  }
  const repo = repoLink(build)
  if (repo) {
    return {
      bar: [
        {
          id: 'open_repo',
          label: 'Open repository',
          sub: 'Opens the repository the platform was delivered to.',
          style: 'primary',
          disabled: null,
          href: repo,
        },
        { ...download, style: 'secondary' },
        ...(pilot ? [runPilot(input, 'secondary')] : []),
      ],
      more: [],
    }
  }
  return { bar: [download, ...(pilot ? [runPilot(input, 'secondary')] : [])], more: [] }
}

export function buildActions(input: ActionInput): ActionPlan {
  return withPending(planFor(input), input.pending)
}

function planFor(input: ActionInput): ActionPlan {
  const build = input.build
  const accessPaused = Boolean(input.accessPaused)
  if (build?.state === 'succeeded') return { ...finishedPlan(input, build), accessPaused }
  if (build && (build.state === 'failed' || build.state === 'stalled')) {
    return { ...stoppedPlan(input, build), accessPaused }
  }
  // Starting or running.
  const stopping = Boolean(input.stopping) || build?.coder_control === 'stop'
  const stop: BuildAction = stopping
    ? { id: 'stop', label: 'Stopping…', sub: 'Waiting for the build to stop. Work so far is kept.', style: 'danger', disabled: 'Stopping…' }
    : {
        id: 'stop',
        label: 'Stop build',
        sub: 'Asks the build to stop. Work so far is kept.',
        style: 'danger',
        disabled: null,
        confirm: STOP_CONFIRM,
      }
  return { bar: [stop], more: [], accessPaused }
}
