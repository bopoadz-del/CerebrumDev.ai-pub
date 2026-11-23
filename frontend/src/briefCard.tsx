import { useId, useState } from 'react'
import type { IntakeFields, IntakeState, TypedFloorAction } from './api/factory'

/** The answers a build cannot start without (the vertical is optional). */
const REQUIRED: (keyof IntakeFields)[] = ['country', 'currency', 'build_level']

const FIELD_NAMES: Record<keyof IntakeFields, string> = {
  vertical: 'industry',
  country: 'country',
  currency: 'currency',
  build_level: 'build level',
}

export type BriefStatus = 'draft' | 'suggested by chat' | 'locked' | 'stopped' | 'built'

/**
 * The status word on the brief card. A finished or stopped build names its
 * state; an approved feature list (or a running build) locks the brief until
 * the intake is reopened; a pending suggestion says it came from the chat.
 */
export function briefStatus(input: {
  intake: IntakeState
  approved: boolean
  buildState?: string | null
}): BriefStatus {
  const { intake, approved, buildState } = input
  if (!intake.reopened) {
    if (buildState === 'succeeded') return 'built'
    if (buildState === 'failed' || buildState === 'stalled') return 'stopped'
    if (approved) return 'locked'
  }
  if (suggestion(intake)) return 'suggested by chat'
  return 'draft'
}

function hasValues(fields: IntakeFields | null | undefined): fields is IntakeFields {
  return Boolean(fields && Object.values(fields).some((v) => v))
}

/**
 * The pending proposal when it would change an answer; null otherwise. A
 * reopened brief is pre-filled with the declared answers, and a proposal that
 * changes nothing is not a suggestion: no "Confirm these" for it.
 */
export function suggestion(intake: IntakeState): IntakeFields | null {
  const proposal = intake.proposal
  if (!hasValues(proposal)) return null
  const declared = intake.declared ?? {}
  const changes = (Object.keys(proposal) as (keyof IntakeFields)[]).some(
    (k) => proposal[k] && proposal[k] !== declared[k],
  )
  return changes ? proposal : null
}

/** "construction" -> "Construction"; "contract_settings" -> "Contract settings". */
export function readableName(raw: string): string {
  const words = raw.replace(/[_-]+/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : raw
}

/** A display name from the browser's own Intl data; the code when it has none. */
function intlName(type: 'region' | 'currency', code: string): string | null {
  try {
    const names = new Intl.DisplayNames(undefined, { type })
    const name = names.of(type === 'region' ? code.toUpperCase() : code.toUpperCase())
    return name && name.toUpperCase() !== code.toUpperCase() ? name : null
  } catch {
    return null
  }
}

/** "United Arab Emirates (AE)": the browser's name with the code. */
export function nameWithCode(type: 'region' | 'currency', code: string): string {
  const name = intlName(type, code)
  return name ? `${name} (${code.toUpperCase()})` : code.toUpperCase()
}

/** The short form for the collapsed line: country by name, currency by code. */
function shortValue(key: keyof IntakeFields, value: string): string {
  if (key === 'country') return intlName('region', value) ?? value.toUpperCase()
  if (key === 'currency') return value.toUpperCase()
  return readableName(value)
}

/** The long form for a brief row: names with codes. */
function longValue(key: keyof IntakeFields, value: string): string {
  if (key === 'country') return nameWithCode('region', value)
  if (key === 'currency') return nameWithCode('currency', value)
  return readableName(value)
}

const ORDER: (keyof IntakeFields)[] = ['vertical', 'country', 'currency', 'build_level']

function oneLine(fields: IntakeFields): string {
  return ORDER.filter((k) => fields[k])
    .map((k) => shortValue(k, String(fields[k])))
    .join(' · ')
}

function featureCount(n: number, approved: boolean): string {
  const noun = n === 1 ? 'feature' : 'features'
  return approved ? `${n} ${noun}` : `${n} ${noun} in the draft`
}

const DELIVERY_NAMES: Record<string, string> = {
  zip: 'Download (.zip)',
  github_repo: 'GitHub repository',
}

/**
 * The Floor's "Your brief" card, pinned at the top in every state. Collapsed
 * it is the status word and one line of values with real names; expanded it
 * lists every answer by label, the feature list by name and the delivery.
 * Missing answers read "Not set". A chat suggestion is its own strip with
 * "Confirm these" and never replaces the confirmed values. "Edit brief"
 * opens the edit sheet. After approval the brief is read-only, with a lock,
 * until the intake is reopened.
 */
export function BriefCard({
  intake,
  status,
  features,
  featuresApproved,
  productName,
  delivery,
  firstAsk,
  onShowConversation,
  onTyped,
  onEdit,
  disabled,
}: {
  intake: IntakeState
  status: BriefStatus
  /** Feature ids: the saved list once approved, else the newest draft. */
  features: string[] | null
  featuresApproved: boolean
  productName?: string | null
  delivery?: string | null
  /** The user's first message: what they asked for. */
  firstAsk?: string | null
  onShowConversation?: () => void
  /** Sends a typed Floor action (confirm_intake). */
  onTyped: (typed: TypedFloorAction) => void
  /** Opens the edit-brief sheet. */
  onEdit: () => void
  disabled?: boolean
}) {
  const [expanded, setExpanded] = useState(false)
  const detailsId = useId()
  const declared = intake.declared ?? {}
  const proposal = suggestion(intake)
  const editable = status === 'draft' || status === 'suggested by chat'
  const missing = REQUIRED.filter((k) => !declared[k])
  const values = oneLine(declared)
  const count = features ? featureCount(features.length, featuresApproved) : null

  return (
    <section
      className={'brief-card' + (editable ? '' : ' brief-locked')}
      aria-label="Your brief"
      data-testid="brief-card"
    >
      <div className="brief-head">
        <h2 className="brief-title">Your brief</h2>
        <span className="brief-status" data-testid="brief-status">
          {!editable && (
            <svg className="brief-lock" data-testid="brief-lock" viewBox="0 0 16 16" aria-hidden="true">
              <path d="M5 7V5a3 3 0 0 1 6 0v2h1v7H4V7h1zm1.5 0h3V5a1.5 1.5 0 0 0-3 0v2z" />
            </svg>
          )}
          {status}
        </span>
      </div>
      <p className="brief-values" data-testid="intake-declared">
        {values && <span data-testid="brief-values">{values}</span>}
        {values && missing.length > 0 && ' · '}
        {missing.length > 0 && (
          <span className="brief-missing" data-testid="brief-missing">
            Not set: {missing.map((k) => FIELD_NAMES[k]).join(', ')}
          </span>
        )}
      </p>
      {proposal && editable && (
        <div className="brief-suggestion" data-testid="intake-proposal">
          <p className="brief-suggestion-text">
            <span className="brief-suggestion-label">Suggested by chat: </span>
            {ORDER.filter((k) => proposal[k])
              .map((k) => longValue(k, String(proposal[k])))
              .join(' · ')}
          </p>
          <button
            type="button"
            data-testid="intake-confirm"
            disabled={disabled}
            onClick={() => onTyped({ action: 'confirm_intake' })}
          >
            Confirm these
          </button>
        </div>
      )}
      <div className="brief-row-actions">
        <button
          type="button"
          className="quiet brief-expand"
          data-testid="brief-expand"
          aria-expanded={expanded}
          aria-controls={detailsId}
          onClick={() => setExpanded((e) => !e)}
        >
          <span data-testid="brief-feature-count">{count ?? 'No feature list yet'}</span>
          <span className="brief-chevron" aria-hidden="true">
            {expanded ? '▴' : '▾'}
          </span>
        </button>
        {editable && (
          <button
            type="button"
            className="quiet"
            data-testid="intake-change"
            aria-haspopup="dialog"
            disabled={disabled}
            onClick={onEdit}
          >
            Edit brief
          </button>
        )}
      </div>
      {expanded && (
        <dl className="brief-details" id={detailsId} data-testid="brief-details">
          {firstAsk && (
            <div className="brief-detail">
              <dt>You asked for</dt>
              <dd>
                <span className="brief-ask">{firstAsk}</span>
                {onShowConversation && (
                  <button type="button" className="link" onClick={onShowConversation}>
                    Show conversation
                  </button>
                )}
              </dd>
            </div>
          )}
          {productName && (
            <div className="brief-detail">
              <dt>Product</dt>
              <dd>{productName}</dd>
            </div>
          )}
          {ORDER.map((k) => (
            <div className="brief-detail" key={k}>
              <dt>{readableName(FIELD_NAMES[k])}</dt>
              <dd data-testid={`brief-${k}`}>
                {declared[k] ? (
                  longValue(k, String(declared[k]))
                ) : k === 'vertical' ? (
                  'General'
                ) : (
                  <span className="brief-missing">Not set</span>
                )}
              </dd>
            </div>
          ))}
          <div className="brief-detail">
            <dt>Features included</dt>
            <dd>
              {features && features.length > 0 ? (
                <ul className="brief-features" data-testid="brief-features">
                  {features.map((id) => (
                    <li key={id}>{readableName(id)}</li>
                  ))}
                </ul>
              ) : (
                'No feature list yet'
              )}
            </dd>
          </div>
          {delivery && (
            <div className="brief-detail">
              <dt>Delivery</dt>
              <dd>{DELIVERY_NAMES[delivery] ?? readableName(delivery)}</dd>
            </div>
          )}
        </dl>
      )}
    </section>
  )
}
