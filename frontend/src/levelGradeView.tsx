import type { BuildStatus } from './api/factory'
import {
  hasSourcedLevel,
  honestLevel,
  levelGradeLabel,
  threeGateEntries,
  type LevelGradeName,
} from './buildProgress'

const PILL_CLASS: Record<LevelGradeName, string> = {
  SCAFFOLD: 'status-pill status-pill-failed',
  CODE_GREEN: 'status-pill status-pill-prototype',
  STORE_GREEN: 'status-pill status-pill-store',
  FOUNDING_CUSTOMER_READY: 'status-pill status-pill-ready',
}

function gateClass(verdict: string): string {
  const key = verdict.replace(/\s+/g, '_').toUpperCase()
  if (key === 'PASS') return 'gate-chip gate-pass'
  if (key === 'FAIL') return 'gate-chip gate-fail'
  if (key === 'NOT RUN' || key === 'NOT_RUN') return 'gate-chip gate-notrun'
  return 'gate-chip'
}

/**
 * PRODUCT / STORE / Level chips. Succeeded builds show the fail-closed
 * grade; failed/stalled keep their own status pills and only add three-gate.
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
  const level = honestLevel(build)
  const gates = threeGateEntries(build)
  const sourced = hasSourcedLevel(build)
  const showLevel = build.state === 'succeeded' && level
  if (!showLevel && !gates) return null

  const levelTestId =
    level === 'CODE_GREEN'
      ? `${testIdPrefix}-prototype-pill`
      : level === 'STORE_GREEN' || level === 'FOUNDING_CUSTOMER_READY'
        ? `${testIdPrefix}-pilot-ready-pill`
        : `${testIdPrefix}-level-grade`

  const builtTo = build.build_level
  return (
    <div className="level-grade-strip" data-testid={`${testIdPrefix}-level-grade`}>
      {builtTo?.build_level && (
        <span
          className="gate-chip"
          data-testid={`${testIdPrefix}-build-level`}
          data-level={builtTo.build_level}
          title={`The build level the user chose; it stops at the ${builtTo.stop_gate} gate`}
        >
          Built to: {builtTo.build_level} (stops at {builtTo.stop_gate})
        </span>
      )}
      {showLevel && level && (
        <span className={PILL_CLASS[level]} data-testid={levelTestId} data-level={level}>
          {levelGradeLabel(level, sourced)}
        </span>
      )}
      {gates && (
        <span className="three-gate" data-testid={`${testIdPrefix}-three-gate`}>
          {gates.map((g) => (
            <span
              key={g.name}
              className={gateClass(g.verdict)}
              data-testid={`${testIdPrefix}-gate-${g.name.toLowerCase()}`}
            >
              {g.name} {g.verdict}
            </span>
          ))}
        </span>
      )}
    </div>
  )
}
