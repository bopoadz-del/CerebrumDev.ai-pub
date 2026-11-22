import { useEffect, useRef, useState, type ReactNode, type Ref } from 'react'
import type { BuildDecision, BuildStatus } from './api/factory'

/**
 * The BUILD card: five slots in one order for every state.
 *
 *   1. the state line  -- an icon and a state word, coloured by the state
 *   2. one sentence    -- written here from typed facts, never from error text
 *   3. the actions     -- passed in by the Floor
 *   4. the steps       -- passed in by the Floor (the existing strip for now)
 *   5. Technical detail -- closed; the only place machine text appears
 *
 * Everything the build record carries that is not plain language -- reason
 * codes, wire ids, FAILED(...) tuples, file paths, the reference, the fix-round
 * history and the activity log -- is rendered once, inside Technical detail.
 */

export type BuildTone = 'info' | 'warning' | 'danger' | 'success'

export interface BuildFace {
  tone: BuildTone
  /** The state word: Building, Stopped, Ready. */
  word: string
  /** Where the build stands, e.g. "step 3 of 5". Null when the record does not say. */
  where: string | null
  /** The one sentence, in plain words. Empty for a finished build (the Floor writes its own note). */
  sentence: string
  /** The amber line of a fix round that is running now. */
  fixRoundNow: string | null
  /** "1 fix round used (of 1)" on a stopped build that used any. */
  fixRoundsUsed: string | null
}

/** Decision classes that send work back for another attempt inside a step. */
const FIX_ROUND_CLASSES = new Set(['REWORK', 'REGENERATE_TEST', 'REPROMPT'])

function fixRounds(build: BuildStatus): BuildDecision[] {
  return (build.decisions ?? []).filter((d) => FIX_ROUND_CLASSES.has(d.class))
}

function phaseList(build: BuildStatus): string[] {
  if (build.phases?.length) return build.phases
  return (build.phase_trail ?? []).map((row) => row.phase)
}

function completedList(build: BuildStatus): string[] {
  if (build.completed?.length) return build.completed
  return (build.phase_trail ?? []).filter((row) => row.outcome === 'passed').map((row) => row.phase)
}

/** 1-based step number of a phase id inside the run's own phase list. */
function stepOf(build: BuildStatus, phase: string | undefined | null): number | null {
  if (!phase) return null
  const i = phaseList(build).indexOf(phase)
  return i >= 0 ? i + 1 : null
}

/** The step a stopped build stopped at, 1-based, from the run's own phase list. */
export function stoppedAt(build: BuildStatus): number | null {
  return stepOf(build, build.failure?.location || build.failure?.phase || build.current_phase?.id)
}

function stepTotal(build: BuildStatus): number | null {
  const n = build.phase_total ?? (phaseList(build).length || null)
  return typeof n === 'number' && n > 0 ? n : null
}

/** Finished steps, counted from the run's own completed list. */
function stepsKept(build: BuildStatus): number {
  const phases = phaseList(build)
  const done = new Set(completedList(build))
  if (phases.length === 0) return done.size
  let n = 0
  for (const p of phases) {
    if (!done.has(p)) break
    n += 1
  }
  return n
}

export function keptSentence(n: number): string {
  if (n <= 0) return 'No step was finished yet.'
  if (n === 1) return 'Step 1 is done and kept.'
  if (n === 2) return 'Steps 1 and 2 are done and kept.'
  return `Steps 1 to ${n} are done and kept.`
}

function minutes(seconds: number): number {
  return Math.max(1, Math.round(seconds / 60))
}

function fixRoundsUsedLine(build: BuildStatus): string | null {
  const rounds = fixRounds(build)
  if (rounds.length === 0) return null
  const budget = rounds[rounds.length - 1].gate_budget
  const used = `${rounds.length} fix round${rounds.length === 1 ? '' : 's'} used`
  return typeof budget === 'number' && budget > 0 ? `${used} (of ${budget})` : used
}

export function buildFace(build: BuildStatus | null): BuildFace {
  const total = build ? stepTotal(build) : null
  if (!build || build.state === 'not_started' || build.state === 'waiting' || build.state === 'unknown') {
    return {
      tone: 'info',
      word: 'Building',
      where: null,
      sentence: 'The feature list is approved. The Factory is starting the build.',
      fixRoundNow: null,
      fixRoundsUsed: null,
    }
  }
  if (build.state === 'succeeded') {
    return { tone: 'success', word: 'Ready', where: null, sentence: '', fixRoundNow: null, fixRoundsUsed: null }
  }
  if (build.state === 'failed' || build.state === 'stalled') {
    const at = stoppedAt(build)
    const where = at && total ? `step ${at} of ${total}` : null
    const kept = keptSentence(stepsKept(build))
    let why: string
    if (build.coder_control === 'stop') {
      why = at ? `You stopped this build at step ${at}.` : 'You stopped this build.'
    } else if (build.state === 'stalled') {
      why =
        typeof build.last_event_age_s === 'number'
          ? `No update from the build for ${minutes(build.last_event_age_s)} minutes.`
          : 'No update from the build for a long time.'
    } else {
      why = 'The build stopped before it finished.'
    }
    return {
      tone: 'danger',
      word: 'Stopped',
      where,
      sentence: `${why} ${kept}`,
      fixRoundNow: null,
      fixRoundsUsed: fixRoundsUsedLine(build),
    }
  }
  // building
  const at = build.phase_index ?? stepOf(build, build.current_phase?.id)
  const where = at && total ? `step ${at} of ${total}` : null
  let fixRoundNow: string | null = null
  if (build.failure) {
    const rounds = fixRounds(build)
    const last = rounds[rounds.length - 1]
    const lastRetry = last && last.gate_budget > 0 && last.round_gate >= last.gate_budget
    fixRoundNow =
      'A quality check sent work back. The Factory is fixing it now.' +
      (lastRetry ? ' This is the last retry.' : '')
  }
  return {
    tone: fixRoundNow ? 'warning' : 'info',
    word: 'Building',
    where,
    sentence: 'The Factory is building your platform.',
    fixRoundNow,
    fixRoundsUsed: null,
  }
}

/** The raw technical message, once: the FAILED tuple, else the detail, else the reason code. */
export function rawMessage(build: BuildStatus | null): string | null {
  if (!build) return null
  if (build.state !== 'failed' && build.state !== 'stalled' && !build.failure) return null
  return build.failed_label || build.detail || build.failure?.reason || null
}

export function errorReport(
  build: BuildStatus | null,
  ctx: { sessionId: string; productTitle?: string | null },
): string {
  const face = buildFace(build)
  const lines = [
    `Session: ${ctx.sessionId}`,
    ctx.productTitle ? `Product: ${ctx.productTitle}` : null,
    `State: ${face.word}${face.where ? ` at ${face.where}` : ''}`,
    face.sentence ? `What happened: ${face.sentence}` : null,
    build?.failure?.location || build?.failure?.phase
      ? `Step id: ${build?.failure?.location || build?.failure?.phase}`
      : null,
    build?.failure?.code ? `Reason code: ${build.failure.code}` : null,
    build?.failure?.ref ? `Reference: ${build.failure.ref}` : null,
    rawMessage(build) ? `Technical message: ${rawMessage(build)}` : null,
  ]
  return lines.filter((l): l is string => Boolean(l)).join('\n')
}

type CopyState = 'idle' | 'copied' | 'failed'

function CopyErrorReport({ text }: { text: string }) {
  const [state, setState] = useState<CopyState>('idle')
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('no clipboard')
      await navigator.clipboard.writeText(text)
      setState('copied')
    } catch {
      setState('failed')
    }
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => setState('idle'), 4000)
  }
  return (
    <div className="tech-copy">
      <button type="button" className="ghost" data-testid="floor-copy-error-report" onClick={() => void copy()}>
        Copy error report
      </button>
      <span className="tech-copy-result" role="status" aria-live="polite" data-testid="floor-copy-result">
        {state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy. Select the text above instead.' : ''}
      </span>
    </div>
  )
}

function roundText(d: BuildDecision): string {
  const round = d.round_gate ? `round ${d.round_gate}/${d.gate_budget}` : ''
  const ceiling = d.round_build ? ` (build ${d.round_build}/${d.build_ceiling})` : ''
  return `${d.class} ${d.gate} ${round}${ceiling} — ${d.check}${d.finding ? `: ${d.finding}` : ''}`
}

export function TechnicalDetail({
  build,
  sessionId,
  productTitle,
  extra,
  note,
}: {
  build: BuildStatus | null
  sessionId: string
  productTitle?: string | null
  /** A short plain note shown on the closed row (the fix rounds used). */
  note?: string | null
  /** Machine chips the Floor moves off the face (gate ids, the level's stop gate). */
  extra?: ReactNode
}) {
  if (!build) return null
  const failure = build.failure
  const stopped = build.state === 'failed' || build.state === 'stalled'
  const raw = rawMessage(build)
  const decisions = build.decisions ?? []
  const activity = build.activity_log ?? []
  const recovered = !failure ? build.recovered_failure : null
  const notDelivered = build.not_delivered ?? []
  const hasLog = Boolean(build.coder_log || build.coder_log_present)
  const hasAnything =
    Boolean(
      failure || raw || decisions.length || activity.length || recovered || notDelivered.length || hasLog || extra,
    )
  if (!hasAnything) return null
  return (
    <details className="tech-detail" data-testid="floor-tech-detail">
      <summary>
        <span className="tech-summary-label">Technical detail</span>
        {note ? (
          <span className="tech-summary-note" data-testid="floor-fix-rounds">
            {note}
          </span>
        ) : null}
      </summary>
      <div className="tech-body">
        {extra}
        {failure && (
          <p
            className="tech-line"
            data-testid={stopped ? 'floor-failure-line' : 'floor-rework-detail'}
          >
            {failure.location || failure.phase || 'Build'} {stopped ? 'failed' : 'sent it back'}
            {failure.summary ? ` — ${failure.summary}` : failure.code && failure.code !== raw ? ` — ${failure.code}` : ''}
          </p>
        )}
        {raw && (
          <p className="tech-line" data-testid={build.failed_label ? 'floor-failed-label' : 'floor-raw-message'}>
            {raw}
          </p>
        )}
        {failure?.ref && (
          <p className="tech-line" data-testid="floor-failure-ref">
            Reference <code>{failure.ref}</code>
          </p>
        )}
        {decisions.length > 0 && (
          <>
            <p className="tech-label">History</p>
            <ol className="tech-list" data-testid="floor-decisions">
              {decisions.map((d, i) => (
                <li key={i} data-testid="floor-decision" data-class={d.class}>
                  {roundText(d)}
                </li>
              ))}
            </ol>
          </>
        )}
        {recovered && (
          <p className="tech-line" data-testid="floor-recovered-line">
            Recovered in rework — {recovered.location || recovered.phase || 'a phase'} failed once (
            {recovered.reason || 'unknown'}) and was fixed.
          </p>
        )}
        {notDelivered.length > 0 && (
          <p className="tech-line" data-testid="floor-not-delivered-line">
            Not delivered (Store-block repair failures):{' '}
            {notDelivered
              .map((row) => `${row.capabilities.join(', ')} (Store block ${row.block_id}: ${row.reason || 'repair failed'})`)
              .join('; ')}
          </p>
        )}
        {activity.length > 0 && (
          <>
            <p className="tech-label">Activity</p>
            <ol className="tech-list" data-testid="floor-activity-log">
              {activity.map((line, i) => (
                <li key={(line.ts ?? '') + ':' + i}>
                  {line.role ? `${line.role}: ` : ''}
                  {line.text}
                </li>
              ))}
            </ol>
          </>
        )}
        {hasLog && (
          <pre className="tech-log" data-testid="floor-coder-log">
            {build.coder_log || 'Coder session starting…'}
          </pre>
        )}
        {(failure || raw) && (
          <CopyErrorReport text={errorReport(build, { sessionId, productTitle })} />
        )}
      </div>
    </details>
  )
}

const TONE_ICON: Record<BuildTone, string> = {
  info: '●',
  warning: '↻',
  danger: '!',
  success: '✓',
}

export function BuildCard({
  build,
  sessionId,
  productTitle,
  title,
  note,
  actions,
  steps,
  detail,
  footer,
  cardRef,
}: {
  build: BuildStatus | null
  sessionId: string
  productTitle?: string | null
  /** Extra words after the state word (the finished build's title for now). */
  title?: ReactNode
  /** The sentence when the face writes none (a finished build). */
  note?: ReactNode
  actions?: ReactNode
  steps?: ReactNode
  /** Extra machine text for Technical detail. */
  detail?: ReactNode
  footer?: ReactNode
  cardRef?: Ref<HTMLDivElement>
}) {
  const face = buildFace(build)
  return (
    <div
      ref={cardRef}
      className={`build-card tone-${face.tone}`}
      role="status"
      data-testid="floor-coder-takeover"
      data-tone={face.tone}
    >
      <h3 className="build-state" data-testid="floor-build-state">
        <span className="build-state-icon" aria-hidden="true">
          {TONE_ICON[face.tone]}
        </span>
        <span className="build-state-word">
          {face.word}
          {face.where ? ` at ${face.where}` : ''}
        </span>
        {title ? <span className="build-state-title">{title}</span> : null}
      </h3>
      {face.sentence ? (
        <p className="build-sentence" data-testid="floor-build-sentence">
          {face.sentence}
        </p>
      ) : note ? (
        <p className="build-sentence" data-testid="floor-build-sentence">
          {note}
        </p>
      ) : null}
      {face.fixRoundNow && (
        <p className="build-fix-round" data-testid="floor-rework-line">
          <span aria-hidden="true">↻ </span>
          {face.fixRoundNow}
        </p>
      )}
      {actions ? <div className="build-actions-slot">{actions}</div> : null}
      {steps ? <div className="build-steps-slot" data-testid="floor-build-steps">{steps}</div> : null}
      <TechnicalDetail
        build={build}
        sessionId={sessionId}
        productTitle={productTitle}
        extra={detail}
        note={face.fixRoundsUsed}
      />
      {footer}
    </div>
  )
}
