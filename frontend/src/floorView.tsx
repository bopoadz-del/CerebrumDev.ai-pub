import { useCallback, useEffect, useRef, useState } from 'react'
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
  formatAcceptanceScore,
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
  kernelName,
} from './buildProgress'
import { LevelGradeStrip } from './levelGradeView'
import { VerticalPicker } from './verticalPicker'
import { LocalePicker } from './localePicker'
import { IntakeLine } from './intakeLine'
import { displayProductName, humanizeProductId, latestBlueprintIn } from './productDisplay'

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
  accessPaused = false,
}: {
  blueprint: NonNullable<ChatMsg['blueprint']>
  busy: boolean
  onApprove: (excludedIds: string[], delivery: 'zip' | 'github_repo') => void
  onRefine: (typed: TypedFloorAction) => void
  accessPaused?: boolean
}) {
  const caps = blueprint.capabilities ?? []
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

const KERNEL_JOBS: Record<string, { title: string; agent: boolean }> = {
  COLLECTOR: { title: 'Binding surveyor', agent: true },
  CLONER: { title: 'Block stocker', agent: false },
  WRITER: { title: 'Platform manufacturer', agent: true },
  TESTER: { title: 'Acceptance inspector', agent: false },
  STORE_MANAGER: { title: 'Store registrar', agent: false },
}


function KernelStrip({ build }: { build: BuildStatus | null }) {
  const phases = build?.phases?.length
    ? build.phases
    : ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']
  const done = new Set(build?.completed ?? [])
  const current = build?.current_phase?.id
  const failedAt = build?.failure?.location || build?.failure?.phase
  const failedReason = build?.failure?.reason || 'unknown'
  const failedDetail = build?.failure?.detail || ''
  // A failure on a run that is still moving is a rework round -- the phase
  // sent the work back and the machine is fixing it. Red is reserved for a
  // run that actually stopped; live 2026-09-30 TESTER suite_red painted the
  // same red as a dead build while WORKERS were still writing.
  const stopped = build?.state === 'failed' || build?.state === 'stalled'
  return (
    <ol className="kernel-strip">
      {phases.map((phase) => {
        const job = KERNEL_JOBS[phase]
        const cls = done.has(phase) ? 'done' : phase === current ? 'current' : undefined
        const failed = phase === failedAt && stopped
        const rework = phase === failedAt && !stopped
        return (
          <li
            key={phase}
            className={[cls, failed ? 'failed' : undefined, rework ? 'rework' : undefined]
              .filter(Boolean)
              .join(' ')}
            title={
              failed
                ? `${kernelName(phase)} failed — ${failedReason}${failedDetail ? ': ' + failedDetail : ''}`
                : rework
                  ? `${kernelName(phase)} sent it back to the coder — ${failedReason}${failedDetail ? ': ' + failedDetail : ''}`
                  : undefined
            }
            data-testid={
              failed
                ? `floor-phase-failed-${phase}`
                : rework
                  ? `floor-phase-rework-${phase}`
                  : undefined
            }
          >
            <span className="kernel-id">{kernelName(phase)}</span>
            {job ? <span className="kernel-title">{job.title}</span> : null}
            {job?.agent ? <span className="kernel-agent">agent</span> : null}
          </li>
        )
      })}
    </ol>
  )
}

function CoderProgress({ build, nowMs }: { build: BuildStatus; nowMs: number }) {
  const headline = formatPhaseHeadline(build)
  const counts = formatPhaseCounts(build)
  const heartbeat = formatHeartbeat(build, nowMs)
  const last = build.last_event || build.activity
  // The agent narrates its own pass; show the tail, not just the newest
  // sentence, so a working agent is visibly distinct from a wedged one.
  const activity = build.activity_log ?? []
  const next = build.next_phase?.id
  const fraction = phaseBarFraction(build)
  return (
    <div className="coder-progress">
      <p className="coder-phase">
        Writing your platform — <strong>{headline}</strong>
        {build.current_phase?.label ? (
          <span className="coder-phase-label"> — {build.current_phase.label}</span>
        ) : null}
        {next ? <span className="coder-next"> then {next}</span> : null}
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
      {activity.length > 0 ? (
        <ol className="coder-activity" data-testid="floor-activity-log">
          {activity.map((line, i) => (
            <li
              key={(line.ts ?? '') + ':' + i}
              className={i === activity.length - 1 ? 'coder-activity-now' : undefined}
            >
              {line.role ? <span className="coder-activity-role">{line.role}</span> : null}
              <span className="coder-activity-text">{line.text}</span>
            </li>
          ))}
        </ol>
      ) : (
        last && <p className="coder-last">Last: {last}</p>
      )}
      {heartbeat && <p className="coder-heartbeat">{heartbeat}</p>}
      {(build.coder_log || build.coder_log_present) && (
        <pre className="coder-session-log" data-testid="floor-coder-log">
          {build.coder_log || 'Coder session starting…'}
        </pre>
      )}
    </div>
  )
}

function coderTakeoverHeading(build: BuildStatus | null): string {
  if (build?.state === 'succeeded') {
    if (isPilotZipReady(build)) return 'Coding agent finished'
    if (isAcceptancePendingPrototype(build)) {
      return `Acceptance ${formatAcceptanceScore(build)} — not pilot-ready`
    }
    return 'Code-cycle prototype ready'
  }
  return 'Coding agent has taken over'
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
      const score = formatAcceptanceScore(build)
      const pending =
        `Acceptance is ${score} — not k/k. Every platform must pass these 21 measured ` +
        'checks before export, and export stays closed until scripts/acceptance.py passes ' +
        'inside the Store-built image. This is a code-green prototype, not a failed build.'
      return finished ? `${finished}. ${pending}` : pending
    }
    if (build.build_level?.stop_gate === 'CODE') {
      // The user chose prototype: CODE_GREEN is this build's finish line.
      const done = `Built to ${build.build_level.build_level}: done at CODE_GREEN — the pilot suite and the Store gate do not run at this level.`
      return finished ? `${finished}. ${done}` : done
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
  if (build.state === 'failed' || build.state === 'stalled') {
    return 'The coding agent stopped: ' + (build.detail ?? 'build did not pass its gates') + '.'
  }
  const headline = formatPhaseHeadline(build)
  const last = build.last_event || build.activity
  if (last) {
    return 'Writing your platform — ' + headline + ' (last: ' + last + ')'
  }
  return 'Writing your platform — ' + headline
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

export function Floor({
  sessionId,
  goPlatforms,
  accessPaused = false,
  notice = null,
  onNewSession,
}: {
  sessionId: string
  goPlatforms: () => void
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
  // The user's own vertical choice (picked or typed). Sent with each chat as
  // a typed field; '' = no choice (a general product, no domain kit).
  const [vertical, setVertical] = useState('')
  // Sent only when the user changed it, so a choice made another way (the
  // typed "vertical is ..." command) is never cleared by an untouched picker.
  const [verticalDirty, setVerticalDirty] = useState(false)
  // The intake line: what the user declared and what the chat proposed.
  const [intake, setIntake] = useState<IntakeState>({ declared: {}, proposal: null })
  const pickVertical = useCallback((v: string) => {
    setVertical(v)
    setVerticalDirty(true)
    setIntake((prev) => ({ ...prev, declared: { ...prev.declared, vertical: v || null } }))
  }, [])
  const syncVertical = useCallback((v: string) => setVertical(v), [])
  // The country/currency the user declares (typed fields, shape-checked).
  // Sent only when changed, like the vertical; '' = undeclared.
  const [locale, setLocale] = useState<DeclaredLocale>({ country: '', currency: '' })
  const [localeDirty, setLocaleDirty] = useState(false)
  const pickLocale = useCallback((next: DeclaredLocale) => {
    setLocale(next)
    setLocaleDirty(true)
    setIntake((prev) => ({
      ...prev,
      declared: {
        ...prev.declared,
        country: next.country || null,
        currency: next.currency || null,
      },
    }))
  }, [])
  const syncLocale = useCallback((next: DeclaredLocale) => setLocale(next), [])
  const [busy, setBusy] = useState(false)
  const [coderBuild, setCoderBuild] = useState<BuildStatus | null>(null)
  const [productDesign, setProductDesign] = useState<ProductDesign | null>(null)
  const [coderActive, setCoderActive] = useState(false)
  const [watchEpoch, setWatchEpoch] = useState(0)
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [newSessionBusy, setNewSessionBusy] = useState(false)
  const [newSessionError, setNewSessionError] = useState<string | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [controlBusy, setControlBusy] = useState(false)
  const cliHonesty = useFactoryCodeCliHonesty()
  const bottomRef = useRef<HTMLDivElement>(null)
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
        if (res.chosen) setVertical(res.chosen)
        if (res.country || res.currency)
          setLocale({ country: res.country || '', currency: res.currency || '' })
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [msgs, coderBuild])

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
  useEffect(() => {
    if (liveCoderBuild?.state !== 'building') return
    const id = window.setInterval(() => setNowMs(Date.now()), 5000)
    return () => window.clearInterval(id)
  }, [liveCoderBuild?.state])

  const sendCore = useCallback(
    async (
      message: string,
      typed?: TypedFloorAction,
    ): Promise<{ runnerStarted: boolean }> => {
      let runnerStarted = false
      // A typed action shows what the user pressed; it is not chat text.
      const shown = typed
        ? typedActionLabel(typed)
        : message
      setMsgs((m) => [...m, { role: 'user', text: shown }, { role: 'factory', text: '' }])
      try {
        // Trailing typed fields, only when the user changed them.
        const typedFields: [vertical?: string | null, locale?: DeclaredLocale | null] = localeDirty
          ? [verticalDirty ? vertical : undefined, locale]
          : verticalDirty
            ? [vertical]
            : []
        // A typed action rides after the vertical/locale slots; free text
        // keeps the original call shape.
        const trailing: [
          vertical?: string | null,
          locale?: DeclaredLocale | null,
          typed?: TypedFloorAction | null,
        ] = typed ? [typedFields[0], typedFields[1], typed] : typedFields
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
          if (ev.event === 'intake') {
            const d = (typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data) as IntakeState | null
            if (d && typeof d === 'object' && d.declared) {
              setIntake({ declared: d.declared, proposal: d.proposal ?? null })
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
        if (verticalDirty) setVerticalDirty(false)
        if (localeDirty) setLocaleDirty(false)
      } catch (e) {
        setMsgs((m) => [
          ...m.slice(0, -1),
          { role: 'factory', text: e instanceof Error ? e.message : 'chat failed', card: 'error' },
        ])
      }
      return { runnerStarted }
    },
    [sessionId, vertical, verticalDirty, locale, localeDirty],
  )

  const send = useCallback(
    async (text: string) => {
      const message = text.trim()
      if (!message || busy || accessPaused) return
      setInput('')
      setBusy(true)
      try {
        await sendCore(message)
      } finally {
        setBusy(false)
      }
    },
    [accessPaused, busy, sendCore],
  )

  const sendTyped = useCallback(
    async (typed: TypedFloorAction) => {
      if (busy || accessPaused) return
      setBusy(true)
      try {
        await sendCore('', typed)
      } finally {
        setBusy(false)
      }
    },
    [accessPaused, busy, sendCore],
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
      setNewSessionError(e instanceof Error ? e.message : 'Could not start a new session')
    } finally {
      setNewSessionBusy(false)
    }
  }

  async function takeCopy() {
    // The typed take_copy records the choice; the copy is the as-is export,
    // its MANIFEST naming FAILED(gate, check, finding), never certified.
    await sendTyped({ action: 'take_copy' })
    setDownloading(true)
    setDownloadError(null)
    try {
      await downloadProductPackage(sessionId, { asIs: true })
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'export failed')
    } finally {
      setDownloading(false)
    }
  }

  async function download() {
    const aff = exportAffordance(liveCoderBuild)
    if (aff.disabled) return
    setDownloading(true)
    setDownloadError(null)
    try {
      if (aff.asIs) {
        // Gate-failed build, owner asked anyway: the server ships it loudly
        // labeled as-is. No waiting on a build that already ended.
        await downloadProductPackage(sessionId, { asIs: true })
        return
      }
      const status =
        liveCoderBuild?.state === 'succeeded'
          ? liveCoderBuild
          : await awaitBuild(sessionId, (s) =>
              setCoderBuild((prev) => preferHonestBuild(s, prev)),
            )
      if (status.state === 'failed' || status.state === 'stalled') {
        setDownloadError(
          `The build did not pass its gates, so it will not be shipped: ${status.detail ?? 'unknown reason'}`,
        )
        return
      }
      await downloadProductPackage(sessionId)
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : 'export failed')
    } finally {
      setDownloading(false)
    }
  }

  const [rerunBusy, setRerunBusy] = useState(false)
  const [rerunError, setRerunError] = useState<string | null>(null)
  async function rerunWriter() {
    // The failed run's honest next action is a retry: POST /product/generate
    // resumes the platform's own workspace at its stopped phase with a fresh
    // rework budget. A fresh workspace is only ever the typed Start over.
    setRerunBusy(true)
    setRerunError(null)
    try {
      await product.generate(sessionId)
      const s = await product.buildStatus(sessionId)
      setCoderBuild((prev) => preferHonestBuild(s.build, prev))
    } catch (e) {
      setRerunError(e instanceof Error ? e.message : 'rerun failed')
    } finally {
      setRerunBusy(false)
    }
  }

  const coderBuilding =
    coderActive &&
    liveCoderBuild?.state !== 'succeeded' &&
    liveCoderBuild?.state !== 'failed' &&
    liveCoderBuild?.state !== 'stalled'
  const coderSucceeded = liveCoderBuild?.state === 'succeeded'
  const coderFailed = liveCoderBuild?.state === 'failed'
  const coderPilotReady = isPilotZipReady(liveCoderBuild)
  const coderTerminal =
    liveCoderBuild?.state === 'failed' || liveCoderBuild?.state === 'stalled'
  const exportBtn = exportAffordance(liveCoderBuild)
  const latestGenerationIdx = (() => {
    for (let i = msgs.length - 1; i >= 0; i -= 1) {
      if (msgs[i].card === 'generation') return i
    }
    return -1
  })()
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

  return (
    <div className="floor">
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
        {notice && (
          <div className="notice-box already-signed-in" role="status">
            {notice}
          </div>
        )}
        <p className="dim">Describe the platform. Approve the feature list. The coding agent takes over and writes it.</p>
        <p className="dim notice-not-yet">
          What this is not yet: the factory generates a working prototype — real code,
          tests and deploy files — not a finished production system. Third-party
          integrations in generated products are stubs until you connect your own
          credentials, deployment is a step you run rather than something that happens
          for you, and free-trial accounts have server-enforced caps on generations,
          daily chat and exports. Answers are grounding-checked: when a claim can't be
          verified it is withheld, not invented.
        </p>
        <FactoryCodeCliStatus message={cliHonesty} testId="floor-factory-cli-status" />
      </header>
      <div className="chat-scroll">
        {msgs.map((m, i) => {
          // Terminal runs own the floor via the STOPPED/stalled panel.
          // Hide teal "taken over" generation chrome so it cannot sit above it.
          if (m.card === 'generation' && coderTerminal) return null
          return (
            <div key={i} className={'bubble-row ' + m.role}>
              <div className={'bubble ' + m.role + ' ' + (m.card ?? '')}>
                {m.text || (m.role === 'factory' && busy && i === msgs.length - 1 ? <span className="typing">…</span> : null)}
                {m.card === 'blueprint' && m.blueprint && (
                  <BlueprintCard
                    blueprint={m.blueprint}
                    busy={busy || coderActive}
                    accessPaused={accessPaused}
                    onApprove={(excludedIds, delivery) =>
                void approveWithSelection(excludedIds, delivery)
              }
                    onRefine={(typed) => void sendTyped(typed)}
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
                    {i === latestGenerationIdx && !coderBuilding && (
                      <button type="button" onClick={goPlatforms}>
                        Open Your Platforms
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>
      {coderActive && (
        <div
          className={
            'coder-takeover' +
            (liveCoderBuild?.stale ||
            liveCoderBuild?.state === 'stalled' ||
            liveCoderBuild?.state === 'failed'
              ? ' stale'
              : '')
          }
          role="status"
          data-testid="floor-coder-takeover"
        >
          <h3>
            {liveCoderBuild?.state === 'succeeded' ? (
              <>
                <span data-testid="floor-product-title">{productTitle}</span>{' '}
                <span className="mono" data-testid="floor-acceptance-score">
                  {formatAcceptanceScore(liveCoderBuild)}
                </span>
                {' — '}
                {coderTakeoverHeading(liveCoderBuild)}
              </>
            ) : liveCoderBuild?.state === 'stalled' ? (
              'Coding agent stalled'
            ) : liveCoderBuild?.state === 'failed' ? (
              'Coding agent stopped'
            ) : (
              'Coding agent has taken over'
            )}
          </h3>
          <LevelGradeStrip build={liveCoderBuild} testIdPrefix="floor" />
          <KernelStrip build={liveCoderBuild} />
          {liveCoderBuild?.failure &&
            (liveCoderBuild.state === 'failed' || liveCoderBuild.state === 'stalled' ? (
              <p className="coder-failure-line" data-testid="floor-failure-line" role="alert">
                <strong>
                  {liveCoderBuild.failure.location || liveCoderBuild.failure.phase || 'Build'}{' '}
                  failed
                </strong>
                {liveCoderBuild.failure.reason ? ` — ${liveCoderBuild.failure.reason}` : ''}
                {liveCoderBuild.failure.detail ? `: ${liveCoderBuild.failure.detail}` : ''}
              </p>
            ) : (
              // The run is still moving: a mid-fail is a rework round, not a
              // stop. Orange status, no red alert -- red means it ended.
              <p className="coder-rework-line" data-testid="floor-rework-line" role="status">
                <strong>
                  {liveCoderBuild.failure.location || liveCoderBuild.failure.phase || 'A phase'}{' '}
                  sent it back to the coder
                </strong>
                {liveCoderBuild.failure.reason ? ` — ${liveCoderBuild.failure.reason}` : ''}
                {liveCoderBuild.failure.detail ? `: ${liveCoderBuild.failure.detail}` : ''}
                {' — rework in progress.'}
              </p>
            ))}
          {(liveCoderBuild?.decisions?.length ?? 0) > 0 && (
            <ol className="coder-decisions" data-testid="floor-decisions">
              {liveCoderBuild!.decisions!.map((d, i) => (
                <li key={i} data-testid="floor-decision" data-class={d.class}>
                  <strong>{d.class}</strong> {d.gate} round {d.round_gate}/{d.gate_budget} (build{' '}
                  {d.round_build}/{d.build_ceiling}) — {d.check}
                  {d.finding ? `: ${d.finding}` : ''}
                </li>
              ))}
            </ol>
          )}
          {(liveCoderBuild?.suggested_checks?.length ?? 0) > 0 && (
            <SuggestedChecks checks={liveCoderBuild!.suggested_checks!} />
          )}
          {!liveCoderBuild?.failure && liveCoderBuild?.recovered_failure && (
            <p className="coder-recovered-line" data-testid="floor-recovered-line">
              Recovered in rework —{' '}
              {liveCoderBuild.recovered_failure.location ||
                liveCoderBuild.recovered_failure.phase ||
                'a phase'}{' '}
              failed once ({liveCoderBuild.recovered_failure.reason || 'unknown'}) and the
              agent fixed it.
            </p>
          )}
          {liveCoderBuild && liveCoderBuild.state === 'building' ? (
            <>
              <CoderProgress build={liveCoderBuild} nowMs={nowMs} />
              <div className="card-actions coder-monitor-actions">
                <button
                  type="button"
                  className="ghost"
                  data-testid="floor-coder-pause"
                  disabled={controlBusy || liveCoderBuild.coder_control === 'pause'}
                  onClick={() => {
                    setControlBusy(true)
                    void product
                      .coderControl(sessionId, 'pause')
                      .finally(() => setControlBusy(false))
                  }}
                >
                  {liveCoderBuild.coder_control === 'pause' ? 'Paused' : 'Pause'}
                </button>
                {liveCoderBuild.coder_control === 'pause' && (
                  <button
                    type="button"
                    className="ghost"
                    data-testid="floor-coder-resume"
                    disabled={controlBusy}
                    onClick={() => {
                      setControlBusy(true)
                      void product
                        .coderControl(sessionId, 'resume')
                        .finally(() => setControlBusy(false))
                    }}
                  >
                    Resume
                  </button>
                )}
                <button
                  type="button"
                  data-testid="floor-coder-stop"
                  disabled={controlBusy || liveCoderBuild.coder_control === 'stop'}
                  onClick={() => {
                    setControlBusy(true)
                    void product
                      .coderControl(sessionId, 'stop')
                      .finally(() => setControlBusy(false))
                  }}
                >
                  Stop
                </button>
              </div>
            </>
          ) : (
            <p>
              {coderTakeoverNote(liveCoderBuild) ??
                'The feature list is approved. The coding agent is starting WORKERS now.'}
            </p>
          )}
          {coderSucceeded && (
            <div className="card-actions">
              {!coderPilotReady && liveCoderBuild?.build_level?.stop_gate !== 'CODE' && (
                <button
                  type="button"
                  data-testid="continue-to-pilot"
                  onClick={() => void sendTyped({ action: 'run_pilot' })}
                  disabled={busy || accessPaused}
                >
                  Continue to pilot
                </button>
              )}
              <button
                type="button"
                className={exportBtn.ghost ? 'ghost' : undefined}
                onClick={() => void download()}
                disabled={downloading || exportBtn.disabled}
                title={exportBtn.title}
              >
                {downloading ? 'Packing…' : exportBtn.label}
              </button>
              {liveCoderBuild?.repo_url &&
                liveCoderBuild.repo_url.startsWith('http') && (
                  <a
                    className="ghost"
                    href={liveCoderBuild.repo_url}
                    target="_blank"
                    rel="noreferrer"
                    data-testid="floor-repo-link"
                  >
                    Open repository
                  </a>
                )}
            </div>
          )}
          {liveCoderBuild?.state === 'stalled' && (
            <div className="card-actions">
              <span className="status-pill status-pill-failed" data-testid="floor-stalled-pill">
                Build stalled
              </span>
              <button
                type="button"
                data-testid="floor-rerun-writer"
                disabled={busy || rerunBusy || accessPaused}
                onClick={() => void rerunWriter()}
              >
                Re-run the writer
              </button>
              <button
                type="button"
                className="ghost"
                disabled={exportAffordance(liveCoderBuild).disabled || downloading}
                title={exportAffordance(liveCoderBuild).title}
                onClick={() => void download()}
              >
                {exportAffordance(liveCoderBuild).label}
              </button>
              <button type="button" className="ghost" onClick={goPlatforms}>
                Open Your Platforms
              </button>
            </div>
          )}
          {coderFailed && liveCoderBuild?.failed_label && (
            <p className="muted" data-testid="floor-failed-label">
              {liveCoderBuild.failed_label}
            </p>
          )}
          {coderFailed && liveCoderBuild?.next_continue && (
            <p className="muted" data-testid="floor-next-continue">
              {liveCoderBuild.next_continue}
            </p>
          )}
          {coderFailed && liveCoderBuild?.failure_narrative?.text && (
            <p className="muted" data-testid="floor-failure-narrative">
              {liveCoderBuild.failure_narrative.text}
            </p>
          )}
          {coderFailed && (
            // A FAILED build offers exactly three TYPED choices. Chat text
            // never triggers any of them.
            <div className="card-actions" data-testid="floor-failure-choices">
              <span className="status-pill status-pill-failed" data-testid="floor-failed-pill">
                {liveCoderBuild?.failure
                  ? `${liveCoderBuild.failure.location || liveCoderBuild.failure.phase || 'Build'} failed`
                  : 'Pilot suite failed'}
              </span>
              <button
                type="button"
                data-testid="floor-take-copy"
                title="Download the platform as it stands. The export is labelled FAILED and names the failing gate, check and finding; it is not certified."
                disabled={busy || downloading || accessPaused}
                onClick={() => void takeCopy()}
              >
                {downloading ? 'Packing…' : 'Take a copy'}
              </button>
              <button
                type="button"
                className="ghost"
                data-testid="floor-continue-with-intake"
                title="Reopen the intake (country, currency, build level, vertical). Confirm resumes this platform's own branch with a fresh rework budget."
                disabled={busy || accessPaused}
                onClick={() => void sendTyped({ action: 'continue_with_intake' })}
              >
                Continue with new answers
              </button>
              <button
                type="button"
                className="ghost"
                data-testid="floor-start-over"
                title="Build this platform again from the approved feature list on a fresh workspace. The current head is kept as an archive tag; nothing is deleted."
                disabled={busy || accessPaused}
                onClick={() => void sendTyped({ action: 'start_over' })}
              >
                Start over
              </button>
            </div>
          )}
          {downloadError && <div className="error-box">{downloadError}</div>}
          {rerunError && <div className="error-box">{rerunError}</div>}
          {newSessionError && <div className="error-box">{newSessionError}</div>}
        </div>
      )}
      <IntakeLine
        intake={intake}
        onTyped={(typed) => void sendTyped(typed)}
        disabled={busy || coderBuilding || accessPaused}
        editControls={
          <>
            <VerticalPicker
              sessionId={sessionId}
              value={vertical}
              onChange={pickVertical}
              onLoaded={syncVertical}
              onLocaleLoaded={syncLocale}
              disabled={busy || coderBuilding || accessPaused}
            />
            <LocalePicker
              country={locale.country}
              currency={locale.currency}
              onChange={pickLocale}
              disabled={busy || coderBuilding || accessPaused}
            />
          </>
        }
      />
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
      >
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
  )
}
