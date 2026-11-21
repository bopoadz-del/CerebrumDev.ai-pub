import { useId } from 'react'
import type { DeclaredVertical } from './api/factory'
import { readableName } from './briefCard'

/**
 * The user's industry, chosen in the edit-brief sheet -- one chip per
 * industry the Store's kits declare they serve, plus "General" (no domain
 * kit) and "Other" (typed). The Factory never infers an industry from the
 * brief's words or from the blocks a draft binds; with no choice the product
 * is built as a general product with no domain kit.
 */
export function IndustryChips({
  options,
  value,
  other,
  onChange,
  disabled,
}: {
  /** What the Store's kits declare (product.verticals). */
  options: DeclaredVertical[]
  /** The chosen industry id; '' is General. */
  value: string
  /** True while the user types their own industry. */
  other: boolean
  onChange: (next: { value: string; other: boolean }) => void
  disabled?: boolean
}) {
  const id = useId()
  const known = options.some((o) => o.vertical === value)
  const otherOn = other || (value !== '' && !known)
  const chosen = options.find((o) => o.vertical === value && !otherOn)
  const chip = (key: string, label: string, pressed: boolean, onClick: () => void, sub?: string) => (
    <button
      key={key}
      type="button"
      className={'chip-choice' + (pressed ? ' chip-on' : '')}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
    >
      {label}
      {sub && <span className="chip-sub">{sub}</span>}
    </button>
  )
  return (
    <fieldset className="sheet-field">
      <legend className="field-label">Industry</legend>
      <div className="chip-row" data-testid="industry-chips">
        {chip('general', 'General', value === '' && !otherOn, () => onChange({ value: '', other: false }))}
        {options.map((o) =>
          chip(
            `${o.vertical}-${o.kit}`,
            readableName(o.vertical),
            value === o.vertical && !otherOn,
            () => onChange({ value: o.vertical, other: false }),
            o.build_ready ? undefined : 'not certified',
          ),
        )}
        {chip('other', 'Other', otherOn, () => onChange({ value: known ? '' : value, other: true }))}
      </div>
      {chosen && !chosen.build_ready && (
        <p className="field-hint">
          Not certified: the Store has not marked this kit ready for builds yet.
        </p>
      )}
      {value === '' && !otherOn && (
        <p className="field-hint">A general product, built without an industry kit.</p>
      )}
      {otherOn && (
        <>
          <label className="field-label" htmlFor={`${id}-other`}>
            Your industry
          </label>
          <input
            id={`${id}-other`}
            className="field-input"
            value={value}
            disabled={disabled}
            onChange={(e) => onChange({ value: e.target.value, other: true })}
          />
        </>
      )}
    </fieldset>
  )
}
