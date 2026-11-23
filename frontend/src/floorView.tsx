import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  awaitBuild,
  chatEventText,
  chatStream,
  downloadProductPackage,
  product,
  sessions,
  watchBuildStatus,
  type BuildStatus,
  type ChatEvent,
  type DeclaredLocale,
  type DeclaredVertical,
  type GoldenSuggestion,
  type IntakeState,
  type ProductDesign,
  type TypedFloorAction,
} from './api/factory'
import { FactoryCodeCliStatus, useFactoryCodeCliHonesty } from './factoryReadinessView'
import { SuggestedChecks } from './suggestedChecks'
import {
  exportAffordance,
  formatFinishedAuthorship,
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  measuredScore,
  hasSourcedLevel,
  honestLevel,
  isAcceptancePendingPrototype,
  isPilotZipReady,
  shouldDemoteFounding,
  phaseBarFraction,
  nRequiredFromProductInputs,
  preferHonestBuild,
  stampBuildObservation,
  withClientStall,
  withResolvedNRequired,
} from './buildProgress'
import { LevelGradeDetail, LevelGradeStrip } from './levelGradeView'
import { Stepper } from './stepper'
import { checksSkipped, CHECK_NAMES, levelWord, orList, stepLabel } from './stepNames'
import { BuildCard, errorReport } from './buildCard'
import { ActionBar, useWideLayout } from './actionBar'
import { buildActions, type ActionId } from './buildActions'
import { actionOutcome, doneText, holdCardTop, humanError, type ActionOutcome, type InfoReply } from './actionResult'
import { BriefCard, briefStatus } from './briefCard'
import { BriefSheet, type BriefAnswers } from './briefSheet'
import { FloorUpload } from './FloorUpload'
import { displayProductName, humanizeProductId, latestBlueprintIn } from './productDisplay'
import { distanceFromBottom, scrollBehavior, settledKey, shouldFollow } from './floorScroll'

interface Capability {
  id: string
  description?: string
  strategy_hint?: string
  block_ids?: string[]
}

interface ChatMsg {
  role: 'user' | 'factory' | 'system'
  text: string
  card?: 'blueprint' | 'generation' | 'error' | 'info'
  /** A message that failed to send: the error bubble offers to send it again. */
  retry?: string
  engine?: string
  triggeredBy?: string
  blueprint?: {
    product_name?: string
    vertical?: string
    /** The build level the user chose: prototype | light | pilot | production. */
    build_level?: string | null
    summary?: string
    capabilities?: Capability[]
    drafting_mode?: string
    drafting_note?: string
    /** Client's delivery choice at request time: zip | github_repo. */
    delivery_format?: 'zip' | 'github_repo'
    roles?: string[]
    users?: string[]
    done_when?: string[]
    /**
     * The golden OFFERED on this draft (H1, F-4): an accept/decline offer, not
     * a substitution — the base blueprint above stays the user's own draft.
     */
    golden_suggestion?: GoldenSuggestion | null
  }
  plainLanguage?: string
}

/** What the user pressed, shown in the chat log (a typed action is not text). */
export function typedActionLabel(typed: TypedFloorAction): string {
  const value = typed.value ? ': ' + typed.value : ''
  return '[' + typed.action.replace(/_/g, ' ') + value + ']'
}

export function BlueprintCard({
  blueprint,
  busy,
  onApprove,
  onRefine,
  onAcceptGolden,
  onDeclineGolden,
  accessPaused = false,
}: {
  blueprint: NonNullable<ChatMsg['blueprint']>
  busy: boolean
  onApprove: (excludedIds: string[], delivery: 'zip' | 'github_repo') => void
  onRefine: (typed: TypedFloorAction) => void
  onAcceptGolden?: () => void
  onDeclineGolden?: () => void
  accessPaused?: boolean
}) {
  const caps = blueprint.capabilities ?? []
  const golden: GoldenSuggestion | null = blueprint.golden_suggestion ?? null
  const [ticked, setTicked] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(caps.map((c) => [c.id, true])),
  )
  const excluded = caps.filter((c) => ticked[c.id] === false).map((c) => c.id)
  const selectedCount = caps.length - excluded.length
  const [delivery, setDelivery] = useState<'zip' | 'github_repo'>('zip')
  const [renameTo, setRenameTo] = useState('')
  const [capId, setCapId] = useState('')
  return (
    <div className="blueprint-card">
      <div className="bp-header">
        <strong>{blueprint.product_name ?? 'Untitled platform'}</strong>
        <span className="bp-vertical">{blueprint.vertical ?? '—'}</span>
        {blueprint.drafting_mode && (
          <span
            className={'bp-drafting-mode ' + blueprint.drafting_mode}
            data-testid="bp-drafting-mode"
            title={blueprint.drafting_note ?? undefined}
          >
            {blueprint.drafting_mode === 'architect_llm'
              ? 'architect LLM'
              : blueprint.drafting_mode === 'golden'
                ? 'golden blueprint'
                : 'template fallback — no LLM'}
          </span>
        )}
      </div>
      {blueprint.drafting_mode === 'keyword_fallback' && (
        <p
          className="bp-fallback-warning"
          data-testid="bp-drafting-fallback-warning"
          role="alert"
        >
          ⚠ The architect LLM did not draft this blueprint
          {blueprint.drafting_note ? ' (' + blueprint.drafting_note + ')' : ''} — it was
          assembled from deterministic templates. Approve only if a template product is
          what you intend to build.
        </p>
      )}
      {blueprint.summary && <p className="bp-summary">{blueprint.summary}</p>}
      {golden && !accessPaused && (onAcceptGolden || onDeclineGolden) && (
        <div className="bp-golden-offer" data-testid="bp-golden-offer" role="group">
          <p className="bp-golden-offer-label">
            {golden.label}: <strong>{golden.golden_name}</strong>
            {typeof golden.overlap === 'number'
              ? ' (' + Math.round(golden.overlap * 100) + '% overlap)'
              : ''}
          </p>
          <div className="card-actions">
            {onAcceptGolden && (
              <button
                data-testid="bp-accept-golden"
                disabled={busy}
                onClick={() => onAcceptGolden()}
              >
                Accept golden
              </button>
            )}
            {onDeclineGolden && (
              <button
                className="link"
                data-testid="bp-decline-golden"
                disabled={busy}
                onClick={() => onDeclineGolden()}
              >
                Decline
              </button>
            )}
          </div>
        </div>
      )}
      {(blueprint.users?.length || blueprint.roles?.length) ? (
        <p className="bp-summary dim" data-testid="bp-plain-who">
          {blueprint.users?.length ? 'For: ' + blueprint.users.join(', ') : ''}
          {blueprint.users?.length && blueprint.roles?.length ? ' · ' : ''}
          {blueprint.roles?.length ? 'Roles: ' + blueprint.roles.join(', ') : ''}
        </p>
      ) : null}
      {blueprint.done_when && blueprint.done_when.length > 0 && (
        <ul className="bp-caps" data-testid="bp-done-when">
          {blueprint.done_when.map((item) => (
            <li key={item}>Done when: {item}</li>
          ))}
        </ul>
      )}
      <h4>Capabilities ({caps.length})</h4>
      {!accessPaused && (
        <p className="bp-pick-hint dim">Tick what the platform should include, then approve.</p>
      )}
      <ul className="bp-caps">
        {caps.map((c) => (
          <li key={c.id} className={ticked[c.id] === false ? 'bp-cap-excluded' : ''}>
            <label className="bp-cap-pick">
              <input
                type="checkbox"
                checked={ticked[c.id] !== false}
                disabled={busy || accessPaused}
                onChange={(e) =>
                  setTicked((t) => ({ ...t, [c.id]: e.target.checked }))
                }
              />
              <span className="bp-cap-id">{humanizeProductId(c.id)}</span>
              <span className={'bp-strategy ' + (c.strategy_hint ?? 'REUSE')}>{c.strategy_hint ?? 'REUSE'}</span>
            </label>
            {c.description && <p className="bp-cap-desc">{c.description}</p>}
            {c.block_ids && c.block_ids.length > 0 && (
              <p className="bp-cap-blocks">blocks: {c.block_ids.join(', ')}</p>
            )}
          </li>
        ))}
      </ul>
      {!accessPaused && (
        <>
          <div className="card-actions">
            <label className="bp-delivery">
              Deliver as:{' '}
              <select
                data-testid="bp-delivery-format"
                value={delivery}
                disabled={busy}
                onChange={(e) =>
                  setDelivery(e.target.value === 'github_repo' ? 'github_repo' : 'zip')
                }
              >
                <option value="zip">ZIP file in the chat</option>
                <option value="github_repo">GitHub repository</option>
              </select>
            </label>
            <button
              disabled={busy || selectedCount === 0}
              onClick={() => onApprove(excluded, delivery)}
            >
              {excluded.length > 0
                ? 'Approve & build (' + selectedCount + ' of ' + caps.length + ')'
                : 'Approve & build'}
            </button>
          </div>
          <div className="bp-refine-hint" data-testid="bp-refine-controls">
            Refine:{' '}
            <button
              className="link"
              disabled={busy}
              onClick={() => onRefine({ action: 'list_capabilities' })}
            >
              list capabilities
            </button>{' '}
            ·{' '}
            <input
              data-testid="bp-cap-id"
              aria-label="Capability id"
              placeholder="capability id"
              value={capId}
              disabled={busy}
              onChange={(e) => setCapId(e.target.value)}
            />{' '}
            <button
              className="link"
              data-testid="bp-add-cap"
              disabled={busy || !capId.trim()}
              onClick={() => onRefine({ action: 'add_capability', value: capId.trim() })}
            >
              add
            </button>{' '}
            <button
              className="link"
              data-testid="bp-remove-cap"
              disabled={busy || !capId.trim()}
              onClick={() => onRefine({ action: 'remove_capability', value: capId.trim() })}
            >
              remove
            </button>{' '}
            ·{' '}
            <input
              data-testid="bp-rename"
              aria-label="Product name"
              placeholder="new product name"
              value={renameTo}
              disabled={busy}
              onChange={(e) => setRenameTo(e.target.value)}
            />{' '}
            <button
              className="link"
              data-testid="bp-rename-apply"
              disabled={busy || !renameTo.trim()}
              onClick={() => onRefine({ action: 'rename', value: renameTo.trim() })}
            >
              rename
            </button>{' '}
          </div>
        </>
      )}
    </div>
  )
}

function CoderProgress({ build, nowMs }: { build: BuildStatus; nowMs: number }) {
  const headline = formatPhaseHeadline(build)
  const counts = formatPhaseCounts(build)
  const heartbeat = formatHeartbeat(build, nowMs)
  // The agent's own narration (the activity log) and the session log live
  // in Technical detail; the face keeps the newest line only.
  const last = build.last_event || build.activity
  const next = build.next_phase?.id
  const fraction = phaseBarFraction(build)
  return (
    <div className="coder-progress">
      <p className="coder-phase">
        <strong>{headline}</strong>
        {next ? <span className="coder-next"> · next: {stepLabel(next)}</span> : null}
      </p>
      {build.queued ? (
        <p className="coder-queued" role="status">
          Waiting for a build slot — position {build.queued.position ?? '?'}
          {build.queued.ahead ? ` (${build.queued.ahead} ahead)` : ''}
        </p>
      ) : null}
      {fraction != null && (
        <div
          className="coder-bar"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fraction * 100)}
          aria-label={counts ?? headline}
        >
          <span style={{ width: `${Math.round(fraction * 100)}%` }} />
        </div>
      )}
      {counts && <p className="coder-counts">{counts}</p>}
      {(build.activity_log?.length ?? 0) === 0 && last && <p className="coder-last">Last: {last}</p>}
      {heartbeat && <p className="coder-heartbeat">{heartbeat}</p>}
    </div>
  )
}

/**
 * The finished build's one sentence when its level stops before the last
 * check: what the level leaves out, in the user's word for the level, and
 * the way to a higher one. Null when the level includes every check.
 */
function levelSkipSentence(build: BuildStatus): string | null {
  const level = levelWord(build.build_level?.build_level)
  const skipped = checksSkipped(build.build_level?.stop_gate)
  if (!level || !build.build_level?.stop_gate || skipped.length === 0) return null
  const phrases = skipped.map((id) => CHECK_NAMES[id].phrase)
  return `${level} does not include ${orList(phrases)}. For a higher level, start a new session.`
}

function coderTakeoverNote(build: BuildStatus | null): string | null {
  if (!build) return null
  if (build.state === 'succeeded') {
    const finished = formatFinishedAuthorship(build.authorship, {
      pilotReady: isPilotZipReady(build),
      demoteFounding: shouldDemoteFounding(build),
    })
    const level = honestLevel(build)
    const sourced = hasSourcedLevel(build)
    if (isPilotZipReady(build)) {
      if (sourced && level === 'FOUNDING_CUSTOMER_READY') {
        return (finished ?? 'Coding agent finished') + '. Founding-customer-ready. Download ready.'
      }
      if (sourced && level === 'STORE_GREEN') {
        return (finished ?? 'Coding agent finished') + '. Store-green zip ready — not founding-customer-ready.'
      }
      if (finished?.startsWith('Finished')) return finished + '. Download ready.'
      return finished ?? 'Coding agent finished. Download it from Your Platforms.'
    }
    if (isAcceptancePendingPrototype(build)) {
      const score = measuredScore(build)
      const pending =
        (score ? `Acceptance is ${score} — not k/k. ` : 'Acceptance was not measured. ') +
        'Every platform must pass these 21 measured ' +
        'checks before export, and export stays closed until scripts/acceptance.py passes ' +
        'inside the Store-built image. This is a code-green prototype, not a failed build.'
      return finished ? `${finished}. ${pending}` : pending
    }
    const skip = levelSkipSentence(build)
    if (skip) {
      // The user chose a level that stops early: this is the build's finish
      // line, said once, in one sentence.
      return skip
    }
    if (finished) {
      return (
        finished +
        (build.auto_pilot
          ? '. Download is a code-cycle prototype — the pilot cycle should open automatically.'
          : '. Download is a code-cycle prototype — continue to open a pilot cycle.')
      )
    }
    return build.auto_pilot
      ? 'Code-cycle 5/5 passed. Not yet pilot-ready. The pilot cycle should open automatically.'
      : 'Code-cycle 5/5 passed. Not yet pilot-ready. Continue to open a pilot cycle.'
  }
  // A stopped or running build gets its sentence from the build card's face;
  // the raw detail lives once, in Technical detail.
  return null
}

function latestProductCard(msgs: ChatMsg[]): ChatMsg | undefined {
  for (let i = msgs.length - 1; i >= 0; i -= 1) {
    const card = msgs[i].card
    if (card === 'blueprint' || (card === 'generation' && msgs[i].engine === 'runner')) {
      return msgs[i]
    }
  }
  return undefined
}

function hydrateFromDesign(
  design: ProductDesign,
  history: { role?: string; content?: string }[] = [],
): {
  msgs: ChatMsg[]
  coderActive: boolean
} {
  const msgs: ChatMsg[] = [
    {
      role: 'factory',
      text: 'This is the factory floor. Describe the platform you need — I will draft a blueprint, and when you approve the feature list the coding agent takes over and writes it.',
    },
  ]
  // The saved conversation renders between the greeting and the status
  // card. Without this, a reload after takeover replaced the owner's
  // brief and the whole drafting exchange with two synthetic bubbles
  // (live: MEP Construction Platform, 2026-09-29) — the backend kept
  // chat_history the entire time; the Floor never asked for it. Lines
  // that exactly duplicate the greeting or a status card are skipped so
  // takeover never reads twice.
  const synthetic = new Set([
    msgs[0].text,
    'The coding agent has taken over the floor.',
  ])
  for (const turn of history) {
    const text = String(turn?.content ?? '').trim()
    if (!text || synthetic.has(text)) continue
    msgs.push({ role: turn?.role === 'user' ? 'user' : 'factory', text })
  }
  const rawBp = design.blueprint as ChatMsg['blueprint'] | null | undefined
  const intake = design.intake_blueprint as {
    roles?: { value?: string[] }
    users?: { value?: string[] }
    done_when?: { value?: string[] }
  } | null | undefined
  const bp = rawBp
    ? {
        ...rawBp,
        roles: rawBp.roles ?? intake?.roles?.value,
        users: rawBp.users ?? intake?.users?.value,
        done_when: rawBp.done_when ?? intake?.done_when?.value,
      }
    : rawBp
  const gen = design.generation
  const pendingDraft = Boolean(bp && !design.blueprint_approved)
  if (pendingDraft && bp) {
    msgs.push({
      role: 'factory',
      text: 'Blueprint drafted: ' + (bp.product_name ?? 'platform') + ' (' + (bp.vertical ?? '—') + '). Approve the feature list to start the coding agent.',
      card: 'blueprint',
      blueprint: bp,
    })
    // A leftover generation from a prior runner must not paint takeover
    // over a newly drafted, still-pending feature list.
    return { msgs, coderActive: false }
  }
  if (gen?.engine === 'runner') {
    msgs.push({
      role: 'factory',
      text: 'The coding agent has taken over the floor.',
      card: 'generation',
      engine: 'runner',
      triggeredBy: gen.triggered_by,
    })
    return { msgs, coderActive: true }
  }
  return { msgs, coderActive: false }
}

/** JSON from an SSE data line, or null when it is not JSON. */
function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** A suggestion's answers without its empty fields, to lay over the declared. */
function definedOnly(fields: IntakeState['proposal']): Partial<NonNullable<IntakeState['proposal']>> {
  return Object.fromEntries(Object.entries(fields ?? {}).filter(([, v]) => v)) as Partial<
    NonNullable<IntakeState['proposal']>
  >
}

export function Floor({
  sessionId,
  goPlans,
  accessPaused = false,
  notice = null,
  onNewSession,
}: {
  sessionId: string
  goPlatforms: () => void
  /** Opens the Plan tab (the "See plans" banner when access is paused). */
  goPlans?: () => void
  accessPaused?: boolean
  notice?: string | null
  onNewSession?: () => void | Promise<void>
}) {
  const [msgs, setMsgs] = useState<ChatMsg[]>([
    {
      role: 'factory',
      text: 'This is the factory floor. Describe the platform you need — I will draft a blueprint, and when you approve the feature list the coding agent takes over and writes it.',
    },
  ])
  const [input, setInput] = useState('')
  // The brief: what the server holds as declared and what the chat proposed.
  // Only the server's answer is shown; nothing typed lands here ahead of it.
  const [intake, setIntake] = useState<IntakeState>({ declared: {}, proposal: null })
  // The industries the Store's kits declare, for the edit sheet's chips.
  const [verticalOptions, setVerticalOptions] = useState<DeclaredVertical[]>([])
  // The edit-brief sheet, and the server's refusal of its last Save.
  const [sheetOpen, setSheetOpen] = useState(false)
  const [sheetError, setSheetError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [coderBuild, setCoderBuild] = useState<BuildStatus | null>(null)
  const [productDesign, setProductDesign] = useState<ProductDesign | null>(null)
  const [coderActive, setCoderActive] = useState(false)
  const [watchEpoch, setWatchEpoch] = useState(0)
  const [downloading, setDownloading] = useState(false)
  const [newSessionBusy, setNewSessionBusy] = useState(false)
  const [newSessionError, setNewSessionError] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  // Stop was pressed and the build has not stopped yet; the action whose
  // press is in flight; what the last press said back, in the action bar.
  const [stopping, setStopping] = useState(false)
  const [pending, setPending] = useState<ActionId | null>(null)
  // Counts finished presses; the render that bumps it releases the card hold.
  const [pressesEnded, setPressesEnded] = useState(0)
  const [outcome, setOutcome] = useState<ActionOutcome | null>(null)
  // A press of the action bar adds its line to the conversation without
  // moving the reader away from the buttons.
  const actionPressRef = useRef(false)
  // Where the build card sat on screen when a button was pressed: until the
  // answer has landed it is held there, whatever grows above it.
  const pressAnchorRef = useRef<number | null>(null)
  const wide = useWideLayout()
  const cliHonesty = useFactoryCodeCliHonesty()
  // The Floor's one scroller (page head, conversation, build card).
  const streamRef = useRef<HTMLDivElement>(null)
  const takeoverRef = useRef<HTMLDivElement>(null)
  // The reader's distance from the bottom at their last scroll, measured
  // before new content lands, so it is the reader's position and not the new
  // content's height. A fresh session starts at the bottom.
  const readerDistanceRef = useRef(0)
  const msgCountRef = useRef(0)
  // A hydration replaces the opening message with the saved history; that is
  // a reload, not a new message, so it is never followed.
  const hydratedRef = useRef(false)
  const settledRef = useRef<string | null>(null)
  // Approve appends a pending factory bubble before the generation SSE.
  // The msgs-sync effect must not treat that leftover blueprint card as
  // "still drafting" and tear down coding chrome before the first poll.
  const approveHoldRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      product.get(sessionId),
      // History is additive: a failed fetch must never block hydration.
      Promise.resolve()
        .then(() => sessions.state(sessionId))
        .then((s) => s?.chat_history ?? [])
        .catch(() => [] as { role?: string; content?: string }[]),
    ])
      .then(([design, history]) => {
        if (cancelled || !design) return
        setProductDesign(design)
        const hydrated = hydrateFromDesign(design, history)
        let applied = false
        setMsgs((current) => {
          if (current.length > 1) return current
          applied = true
          hydratedRef.current = true
          return hydrated.msgs
        })
        // Same turn as the hydrated generation card so the first watch
        // tick is not one effect behind takeover chrome (CI flake:
        // heading up, COLLECTOR 1/5 not yet painted).
        if (applied && hydrated.coderActive) {
          setCoderActive(true)
          const seeded = design.generation?.build
          if (seeded) setCoderBuild((prev) => preferHonestBuild(seeded, prev))
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [sessionId])

  // The session's saved intake (declared fields + any pending proposal).
  useEffect(() => {
    let cancelled = false
    Promise.resolve()
      .then(() => product.verticals(sessionId))
      .then((res) => {
        if (cancelled || !res) return
        if (res.intake) setIntake(res.intake)
        setVerticalOptions(res.verticals || [])
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [sessionId])

  // A build started in this session: re-read the saved design, so the brief
  // lists the approved feature list (the draft card still holds the features
  // the user left out). Read again as the build moves on, until the saved
  // design says approved.
  const designApproved = Boolean(productDesign?.blueprint_approved)
  const buildStep = coderBuild?.phases_done ?? 0
  useEffect(() => {
    if (!coderActive || designApproved) return
    let cancelled = false
    Promise.resolve()
      .then(() => product.get(sessionId))
      .then((design) => {
        // Only an approved design replaces the one on screen: an older,
        // not-yet-approved copy must never undo a newer local resolve.
        if (!cancelled && design?.blueprint_approved) setProductDesign(design)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [coderActive, designApproved, buildStep, sessionId])

  // Follow a NEW message only when the reader is near the bottom. A streamed
  // word changes the last message, not the count, so it never moves the reader;
  // neither does a build status poll.
  useEffect(() => {
    const count = msgs.length
    const grew = count > msgCountRef.current
    msgCountRef.current = count
    const el = streamRef.current
    if (hydratedRef.current) {
      // A reload lands on the TOP of the newest item (the build card, or the
      // last message, e.g. a feature list waiting for Approve), never on the
      // end of a long card.
      hydratedRef.current = false
      if (!el) return
      const rows = el.querySelectorAll<HTMLElement>('.bubble-row')
      const newest = takeoverRef.current ?? rows[rows.length - 1]
      if (newest) {
        const top = newest.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop
        el.scrollTo?.({ top: Math.max(0, top - 8) })
      }
      readerDistanceRef.current = distanceFromBottom(el)
      return
    }
    if (!grew || !el || actionPressRef.current) return
    if (!shouldFollow('new-message', readerDistanceRef.current)) return
    el.scrollTo?.({ top: el.scrollHeight, behavior: scrollBehavior() })
  }, [msgs])

  const onStreamScroll = useCallback(() => {
    const el = streamRef.current
    if (!el) return
    readerDistanceRef.current = distanceFromBottom(el)
  }, [])

  useEffect(() => {
    const latest = latestProductCard(msgs)
    if (latest?.card === 'blueprint') {
      if (approveHoldRef.current) return
      setCoderActive(false)
      setCoderBuild(null)
      return
    }
    if (latest?.card === 'generation' && latest.engine === 'runner') {
      setCoderActive(true)
    }
  }, [msgs])

  useEffect(() => {
    if (!coderActive) return
    const ac = new AbortController()
    void watchBuildStatus(
      sessionId,
      (s) => {
        if (!ac.signal.aborted) {
          setCoderBuild((prev) => preferHonestBuild(s, prev))
        }
      },
      { signal: ac.signal },
    ).catch(() => {})
    return () => ac.abort()
  }, [coderActive, sessionId, watchEpoch])

  const liveCoderBuild = withClientStall(
    withResolvedNRequired(
      coderBuild,
      nRequiredFromProductInputs(productDesign?.blueprint ?? productDesign?.plan),
    ),
    nowMs,
  )
  // When the build first reaches stopped or finished, bring the TOP of the
  // build card into view once. A later poll in the same state does nothing.
  const settled = coderActive ? settledKey(liveCoderBuild?.state) : null
  useEffect(() => {
    if (settled === settledRef.current) return
    settledRef.current = settled
    if (!settled) return
    const stream = streamRef.current
    const card = takeoverRef.current
    if (!stream || !card) return
    const top = card.getBoundingClientRect().top - stream.getBoundingClientRect().top + stream.scrollTop
    stream.scrollTo?.({ top: Math.max(0, top - 8), behavior: scrollBehavior() })
  }, [settled])

  // "Stopping…" holds until the build leaves building; a new run starts clean.
  const runningNow = liveCoderBuild?.state === 'building'
  useEffect(() => {
    if (!runningNow) setStopping(false)
  }, [runningNow])

  useEffect(() => {
    if (liveCoderBuild?.state !== 'building') return
    const id = window.setInterval(() => setNowMs(Date.now()), 5000)
    return () => window.clearInterval(id)
  }, [liveCoderBuild?.state])

  const sendCore = useCallback(
    async (
      message: string,
      typed?: TypedFloorAction,
      answers?: BriefAnswers,
    ): Promise<{
      runnerStarted: boolean
      refused: string | null
      info: InfoReply | null
      reopened: boolean
      failed: string | null
    }> => {
      let runnerStarted = false
      let refused: string | null = null
      let info: InfoReply | null = null
      let reopened = false
      let failed: string | null = null
      // A typed action shows what the user pressed; it is not chat text.
      const shown = typed
        ? typedActionLabel(typed)
        : message
      setMsgs((m) => [...m, { role: 'user', text: shown }, { role: 'factory', text: '' }])
      try {
        // The brief's answers travel only with the edit sheet's Save, all
        // four in one request; nothing rides along with any other request.
        const trailing: [
          vertical?: string | null,
          locale?: DeclaredLocale | null,
          typed?: TypedFloorAction | null,
          buildLevel?: string | null,
        ] = answers
          ? [
              answers.vertical,
              { country: answers.country, currency: answers.currency },
              typed,
              answers.build_level,
            ]
          : typed
            ? [undefined, undefined, typed]
            : []
        await chatStream(
          sessionId,
          message,
          (ev: ChatEvent) => {
          const token = chatEventText(ev)
          if (token !== null) {
            setMsgs((m) => {
              const copy = [...m]
              const last = copy[copy.length - 1]
              copy[copy.length - 1] = { ...last, text: last.text + token }
              return copy
            })
            return
          }
          if (ev.event === 'info') {
            const d = (typeof ev.data === 'string' ? safeJson(ev.data) : ev.data) as InfoReply | null
            if (!d || typeof d !== 'object') return
            info = d
            if (d.ok === false) refused = d.summary || 'Refused.'
            // A reply whose words are not streamed is never an empty bubble.
            if (d.stream_delta === false && d.summary) {
              const text = d.summary
              setMsgs((m) => {
                const copy = [...m]
                const last = copy[copy.length - 1]
                if (last?.role === 'factory' && !last.text.trim() && !last.card) {
                  copy[copy.length - 1] = { ...last, text, card: 'info' }
                }
                return copy
              })
            }
            return
          }
          if (ev.event === 'intake') {
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as IntakeState | null
            if (d && typeof d === 'object' && d.declared) {
              reopened = Boolean(d.reopened)
              setIntake({ declared: d.declared, proposal: d.proposal ?? null, reopened })
            }
            return
          }
          if (ev.event === 'blueprint') {
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as {
              summary?: string
              blueprint?: ChatMsg['blueprint']
              intake_blueprint?: {
                roles?: { value?: string[] }
                users?: { value?: string[] }
                done_when?: { value?: string[] }
              }
              plain_language?: string
            }
            const summary = d?.summary ?? 'Blueprint drafted.'
            const intake = d?.intake_blueprint
            const merged = d?.blueprint
              ? {
                  ...d.blueprint,
                  roles: intake?.roles?.value,
                  users: intake?.users?.value,
                  done_when: intake?.done_when?.value,
                }
              : d?.blueprint
            setCoderActive(false)
            setCoderBuild(null)
            setMsgs((m) => [
              ...m.slice(0, -1),
              {
                role: 'factory',
                text: summary,
                card: 'blueprint',
                blueprint: merged,
                plainLanguage: d?.plain_language,
              },
            ])
          } else if (ev.event === 'generation') {
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as {
              summary?: string
              triggered_by?: string
              generation?: { engine?: string; triggered_by?: string }
            } | null
            const summary = d?.summary ?? 'Platform generated.'
            const engine = d?.generation?.engine
            const triggeredBy = d?.triggered_by ?? d?.generation?.triggered_by
            if (engine === 'runner') {
              // Clear a prior FINISHED snapshot immediately so a pilot reopen
              // cannot keep "Download ready" pinned while Platforms is Building…
              runnerStarted = true
              setCoderBuild((prev) =>
                stampBuildObservation({ state: 'building', detail: 'build in progress' }, prev),
              )
              setCoderActive(true)
              setWatchEpoch((n) => n + 1)
            }
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: summary, card: 'generation', engine, triggeredBy },
            ])
          } else if (ev.event === 'error') {
            setMsgs((m) => [
              ...m.slice(0, -1),
              { role: 'factory', text: String(ev.data ?? 'Something went wrong.'), card: 'error' },
            ])
          } else if (ev.event === 'chain' || ev.event === 'rules') {
            setMsgs((m) => {
              const copy = [...m]
              const last = copy[copy.length - 1]
              if (last?.text?.trim()) return copy
              copy[copy.length - 1] = {
                ...last,
                text: 'That sounds like kit configuration. The floor builds whole platforms — describe the platform you want instead.',
                card: 'info',
              }
              return copy
            })
          }
          },
          ...trailing,
        )
      } catch (e) {
        failed = humanError(e, 'The message did not reach the Factory. Try again.')
        const errorText = failed
        setMsgs((m) => [
          ...m.slice(0, -1),
          { role: 'factory', text: errorText, card: 'error', retry: typed ? undefined : message },
        ])
      }
      // A reply that ended with no words and no card leaves no empty bubble.
      setMsgs((m) => {
        const last = m[m.length - 1]
        if (last?.role !== 'factory' || last.text.trim() || last.card) return m
        return m.slice(0, -1)
      })
      return { runnerStarted, refused, info, reopened, failed }
    },
    [sessionId],
  )

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busy || accessPaused) return
      setInput('')
      setBusy(true)
      try {
        const { failed } = await sendCore(message)
        // A failed send puts the words back, unless something new was typed.
        if (failed) setInput((now) => (now ? now : message))
      } finally {
        setBusy(false)
      }
    },
    [accessPaused, busy, sendCore],
  )

  // The edit sheet's Save: all four answers and the typed Confirm in one
  // request. The card then shows what the server returned; a refusal stays
  // in the sheet with the server's reason.
  const saveBrief = useCallback(
    async (answers: BriefAnswers) => {
      if (busy || accessPaused) return
      setBusy(true)
      setSheetError(null)
      try {
        const resume = Boolean(intake.reopened)
        const { refused, failed, runnerStarted } = await sendCore('', { action: 'confirm_intake' }, answers)
        if (refused || failed) setSheetError(refused || failed)
        else {
          setSheetOpen(false)
          if (resume && runnerStarted) setOutcome({ tone: 'done', text: 'Saved. Started again.', next: null })
        }
      } finally {
        setBusy(false)
      }
    },
    [accessPaused, busy, intake.reopened, sendCore],
  )

  const sendTyped = useCallback(
    async (typed: TypedFloorAction) => {
      if (busy || accessPaused) return null
      setBusy(true)
      try {
        return await sendCore('', typed)
      } finally {
        setBusy(false)
      }
    },
    [accessPaused, busy, sendCore],
  )

  // H1 (F-4): the user resolves the golden OFFERED on the current draft. Both
  // call the dedicated routes and re-render the blueprint card with the
  // resulting blueprint — accept swaps in the golden, decline keeps the user's
  // draft and clears the offer so the accept/decline card disappears.
  // F-4c: `busy` is React state, so two clicks in one batch both read the old
  // value; this ref flips synchronously and blocks the second request.
  const resolveInFlightRef = useRef(false)
  const resolveGolden = useCallback(
    async (mode: 'accept' | 'decline') => {
      if (busy || accessPaused || resolveInFlightRef.current) return
      resolveInFlightRef.current = true
      setBusy(true)
      try {
        const resp =
          mode === 'accept'
            ? await product.acceptGolden(sessionId)
            : await product.declineGolden(sessionId)
        const bp = resp.blueprint as NonNullable<ChatMsg['blueprint']>
        setProductDesign((d) => (d ? { ...d, blueprint: resp.blueprint } : d))
        setMsgs((m) => {
          const copy = [...m]
          for (let i = copy.length - 1; i >= 0; i -= 1) {
            if (copy[i].card === 'blueprint') {
              copy[i] = {
                ...copy[i],
                text:
                  mode === 'accept'
                    ? 'Golden accepted — this platform now builds the golden blueprint. Approve the feature list to start the coding agent.'
                    : 'Golden declined — keeping your own draft. Approve the feature list to start the coding agent.',
                blueprint: bp,
              }
              return copy
            }
          }
          return copy
        })
      } catch (e) {
        setMsgs((m) => [
          ...m,
          {
            role: 'factory',
            text: e instanceof Error ? e.message : 'could not resolve the golden offer',
            card: 'error',
          },
        ])
      } finally {
        resolveInFlightRef.current = false
        setBusy(false)
      }
    },
    [accessPaused, busy, sessionId],
  )

  const approveWithSelection = useCallback(
    async (excludedIds: string[], delivery: 'zip' | 'github_repo') => {
      if (busy || accessPaused) return
      approveHoldRef.current = true
      setBusy(true)
      // The delivery format is the client's choice at request time: stamp
      // it on the session blueprint before the chat approve triggers the
      // build, so STORE delivery honours it.
      const latest = latestProductCard(msgs)
      const bpForDelivery = latest?.blueprint
      if (bpForDelivery) {
        try {
          await product.approve(sessionId, false, {
            ...bpForDelivery,
            delivery_format: delivery,
          })
        } catch {
          // The chat approve below still proceeds; delivery defaults to zip.
        }
      }
      // Mount coding chrome immediately so the Floor cannot flash an empty
      // composer / hide takeover while chat SSE and the first status poll
      // are still in flight.
      setCoderActive(true)
      setCoderBuild((prev) =>
        stampBuildObservation({ state: 'building', detail: 'build in progress' }, prev),
      )
      let runnerStarted = false
      try {
        for (const id of excludedIds) {
          const removed = await sendCore('', { action: 'remove_capability', value: id })
          runnerStarted = runnerStarted || removed.runnerStarted
        }
        const approved = await sendCore('', { action: 'approve' })
        runnerStarted = runnerStarted || approved.runnerStarted
      } catch {
        runnerStarted = false
      } finally {
        approveHoldRef.current = false
        setBusy(false)
        if (!runnerStarted) {
          setCoderActive(false)
          setCoderBuild(null)
        }
      }
    },
    [accessPaused, busy, msgs, sendCore, sessionId],
  )

  async function startNewSession() {
    if (!onNewSession || newSessionBusy || accessPaused) return
    setNewSessionBusy(true)
    setNewSessionError(null)
    try {
      await onNewSession()
    } catch (e) {
      setNewSessionError(humanError(e, 'Could not start a new session. Try again.'))
    } finally {
      setNewSessionBusy(false)
    }
  }

  const EXPORT_FAILED = 'The download could not be made. Try again.'

  // A copy of a build that did not pass. A FAILED build records the typed
  // take_copy first (its MANIFEST names FAILED(gate, check, finding), never
  // certified); a stall or a refused finished build downloads as-is.
  async function downloadCopy(): Promise<ActionOutcome> {
    if (liveCoderBuild?.state === 'failed') {
      const reply = await sendTyped({ action: 'take_copy' })
      if (reply?.failed) return { tone: 'error', text: reply.failed, next: null }
      if (reply?.info?.ok === false) return actionOutcome('download_copy', reply.info)
    }
    setDownloading(true)
    try {
      await downloadProductPackage(sessionId, { asIs: true })
      return { tone: 'done', text: doneText('download_copy'), next: null }
    } catch (e) {
      return { tone: 'error', text: humanError(e, EXPORT_FAILED), next: null }
    } finally {
      setDownloading(false)
    }
  }

  async function download(): Promise<ActionOutcome | null> {
    const aff = exportAffordance(liveCoderBuild)
    if (aff.disabled) return null
    setDownloading(true)
    try {
      if (aff.asIs) {
        // Gate-failed build, owner asked anyway: the server ships it loudly
        // labeled as-is. No waiting on a build that already ended.
        await downloadProductPackage(sessionId, { asIs: true })
        return { tone: 'done', text: doneText('download_copy'), next: null }
      }
      const status =
        liveCoderBuild?.state === 'succeeded'
          ? liveCoderBuild
          : await awaitBuild(sessionId, (s) =>
              setCoderBuild((prev) => preferHonestBuild(s, prev)),
            )
      if (status.state === 'failed' || status.state === 'stalled') {
        return {
          tone: 'refused',
          text: 'Refused: the build did not pass its checks, so it is not shipped as certified.',
          next: null,
        }
      }
      await downloadProductPackage(sessionId)
      return { tone: 'done', text: doneText('download_platform'), next: null }
    } catch (e) {
      return { tone: 'error', text: humanError(e, EXPORT_FAILED), next: null }
    } finally {
      setDownloading(false)
    }
  }

  async function stopBuild(): Promise<ActionOutcome> {
    setStopping(true)
    try {
      await product.coderControl(sessionId, 'stop')
      return { tone: 'done', text: doneText('stop'), next: null }
    } catch (e) {
      setStopping(false)
      return { tone: 'error', text: `Could not stop the build. ${humanError(e, 'Try again.')}`, next: null }
    }
  }

  async function copyErrorReport(): Promise<ActionOutcome> {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('no clipboard')
      await navigator.clipboard.writeText(
        errorReport(liveCoderBuild, { sessionId, productTitle }),
      )
      return { tone: 'done', text: doneText('copy_report'), next: null }
    } catch {
      return {
        tone: 'error',
        text: 'Could not copy. Open Technical detail and select the text instead.',
        next: null,
      }
    }
  }

  // A typed press: refused and why, or done. "Change answers" opens the
  // brief sheet only when the server's reply reopened the intake.
  async function typedPress(id: ActionId, typed: TypedFloorAction): Promise<ActionOutcome | null> {
    const reply = await sendTyped(typed)
    if (!reply) return null
    if (reply.failed) return { tone: 'error', text: reply.failed, next: null }
    const result = actionOutcome(id, reply.info)
    if (id === 'change_answers' && result.tone === 'done') {
      if (!reply.reopened) {
        return { tone: 'refused', text: 'Refused: the brief could not be reopened.', next: null }
      }
      setSheetError(null)
      setSheetOpen(true)
    }
    return result
  }

  function pressOf(id: ActionId): Promise<ActionOutcome | null> {
    if (id === 'try_again') return typedPress(id, { action: 'continue' })
    if (id === 'change_answers') return typedPress(id, { action: 'continue_with_intake' })
    if (id === 'start_over') return typedPress(id, { action: 'start_over', value: 'confirmed' })
    if (id === 'run_pilot') return typedPress(id, { action: 'run_pilot' })
    if (id === 'stop') return stopBuild()
    if (id === 'download_copy') return downloadCopy()
    if (id === 'download_platform') return download()
    if (id === 'copy_report') return copyErrorReport()
    return Promise.resolve(null)
  }

  // Every press of the action bar goes through here: one action, one path.
  // The pressed button shows its busy label; the result lands in the bar.
  // Try again is the typed continue for a failed build and a stall alike, so
  // the generation event restarts the status watch either way.
  async function runAction(id: ActionId) {
    if (pending) return
    setOutcome(null)
    setPending(id)
    actionPressRef.current = true
    pressAnchorRef.current = takeoverRef.current?.getBoundingClientRect().top ?? null
    try {
      setOutcome(await pressOf(id))
    } finally {
      actionPressRef.current = false
      setPending(null)
      // The render that carries this count releases the hold (below).
      setPressesEnded((n) => n + 1)
    }
  }

  // Hold the build card where it was pressed: new lines in the conversation,
  // a reopened brief or the result line never move the reader.
  const holdCard = useCallback(() => {
    const stream = streamRef.current
    const card = takeoverRef.current
    if (!stream || !card) return
    holdCardTop(stream, card.getBoundingClientRect().top, pressAnchorRef.current)
  }, [])
  useLayoutEffect(holdCard)
  // Release the hold from the render that ends the press, not on a timer: that
  // render carries every line the press wrote (a reply replacing its typing
  // dots included), and the layout effect above has already held the card for
  // it. A timed release can land before those lines are drawn.
  useLayoutEffect(() => {
    pressAnchorRef.current = null
  }, [pressesEnded])

  const coderBuilding =
    coderActive &&
    liveCoderBuild?.state !== 'succeeded' &&
    liveCoderBuild?.state !== 'failed' &&
    liveCoderBuild?.state !== 'stalled'
  const coderSucceeded = liveCoderBuild?.state === 'succeeded'
  const coderTerminal =
    liveCoderBuild?.state === 'failed' || liveCoderBuild?.state === 'stalled'
  const designBp = productDesign?.blueprint as
    | { product_name?: string; name?: string }
    | null
    | undefined
  const cardBp = latestBlueprintIn(msgs)
  const productTitle = displayProductName({
    productName: designBp?.product_name ?? cardBp?.product_name,
    altName: designBp?.name,
    productId: productDesign?.generation?.product_id,
  })

  // The brief's features: the saved list once approved (also after a
  // reload), otherwise the newest draft in the conversation.
  const designCaps = (productDesign?.blueprint as { capabilities?: Capability[] } | null | undefined)
    ?.capabilities
  const featuresApproved = Boolean(productDesign?.blueprint_approved && designCaps)
  const briefCaps = featuresApproved ? designCaps : cardBp?.capabilities ?? designCaps
  const briefFeatures = briefCaps ? briefCaps.map((c) => c.id) : null
  const briefDelivery =
    liveCoderBuild?.delivery_format ??
    (productDesign?.blueprint as { delivery_format?: string } | null | undefined)?.delivery_format ??
    null
  const firstAsk = msgs.find((m) => m.role === 'user' && m.text.trim())?.text ?? null
  const hasBlueprint = Boolean(designBp || cardBp)
  const statusOfBrief = briefStatus({
    intake,
    approved: Boolean(productDesign?.blueprint_approved) || coderActive,
    buildState: coderActive ? liveCoderBuild?.state : null,
  })

  const briefEditable = statusOfBrief === 'draft' || statusOfBrief === 'suggested by chat'

  // One action bar, in exactly one place: pinned above the composer on
  // phones, inside the BUILD card from 768px.
  const actionPlan = coderActive
    ? buildActions({ build: liveCoderBuild, accessPaused, busy, downloading, stopping, pending })
    : null
  const actionBar = actionPlan ? (
    <ActionBar
      plan={actionPlan}
      placement={wide ? 'card' : 'bar'}
      onRun={(id) => void runAction(id)}
      onSeePlans={goPlans}
      outcome={outcome}
      onDismiss={() => setOutcome(null)}
    />
  ) : null

  return (
    <div className="floor">
      <BriefCard
        intake={intake}
        status={statusOfBrief}
        features={briefFeatures}
        featuresApproved={featuresApproved}
        productName={hasBlueprint ? productTitle : null}
        delivery={briefDelivery}
        firstAsk={firstAsk}
        onShowConversation={() => streamRef.current?.scrollTo?.({ top: 0 })}
        onTyped={(typed) => void sendTyped(typed)}
        disabled={busy || accessPaused}
        onEdit={() => {
          setSheetError(null)
          setSheetOpen(true)
        }}
      />
      {sheetOpen && briefEditable && (
        <BriefSheet
          initial={{ ...intake.declared, ...definedOnly(intake.proposal) }}
          verticals={verticalOptions}
          resume={Boolean(intake.reopened)}
          busy={busy}
          error={sheetError}
          onSave={(answers) => void saveBrief(answers)}
          onClose={() => setSheetOpen(false)}
        />
      )}
      <div
        className="chat-scroll floor-stream"
        ref={streamRef}
        onScroll={onStreamScroll}
        data-testid="floor-stream"
      >
        <header className="page-head">
          <div className="page-head-row">
            <h2>Factory Floor</h2>
            {onNewSession && (
              <button
                type="button"
                className="ghost"
                data-testid="floor-new-session"
                disabled={busy || newSessionBusy || accessPaused}
                onClick={() => void startNewSession()}
              >
                {newSessionBusy ? 'Starting…' : 'New session'}
              </button>
            )}
          </div>
          {newSessionError && (
            <div className="error-box new-session-error" role="alert" data-testid="floor-new-session-error">
              <span>{newSessionError}</span>
              <button
                type="button"
                className="quiet icon-button"
                aria-label="Close this message"
                onClick={() => setNewSessionError(null)}
              >
                ×
              </button>
            </div>
          )}
          {notice && (
            <div className="notice-box already-signed-in" role="status">
              {notice}
            </div>
          )}
          <p className="dim">Describe the platform. Approve the feature list. The coding agent takes over and writes it.</p>
          <NotYetNotice />
          <FactoryCodeCliStatus message={cliHonesty} testId="floor-factory-cli-status" />
        </header>
          {msgs.map((m, i) => {
            // Terminal runs own the floor via the STOPPED/stalled panel.
            // Hide teal "taken over" generation chrome so it cannot sit above it.
            if (m.card === 'generation' && coderTerminal) return null
            return (
              <div key={i} className={'bubble-row ' + m.role}>
                <div className={'bubble ' + m.role + ' ' + (m.card ?? '')}>
                  {m.text ||
                    (m.role === 'factory' && busy && i === msgs.length - 1 ? <WaitingMark /> : null)}
                  {m.card === 'error' && m.retry && (
                    <div className="card-actions">
                      <button
                        type="button"
                        className="ghost"
                        data-testid="chat-error-retry"
                        disabled={busy || accessPaused}
                        onClick={() => void send(m.retry!)}
                      >
                        Try again
                      </button>
                    </div>
                  )}
                  {m.card === 'blueprint' && m.blueprint && (
                    <BlueprintCard
                      blueprint={m.blueprint}
                      busy={busy || coderActive}
                      accessPaused={accessPaused}
                      onApprove={(excludedIds, delivery) =>
                  void approveWithSelection(excludedIds, delivery)
                }
                      onRefine={(typed) => void sendTyped(typed)}
                      onAcceptGolden={() => void resolveGolden('accept')}
                      onDeclineGolden={() => void resolveGolden('decline')}
                    />
                  )}
                  {m.card === 'generation' && (
                    <div className="card-actions">
                      {m.engine === 'runner' && (
                        <span
                          className="bp-drafting-mode architect_llm"
                          title="The coding agent took over after you approved the feature list"
                        >
                          coding agent
                        </span>
                      )}
                      {m.triggeredBy === 'chat_llm' && (
                        <span
                          className="bp-drafting-mode architect_llm"
                          title="The Floor chat LLM called start_coder"
                        >
                          chat LLM
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        {coderActive && (
          <BuildCard
            cardRef={takeoverRef}
            build={liveCoderBuild}
            sessionId={sessionId}
            productTitle={productTitle}
            title={
              liveCoderBuild?.state === 'succeeded' ? (
                <>
                  {levelWord(liveCoderBuild.build_level?.build_level) ? (
                    <span className="build-state-level" data-testid="floor-ready-level">
                      {' — '}
                      {levelWord(liveCoderBuild.build_level?.build_level)}
                    </span>
                  ) : null}{' '}
                  <span className="build-state-product" data-testid="floor-product-title">
                    {productTitle}
                  </span>
                  {measuredScore(liveCoderBuild) ? (
                    <span className="build-state-score" data-testid="floor-acceptance-score">
                      {' '}Acceptance {measuredScore(liveCoderBuild)}
                    </span>
                  ) : null}
                </>
              ) : undefined
            }
            note={coderSucceeded ? coderTakeoverNote(liveCoderBuild) : undefined}
            actions={wide ? actionBar : undefined}
            steps={
              <>
                {coderSucceeded && <LevelGradeStrip build={liveCoderBuild} testIdPrefix="floor" />}
                <Stepper build={liveCoderBuild} />
                {liveCoderBuild && liveCoderBuild.state === 'building' && (
                  <CoderProgress build={liveCoderBuild} nowMs={nowMs} />
                )}
              </>
            }
            detail={
              // Grade names, the stop gate and raw gate verdicts: Technical detail only.
              <LevelGradeDetail build={liveCoderBuild} testIdPrefix="floor" />
            }
            footer={
              <>
                {(liveCoderBuild?.suggested_checks?.length ?? 0) > 0 && (
                  <SuggestedChecks checks={liveCoderBuild!.suggested_checks!} />
                )}
              </>
            }
          />
        )}
      </div>
      <div className="floor-footer">
        {!wide && actionBar}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault()
            send(input)
          }}
        >
          <FloorUpload
            sessionId={sessionId}
            disabled={busy || coderBuilding || accessPaused}
          />
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              accessPaused
                ? 'Factory access is paused'
                : coderBuilding
                  ? 'The coding agent has taken over this floor…'
                  : 'Try: "Build me a secure multi-user platform for my team…"'
            }
            disabled={busy || coderBuilding || accessPaused}
          />
          <button type="submit" disabled={busy || coderBuilding || accessPaused || !input.trim()}>
            Send
          </button>
        </form>
        {accessPaused && (
          <p className="dim composer-paused">Factory access is paused.</p>
        )}
      </div>
    </div>
  )
}

/** A reply is on its way: a moving, labelled mark (still under reduced motion). */
export function WaitingMark() {
  return (
    <span className="typing" role="status" data-testid="chat-waiting">
      <span className="typing-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className="typing-label">The Factory is answering…</span>
    </span>
  )
}

const NOT_YET_KEY = 'cerebrum.floor.notYetOpen'

function readNotYetOpen(): boolean {
  try {
    return window.localStorage.getItem(NOT_YET_KEY) === '1'
  } catch {
    return false
  }
}

/**
 * "What this is not yet" is a one-line link. Opening it shows the notice;
 * closing it is remembered, so the notice stays a single line afterwards.
 */
export function NotYetNotice() {
  const [open, setOpen] = useState(readNotYetOpen)
  const toggle = () => {
    const next = !open
    setOpen(next)
    try {
      window.localStorage.setItem(NOT_YET_KEY, next ? '1' : '0')
    } catch {
      // Storage refused (private mode): the choice lasts for this page only.
    }
  }
  return (
    <div className="notice-not-yet-wrap">
      <button
        type="button"
        className="notice-not-yet-link"
        aria-expanded={open}
        aria-controls="floor-not-yet"
        data-testid="floor-not-yet-link"
        onClick={toggle}
      >
        What this is not yet <span aria-hidden="true">{open ? '\u25B4' : '\u203A'}</span>
      </button>
      {open && (
        <p className="dim notice-not-yet" id="floor-not-yet" data-testid="floor-not-yet">
          What this is not yet: the factory generates a working prototype — real code,
          tests and deploy files — not a finished production system. Third-party
          integrations in generated products are stubs until you connect your own
          credentials, deployment is a step you run rather than something that happens
          for you, and free-trial accounts have server-enforced caps on generations,
          daily chat and exports. Answers are grounding-checked: when a claim can't be
          verified it is withheld, not invented.
        </p>
      )}
    </div>
  )
}
