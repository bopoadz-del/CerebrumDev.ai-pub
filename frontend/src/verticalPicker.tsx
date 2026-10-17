import { useEffect, useState } from 'react'
import { product, type DeclaredVertical } from './api/factory'

/**
 * The user's vertical, chosen on the Floor -- picked from what the Store's
 * kits declare they serve, or typed. The Factory never infers a vertical from
 * the brief's words or from the blocks a draft binds; with no choice the
 * product is built as a plain "product" with no domain kit.
 */
export function VerticalPicker({
  sessionId,
  value,
  onChange,
  onLoaded,
  onLocaleLoaded,
  disabled,
}: {
  sessionId: string
  value: string
  /** The user changed the vertical (picked or typed). */
  onChange: (vertical: string) => void
  /** The session's saved choice, loaded from the server (not a user change). */
  onLoaded?: (vertical: string) => void
  /** The session's saved country/currency, loaded from the server. */
  onLocaleLoaded?: (locale: { country: string; currency: string }) => void
  disabled?: boolean
}) {
  const [options, setOptions] = useState<DeclaredVertical[]>([])

  useEffect(() => {
    let cancelled = false
    product
      .verticals(sessionId)
      .then((res) => {
        if (cancelled) return
        setOptions(res.verticals || [])
        if (res.chosen && onLoaded) onLoaded(res.chosen)
        if ((res.country || res.currency) && onLocaleLoaded)
          onLocaleLoaded({ country: res.country || '', currency: res.currency || '' })
      })
      .catch(() => {
        // No Store reachable: the user can still type a vertical.
      })
    return () => {
      cancelled = true
    }
  }, [sessionId, onLoaded, onLocaleLoaded])

  const listId = `vertical-options-${sessionId}`
  return (
    <label className="vertical-picker">
      <span className="dim">Vertical</span>
      <input
        aria-label="Vertical"
        list={listId}
        value={value}
        placeholder="pick or type (blank = general product)"
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      />
      <datalist id={listId}>
        {options.map((o) => (
          <option key={`${o.vertical}-${o.kit}`} value={o.vertical}>
            {o.build_ready ? `${o.kit} kit` : `${o.kit} kit (not certified)`}
          </option>
        ))}
      </datalist>
    </label>
  )
}
