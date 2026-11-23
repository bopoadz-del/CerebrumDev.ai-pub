import { BUILD_LEVELS, BUILD_LEVEL_SENTENCES } from './api/factory'
import { readableName } from './briefCard'

/**
 * The build level in the edit-brief sheet: one option card per level the
 * shared action spec defines, each with the server's plain sentence for it.
 * No level is preselected (there is no default level); a pick only changes
 * the sheet, and only the sheet's Save sends it.
 */
export function BuildLevelCards({
  value,
  onChange,
  disabled,
}: {
  value: string
  onChange: (level: string) => void
  disabled?: boolean
}) {
  return (
    <fieldset className="sheet-field" data-testid="level-cards">
      <legend className="field-label">Build level</legend>
      {BUILD_LEVELS.map((level) => (
        <label key={level} className={'level-card' + (value === level ? ' level-on' : '')}>
          <input
            type="radio"
            name="build-level"
            value={level}
            checked={value === level}
            disabled={disabled}
            onChange={() => onChange(level)}
          />
          <span className="level-text">
            <span className="level-name">{readableName(level)}</span>
            {BUILD_LEVEL_SENTENCES[level] && (
              <span className="level-sentence">{BUILD_LEVEL_SENTENCES[level]}</span>
            )}
          </span>
        </label>
      ))}
    </fieldset>
  )
}
