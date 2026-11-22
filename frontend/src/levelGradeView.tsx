import type { BuildStatus } from './api/factory'
import {
  hasSourcedLevel,
  honestLevel,
  levelGradeLabel,
  threeGateEntries,
  type LevelGradeName,
} from './buildProgress'
import { checkLabel, checksIncluded, levelWord, verdictWord } from './stepNames'

const PILL_CLASS: Record<LevelGradeName, string> = {
  SCAFFOLD: 'status-pill status-pill-failed',
  CODE_GREEN: 'status-pill status-pill-prototype',
  STORE_GREEN: 'status-pill status-pill-store',
  FOUNDING_CUSTOMER_READY: 'status-pill status-pill-ready',
}

type CheckTone = 'success' | 'danger' | 'neutral'

function checkTone(verdict: string): CheckTone {
  const word = verdictWord(verdict)
  if (word === 'passed') return 'success'
  if (word === 'failed') return 'danger'
  return 'neutral'
}

const CHECK_ICON: Record<CheckTone, string> = { success: '✓', danger: '!', neutral: '–' }

/** "Build level: Prototype", in the user's own word. Null when the run declared none. */
export function buildLevelText(build: BuildStatus | null | undefined): string | null {
  const word = levelWord(build?.build_level?.build_level)
  return word ? `Build level: ${word}` : null
}

/** The checks the build's level includes, each with its verdict in words. */
export function includedChecks(
  build: BuildStatus | null | undefined,
): { id: string; label: string; verdict: string; tone: CheckTone }[] {
  const gates = threeGateEntries(build)
  if (!gates) return []
  const included = new Set(checksIncluded(build?.build_level?.stop_gate))
  return gates
    .filter((g) => included.has(g.name))
    .map((g) => {
      const label = checkLabel(g.name)
      const verdict = verdictWord(g.verdict)
      return label && verdict ? { id: g.name, label, verdict, tone: checkTone(g.verdict) } : null
    })
    .filter((row): row is { id: string; label: string; verdict: string; tone: CheckTone } => row !== null)
}

/**
 * The face of a finished or stopped build's level: the build level the user
 * chose, and only the checks that level includes, each as an icon and words
 * ("Code checks passed"). A check the level skips is not shown at all.
 */
export function LevelGradeStrip({
  build,
  testIdPrefix,
}: {
  build: BuildStatus | null
  testIdPrefix: string
}) {
  if (!build) return null
  if (build.state === 'building' || build.state === 'not_started') return null
  const level = buildLevelText(build)
  const checks = includedChecks(build)
  if (!level && checks.length === 0) return null
  return (
    <div className="level-grade-strip" data-testid={`${testIdPrefix}-level-grade`}>
      {level && (
        <span
          className="badge"
          data-testid={`${testIdPrefix}-build-level`}
          data-level={build.build_level?.build_level}
        >
          {level}
        </span>
      )}
      {checks.length > 0 && (
        <span className="three-gate" data-testid={`${testIdPrefix}-three-gate`}>
          {checks.map((c) => (
            <span
              key={c.id}
              className={`badge badge-${c.tone}`}
              data-testid={`${testIdPrefix}-gate-${c.id.toLowerCase()}`}
            >
              <span aria-hidden="true">{CHECK_ICON[c.tone]}</span>
              {c.label} {c.verdict}
            </span>
          ))}
        </span>
      )}
    </div>
  )
}

/**
 * The fail-closed grade pill (Scaffold, Code-green, Store-green,
 * Founding-customer-ready). On the Floor it lives in Technical detail; the
 * Platforms page keeps it on its face until that page's own slice.
 */
export function LevelGradePill({
  build,
  testIdPrefix,
}: {
  build: BuildStatus | null
  testIdPrefix: string
}) {
  const level = build?.state === 'succeeded' ? honestLevel(build) : null
  if (!build || !level) return null
  const levelTestId =
    level === 'CODE_GREEN'
      ? `${testIdPrefix}-prototype-pill`
      : level === 'STORE_GREEN' || level === 'FOUNDING_CUSTOMER_READY'
        ? `${testIdPrefix}-pilot-ready-pill`
        : `${testIdPrefix}-level-grade-name`
  return (
    <span className={PILL_CLASS[level]} data-testid={levelTestId} data-level={level}>
      {levelGradeLabel(level, hasSourcedLevel(build))}
    </span>
  )
}

/**
 * The machine grade, for Technical detail only: the grade name, the stop
 * gate and the raw three-gate verdicts. These are the "names that leave the
 * screen" of spec 4.4; they are kept because support reads them.
 */
export function LevelGradeDetail({
  build,
  testIdPrefix,
}: {
  build: BuildStatus | null
  testIdPrefix: string
}) {
  if (!build || build.state === 'building' || build.state === 'not_started') return null
  const level = build.state === 'succeeded' ? honestLevel(build) : null
  const gates = threeGateEntries(build)
  const stop = build.build_level?.stop_gate
  if (!level && !gates && !stop) return null
  return (
    <p className="tech-line" data-testid={`${testIdPrefix}-grade-detail`}>
      <LevelGradePill build={build} testIdPrefix={testIdPrefix} />
      {stop ? <span> stop gate {stop}</span> : null}
      {gates ? <span> {gates.map((g) => `${g.name} ${g.verdict}`).join(', ')}</span> : null}
    </p>
  )
}
