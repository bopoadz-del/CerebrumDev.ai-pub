import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  auth,
  awaitBuild,
  billing,
  downloadProductPackage,
  getEmail,
  getRememberedPassword,
  product,
  rememberLogin,
  subscriptionDisplay,
  watchBuildStatus,
  type AccountInfo,
  type BillingStatus,
  type BuildStatus,
  type ProductDesign,
} from './api/factory'
import {
  exportAffordance,
  formatAcceptanceScore,
  formatFinishedAuthorship,
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  hasSourcedLevel,
  honestLevel,
  isAcceptancePendingPrototype,
  isPilotZipReady,
  nRequiredFromProductInputs,
  platformsLeadCopy,
  preferHonestBuild,
  shouldDemoteFounding,
  withClientStall,
  withResolvedNRequired,
} from './buildProgress'
import { FactoryCodeCliStatus, useFactoryCodeCliHonesty } from './factoryReadinessView'
import { LevelGradeStrip } from './levelGradeView'
import { LoadingSkeleton } from './LoadingSkeleton'
import { platformCardTitle } from './productDisplay'

export { humanizeProductId, platformCardTitle } from './productDisplay'

/* -------------------------------- Platforms -------------------------------- */

export function Platforms({
  sessionId,
  goFloor,
}: {
  sessionId: string
  goFloor?: () => void
}) {
  const [design, setDesign] = useState<ProductDesign | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [downloading, setDownloading] = useState(false)
  const [build, setBuild] = useState<BuildStatus | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const cliHonesty = useFactoryCodeCliHonesty()

  const refresh = useCallback(
    (opts?: { initial?: boolean }) => {
      setError(null)
      if (opts?.initial) setLoading(true)
      else setRefreshing(true)
      return product
        .get(sessionId)
        .then(async (next) => {
          setDesign(next)
          // GET /product re-reads the ledger onto generation.build (live
          // n_required re-eval after #392). Seed the card from that so a
          // sticky last_error / pre-#392 RUN_FAILED snapshot cannot paint
          // "need ≥5" while package/Floor already SUCCESS. Refresh still
          // fetches build-status; a cached THIN_AUTHORSHIP failed tick
          // must not clobber a live SUCCESS.
          if (!next.generation) {
            setBuild(null)
            return
          }
          if (next.generation.build) {
            setBuild((prev) => preferHonestBuild(next.generation!.build as BuildStatus, prev))
          }
          if (opts?.initial) return
          const { build: nextBuild } = await product.buildStatus(sessionId)
          setBuild((prev) => preferHonestBuild(nextBuild, prev))
        })
        .catch((e) => setError(e instanceof Error ? e.message : 'failed to load'))
        .finally(() => {
          setLoading(false)
          setRefreshing(false)
        })
    },
    [sessionId],
  )

  useEffect(() => {
    void refresh({ initial: true })
  }, [refresh])

  // Keep watching across succeed → building (pilot reopen). Depend on a
  // primitive so Refresh (new generation object identity) does not tear down
  // the in-flight poll.
  const watchingBuild = Boolean(design?.generation)
  useEffect(() => {
    if (!watchingBuild) return
    const ac = new AbortController()
    void watchBuildStatus(
      sessionId,
      (s) => {
        if (!ac.signal.aborted) {
          setBuild((prev) => preferHonestBuild(s, prev))
        }
      },
      { signal: ac.signal },
    ).catch((e) => {
      if (!ac.signal.aborted) {
        setError(e instanceof Error ? e.message : 'build status failed')
      }
    })
    return () => ac.abort()
  }, [watchingBuild, sessionId])

  const bp = design?.blueprint as
    | { product_name?: string; name?: string; vertical?: string; capabilities?: unknown[] }
    | null
    | undefined
  const gen = design?.generation
  const nRequired =
    nRequiredFromProductInputs(bp) ?? nRequiredFromProductInputs(design?.plan)
  const liveBuild = withClientStall(
    withResolvedNRequired(build ?? gen?.build ?? null, nRequired),
    nowMs,
  )
  useEffect(() => {
    if (liveBuild?.state !== 'building') return
    const id = window.setInterval(() => setNowMs(Date.now()), 5000)
    return () => window.clearInterval(id)
  }, [liveBuild?.state])
  const authorship = liveBuild?.authorship
  const stalled = liveBuild?.state === 'stalled'
  const failed = liveBuild?.state === 'failed'
  const pilotReady = isPilotZipReady(liveBuild)
  const level = honestLevel(liveBuild)
  const codeOnlySuccess = liveBuild?.state === 'succeeded' && !pilotReady
  const buildNote = (() => {
    if (!liveBuild) return null
    if (liveBuild.state === 'failed') {
      const detail = liveBuild.detail ?? 'build did not pass its gates'
      return `Build failed — ${detail}`
    }
    if (liveBuild.state === 'stalled') {
      return `Build stalled — ${liveBuild.detail ?? 'no recent activity'}`
    }
    if (
      liveBuild.state === 'succeeded' &&
      hasSourcedLevel(liveBuild) &&
      level === 'FOUNDING_CUSTOMER_READY'
    ) {
      return 'Founding-customer-ready — PRODUCT and STORE gates passed.'
    }
    if (liveBuild.state === 'succeeded' && hasSourcedLevel(liveBuild) && level === 'STORE_GREEN') {
      return 'Store-green — not founding-customer-ready.'
    }
    if (liveBuild.state === 'succeeded' && !pilotReady) {
      if (isAcceptancePendingPrototype(liveBuild)) {
        const score = formatAcceptanceScore(liveBuild)
        return `Acceptance ${score} — not pilot-ready. Export stays closed until scripts/acceptance.py is k/k.`
      }
      return liveBuild.auto_pilot
        ? 'Code-cycle prototype — not pilot-ready. The pilot cycle should open automatically.'
        : 'Code-cycle prototype — not pilot-ready. Continue to pilot on the Factory Floor.'
    }
    if (liveBuild.state !== 'building') return null
    const headline = formatPhaseHeadline(liveBuild)
    const counts = formatPhaseCounts(liveBuild)
    const last = liveBuild.last_event || liveBuild.activity
    const heartbeat = formatHeartbeat(liveBuild, nowMs)
    const bits = [`Coding agent at work — ${headline}`]
    if (counts) bits.push(counts)
    if (last) bits.push(`last: ${last}`)
    if (heartbeat) bits.push(heartbeat)
    return bits.join(' · ')
  })()
  const exportBtn = exportAffordance(
    liveBuild ?? (gen ? { state: 'building' } : null),
  )
  const downloadLabel = downloading ? 'Packing…' : exportBtn.label
  const leadCopy = platformsLeadCopy(liveBuild, Boolean(gen))

  async function download() {
    if (exportBtn.disabled) return
    setDownloading(true)
    setError(null)
    try {
      if (exportBtn.asIs) {
        // Gate-failed build, owner asked anyway: the server ships it loudly
        // labeled as-is (EXPORTED-AS-IS.md, no certification manifest).
        await downloadProductPackage(sessionId, { asIs: true })
        return
      }
      const status =
        liveBuild?.state === 'succeeded'
          ? liveBuild
          : await awaitBuild(sessionId, (s) =>
              setBuild((prev) => preferHonestBuild(s, prev)),
            )
      if (!status || status.state === 'failed' || status.state === 'stalled') {
        setError(
          `The build did not pass its gates, so it will not be shipped: ${status?.detail ?? 'unknown reason'}`,
        )
        return
      }
      await downloadProductPackage(sessionId)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'export failed')
    } finally {
      setDownloading(false)
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <h2>Your Platforms</h2>
        <p className="dim" data-testid="platforms-lead">
          {leadCopy}
        </p>
      </header>
      <FactoryCodeCliStatus message={cliHonesty} testId="platforms-factory-cli-status" />
      {error && <div className="error-box">{error}</div>}
      {buildNote && (
        <div
          className={'panel dim' + (stalled || failed ? ' error-box' : '')}
          role="status"
          data-testid={failed ? 'platforms-failed-badge' : undefined}
        >
          {buildNote}
          {failed && liveBuild?.findings && liveBuild.findings.length > 0 && (
            <ul className="findings-list">
              {liveBuild.findings.slice(0, 5).map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {loading ? (
        <LoadingSkeleton label="Loading platforms" />
      ) : !gen ? (
        <div className="panel empty-state" data-testid="platforms-empty-state">
          <h3>No platform built yet</h3>
          <p className="dim">Go to the Factory Floor and describe what you need. Your build lands here.</p>
          {design?.blueprint && !design.blueprint_approved && (
            <p className="dim">A blueprint is drafted on the floor — approve it to build.</p>
          )}
          {bp && (
            <p className="dim" data-testid="platforms-draft-hint">
              Draft: <strong>{bp.product_name}</strong> ({bp.vertical})
            </p>
          )}
        </div>
      ) : (
        <div className="panel">
          <h3 data-testid="platforms-product-title">
            {platformCardTitle(bp?.product_name, gen.product_id)}{' '}
            <span className="mono" data-testid="platforms-acceptance-score">
              {formatAcceptanceScore(liveBuild)}
            </span>
          </h3>
          {stalled && (
            <span className="status-pill status-pill-failed" data-testid="platforms-stalled-pill">
              Build stalled
            </span>
          )}
          {failed && (
            <span className="status-pill status-pill-failed" data-testid="platforms-failed-pill">
              Pilot suite failed
            </span>
          )}
          <LevelGradeStrip build={liveBuild} testIdPrefix="platforms" />
          <dl className="kv">
            <dt>Blueprint</dt>
            <dd>{bp?.product_name ?? '—'}</dd>
            <dt>Engine</dt>
            <dd>
              {typeof gen.engine === 'string'
                ? gen.engine
                : liveBuild?.state === 'succeeded' || failed
                  ? 'runner'
                  : '—'}
            </dd>
            <dt>Inputs hash</dt>
            <dd className="mono">{gen.inputs_hash ?? '—'}</dd>
            <dt>Output</dt>
            <dd className="mono">{gen.output_dir ?? '—'}</dd>
          </dl>
          {liveBuild?.state === 'succeeded' && authorship && (
            <>
              <p className="bp-summary">
                {formatFinishedAuthorship(authorship, {
                  pilotReady,
                  demoteFounding: shouldDemoteFounding(liveBuild),
                }) ??
                  (pilotReady
                    ? 'Coding agent finished. Download it from Your Platforms.'
                    : 'Code-cycle prototype. Not yet pilot-ready.')}
              </p>
              {authorship.coder_failures &&
                Object.keys(authorship.coder_failures).length > 0 && (
                  <p className="dim">
                    Coder skip: {Object.values(authorship.coder_failures)[0]}
                  </p>
                )}
            </>
          )}
          {codeOnlySuccess && goFloor && (
            <button
              type="button"
              data-testid="continue-to-pilot"
              onClick={goFloor}
            >
              Continue to pilot on Factory Floor
            </button>
          )}
          <button
            type="button"
            className={exportBtn.ghost || stalled || failed ? 'ghost' : undefined}
            onClick={download}
            disabled={downloading || exportBtn.disabled || refreshing}
            title={exportBtn.title}
            aria-disabled={downloading || exportBtn.disabled || refreshing || undefined}
          >
            {downloadLabel}
          </button>
          <button
            className="ghost"
            type="button"
            onClick={() => void refresh()}
            disabled={refreshing}
            aria-busy={refreshing || undefined}
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      )}
    </div>
  )
}

/** Honesty footer when Stripe is unconfigured. Mention Upgrade only if that button is shown. */
export function paymentsNotConnectedNote(opts: {
  showUpgrade: boolean
  enforcement?: boolean
}): string {
  const parts = [
    'Payments are not connected on this deployment yet — the factory owner links the Stripe account.',
  ]
  if (opts.showUpgrade) {
    parts.push('Upgrade still says so instead of opening a blank checkout.')
  }
  parts.push(
    opts.enforcement
      ? 'Billing enforcement is on for this deployment, so sessions and factory runs stop once a trial ends — until the owner connects Stripe or updates your subscription.'
      : 'Your current access is unaffected.',
  )
  return parts.join(' ')
}

export function Subscription() {
  const [status, setStatus] = useState<BillingStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const loadStatus = useCallback(() => {
    setError(null)
    setLoading(true)
    billing
      .status()
      .then(setStatus)
      .catch((e) => {
        setStatus(null)
        setError(e instanceof Error ? e.message : 'failed to load')
      })
      .finally(() => setLoading(false))
  }, [])

  useEffect(loadStatus, [loadStatus])

  async function upgrade() {
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const res = await billing.checkout()
      const url = res.url ?? res.checkout_url
      if (url) {
        window.location.href = url
        return
      }
      setNote('Checkout returned no redirect — the factory team has been notified.')
    } catch {
      setNote(
        'Payments are not connected on this deployment yet — the factory owner links the Stripe account. ' +
          'This is the only piece still pending; your current access is unaffected.',
      )
    } finally {
      setBusy(false)
    }
  }

  async function manage() {
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const res = await billing.portal()
      const url = res.url ?? res.portal_url
      if (url) {
        window.location.href = url
        return
      }
      setNote('Billing portal returned no redirect.')
    } catch {
      setNote('The billing portal opens once payments are connected on this deployment.')
    } finally {
      setBusy(false)
    }
  }

  const view = status ? subscriptionDisplay(status) : null
  const subStatus = view?.statusLabel ?? ''

  return (
    <div className="page">
      <header className="page-head">
        <h2>Subscription</h2>
        <p className="dim">Your plan decides how deep the factory builds for you.</p>
      </header>
      {error && (
        <div className="error-box">
          {error}
          <div>
            <button className="ghost" type="button" onClick={loadStatus}>
              Retry
            </button>
          </div>
        </div>
      )}
      {loading ? (
        <LoadingSkeleton label="Loading subscription" lines={3} />
      ) : (
        <div className="panel">
          {status && view ? (
            <>
              <dl className="kv">
                <dt>Plan</dt>
                <dd className="capitalize">{view.planLabel}</dd>
                <dt>Status</dt>
                <dd className="capitalize">{view.statusLabel}</dd>
                {view.showTrialDays && (
                  <>
                    <dt>Trial days left</dt>
                    <dd>{view.trialDaysLeft}</dd>
                  </>
                )}
                <dt>Factory access</dt>
                <dd>{view.accessLabel}</dd>
              </dl>
              <div className="plan-cards">
                {view.currentPlan === 'trial' && (
                  <div className="plan-card highlight" data-plan="trial" aria-current="true">
                    <p className="plan-current">Current</p>
                    <h4>Trial</h4>
                    <p className="dim">Full factory access while you evaluate. No card required.</p>
                  </div>
                )}
                <div
                  className={`plan-card${view.currentPlan === 'factory' ? ' highlight' : ''}`}
                  data-plan="factory"
                  aria-current={view.currentPlan === 'factory' ? 'true' : undefined}
                >
                  {view.currentPlan === 'factory' && <p className="plan-current">Current</p>}
                  <h4>Factory</h4>
                  <p className="dim">
                    Deeper builds, more sessions, priority generation.
                    {view.currentPlan !== 'factory' ? ' Upgrade when you are ready.' : ''}
                  </p>
                  {view.currentPlan !== 'factory' && (
                    <button onClick={upgrade} disabled={busy}>
                      {busy ? 'Working…' : 'Upgrade'}
                    </button>
                  )}
                </div>
              </div>
              {subStatus === 'active' && (
                <button className="ghost" onClick={manage} disabled={busy}>
                  Manage billing
                </button>
              )}
              {status.checkout_available === false && (
                <p className="dim note">
                  {paymentsNotConnectedNote({
                    showUpgrade: view.currentPlan !== 'factory',
                    enforcement: Boolean(status.enforcement),
                  })}
                </p>
              )}
            </>
          ) : error ? (
            <p className="dim">Could not load subscription status.</p>
          ) : (
            <p className="dim">Could not load subscription status.</p>
          )}
          {note && <p className="dim note">{note}</p>}
        </div>
      )}
    </div>
  )
}

function verifiedLabel(me: AccountInfo | null): string {
  if (typeof me?.email_verified !== 'boolean') return '—'
  return me.email_verified ? 'Yes' : 'No'
}

// Owner-only ops: switch the coding model when it stops responding, and
// reboot the service. Gated by the master key the owner enters here; the key
// is kept only in this browser (localStorage), never sent anywhere but the
// admin endpoints. Hidden entirely until a key is entered.
function AdminOps() {
  const [key, setKey] = useState<string>(() => {
    try {
      return localStorage.getItem('cerebrum_admin_key') || ''
    } catch {
      return ''
    }
  })
  const [open, setOpen] = useState(false)
  const [model, setModel] = useState('')
  const [provider, setProvider] = useState('')
  const [effective, setEffective] = useState<{ model?: string; provider?: string }>({})
  const [providers, setProviders] = useState<string[]>(['deepseek', 'openrouter', 'anthropic', 'kimi'])
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [hygiene, setHygiene] = useState<{
    repo_size_kb?: number | null
    last_run?: { at?: string; archived?: number; errors?: number; live_build_branches?: number } | null
    detail?: string
  } | null>(null)

  function saveKey(k: string) {
    setKey(k)
    try {
      if (k) localStorage.setItem('cerebrum_admin_key', k)
      else localStorage.removeItem('cerebrum_admin_key')
    } catch {
      /* private window — fine */
    }
  }

  async function adminFetch(path: string, init?: RequestInit) {
    const res = await fetch(path, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${key}`,
        ...(init?.headers || {}),
      },
    })
    if (res.status === 401 || res.status === 404) throw new Error('Wrong or missing admin key.')
    if (!res.ok) throw new Error(`${res.status}: ${(await res.text()).slice(0, 200)}`)
    return res.json()
  }

  async function load() {
    setErr(null)
    setMsg(null)
    setBusy(true)
    try {
      const d = await adminFetch('/v1/admin/coder-model')
      setEffective(d.effective || {})
      if (Array.isArray(d.provider_options)) setProviders(d.provider_options)
      setOpen(true)
      try {
        setHygiene(await adminFetch('/v1/admin/builds-hygiene'))
      } catch {
        setHygiene(null)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'failed to load')
    } finally {
      setBusy(false)
    }
  }

  async function apply() {
    setErr(null)
    setMsg(null)
    setBusy(true)
    try {
      const body: Record<string, string> = {}
      if (model.trim()) body.model = model.trim()
      if (provider.trim()) body.provider = provider.trim()
      if (!Object.keys(body).length) {
        setErr('Enter a model id (or pick a provider) first.')
        return
      }
      const d = await adminFetch('/v1/admin/coder-model', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setEffective(d.effective || {})
      setModel('')
      setProvider('')
      setMsg(d.message || 'Applied. Re-run the writer to pick it up.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'failed to apply')
    } finally {
      setBusy(false)
    }
  }

  async function reboot() {
    if (!window.confirm('Reboot the Factory now? It will be back in ~30–60s.')) return
    setErr(null)
    setMsg(null)
    setBusy(true)
    try {
      const d = await adminFetch('/v1/admin/reboot', { method: 'POST' })
      setMsg(d.message || 'Rebooting — back in a moment.')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'failed to reboot')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="panel admin-ops">
      <h3>Factory controls</h3>
      <p className="dim">Owner only. Enter your admin key to switch the coding model or reboot.</p>
      <div className="admin-key-row">
        <input
          type="password"
          placeholder="admin key"
          autoComplete="off"
          value={key}
          onChange={(e) => saveKey(e.target.value)}
        />
        <button type="button" disabled={!key || busy} onClick={() => void load()}>
          {busy ? 'Working…' : open ? 'Refresh' : 'Unlock'}
        </button>
      </div>
      {open && (
        <>
          <p className="dim">
            Coder now: <strong className="mono">{effective.model || '—'}</strong> on{' '}
            <strong className="mono">{effective.provider || '—'}</strong>
          </p>
          <div className="admin-model-row">
            <input
              type="text"
              placeholder="model id (e.g. deepseek-2.0)"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="">keep provider</option>
              {providers.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <button type="button" disabled={busy} onClick={() => void apply()}>
              Apply
            </button>
          </div>
          <button type="button" className="danger" disabled={busy} onClick={() => void reboot()}>
            Reboot Factory
          </button>
          {hygiene && (
            <p className="dim" data-testid="admin-builds-hygiene">
              cerebrum-builds:{' '}
              <strong className="mono">
                {hygiene.repo_size_kb != null
                  ? `${(hygiene.repo_size_kb / 1024).toFixed(1)} MB`
                  : hygiene.detail || 'size unknown'}
              </strong>
              {hygiene.last_run
                ? ` · last hygiene ${hygiene.last_run.at}: ${hygiene.last_run.archived ?? 0} archived, ` +
                  `${hygiene.last_run.live_build_branches ?? 0} live build branches` +
                  (hygiene.last_run.errors ? `, ${hygiene.last_run.errors} errors` : '')
                : ' · no hygiene run recorded yet'}
            </p>
          )}
        </>
      )}
      {msg && <p className="dim note">{msg}</p>}
      {err && <div className="error-box">{err}</div>}
    </div>
  )
}

export function Account({
  onLogout,
  initialMe,
}: {
  onLogout: () => void
  initialMe?: AccountInfo | null
}) {
  const [me, setMe] = useState<AccountInfo | null>(initialMe ?? null)
  const [verifyToken, setVerifyToken] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [resetBusy, setResetBusy] = useState(false)
  const [changeBusy, setChangeBusy] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const accountEmail = me?.email ? String(me.email) : getEmail()

  useEffect(() => {
    let cancelled = false
    auth
      .me()
      .then((m) => {
        if (!cancelled) setMe(m)
      })
      .catch(() => {
        if (!cancelled && !initialMe) setMe(null)
      })
    return () => {
      cancelled = true
    }
  }, [initialMe])

  async function verify(e: FormEvent) {
    e.preventDefault()
    setNote(null)
    setError(null)
    setBusy(true)
    try {
      await auth.verifyEmail(verifyToken)
      setNote('Email verified.')
      const m = await auth.me()
      setMe(m)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'verification failed')
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    setNote(null)
    setError(null)
    setBusy(true)
    try {
      const res = await auth.resendVerification()
      if (res.already_verified) {
        setNote('Email already verified.')
        const m = await auth.me()
        setMe(m)
        return
      }
      const v = res.verification
      if (v?.dev_verification_token) {
        setVerifyToken(v.dev_verification_token)
        setNote(
          v.note ??
            'SMTP is not configured on this deployment — the verification token is filled in below.',
        )
      } else if (v?.email_sent) {
        setNote('Verification email sent. Check your inbox.')
      } else {
        setNote(v?.note ?? 'Could not send a verification email.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'resend failed')
    } finally {
      setBusy(false)
    }
  }

  async function changePassword(e: FormEvent) {
    e.preventDefault()
    setNote(null)
    setError(null)
    if (newPassword !== confirmPassword) {
      setError('New password and confirmation do not match.')
      return
    }
    setChangeBusy(true)
    try {
      const res = await auth.changePassword(currentPassword, newPassword)
      setNote(res.message ?? 'Password updated. Other sessions were signed out.')
      if (accountEmail && getRememberedPassword()) {
        rememberLogin(accountEmail, newPassword)
      }
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'password change failed')
    } finally {
      setChangeBusy(false)
    }
  }

  async function sendPasswordReset() {
    const email = accountEmail?.trim()
    if (!email) {
      setNote(null)
      setError('No signed-in email to send a reset to.')
      return
    }
    setNote(null)
    setError(null)
    setResetBusy(true)
    try {
      const res = await auth.forgotPassword(email)
      if (res.dev_reset_token) {
        setNote(
          res.note ??
            'SMTP is not configured on this deployment — a reset token was issued. Finish on the reset-password page; this page does not change your password.',
        )
      } else {
        setNote(res.message ?? 'If the email is registered, a reset link follows.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'password reset failed')
    } finally {
      setResetBusy(false)
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <h2>Account</h2>
      </header>
      <div className="panel">
        <dl className="kv">
          <dt>Email</dt>
          <dd>{accountEmail ?? '—'}</dd>
          <dt>Email verified</dt>
          <dd>{verifiedLabel(me)}</dd>
          <dt>Account</dt>
          <dd className="mono">{me?.account_id ? String(me.account_id) : '—'}</dd>
        </dl>
        {me && me.email_verified === false && (
          <>
            <form className="verify-row" onSubmit={verify}>
              <input
                type="text"
                required
                placeholder="verification token"
                value={verifyToken}
                onChange={(e) => setVerifyToken(e.target.value)}
              />
              <button type="submit" disabled={busy}>
                {busy ? 'Working…' : 'Verify email'}
              </button>
            </form>
            <button type="button" className="ghost" disabled={busy} onClick={() => void resend()}>
              Resend verification email
            </button>
          </>
        )}
        <form className="change-password-form" onSubmit={changePassword}>
          <h3>Change password</h3>
          <input
            type="password"
            required
            autoComplete="current-password"
            placeholder="current password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            placeholder="new password (8+ characters)"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            placeholder="confirm new password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
          <button type="submit" disabled={changeBusy}>
            {changeBusy ? 'Working…' : 'Change password'}
          </button>
        </form>
        {note && <p className="dim note">{note}</p>}
        {error && <div className="error-box">{error}</div>}
        <div className="account-actions">
          <button
            type="button"
            disabled={resetBusy || !accountEmail}
            onClick={() => void sendPasswordReset()}
          >
            {resetBusy ? 'Working…' : 'Send password reset'}
          </button>
          <button className="danger" onClick={onLogout}>
            Sign out
          </button>
        </div>
      </div>
      <AdminOps />
    </div>
  )
}
