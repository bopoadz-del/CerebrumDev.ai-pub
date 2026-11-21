import { useEffect, useId, useMemo, useRef, useState } from 'react'
import type { DeclaredVertical, IntakeFields } from './api/factory'
import { BuildLevelCards } from './intakeLine'
import {
  CodePicker,
  currencyOptions,
  optionLabel,
  regionOptions,
  resolveCode,
  type CodeOption,
} from './localePicker'
import { IndustryChips } from './verticalPicker'

/** What one Save sends: all four answers, '' for one that is not set. */
export interface BriefAnswers {
  vertical: string
  country: string
  currency: string
  build_level: string
}

/** The sheet's fields as typed (country and currency as the box's text). */
export interface BriefDraft {
  vertical: string
  verticalOther: boolean
  countryText: string
  currencyText: string
  level: string
}

function textFor(code: string | null | undefined, options: CodeOption[]): string {
  if (!code) return ''
  const option = options.find((o) => o.code === code.toUpperCase())
  return option ? optionLabel(option) : code.toUpperCase()
}

/** The sheet opens on the server's answers, with a pending suggestion on top. */
export function draftFrom(
  initial: IntakeFields,
  regions: CodeOption[],
  currencies: CodeOption[],
): BriefDraft {
  return {
    vertical: initial.vertical ?? '',
    verticalOther: false,
    countryText: textFor(initial.country, regions),
    currencyText: textFor(initial.currency, currencies),
    level: initial.build_level ?? '',
  }
}

/** The answers a draft stands for, and what stops it being saved. */
export function checkDraft(
  draft: BriefDraft,
  initial: IntakeFields,
  regions: CodeOption[],
  currencies: CodeOption[],
): {
  answers: BriefAnswers
  countryError: string | null
  currencyError: string | null
  pairError: string | null
  changed: boolean
} {
  const country = resolveCode(draft.countryText, regions, 2)
  const currency = resolveCode(draft.currencyText, currencies, 3)
  const countryError =
    draft.countryText.trim() && !country
      ? 'Not a country this browser knows. Pick one from the list, or type its two-letter code.'
      : null
  const currencyError =
    draft.currencyText.trim() && !currency
      ? 'Not a currency this browser knows. Pick one from the list, or type its three-letter code.'
      : null
  const hasCountry = Boolean(draft.countryText.trim())
  const hasCurrency = Boolean(draft.currencyText.trim())
  const pairError =
    hasCountry !== hasCurrency ? 'Set both a country and a currency, or neither.' : null
  const answers: BriefAnswers = {
    vertical: draft.vertical.trim(),
    country: country ?? '',
    currency: currency ?? '',
    build_level: draft.level,
  }
  const changed =
    answers.vertical !== (initial.vertical ?? '') ||
    answers.country !== (initial.country ?? '').toUpperCase() ||
    answers.currency !== (initial.currency ?? '').toUpperCase() ||
    answers.build_level !== (initial.build_level ?? '')
  return { answers, countryError, currencyError, pairError, changed }
}

const FOCUSABLE =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]'

/**
 * The edit-brief sheet: a bottom sheet on phones, a centred dialog from
 * 768px. One field per row with its label above: build level as option
 * cards with the server's sentence, industry as chips from the Store's
 * kits, country and currency as searchable pickers with name and code.
 * One Save sends all four answers in one request; Cancel sends nothing.
 * After a stopped build (the intake reopened) Save also tries again.
 */
export function BriefSheet({
  initial,
  verticals,
  resume,
  busy,
  error,
  onSave,
  onClose,
}: {
  /** The answers to start from: declared, with a pending suggestion on top. */
  initial: IntakeFields
  verticals: DeclaredVertical[]
  /** The intake was reopened after a stopped build: Save resumes it. */
  resume?: boolean
  busy?: boolean
  /** The server's refusal of the last Save, shown in the sheet. */
  error?: string | null
  onSave: (answers: BriefAnswers) => void
  onClose: () => void
}) {
  const titleId = useId()
  const sheetRef = useRef<HTMLDivElement>(null)
  const regions = useMemo(() => regionOptions(), [])
  const currencies = useMemo(() => currencyOptions(), [])
  const [draft, setDraft] = useState<BriefDraft>(() => draftFrom(initial, regions, currencies))
  const { answers, countryError, currencyError, pairError, changed } = checkDraft(
    draft,
    initial,
    regions,
    currencies,
  )
  const blocked = countryError ?? currencyError ?? pairError
  const reason = busy
    ? null
    : blocked
      ? 'Fix the marked answer to save.'
      : !changed
        ? 'Nothing changed yet.'
        : null
  const canSave = !busy && !blocked && changed

  // Focus moves into the sheet on open and back to the opener on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    sheetRef.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus()
    return () => opener?.focus?.()
  }, [])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
      return
    }
    if (e.key !== 'Tab' || !sheetRef.current) return
    const items = Array.from(sheetRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  return (
    <div className="sheet-layer">
      <div className="sheet-backdrop" aria-hidden="true" onClick={onClose} />
      <div
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="brief-sheet"
        ref={sheetRef}
        onKeyDown={onKeyDown}
      >
        <div className="sheet-head">
          <h2 className="sheet-title" id={titleId}>
            Your brief
          </h2>
          <button type="button" className="quiet icon-button" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="sheet-body">
          <BuildLevelCards
            value={draft.level}
            disabled={busy}
            onChange={(level) => setDraft((d) => ({ ...d, level }))}
          />
          <IndustryChips
            options={verticals}
            value={draft.vertical}
            other={draft.verticalOther}
            disabled={busy}
            onChange={({ value, other }) =>
              setDraft((d) => ({ ...d, vertical: value, verticalOther: other }))
            }
          />
          <CodePicker
            label="Country"
            text={draft.countryText}
            options={regions}
            hint="Type a name or a two-letter code"
            error={countryError}
            disabled={busy}
            onText={(countryText) => setDraft((d) => ({ ...d, countryText }))}
          />
          <CodePicker
            label="Currency"
            text={draft.currencyText}
            options={currencies}
            hint="Type a name or a three-letter code"
            error={currencyError}
            disabled={busy}
            onText={(currencyText) => setDraft((d) => ({ ...d, currencyText }))}
          />
          {pairError && (
            <p className="field-error" role="alert" data-testid="pair-error">
              {pairError}
            </p>
          )}
          {error && (
            <p className="field-error" role="alert" data-testid="sheet-refused">
              {error}
            </p>
          )}
        </div>
        <div className="sheet-foot">
          <button type="button" className="ghost" onClick={onClose}>
            Cancel
          </button>
          <div className="sheet-save">
            <button
              type="button"
              data-testid="brief-save"
              disabled={!canSave}
              onClick={() => canSave && onSave(answers)}
            >
              {busy ? 'Saving…' : resume ? 'Save and try again' : 'Save'}
            </button>
            {resume && !reason && (
              <span className="button-sub">Resumes this build with the new answers.</span>
            )}
            {reason && (
              <span className="button-sub" data-testid="brief-save-reason">
                {reason}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
