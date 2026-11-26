import type { BuildStatus } from './api/factory'
import { reviewState, stopTone, type ReviewState } from './buildProgress'
import { REVIEW_NAME, STEP_ORDER, stepLabel } from './stepNames'

/**
 * The stepper: the run's steps as numbered dots on one row (spec 4.6).
 *
 * - a label only on the current or stopped step;
 * - an icon for done, current, stopped and not reached, and a state word for
 *   screen readers -- state is never colour alone;
 * - the review inside step 3 ("Quality check") is a count on its step, and
 *   only when the grader's own gate left a record (``reviewState``).
 *
 * The wire ids stay in ``data-step`` and test ids, never in the text.
 */

export type StepState = 'done' | 'current' | 'stopped' | 'fixing' | 'todo'

export interface StepView {
  id: string
  position: number
  label: string
  state: StepState
}

const STATE_WORD: Record<StepState, string> = {
  done: 'done',
  current: 'in progress',
  stopped: 'stopped',
  fixing: 'fixing',
  todo: 'not reached',
}

const STATE_ICON: Record<StepState, string> = {
  done: '✓',
  current: '●',
  stopped: '!',
  fixing: '↻',
  todo: '',
}

function phaseList(build: BuildStatus | null): string[] {
  if (build?.phases?.length) return build.phases
  const trail = (build?.phase_trail ?? []).map((row) => row.phase)
  return trail.length ? trail : [...STEP_ORDER]
}

/** Each step's state, from the run's own record. At most one step is current or stopped. */
export function stepViews(build: BuildStatus | null): StepView[] {
  const phases = phaseList(build)
  const done = new Set(build?.completed ?? [])
  const stopped = build?.state === 'failed' || build?.state === 'stalled'
  const failedAt = build?.failure?.location || build?.failure?.phase || null
  const running = build?.state === 'building' || build?.state === 'waiting'
  const current = build?.current_phase?.id ?? null
  // The one step the build stands at: where it stopped, or where it is now.
  const at = stopped
    ? failedAt || current || phases.find((p) => !done.has(p)) || null
    : running
      ? current || phases.find((p) => !done.has(p)) || null
      : null
  return phases.map((id, i) => {
    let state: StepState = done.has(id) ? 'done' : 'todo'
    if (build?.state === 'succeeded') state = 'done'
    else if (id === at && !done.has(id)) {
      if (stopped) state = 'stopped'
      else state = failedAt === id ? 'fixing' : 'current'
    } else if (id === failedAt && running && !done.has(id)) {
      // A step that sent work back while the run moves on is a fix round, not a stop.
      state = 'fixing'
    }
    return { id, position: i + 1, label: stepLabel(id, i + 1), state }
  })
}

function reviewLine(review: ReviewState): string {
  if (review.state === 'not_passed') return `${REVIEW_NAME} did not pass`
  return `${REVIEW_NAME} sent work back ${review.rounds === 1 ? 'once' : `${review.rounds} times`}`
}

/** The step the review belongs to: the writer's step, found by the table, not by position. */
function reviewStep(steps: StepView[]): StepView | undefined {
  return steps.find((s) => s.label === stepLabel('WRITER'))
}

export function Stepper({ build }: { build: BuildStatus | null }) {
  const steps = stepViews(build)
  const review = reviewState(build)
  // Red only for a check that refused the work; a timeout or a Stop is halted.
  // With the server's stop record, its kind decides, as it does on the card:
  // only a check that did not pass, or a build that went quiet, is red.
  const kind = build?.stop?.kind
  const tone = kind ? (kind === 'check_failed' || kind === 'no_progress' ? 'failed' : 'halted') : stopTone(build?.failure)
  const reviewAt = review ? reviewStep(steps) : undefined
  const focus = steps.find((s) => s.state === 'current' || s.state === 'stopped' || s.state === 'fixing')
  const doneCount = steps.filter((s) => s.state === 'done').length
  const caption = focus
    ? `${focus.label}${review && reviewAt === focus ? ` — ${reviewLine(review)}` : ''}`
    : build?.state === 'succeeded'
      ? `${doneCount} of ${steps.length} steps done`
      : null
  return (
    <div className="stepper" data-testid="floor-stepper">
      <ol className="stepper-row" data-testid="floor-stepper-row" aria-label={`Build steps, ${doneCount} of ${steps.length} done`}>
        {steps.map((s, i) => {
          const isReview = review && reviewAt === s
          return (
            <li
              key={s.id}
              className={`stepper-step step-${s.state}${s.state === 'stopped' ? ` stop-${tone}` : ''}`}
              data-step={s.id}
              data-state={s.state}
              aria-current={s.state === 'current' || s.state === 'fixing' ? 'step' : undefined}
              data-testid={
                s.state === 'stopped'
                  ? `floor-phase-${tone}-${s.id}`
                  : s.state === 'fixing'
                    ? `floor-phase-rework-${s.id}`
                    : undefined
              }
            >
              {i > 0 ? <span className="stepper-line" aria-hidden="true" /> : null}
              <span className="stepper-dot" aria-hidden="true">
                {STATE_ICON[s.state] || s.position}
              </span>
              <span className="visually-hidden">
                Step {s.position}, {s.label}: {STATE_WORD[s.state]}
                {isReview ? `. ${reviewLine(review)}` : ''}
              </span>
              {isReview ? (
                <span
                  className={`stepper-review review-${review.state}`}
                  aria-hidden="true"
                  data-testid={`floor-phase-grader-${review.state === 'not_passed' ? 'failed' : 'rework'}`}
                >
                  {review.state === 'not_passed' ? '!' : review.rounds}
                </span>
              ) : null}
            </li>
          )
        })}
      </ol>
      {caption ? (
        <p className="stepper-caption" data-testid="floor-stepper-caption">
          {caption}
        </p>
      ) : null}
    </div>
  )
}
