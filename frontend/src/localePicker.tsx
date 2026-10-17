/**
 * The country and currency the user declares on the Floor -- typed, never
 * inferred. Checked by SHAPE only (2 / 3 letters, upper-cased): no list of
 * countries or currencies exists anywhere. Money in the built platform reads
 * these at run time; with none declared the money check reads WITHHELD.
 */
export function LocalePicker({
  country,
  currency,
  onChange,
  disabled,
}: {
  country: string
  currency: string
  onChange: (next: { country: string; currency: string }) => void
  disabled?: boolean
}) {
  const shape = (raw: string, n: number) =>
    raw.replace(/[^a-zA-Z]/g, '').toUpperCase().slice(0, n)
  return (
    <span className="locale-picker">
      <label>
        <span className="dim">Country</span>
        <input
          aria-label="Country"
          value={country}
          maxLength={2}
          placeholder="e.g. 2-letter code"
          onChange={(e) => onChange({ country: shape(e.target.value, 2), currency })}
          disabled={disabled}
        />
      </label>
      <label>
        <span className="dim">Currency</span>
        <input
          aria-label="Currency"
          value={currency}
          maxLength={3}
          placeholder="e.g. 3-letter code"
          onChange={(e) => onChange({ country, currency: shape(e.target.value, 3) })}
          disabled={disabled}
        />
      </label>
    </span>
  )
}
