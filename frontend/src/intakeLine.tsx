import { useState, type ReactNode } from 'react'
import { BUILD_LEVELS, type IntakeFields, type IntakeState, type TypedFloorAction } from './api/factory'

const FIELD_LABELS: Record<keyof IntakeFields, string> = {
  vertical: 'vertical',
  country: 'country',
  currency: 'currency',
  build_level: 'build level',
}

function describe(fields: IntakeFields): string {
  return (Object.keys(FIELD_LABELS) as (keyof IntakeFields)[])
    .filter((k) => fields[k])
    .map((k) => `${FIELD_LABELS[k]} ${fields[k]}`)
    .join(' · ')
}

/**
 * The Floor's intake, as ONE line. The chat asks for the country, currency
 * and build level (vertical optional) and may PROPOSE values from the
 * user's answer; the proposal is shown here with Confirm / Change. Only
 * Confirm -- the typed ``confirm_intake`` action -- stores it. Change reveals
 * the edit controls; there are no always-visible empty boxes.
 */
export function IntakeLine({
  intake,
  onTyped,
  editControls,
  disabled,
}: {
  intake: IntakeState
  /** Sends a typed Floor action (confirm_intake / set_build_level). */
  onTyped: (typed: TypedFloorAction) => void
  /** The vertical and locale pickers, shown only after Change. */
  editControls: ReactNode
  disabled?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const declared = intake.declared ?? {}
  const proposal = intake.proposal
  const declaredText = describe(declared)
  const missing = (['country', 'currency', 'build_level'] as (keyof IntakeFields)[])
    .filter((k) => !declared[k])
    .map((k) => FIELD_LABELS[k])

  return (
    <div className="intake-line" data-testid="intake-line">
      {proposal && describe(proposal) ? (
        <span className="intake-proposal" data-testid="intake-proposal">
          Proposed: {describe(proposal)}{' '}
          <button
            type="button"
            data-testid="intake-confirm"
            disabled={disabled}
            onClick={() => onTyped({ action: 'confirm_intake' })}
          >
            Confirm
          </button>
        </span>
      ) : (
        <span className="intake-declared dim" data-testid="intake-declared">
          {declaredText ? declaredText : 'Nothing declared yet'}
          {missing.length > 0 ? ` — not declared: ${missing.join(', ')}` : ''}
        </span>
      )}{' '}
      <button
        type="button"
        className="link"
        data-testid="intake-change"
        disabled={disabled}
        onClick={() => setEditing((e) => !e)}
      >
        {editing ? 'Done' : 'Change'}
      </button>
      {editing && (
        <div className="intake-edit" data-testid="intake-edit">
          <label>
            <span className="dim">Build level</span>
            <select
              data-testid="intake-build-level"
              aria-label="Build level"
              disabled={disabled}
              value={declared.build_level ?? ''}
              onChange={(e) => {
                if (e.target.value) onTyped({ action: 'set_build_level', value: e.target.value })
              }}
            >
              <option value="" disabled>
                choose…
              </option>
              {BUILD_LEVELS.map((level) => (
                <option key={level} value={level}>
                  {level}
                </option>
              ))}
            </select>
          </label>
          {editControls}
        </div>
      )}
    </div>
  )
}
