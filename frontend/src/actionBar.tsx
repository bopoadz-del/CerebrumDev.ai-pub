import { useEffect, useId, useRef, useState } from 'react'
import type { ActionId, ActionPlan, BuildAction, ConfirmSpec } from './buildActions'

/**
 * The action bar: the state's actions, drawn from one ActionPlan.
 *
 * On phones it is pinned above the composer; from 768px it sits inside the
 * BUILD card. The Floor renders it in exactly one place.
 */

const WIDE_QUERY = '(min-width: 768px)'

function readWide(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia(WIDE_QUERY).matches
    : false
}

/** True from 768px: the actions sit inside the BUILD card, not in a pinned bar. */
export function useWideLayout(): boolean {
  const [wide, setWide] = useState(readWide)
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(WIDE_QUERY)
    const onChange = () => setWide(mq.matches)
    onChange()
    mq.addEventListener?.('change', onChange)
    return () => mq.removeEventListener?.('change', onChange)
  }, [])
  return wide
}

const TEST_IDS: Record<ActionId, string> = {
  stop: 'floor-coder-stop',
  try_again: 'floor-try-again',
  download_copy: 'floor-take-copy',
  change_answers: 'floor-continue-with-intake',
  start_over: 'floor-start-over',
  copy_report: 'floor-action-copy-report',
  download_platform: 'floor-download-platform',
  run_pilot: 'continue-to-pilot',
  open_repo: 'floor-repo-link',
}

export function actionTestId(id: ActionId): string {
  return TEST_IDS[id]
}

function styleClass(a: BuildAction): string {
  if (a.style === 'primary') return 'action-button action-primary'
  if (a.style === 'danger') return 'action-button action-danger'
  if (a.style === 'quiet') return 'action-button action-quiet'
  return 'action-button action-secondary'
}

function ActionItem({ action, onPress }: { action: BuildAction; onPress: (a: BuildAction) => void }) {
  // A disabled action says why, in place of its line; a busy label
  // ("Packing…", "Stopping…") is the reason itself and keeps the line.
  const busyLabel = action.disabled === 'Packing…' || action.disabled === action.label
  const label = action.disabled === 'Packing…' ? 'Packing…' : action.label
  const line = action.disabled && !busyLabel ? action.disabled : action.sub
  return (
    <div className="action-item" data-style={action.style}>
      {action.href ? (
        <a
          className={styleClass(action)}
          href={action.href}
          target="_blank"
          rel="noreferrer"
          data-testid={actionTestId(action.id)}
        >
          {label}
        </a>
      ) : (
        <button
          type="button"
          className={styleClass(action)}
          data-testid={actionTestId(action.id)}
          data-action={action.id}
          disabled={Boolean(action.disabled)}
          onClick={() => onPress(action)}
        >
          {label}
        </button>
      )}
      <span
        className={'button-sub' + (action.disabled && !busyLabel ? ' button-sub-reason' : '')}
        data-testid={`${actionTestId(action.id)}-sub`}
      >
        {line}
      </span>
    </div>
  )
}

function ConfirmSheet({
  spec,
  onKeep,
  onGo,
}: {
  spec: ConfirmSpec
  onKeep: () => void
  onGo: () => void
}) {
  const titleId = useId()
  const keepRef = useRef<HTMLButtonElement>(null)
  // The safe choice holds focus when the sheet opens; focus goes back on close.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    keepRef.current?.focus()
    return () => opener?.focus?.()
  }, [])
  return (
    <div className="sheet-layer">
      <div className="sheet-backdrop" aria-hidden="true" onClick={onKeep} />
      <div
        className="sheet confirm-sheet"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="floor-confirm-sheet"
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.preventDefault()
            onKeep()
          }
        }}
      >
        <div className="sheet-head">
          <h2 className="sheet-title" id={titleId}>
            {spec.question}
          </h2>
        </div>
        <div className="sheet-body">
          <p className="confirm-body">{spec.body}</p>
        </div>
        <div className="sheet-foot confirm-foot">
          <button
            type="button"
            ref={keepRef}
            className="action-button action-secondary"
            data-testid="floor-confirm-keep"
            onClick={onKeep}
          >
            {spec.keep}
          </button>
          <button
            type="button"
            className="action-button action-danger"
            data-testid="floor-confirm-go"
            onClick={onGo}
          >
            {spec.go}
          </button>
        </div>
      </div>
    </div>
  )
}

export function ActionBar({
  plan,
  placement,
  onRun,
  onSeePlans,
  result,
  error,
}: {
  plan: ActionPlan
  placement: 'bar' | 'card'
  onRun: (id: ActionId) => void
  onSeePlans?: () => void
  /** The one-line result of the last press. */
  result?: string | null
  /** The last press failed: what went wrong, in words. */
  error?: string | null
}) {
  const [moreOpen, setMoreOpen] = useState(false)
  const [confirming, setConfirming] = useState<BuildAction | null>(null)
  const menuId = useId()

  const press = (a: BuildAction) => {
    if (a.disabled) return
    setMoreOpen(false)
    if (a.confirm) {
      setConfirming(a)
      return
    }
    onRun(a.id)
  }

  if (plan.bar.length === 0 && plan.more.length === 0 && !plan.accessPaused) return null
  return (
    <div
      className={'action-bar ' + (placement === 'bar' ? 'action-bar-pinned' : 'action-bar-in-card')}
      data-testid="floor-action-bar"
      data-placement={placement}
    >
      {plan.accessPaused && (
        <div className="action-banner" role="status" data-testid="floor-access-banner">
          <span>Access is paused. Downloads still work.</span>
          {onSeePlans && (
            <button type="button" className="action-button action-primary" onClick={onSeePlans}>
              See plans
            </button>
          )}
        </div>
      )}
      <div className="action-list">
        {plan.bar.map((a) => (
          <ActionItem key={a.id} action={a} onPress={press} />
        ))}
        {plan.more.length > 0 && (
          <div className="action-more">
            <button
              type="button"
              className="action-button action-quiet action-more-toggle"
              aria-expanded={moreOpen}
              aria-controls={menuId}
              data-testid="floor-action-more"
              onClick={() => setMoreOpen((o) => !o)}
            >
              More <span aria-hidden="true">{moreOpen ? '▴' : '▾'}</span>
            </button>
            {moreOpen && (
              <div className="action-more-menu" id={menuId} data-testid="floor-action-more-menu">
                {plan.more.map((a) => (
                  <ActionItem key={a.id} action={a} onPress={press} />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {(error || result) && (
        <p
          className={'action-result' + (error ? ' action-result-error' : '')}
          role={error ? 'alert' : 'status'}
          data-testid="floor-action-result"
        >
          {error || result}
        </p>
      )}
      {confirming?.confirm && (
        <ConfirmSheet
          spec={confirming.confirm}
          onKeep={() => setConfirming(null)}
          onGo={() => {
            const id = confirming.id
            setConfirming(null)
            onRun(id)
          }}
        />
      )}
    </div>
  )
}
