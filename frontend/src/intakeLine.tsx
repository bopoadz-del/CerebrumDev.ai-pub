import type { ReactNode } from 'react'
import { BUILD_LEVELS, type IntakeFields, type TypedFloorAction } from './api/factory'

/**
 * The brief's edit controls: the build level and the existing vertical and
 * locale pickers. The brief card shows them only after "Edit brief"; there
 * are no always-visible empty boxes. A level pick sends the typed
 * ``set_build_level`` action; only "Confirm these" (``confirm_intake``)
 * stores a suggestion.
 */
export function IntakeEdit({
  declared,
  onTyped,
  editControls,
  disabled,
}: {
  declared: IntakeFields
  /** Sends a typed Floor action (set_build_level). */
  onTyped: (typed: TypedFloorAction) => void
  /** The vertical and locale pickers. */
  editControls: ReactNode
  disabled?: boolean
}) {
  return (
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
  )
}
