import { useId, useMemo, useState } from 'react'

/**
 * Country and currency pickers for the edit-brief sheet: searchable, with
 * name and code. Every name comes from the browser's own Intl data; no list
 * of countries or currencies is typed into the page. The country list is
 * derived by asking ``Intl.DisplayNames`` for each two-letter code (there is
 * no region key in ``Intl.supportedValuesOf``); the currency list is the
 * browser's ``Intl.supportedValuesOf('currency')``. When the browser has no
 * such data the field falls back to the code's shape (two or three letters).
 */
export interface CodeOption {
  code: string
  name: string
}

const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

function displayNames(type: 'region' | 'currency'): Intl.DisplayNames | null {
  try {
    return new Intl.DisplayNames(undefined, { type, fallback: 'none' })
  } catch {
    return null
  }
}

function named(type: 'region' | 'currency', codes: Iterable<string>): CodeOption[] {
  const names = displayNames(type)
  if (!names) return []
  const out: CodeOption[] = []
  for (const code of codes) {
    let name: string | undefined
    try {
      name = names.of(code)
    } catch {
      name = undefined
    }
    if (name && name.toUpperCase() !== code) out.push({ code, name })
  }
  return out.sort((a, b) => a.name.localeCompare(b.name))
}

function* twoLetterCodes(): Generator<string> {
  for (const a of LETTERS) for (const b of LETTERS) yield a + b
}

let regionCache: CodeOption[] | null = null
let currencyCache: CodeOption[] | null = null

/** Every region the browser names, by its two-letter code. */
export function regionOptions(): CodeOption[] {
  if (!regionCache) regionCache = named('region', twoLetterCodes())
  return regionCache
}

/** Every currency the browser knows, by its three-letter code. */
export function currencyOptions(): CodeOption[] {
  if (!currencyCache) {
    const supported = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf
    let codes: string[] = []
    try {
      codes = supported ? supported('currency') : []
    } catch {
      codes = []
    }
    currencyCache = named('currency', codes)
  }
  return currencyCache
}

/** "United Arab Emirates (AE)"; the bare code when the browser has no name. */
export function optionLabel(option: CodeOption): string {
  return `${option.name} (${option.code})`
}

/**
 * The code a typed text stands for, or null when it stands for none: an
 * exact code, a "Name (CODE)" label, or an exact name. With no option list
 * (a browser without the data) any text of the code's shape is accepted.
 */
export function resolveCode(text: string, options: CodeOption[], length: number): string | null {
  const raw = text.trim()
  if (!raw) return null
  const upper = raw.toUpperCase()
  if (options.length === 0) return new RegExp(`^[A-Z]{${length}}$`).test(upper) ? upper : null
  const labelled = upper.match(/\(([A-Z]+)\)\s*$/)
  const candidate = labelled ? labelled[1] : upper
  const byCode = options.find((o) => o.code === candidate)
  if (byCode) return byCode.code
  const byName = options.find((o) => o.name.toUpperCase() === upper)
  return byName ? byName.code : null
}

/** Options whose name or code contains the typed text, best matches first. */
export function matchOptions(text: string, options: CodeOption[], limit = 6): CodeOption[] {
  const q = text.trim().toUpperCase()
  if (!q) return []
  const starts: CodeOption[] = []
  const contains: CodeOption[] = []
  for (const o of options) {
    const name = o.name.toUpperCase()
    if (o.code === q || name.startsWith(q)) starts.push(o)
    else if (name.includes(q) || o.code.includes(q)) contains.push(o)
  }
  return [...starts, ...contains].slice(0, limit)
}

/**
 * One searchable field: a label above, a text box, and up to six matching
 * options under it as 44px rows. Picking an option fills "Name (CODE)".
 * ``error`` shows inline under the field.
 */
export function CodePicker({
  label,
  text,
  options,
  onText,
  error,
  hint,
  disabled,
}: {
  label: string
  text: string
  options: CodeOption[]
  onText: (text: string) => void
  error?: string | null
  hint?: string
  disabled?: boolean
}) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const matches = useMemo(() => {
    const current = options.find((o) => optionLabel(o) === text)
    return current ? [] : matchOptions(text, options)
  }, [text, options])
  const listId = `${id}-list`
  const errorId = `${id}-error`
  const showList = open && matches.length > 0
  return (
    <div className="sheet-field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="field-input"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
        autoComplete="off"
        value={text}
        placeholder={hint}
        disabled={disabled}
        onChange={(e) => {
          onText(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && showList) {
            e.stopPropagation()
            setOpen(false)
          }
        }}
      />
      {showList && (
        <ul className="code-options" role="listbox" id={listId} aria-label={label}>
          {matches.map((o) => (
            <li key={o.code} role="presentation">
              <button
                type="button"
                role="option"
                aria-selected={false}
                className="code-option"
                onClick={() => {
                  onText(optionLabel(o))
                  setOpen(false)
                }}
              >
                {optionLabel(o)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="field-error" id={errorId} role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
