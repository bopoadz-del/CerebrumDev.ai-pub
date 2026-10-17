import type {
  BuildAuthorship,
  BuildStatus,
  FactoryCodeCliProbe,
} from './api/factory'
import {
  FACTORY_CODE_CLI_CREDENTIALS_MISSING,
  FACTORY_CODE_CLI_FAILED,
  FACTORY_CODE_CLI_NO_MODEL,
} from './api/factory'

/** Match backend build_jobs._STALE_AFTER_S — quiet model call vs frozen UI. */
export const CLIENT_STALE_AFTER_S = 180
/** Match backend build_jobs._STALL_AFTER_S — process likely gone. */
export const CLIENT_STALL_AFTER_S = 1800
/** Match backend llm_watchdog.attempt_wall_s production default (40 min).
 *  Live Render FACTORY_CODER_TIMEOUT_S=160 → old wall 160×3+30=510s.
 *  Prefer the server's model_call_deadline_s when present. */
export const CLIENT_MODEL_CALL_DEADLINE_S = 2400

export type LevelGradeName =
  | 'SCAFFOLD'
  | 'CODE_GREEN'
  | 'STORE_GREEN'
  | 'FOUNDING_CUSTOMER_READY'

const CLAIMED_LEVELS = new Set<string>([
  'SCAFFOLD',
  'CODE_GREEN',
  'STORE_GREEN',
  'FOUNDING_CUSTOMER_READY',
])

/**
 * Fail-closed glass grade. A false ``pilot_ready`` can never read as
 * Store-green or founding-customer-ready, even if ``level_grade.level``
 * overclaims. Missing grade falls back to cycle + pilot_ready.
 *
 * CLI billing/auth miss + template-majority authorship can stay
 * Store-green only when the launching-ready authorship floor holds
 * (need = min(5, max(1, n_required)); unknown n_required keeps need=5).
 * Below that floor the glass demotes Store-green and refuses gold Download.
 * A Store-green claim is never upgraded to founding.
 */
export const FULL_PILOT_MIN_AUTHORED_ACTIONS = 5
/** Store-green measured lines. Authorship floor is not this count. */
export const ACCEPTANCE_REQUIRED = 12
export const ACCEPTANCE_KK = { passed: 12, total: 12, ok: true as const }

export function acceptanceFrom(
  build: BuildStatus | null | undefined,
): NonNullable<BuildStatus['acceptance']> | null {
  const raw = build?.acceptance ?? build?.level_grade?.acceptance
  return raw && typeof raw === 'object' ? raw : null
}

export function acceptanceScore(build: BuildStatus | null | undefined): {
  passed: number
  total: number
} {
  const raw = acceptanceFrom(build)
  const passed = Number(raw?.passed)
  const total = Number(raw?.total)
  return {
    passed: Number.isFinite(passed) && passed >= 0 ? passed : 0,
    total: Number.isFinite(total) && total >= ACCEPTANCE_REQUIRED ? total : ACCEPTANCE_REQUIRED,
  }
}

export function isAcceptanceKk(build: BuildStatus | null | undefined): boolean {
  const raw = acceptanceFrom(build)
  if (!raw || raw.missing === true) return false
  const { passed, total } = acceptanceScore(build)
  return raw.ok === true && passed >= ACCEPTANCE_REQUIRED && passed === total
}

export function formatAcceptanceScore(build: BuildStatus | null | undefined): string {
  const { passed, total } = acceptanceScore(build)
  return `${passed}/${total}`
}

export function honestLevel(build: BuildStatus | null | undefined): LevelGradeName | null {
  if (!build) return null
  const claimed = String(build.level_grade?.level || '').toUpperCase()
  const ready = build.pilot_ready === true
  // Authoritative fail fields win over a founding/CLI receipt overclaim.
  // Do not force SCAFFOLD from coder_receipt alone when pilot_ready is true.
  if (
    productSuiteFailed(build) ||
    outcomeFailed(build) ||
    build.state === 'failed' ||
    build.state === 'stalled'
  ) {
    return 'SCAFFOLD'
  }
  if (isBelowFullPilotAuthorshipFloor(build) && claimsStoreGreenPilot(build)) {
    return build.state === 'succeeded' ? 'CODE_GREEN' : 'SCAFFOLD'
  }
  if (build.pilot_ready === true && !isAcceptanceKk(build)) {
    return build.state === 'succeeded' ? 'CODE_GREEN' : 'SCAFFOLD'
  }
  if (!ready) {
    if (claimed === 'STORE_GREEN' || claimed === 'FOUNDING_CUSTOMER_READY') {
      return build.state === 'succeeded' ? 'CODE_GREEN' : 'SCAFFOLD'
    }
    if (CLAIMED_LEVELS.has(claimed)) return claimed as LevelGradeName
    if (build.state === 'succeeded') return 'CODE_GREEN'
    return null
  }
  // Fail-closed: STORE_GREEN stays Store-green even if a boolean overclaims.
  if (claimed === 'STORE_GREEN') {
    return 'STORE_GREEN'
  }
  if (
    (build.level_grade?.founding_customer_ready === true ||
      claimed === 'FOUNDING_CUSTOMER_READY') &&
    !shouldDemoteFounding(build)
  ) {
    return 'FOUNDING_CUSTOMER_READY'
  }
  return 'STORE_GREEN'
}

export function hasSourcedLevel(build: BuildStatus | null | undefined): boolean {
  return Boolean(build?.level_grade?.level)
}

export function levelGradeLabel(level: LevelGradeName, sourced = true): string {
  if (!sourced) {
    if (level === 'CODE_GREEN') return 'Code-cycle prototype'
    if (level === 'STORE_GREEN' || level === 'FOUNDING_CUSTOMER_READY') return 'Pilot-ready'
    return 'Scaffold'
  }
  switch (level) {
    case 'SCAFFOLD':
      return 'Scaffold'
    case 'CODE_GREEN':
      return 'Code-green (prototype)'
    case 'STORE_GREEN':
      return 'Store-green'
    case 'FOUNDING_CUSTOMER_READY':
      return 'Founding-customer-ready'
  }
}

/** True only for a Store-green / founding zip — never CODE_GREEN or in-flight. */
export function isPilotZipReady(build: BuildStatus | null | undefined): boolean {
  if (!build || build.state !== 'succeeded' || build.pilot_ready !== true) return false
  if (productSuiteFailed(build) || outcomeFailed(build)) return false
  if (isBelowFullPilotAuthorshipFloor(build)) return false
  if (!isAcceptanceKk(build)) return false
  const level = honestLevel(build)
  return level === 'STORE_GREEN' || level === 'FOUNDING_CUSTOMER_READY'
}

export function threeGateEntries(
  build: BuildStatus | null | undefined,
): { name: string; verdict: string }[] | null {
  const gates = build?.level_grade?.three_gate
  if (!gates || typeof gates !== 'object') return null
  const names = ['CODE', 'PRODUCT', 'STORE'] as const
  if (!names.some((name) => name in gates)) return null
  return names.map((name) => ({
    name,
    verdict: String(gates[name] || 'UNKNOWN').replace(/_/g, ' '),
  }))
}

/** CLI keep-path handlers that Floor must not paint as "coder idle". */
export function cliKeptHandlerCount(
  authorship: BuildAuthorship | null | undefined,
): number {
  const kept = authorship?.kept_handler_ids
  return Array.isArray(kept) ? kept.filter(Boolean).length : 0
}

/** SUCCESS copy: never "22 of 28" — that reads as a hang.
 *  Code-cycle SUCCESS is a prototype, not "Finished / Download ready".
 *  Thin CLI-billing keep-path zips stay Pilot-ready, not Finished founding.
 *  A CLI keep-path with agent_written>0 (or kept_handler_ids) is not
 *  "coder idle / 0 artifacts".
 */
export function formatFinishedAuthorship(
  authorship: BuildAuthorship | null | undefined,
  opts?: { pilotReady?: boolean | null; demoteFounding?: boolean | null },
): string | null {
  if (!authorship) return null
  const kept = cliKeptHandlerCount(authorship)
  const writtenRaw = authorship.agent_written
  const written =
    typeof writtenRaw === 'number' && writtenRaw > 0
      ? writtenRaw
      : kept > 0
        ? kept
        : writtenRaw
  const templated = authorship.templated
  if (written === 0 && kept === 0) {
    return 'Coding agent wrote 0 artifacts — this platform is templated (coder idle or no LLM key).'
  }
  const counts =
    typeof written === 'number' && typeof templated === 'number'
      ? `${written} artifacts; ${templated} templated`
      : typeof written === 'number'
        ? `${written} artifacts`
        : null
  if (!counts) return null
  if (opts?.pilotReady === true) {
    if (opts.demoteFounding === true || isThinTemplatedAuthorship(authorship)) {
      return `Pilot-ready — ${counts}`
    }
    return `Finished — ${counts}`
  }
  return `Code-cycle prototype — ${counts}. Not yet pilot-ready`
}

/**
 * What each kernel is CALLED, as distinct from the id it is recorded under.
 *
 * The ids are the wire format: every build_ledger.jsonl ever written, every
 * stored session and every gate name carries 'CLONER' and 'WRITER' as
 * literal strings. Renaming those would rewrite history and break the
 * reading of builds that already happened, so the rename lives here, where
 * it is a label rather than an identity.
 */
const KERNEL_NAMES: Record<string, string> = {
  CLONER: 'RESEARCHER',
  WRITER: 'WORKERS',
}

export function kernelName(phase: string): string {
  return KERNEL_NAMES[phase] ?? phase
}

/** Named current phase plus 1-based index: "WORKERS 3/5", not a bare "2/5". */
export function formatPhaseHeadline(build: BuildStatus): string {
  const id = build.current_phase?.id
  const index = build.phase_index ?? (build.phases_done ?? 0) + 1
  const total = build.phase_total ?? build.phases_total ?? 5
  return id ? `${kernelName(id)} ${index}/${total}` : `${index}/${total}`
}

export function formatPhaseCounts(build: BuildStatus): string | null {
  const progress = build.phase_progress
  if (progress && progress.total > 0) {
    const unit = progress.stage || 'items'
    const base = `${progress.done}/${progress.total} ${unit}`
    // WRITER can restart a handler/route wave at 1/N after finishing 3/N —
    // label it so the drop does not read as a regression.
    return build.client_wave_reset ? `${base} (new pass)` : base
  }
  if (
    typeof build.activity_done === 'number' &&
    typeof build.activity_total === 'number' &&
    build.activity_total > 0
  ) {
    const unit = build.activity_stage || 'items'
    const base = `${build.activity_done}/${build.activity_total} ${unit}`
    return build.client_wave_reset ? `${base} (new pass)` : base
  }
  return null
}

/**
 * Stamp (or preserve) client observation metadata so a frozen server
 * ``last_event_age_s`` still advances on the wall clock between polls.
 * Re-stamp only when the ledger event identity changes.
 */
export function stampBuildObservation(
  next: BuildStatus,
  prev: BuildStatus | null | undefined,
  nowMs = Date.now(),
): BuildStatus {
  const sameEvent =
    Boolean(prev) &&
    prev!.state === next.state &&
    (prev!.last_event_at ?? null) === (next.last_event_at ?? null) &&
    (prev!.last_event ?? null) === (next.last_event ?? null) &&
    (prev!.activity ?? null) === (next.activity ?? null)

  const prevProgress = prev?.phase_progress
  const nextProgress = next.phase_progress
  const waveReset = Boolean(
    prevProgress &&
      nextProgress &&
      (prevProgress.stage || '') === (nextProgress.stage || '') &&
      prevProgress.total === nextProgress.total &&
      nextProgress.done < prevProgress.done,
  )

  if (sameEvent && typeof prev!.client_observed_at_ms === 'number') {
    return {
      ...next,
      client_observed_at_ms: prev!.client_observed_at_ms,
      client_base_age_s:
        typeof prev!.client_base_age_s === 'number'
          ? prev!.client_base_age_s
          : typeof prev!.last_event_age_s === 'number'
            ? prev!.last_event_age_s
            : next.last_event_age_s,
      client_wave_reset: prev!.client_wave_reset,
    }
  }
  return {
    ...next,
    client_observed_at_ms: nowMs,
    client_base_age_s: next.last_event_age_s,
    client_wave_reset: waveReset,
  }
}

/**
 * Relative age of the last ledger event. Prefer wall-clock from
 * ``last_event_at``; otherwise advance a frozen ``last_event_age_s`` from
 * the client observation stamp so "2 min ago" cannot stick for 5 wall minutes.
 */
export function eventAgeSeconds(build: BuildStatus, nowMs = Date.now()): number | null {
  const ages: number[] = []
  if (build.last_event_at) {
    const at = Date.parse(build.last_event_at)
    if (!Number.isNaN(at)) {
      ages.push(Math.max(0, (nowMs - at) / 1000))
    }
  }
  if (
    typeof build.client_base_age_s === 'number' &&
    typeof build.client_observed_at_ms === 'number'
  ) {
    ages.push(build.client_base_age_s + Math.max(0, (nowMs - build.client_observed_at_ms) / 1000))
  } else if (typeof build.last_event_age_s === 'number') {
    ages.push(build.last_event_age_s)
  }
  if (ages.length === 0) return null
  return Math.max(...ages)
}

export function formatHeartbeat(build: BuildStatus, nowMs = Date.now()): string | null {
  if (build.state !== 'building') return null
  const age = eventAgeSeconds(build, nowMs)
  const ago =
    age == null
      ? null
      : age < 5
        ? 'just now'
        : age < 60
          ? `${Math.round(age)}s ago`
          : `${Math.round(age / 60)} min ago`
  const stale = Boolean(build.stale) || (age != null && age >= CLIENT_STALE_AFTER_S)
  const inCall = Boolean(build.model_call_in_progress)
  const deadline =
    typeof build.model_call_deadline_s === 'number'
      ? build.model_call_deadline_s
      : inCall
        ? CLIENT_MODEL_CALL_DEADLINE_S
        : null
  if (inCall && deadline != null && age != null && age >= deadline) {
    return `coder LLM timed out after ${Math.round(age)}s (deadline ${Math.round(deadline)}s) — the model call did not finish`
  }
  if (inCall && stale && (deadline == null || age == null || age < deadline)) {
    return ago
      ? `quiet for ${ago.replace(' ago', '')} — model call still inside ${Math.round(deadline ?? CLIENT_MODEL_CALL_DEADLINE_S)}s watchdog`
      : 'quiet — model call still inside watchdog'
  }
  if (stale) {
    return ago
      ? `quiet for ${ago.replace(' ago', '')} — no new progress`
      : 'quiet — no new progress'
  }
  if (inCall) {
    return ago ? `waiting on coder LLM · ${ago}` : 'waiting on coder LLM'
  }
  return ago ? `still working · ${ago}` : 'still working'
}

export function isUnreadableLedger(build: BuildStatus | null | undefined): boolean {
  const detail = build?.detail || ''
  return /LEDGER_UNREADABLE|ledger unreadable/i.test(detail)
}

const CLI_FAIL_TEXT =
  /FACTORY_CODE_CLI_FAILED|FACTORY_CODE_CLI_BILLING|FACTORY_CODE_CLI_UNAVAILABLE|FACTORY_CODE_CLI_MODEL_DENIED|FACTORY_CODE_CLI_CREDENTIALS_MISSING|FACTORY_CODE_CLI_NO_MODEL|CLI exited/i

/** Receipt or ledger says the coding agent failed. Not enough alone to refuse Export. */
export function isCoderCliFailed(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  const receipt = build.coder_receipt
  if (receipt) {
    if (receipt.ok === false) return true
    if (CLI_FAIL_TEXT.test(String(receipt.honesty_class || ''))) return true
    if (CLI_FAIL_TEXT.test(String(receipt.blocker || ''))) return true
    if (CLI_FAIL_TEXT.test(String(receipt.detail || ''))) return true
  }
  const blobs = [
    build.detail ?? '',
    ...(build.findings ?? []),
    ...(build.level_grade?.blockers ?? []),
    ...Object.values(build.authorship?.coder_failures ?? {}),
  ]
  return blobs.some((text) => CLI_FAIL_TEXT.test(text))
}

function authorshipCount(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) {
    return value
  }
  if (typeof value === 'string' && /^\d+$/.test(value)) return Number(value)
  return null
}

function actionArtifactId(id: unknown): boolean {
  const text = String(id || '').trim()
  if (!text || text.includes(':') || text.includes('/')) return false
  if (text.endsWith('.py') || text.endsWith('.tsx') || text.endsWith('.md')) return false
  if (text.startsWith('template_')) return false
  return true
}

/** Measured action-handler / cli_authored count, or null when unknown. */
export function fullPilotAuthorshipCount(
  build: BuildStatus | null | undefined,
): number | null {
  if (!build) return null
  const authorship = build.authorship
  const receiptIds = build.coder_receipt?.cli_authored_ids
  const authoredIds = authorship?.cli_authored_ids
  const cliN = Array.isArray(receiptIds)
    ? receiptIds.filter(Boolean).length
    : Array.isArray(authoredIds)
      ? authoredIds.filter(Boolean).length
      : null
  const actionPy = authorshipCount(authorship?.action_py)
  if (Array.isArray(authorship?.agent_artifacts)) {
    const actions = authorship.agent_artifacts.filter(actionArtifactId).length
    const best = Math.max(actions, cliN ?? 0, actionPy ?? 0)
    return best
  }
  if (actionPy != null || cliN != null) {
    return Math.max(actionPy ?? 0, cliN ?? 0)
  }
  const written = authorshipCount(authorship?.agent_written)
  if (written != null) return Math.max(written, cliN ?? 0)
  return cliN
}

export function fullPilotAuthorshipNeed(
  build: BuildStatus | null | undefined,
): number {
  const n =
    authorshipCount(build?.authorship?.n_required) ??
    authorshipCount(build?.n_required)
  if (n == null || n <= 0) return FULL_PILOT_MIN_AUTHORED_ACTIONS
  return Math.min(FULL_PILOT_MIN_AUTHORED_ACTIONS, Math.max(1, n))
}

function capabilityId(item: unknown): string {
  if (typeof item === 'string') {
    const text = item.trim()
    if (!text || text.includes(' ') || text.includes(':') || text.includes('/')) return ''
    return text
  }
  if (item && typeof item === 'object') {
    const rec = item as { id?: unknown; capability_id?: unknown }
    return String(rec.id || rec.capability_id || '').trim()
  }
  return ''
}

function capabilityIsRequired(item: unknown): boolean {
  if (item && typeof item === 'object' && 'required' in (item as object)) {
    return (item as { required?: unknown }).required !== false
  }
  return true
}

/** Required-capability count from a session blueprint or plan (live n_required). */
export function nRequiredFromProductInputs(source: unknown): number | null {
  if (source == null) return null
  if (typeof source === 'number' && Number.isFinite(source) && source > 0) {
    return Math.floor(source)
  }
  const rec = source && typeof source === 'object' ? (source as Record<string, unknown>) : null
  const caps = rec
    ? rec.capabilities
    : null
  if (Array.isArray(caps)) {
    const ids: string[] = []
    for (const item of caps) {
      if (!capabilityIsRequired(item)) continue
      const id = capabilityId(item)
      if (id && !ids.includes(id)) ids.push(id)
    }
    if (ids.length > 0) return ids.length
  }
  if (rec) {
    for (const key of ['blueprint', 'plan'] as const) {
      const nested = nRequiredFromProductInputs(rec[key])
      if (nested != null) return nested
    }
  }
  return null
}

/** Stamp live n_required from the session brief when the snapshot omitted it. */
export function withResolvedNRequired(
  build: BuildStatus | null | undefined,
  nRequired: number | null | undefined,
): BuildStatus | null {
  if (!build) return build ?? null
  if (nRequired == null || nRequired <= 0) return build
  if (authorshipCount(build.authorship?.n_required) != null) return build
  if (authorshipCount(build.n_required) != null) return build
  return {
    ...build,
    n_required: nRequired,
    authorship: { ...(build.authorship ?? {}), n_required: nRequired },
  }
}

const THIN_AUTHORSHIP_TEXT = /FACTORY_CODE_CLI_THIN_AUTHORSHIP/i

/** Pre-#392 RUN_FAILED copy that still hard-codes need ≥5. */
export function isStickyThinAuthorshipRefuse(
  build: BuildStatus | null | undefined,
): boolean {
  if (!build) return false
  const blobs = [
    build.detail ?? '',
    ...(build.findings ?? []),
    ...(build.level_grade?.blockers ?? []),
  ]
  return blobs.some((text) => THIN_AUTHORSHIP_TEXT.test(text))
}

/**
 * Mirror backend ``_reevaluate_thin_authorship_failure``.
 * A sticky need≥5 RUN_FAILED must become SUCCESS when live n_required is
 * knowable and authorship meets that floor. Unknown n_required stays need=5.
 */
export function reevaluateStickyThinAuthorship(
  build: BuildStatus | null | undefined,
): BuildStatus | null {
  if (!build) return build ?? null
  if (!isStickyThinAuthorshipRefuse(build)) return build
  if (build.state === 'building' || build.state === 'not_started') return build
  if (isBelowFullPilotAuthorshipFloor(build)) return build
  if (fullPilotAuthorshipCount(build) == null) return build
  if (build.state === 'succeeded' && build.pilot_ready === true) return build
  const cycle = String(build.cycle || 'pilot').trim().toLowerCase() || 'pilot'
  const pilotReady = cycle === 'pilot'
  const threeGate = pilotReady
    ? { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' }
    : { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' }
  return {
    ...build,
    state: 'succeeded',
    outcome: 'SUCCESS',
    cycle,
    pilot_ready: pilotReady,
    honesty: 'full_pilot_authorship_reevaluated',
    detail: build.detail && /CODE\s+PASS/i.test(build.detail)
      ? build.detail
      : `CODE PASS — handlers; PRODUCT ${pilotReady ? 'PASS' : 'NOT RUN'} — suite; STORE ${pilotReady ? 'PASS' : 'NOT RUN'} — ops`,
    level_grade: {
      ...(build.level_grade ?? {}),
      level: pilotReady ? 'STORE_GREEN' : 'CODE_GREEN',
      pilot_ready: pilotReady,
      full_pilot: pilotReady,
      founding_customer_ready: false,
      three_gate: {
        ...(build.level_grade?.three_gate ?? {}),
        ...threeGate,
      },
    },
  }
}

/**
 * Watch / Refresh must not let a cached pre-#392 THIN_AUTHORSHIP failed
 * tick replace a live SUCCESS that package already honors.
 */
export function preferHonestBuild(
  next: BuildStatus,
  prev: BuildStatus | null | undefined,
): BuildStatus {
  const honestNext = reevaluateStickyThinAuthorship(stampBuildObservation(next, prev))
  if (!honestNext) return next
  const honestPrev = prev ? reevaluateStickyThinAuthorship(prev) : null
  if (
    honestPrev &&
    isAuthoritativePilotReady(honestPrev) &&
    honestNext.state === 'failed' &&
    isStickyThinAuthorshipRefuse(next)
  ) {
    return stampBuildObservation(honestPrev, prev)
  }
  return honestNext
}

export function isBelowFullPilotAuthorshipFloor(
  build: BuildStatus | null | undefined,
): boolean {
  if (build?.level_grade?.full_pilot === true) return false
  const count = fullPilotAuthorshipCount(build)
  return count != null && count < fullPilotAuthorshipNeed(build)
}

function claimsStoreGreenPilot(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  if (
    productSuiteFailed(build) ||
    outcomeFailed(build) ||
    build.state === 'failed' ||
    build.state === 'stalled'
  ) {
    return false
  }
  if (build.pilot_ready === true) return true
  const product = String(build.level_grade?.three_gate?.PRODUCT || '')
  const store = String(build.level_grade?.three_gate?.STORE || '')
  return product === 'PASS' && store === 'PASS'
}

/**
 * Succeeded CODE/PRODUCT/STORE (or ledger-pilot) prototype whose Export
 * refusal is acceptance not run / not k/k — not a crashed or red-gate build.
 * A true code-cycle (PRODUCT/STORE not run) is not this shape.
 */
export function isAcceptancePendingPrototype(
  build: BuildStatus | null | undefined,
): boolean {
  if (!build || build.state !== 'succeeded') return false
  if (isUnreadableLedger(build) || productSuiteFailed(build) || outcomeFailed(build)) {
    return false
  }
  if (isBelowFullPilotAuthorshipFloor(build) && claimsStoreGreenPilot(build)) {
    return false
  }
  if (isAcceptanceKk(build)) return false
  return claimsStoreGreenPilot(build)
}

/**
 * Near-zero or template-majority writer keep-path.
 * Live photographs: 1/23 after a CLI billing miss; sess_45729 0639 8/16
 * factory-LLM thin zip that still painted Finished / founding.
 */
export function isThinTemplatedAuthorship(
  authorship: BuildAuthorship | null | undefined,
): boolean {
  if (!authorship) return false
  const written = authorshipCount(authorship.agent_written)
  const templated = authorshipCount(authorship.templated)
  const artifacts = authorshipCount(authorship.artifacts)
  if (written == null) return false
  if (written <= 1) return true
  if (templated != null && templated >= written) return true
  if (artifacts != null && artifacts > 0 && written * 2 <= artifacts) return true
  return false
}

/** Factory-LLM GENERATE keep-path is not a founding writer session. */
export function isFactoryLlmFallthrough(build: BuildStatus | null | undefined): boolean {
  return build?.coder_receipt?.factory_llm_generate_fallthrough === true
}

/**
 * CLI billing/auth miss, factory-LLM fallthrough, or thin / template-majority
 * authorship. Export may still be allowed on a pilot_ready SUCCESS keep-path
 * — founding paint must not. A founding claim with no authorship counts
 * cannot be proven on the glass and is demoted.
 */
export function shouldDemoteFounding(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  if (isCoderCliFailed(build)) return true
  if (isFactoryLlmFallthrough(build)) return true
  if (isThinTemplatedAuthorship(build.authorship)) return true
  const claimed = String(build.level_grade?.level || '').toUpperCase()
  const claimsFounding =
    build.level_grade?.founding_customer_ready === true || claimed === 'FOUNDING_CUSTOMER_READY'
  if (claimsFounding && authorshipCount(build.authorship?.agent_written) == null) {
    return true
  }
  return false
}

export function isScaffoldClaim(build: BuildStatus | null | undefined): boolean {
  return String(build?.level_grade?.level || '').toUpperCase() === 'SCAFFOLD'
}

/** PRODUCT three-gate is red — never a founding / gold Export. */
export function productSuiteFailed(build: BuildStatus | null | undefined): boolean {
  const verdict = build?.level_grade?.three_gate?.PRODUCT
  return Boolean(verdict && /FAIL|RED/i.test(String(verdict)))
}

/** Ledger outcome is a named failure (FAILED_BUDGET_SPENT, …). */
export function outcomeFailed(build: BuildStatus | null | undefined): boolean {
  const outcome = String(build?.outcome || '').trim()
  if (!outcome) return false
  if (/^(SUCCESS|PASS|SUCCEEDED)$/i.test(outcome)) return false
  return /FAIL/i.test(outcome)
}

/**
 * Authoritative pilot-ready SUCCESS. CLI billing miss + factory-grounded
 * REUSE keep-path (#338) is an allowed honesty class when these fields hold.
 */
export function isAuthoritativePilotReady(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  if (build.state !== 'succeeded') return false
  if (build.pilot_ready !== true) return false
  if (outcomeFailed(build)) return false
  if (productSuiteFailed(build)) return false
  return true
}

/**
 * Refuse Finished / gold Download / founding chips. Prefer authoritative
 * build fields (pilot_ready, outcome, state, PRODUCT) over a raw
 * FACTORY_CODE_CLI_FAILED receipt.
 */
export function shouldRefuseExport(build: BuildStatus | null | undefined): boolean {
  if (!build) return false
  if (build.state === 'building' || build.state === 'not_started') return false
  if (isUnreadableLedger(build)) return true
  if (build.state === 'failed') return true
  if (productSuiteFailed(build)) return true
  if (outcomeFailed(build)) return true
  if (isBelowFullPilotAuthorshipFloor(build) && claimsStoreGreenPilot(build)) {
    return true
  }
  if (build.pilot_ready === true && !isAcceptanceKk(build)) {
    return true
  }
  if (isAuthoritativePilotReady(build)) return false
  if (isCoderCliFailed(build)) return true
  if (isScaffoldClaim(build) && build.pilot_ready !== true) return true
  return false
}

function refuseExportDetail(build: BuildStatus): string {
  if (build.detail) return build.detail
  const receipt = build.coder_receipt
  if (receipt?.detail) return receipt.detail
  if (receipt?.blocker) return String(receipt.blocker)
  if (productSuiteFailed(build)) return 'PRODUCT suite failed'
  if (outcomeFailed(build)) return String(build.outcome)
  const fromFailures = Object.values(build.authorship?.coder_failures ?? {}).find((value) =>
    CLI_FAIL_TEXT.test(value),
  )
  return fromFailures || FACTORY_CODE_CLI_FAILED
}

/**
 * Demote Finished / Downloadable paint only when the build is not
 * actually pilot-ready. A FACTORY_CODE_CLI_FAILED receipt after a
 * SUCCESS + pilot_ready keep-path must not strip Export.
 */
export function withExportHonesty(build: BuildStatus | null): BuildStatus | null {
  if (!build) return build
  build = reevaluateStickyThinAuthorship(build)
  if (!build) return build
  if (build.state === 'building' || build.state === 'not_started') return build
  if (
    build.state === 'succeeded' &&
    isBelowFullPilotAuthorshipFloor(build) &&
    claimsStoreGreenPilot(build)
  ) {
    return {
      ...build,
      pilot_ready: false,
      level_grade: {
        ...(build.level_grade ?? {}),
        level: 'CODE_GREEN',
        pilot_ready: false,
        full_pilot: false,
        founding_customer_ready: false,
      },
    }
  }
  if (isAuthoritativePilotReady(build)) return build
  if (!shouldRefuseExport(build)) return build
  const grade = {
    ...(build.level_grade ?? {}),
    level: 'SCAFFOLD',
    pilot_ready: false,
    founding_customer_ready: false,
  }
  if (build.state === 'failed' || build.state === 'stalled') {
    if (build.pilot_ready === true || build.level_grade?.founding_customer_ready === true) {
      return { ...build, pilot_ready: false, level_grade: grade }
    }
    return build
  }
  return {
    ...build,
    state: 'failed',
    pilot_ready: false,
    level_grade: grade,
    detail: refuseExportDetail(build),
  }
}

/** Unreadable / crash honesty: never paint as an active coding run. */
export function withLedgerHonesty(build: BuildStatus | null): BuildStatus | null {
  if (!build) return build
  if (build.state === 'unknown' && isUnreadableLedger(build)) {
    return { ...build, state: 'failed', pilot_ready: false }
  }
  return build
}

/** Promote a forever-"building" snapshot to stalled when the ledger is dead. */
export function withClientStall(
  build: BuildStatus | null,
  nowMs = Date.now(),
): BuildStatus | null {
  build = withExportHonesty(withLedgerHonesty(build))
  if (!build || build.state !== 'building') return build
  const age = eventAgeSeconds(build, nowMs)
  const deadline =
    typeof build.model_call_deadline_s === 'number'
      ? build.model_call_deadline_s
      : build.model_call_in_progress
        ? CLIENT_MODEL_CALL_DEADLINE_S
        : null
  if (build.model_call_in_progress && deadline != null && age != null && age >= deadline) {
    return {
      ...build,
      state: 'failed',
      detail:
        build.detail && build.detail.includes('timed out')
          ? build.detail
          : `coder LLM timed out after ${Math.round(age)}s (deadline ${Math.round(deadline)}s) — the model call did not finish`,
    }
  }
  // A 20–40 min handler write is still coding. Do not stall the Floor
  // at 30 min while the calling-NOTE is inside its watchdog wall.
  if (build.model_call_in_progress && deadline != null && age != null && age < deadline) {
    return build
  }
  if (age == null || age < CLIENT_STALL_AFTER_S) return build
  return {
    ...build,
    state: 'stalled',
    detail:
      build.detail && build.detail !== 'build in progress'
        ? build.detail
        : `no build activity for ${Math.round(age / 60)} min — the build process may be gone; generate again`,
  }
}

/**
 * Platforms page-head. Never invite a successful Download when the zip
 * is failed, stalled, in-flight, missing, or not pilot-ready.
 */
export function platformsLeadCopy(
  build: BuildStatus | null | undefined,
  hasGeneration: boolean,
): string {
  if (!hasGeneration) {
    return 'What the factory built for you. A downloadable export appears here only after a pilot-ready run succeeds.'
  }
  if (isUnreadableLedger(build)) {
    return 'The last build crashed with an unreadable ledger. Build failed — no certified download; you can re-run the writer or download the tree as-is.'
  }
  if (isAcceptancePendingPrototype(build)) {
    const score = formatAcceptanceScore(build)
    return (
      `Acceptance is ${score} — not k/k. Download unavailable until scripts/acceptance.py ` +
      'passes inside the Store-built image. This is a code-green prototype, not a failed build.'
    )
  }
  if (
    shouldRefuseExport(build) &&
    !(isBelowFullPilotAuthorshipFloor(build) && claimsStoreGreenPilot(build))
  ) {
    return 'The last build did not pass its gates. Build failed — no certified download; you can re-run the writer or download the tree as-is.'
  }
  if (isBelowFullPilotAuthorshipFloor(build) && claimsStoreGreenPilot(build)) {
    return 'Authorship is below the full-pilot floor. Download unavailable — a thin Store-green zip is refused.'
  }
  if (!build || build.state === 'building' || build.state === 'not_started' || build.state === 'unknown') {
    return 'The coding agent is writing this platform. Export stays closed until a pilot-ready run succeeds.'
  }
  if (build.state === 'failed') {
    return 'The last build did not pass its gates. Build failed — no certified download; you can re-run the writer or download the tree as-is.'
  }
  if (build.state === 'stalled') {
    return 'The last build stalled. Build stalled — no certified download; you can re-run the writer or download the tree as-is.'
  }
  if (build.state === 'succeeded' && !isPilotZipReady(build)) {
    return 'A code-cycle prototype is on this page. This is not a full-pilot export — do not treat it as launch-ready.'
  }
  return 'What the factory built for you. Download the export and launch it anywhere.'
}

const MISSING_AGENT_CLI_CREDS =
  'The coding agent has no credentials (FACTORY_CODE_CLI_CREDENTIALS_MISSING). ' +
  'Set DEEPSEEK_API_KEY on the service so the CodeWhale worker can authenticate, ' +
  'and arm the writer with FACTORY_CODEWHALE_WRITER=1. OPENROUTER_API_KEY is the ' +
  'fallback leg only — it does not authenticate the coding agent.'

const MISSING_DEEPSEEK_CLI_CREDS =
  'DeepSeek credentials are missing (FACTORY_CODE_CLI_CREDENTIALS_MISSING). ' +
  'DeepSeek is the only primary (OpenAI-compat https://api.deepseek.com): set ' +
  'DEEPSEEK_API_KEY on the service and arm the writer with ' +
  'FACTORY_CODEWHALE_WRITER=1. OpenRouter (OPENROUTER_API_KEY) is the fallback ' +
  'leg only.'

const MISSING_AGENT_CLI_MODEL =
  'The coding agent has no usable default_model (FACTORY_CODE_CLI_NO_MODEL). ' +
  'DeepSeek is the only primary: check DEEPSEEK_API_KEY and the configured ' +
  'DeepSeek model, with OpenRouter (OPENROUTER_API_KEY / OPENROUTER_MODEL) as ' +
  'the fallback leg.'

/**
 * Operator copy from GET /health factory_code_cli. Named blocker wins. A
 * credentials file without default_model is FACTORY_CODE_CLI_NO_MODEL, not a
 * successful probe.
 *
 * DeepSeek is the only primary and OpenRouter the only fallback; the coding
 * agent runs through the CodeWhale worker, so `requires_cli === false` means
 * no CLI credential blocker applies.
 */
export function factoryCodeCliHonesty(
  probe: FactoryCodeCliProbe | null | undefined,
): string | null {
  if (!probe) return null
  if (probe.requires_cli === false) {
    return null
  }
  if (probe.blocker === FACTORY_CODE_CLI_NO_MODEL) {
    return MISSING_AGENT_CLI_MODEL
  }
  if (
    probe.credentials_file_present === true &&
    probe.default_model_configured === false &&
    probe.requires_kimi_credentials !== false
  ) {
    return MISSING_AGENT_CLI_MODEL
  }
  if (probe.blocker === FACTORY_CODE_CLI_CREDENTIALS_MISSING) {
    if (probe.requires_deepseek_credentials) {
      return MISSING_DEEPSEEK_CLI_CREDS
    }
    return MISSING_AGENT_CLI_CREDS
  }
  if (probe.credentials_file_present === false && probe.requires_kimi_credentials !== false) {
    return MISSING_AGENT_CLI_CREDS
  }
  return null
}

/** Floor / Platforms status-pill label for the /health CLI probe. */
export function factoryCodeCliStatusTitle(
  probeOrMessage: FactoryCodeCliProbe | string | null | undefined,
): string {
  const text =
    typeof probeOrMessage === 'string'
      ? probeOrMessage
      : factoryCodeCliHonesty(probeOrMessage) || ''
  if (text.includes(FACTORY_CODE_CLI_NO_MODEL) || text.includes('default_model')) {
    return 'Coding agent has no model'
  }
  // DeepSeek is the only primary, so every credential message names
  // DEEPSEEK_API_KEY — a separate "DeepSeek vs other vendor" title would now
  // match everything and say nothing.
  return 'Coding agent credentials missing'
}

/** Honest export CTA. Gold "Download platform export" is only for Store-green. */
export function exportAffordance(build: BuildStatus | null | undefined): {
  label: string
  disabled: boolean
  ghost: boolean
  asIs?: boolean
  title?: string
} {
  if (
    isUnreadableLedger(build) ||
    build?.state === 'failed' ||
    build?.state === 'stalled' ||
    productSuiteFailed(build) ||
    outcomeFailed(build)
  ) {
    // Owner's order (2026-09-28): a gate-failed build IS downloadable --
    // explicitly as-is. The server ships it loudly labeled (EXPORTED-AS-IS.md
    // naming the failing gate, no certification manifest); the Floor's job is
    // to offer it honestly, not to hide the artifact behind a dead button.
    return {
      label: 'Download as-is (failed gates)',
      disabled: false,
      ghost: true,
      asIs: true,
      title:
        'The gates rejected this build. The zip ships labeled EXPORTED-AS-IS with the failing gate named — no certification claims.',
    }
  }
  if (!build || build.state === 'building' || build.state === 'not_started') {
    return { label: 'Building…', disabled: true, ghost: true }
  }
  if (isBelowFullPilotAuthorshipFloor(build) && claimsStoreGreenPilot(build)) {
    return {
      label: 'Export (.zip) — below full-pilot authorship floor',
      disabled: true,
      ghost: true,
      title:
        `Need ≥${fullPilotAuthorshipNeed(build)} agent-written action handlers or cli_authored_ids — a thin Store-green zip is refused`,
    }
  }
  if (isAcceptancePendingPrototype(build) || (build.pilot_ready === true && !isAcceptanceKk(build))) {
    const score = formatAcceptanceScore(build)
    return {
      label: `Export (.zip) — acceptance ${score}`,
      disabled: true,
      ghost: true,
      title: `Store-green requires scripts/acceptance.py ${score === '12/12' ? 'k/k' : score} PASS inside the Store-built image — authorship is not acceptance`,
    }
  }
  if (shouldRefuseExport(build)) {
    // A succeeded-shaped build whose gates failed (CLI-failed claim, red
    // PRODUCT suite) is a gate failure too: same honest as-is exit.
    return {
      label: 'Download as-is (failed gates)',
      disabled: false,
      ghost: true,
      asIs: true,
      title:
        'The gates rejected this build. The zip ships labeled EXPORTED-AS-IS with the failing gate named — no certification claims.',
    }
  }
  // 'stalled' is handled by the as-is branch at the top: a dead runner
  // thread is a gate-failed artifact too, downloadable only as-is.
  if (build.state === 'succeeded' && !isPilotZipReady(build)) {
    return {
      label: 'Download code-cycle prototype (.zip)',
      disabled: false,
      ghost: true,
      title: 'Code-cycle prototype — not a Store-green / full-pilot zip',
    }
  }
  if (build.state === 'succeeded' && isPilotZipReady(build)) {
    return { label: 'Download platform export (.zip)', disabled: false, ghost: false }
  }
  return { label: 'Building…', disabled: true, ghost: true }
}

export function phaseBarFraction(build: BuildStatus): number | null {
  const progress = build.phase_progress
  if (progress && progress.total > 0) {
    return Math.max(0, Math.min(1, progress.fraction ?? progress.done / progress.total))
  }
  if (
    typeof build.activity_done === 'number' &&
    typeof build.activity_total === 'number' &&
    build.activity_total > 0
  ) {
    return Math.max(0, Math.min(1, build.activity_done / build.activity_total))
  }
  return null
}
