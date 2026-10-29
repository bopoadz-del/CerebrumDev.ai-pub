/**
 * CerebrumDev factory console API client.
 * Real backend only: per-account login tokens (cdt_...), SSE chat,
 * product design state, product package export, billing status.
 */

import floorActionSpec from './floor_actions.json'

const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || ''

const STORAGE_PREFIX = 'cerebrum.factory.'
const EMAIL_KEY = STORAGE_PREFIX + 'email'
/** Owner-requested convenience remember-me. Client-only; never cookies or logs. */
const PASSWORD_KEY = STORAGE_PREFIX + 'password'
const LEGACY_TOKEN_KEY = 'cerebrum.factory.token'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 403 from credential-gated routes when ACCOUNTS_REQUIRE_VERIFIED_EMAIL=1. */
export function isEmailNotVerifiedError(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 403) return false
  return err.message === 'email_not_verified' || err.message.includes('email_not_verified')
}

/**
 * Boot must open Sign in — not "Factory unreachable".
 * /v1/auth/me returns 401 when there is no user session. Older builds (and
 * ALLOW_ANONYMOUS_DEV + `_require_user`) answered 403 with a credential
 * detail; treat that the same so a cold visit never looks like a dead API.
 */
export function isUnauthenticatedError(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false
  if (err.status === 401) return true
  if (err.status !== 403 || isEmailNotVerifiedError(err)) return false
  return /requires an account credential|invalid or missing api key/i.test(err.message)
}

/** Optional Cerebrum-Blocks store: 503 must not look like a dead Factory. */
export function isDomainStoreUnreachable(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 503) return false
  return /domain store unreachable/i.test(err.message)
}

/**
 * Browser/network races ("Failed to fetch") — not an HTTP status.
 * First paint after a cold HTML load hits these intermittently; a 401/403/5xx
 * is a real API answer and is not a race.
 */
export function isTransientNetworkError(err: unknown): boolean {
  if (err instanceof ApiError) return false
  if (typeof err === 'object' && err && (err as { name?: string }).name === 'AbortError') {
    return false
  }
  if (err instanceof TypeError) return true
  if (err instanceof Error) {
    return /failed to fetch|networkerror|network request failed|load failed/i.test(err.message)
  }
  return false
}

/** Cold-load races: browser fetch failure or a proxy/gateway 5xx after retries. */
export function isTransientBootError(err: unknown): boolean {
  if (isTransientNetworkError(err)) return true
  return err instanceof ApiError && (err.status === 502 || err.status === 503 || err.status === 504)
}

function isRetryableFetch(method: string, err: unknown): boolean {
  if (isTransientNetworkError(err)) return true
  if (
    err instanceof ApiError &&
    (err.status === 502 || err.status === 503 || err.status === 504) &&
    (method === 'GET' || method === 'HEAD')
  ) {
    return true
  }
  return false
}

/** Two retries after the first attempt. Tests use 0ms so suites stay fast. */
const RETRY_DELAYS_MS = import.meta.env.MODE === 'test' ? [0, 0] : [200, 600]

export function getEmail(): string | null {
  return localStorage.getItem(EMAIL_KEY)
}
export function getRememberedPassword(): string | null {
  return localStorage.getItem(PASSWORD_KEY)
}
export function getLoginToken(): string | null {
  const raw = localStorage.getItem(LEGACY_TOKEN_KEY)
  return raw && raw.startsWith('cdt_') ? raw : null
}

/**
 * Persist display email and optional ``cdt_`` login token.
 * Cookie (SameSite=None; Secure) is primary; storing the token lets fetch
 * send ``Authorization: Bearer`` when the cookie is absent. Pass the token
 * from login/register; omit it on boot ``me()`` so a Bearer backup remains.
 */
export function setSession(email: string, loginToken?: string): void {
  localStorage.setItem(EMAIL_KEY, email)
  if (typeof loginToken === 'string') {
    if (loginToken.startsWith('cdt_')) {
      localStorage.setItem(LEGACY_TOKEN_KEY, loginToken)
    } else {
      localStorage.removeItem(LEGACY_TOKEN_KEY)
    }
  }
}
/** Save email+password after a remembered login. Password stays in localStorage only. */
export function rememberLogin(email: string, password: string): void {
  localStorage.setItem(EMAIL_KEY, email)
  localStorage.setItem(PASSWORD_KEY, password)
}
/** Forget saved login fields. Does not touch the HttpOnly session cookie. */
export function forgetRememberedLogin(): void {
  localStorage.removeItem(EMAIL_KEY)
  localStorage.removeItem(PASSWORD_KEY)
}
export function clearRememberedPassword(): void {
  localStorage.removeItem(PASSWORD_KEY)
}
export function clearSession(): void {
  localStorage.removeItem(LEGACY_TOKEN_KEY)
  if (!localStorage.getItem(PASSWORD_KEY)) {
    localStorage.removeItem(EMAIL_KEY)
  }
}

/** Drop the display email and ask the API to clear the HttpOnly cookie. */
export async function signOut(): Promise<void> {
  clearSession()
  try {
    await auth.logout()
  } catch {
    /* cookie may already be gone */
  }
}

function authHeaders(contentTypeJson = true): Record<string, string> {
  const headers: Record<string, string> = {}
  if (contentTypeJson) headers['Content-Type'] = 'application/json'
  const token = getLoginToken()
  if (token) headers.Authorization = `Bearer ${token}`
  return headers
}

async function reqOnce<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers = authHeaders()
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: 'include',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  let data: unknown
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }
  if (!res.ok) {
    const d = data as { detail?: unknown; error?: unknown } | null
    const msg = d?.detail ?? d?.error ?? res.statusText
    throw new ApiError(res.status, typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data as T
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let last: unknown
  const attempts = RETRY_DELAYS_MS.length + 1
  for (let i = 0; i < attempts; i++) {
    try {
      return await reqOnce<T>(method, path, body)
    } catch (e) {
      last = e
      if (i === attempts - 1 || !isRetryableFetch(method, e)) throw e
      const wait = RETRY_DELAYS_MS[i] ?? 0
      if (wait > 0) await new Promise((r) => setTimeout(r, wait))
    }
  }
  throw last
}

// --- Auth ------------------------------------------------------------------

export interface VerificationInfo {
  mode?: string
  email_sent?: boolean
  note?: string
  dev_verification_token?: string
}
export interface LoginResponse {
  login_token: string
  email_verified?: boolean
}
export interface RegisterResponse extends LoginResponse {
  account_id?: string
  email_verified?: boolean
  verification?: VerificationInfo
}
export interface ResendVerificationResponse {
  ok?: boolean
  already_verified?: boolean
  email?: string
  email_verified?: boolean
  verification?: VerificationInfo
}
export interface ForgotResponse {
  ok?: boolean
  message?: string
  note?: string
  dev_reset_token?: string
}
export interface AccountInfo {
  email?: string
  account_id?: string
  email_verified?: boolean
  created_at?: string
  [k: string]: unknown
}

export const auth = {
  register: (email: string, password: string) =>
    req<RegisterResponse>('POST', '/v1/auth/register', { email, password }),
  login: (email: string, password: string) =>
    req<LoginResponse>('POST', '/v1/auth/login', { email, password }),
  logout: () => req<{ ok?: boolean }>('POST', '/v1/auth/logout'),
  me: () => req<AccountInfo>('GET', '/v1/auth/me'),
  forgotPassword: (email: string) =>
    req<ForgotResponse>('POST', '/v1/auth/forgot-password', { email }),
  resetPassword: (token: string, newPassword: string) =>
    req<{ ok?: boolean; message?: string }>('POST', '/v1/auth/reset-password', {
      token,
      new_password: newPassword,
    }),
  changePassword: (currentPassword: string, newPassword: string) =>
    req<{ ok?: boolean; message?: string }>('POST', '/v1/auth/change-password', {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  verifyEmail: (token: string) =>
    req<{ ok?: boolean; email_verified?: boolean }>('POST', '/v1/auth/verify-email', { token }),
  resendVerification: () =>
    req<ResendVerificationResponse>('POST', '/v1/auth/resend-verification', {}),
}

// --- Sessions ---------------------------------------------------------------

export interface SessionInfo {
  session_id: string
  /** Product name once drafted, else the user's first real message. */
  title?: string
  stage?: 'empty' | 'talking' | 'blueprint' | 'build'
  updated_at?: string | null
  [k: string]: unknown
}

export const sessions = {
  create: () => req<SessionInfo>('POST', '/v1/sessions/', {}),
  list: () => req<SessionInfo[] | { sessions?: SessionInfo[] }>('GET', '/v1/sessions/'),
  /** Deletes the session for good: chat, uploads and its generated workspace. */
  remove: (sid: string) => req<{ ok: boolean }>('DELETE', `/v1/sessions/${sid}`),
  /** The full session state. The Floor reads chat_history from it so the
   *  pre-build conversation survives a reload instead of being replaced
   *  by two synthetic bubbles. */
  state: (sid: string) =>
    req<{ chat_history?: { role: string; content: string }[] }>('GET', `/v1/sessions/${sid}`),
}

export interface ChatEvent {
  event: string
  data: unknown
}

/** Every action a Floor control can send. ONE committed spec, exported from
 *  the backend (app.factory.floor_actions) and shared with the browser e2e and
 *  scripts/post_deploy_smoke.py; a backend test fails if it drifts. The
 *  Factory never decides an action from the words in the message. */
export type FloorAction = keyof typeof floorActionSpec.actions

/** The build levels the user can choose (ProductBlueprint.build_level), from
 *  the same spec's ``set_build_level`` value set. There is no default. */
export const BUILD_LEVELS: readonly string[] = floorActionSpec.actions.set_build_level.value.one_of

/** The user's typed intake as the server holds it: what they declared, and
 *  what the chat PROPOSED from their answer (stored only on Confirm). */
export interface IntakeFields {
  vertical?: string | null
  country?: string | null
  currency?: string | null
  build_level?: string | null
}

export interface IntakeState {
  declared: IntakeFields
  proposal: IntakeFields | null
  /** True after "Continue with new answers" on a FAILED build: Confirm
   *  resumes the platform's own branch. */
  reopened?: boolean
}

export interface TypedFloorAction {
  action: FloorAction
  value?: string
}

export async function chatStream(
  sessionId: string,
  message: string,
  onEvent: (ev: ChatEvent) => void,
  vertical?: string | null,
  locale?: DeclaredLocale | null,
  typed?: TypedFloorAction | null,
): Promise<void> {
  // The vertical, country and currency travel as their own typed fields,
  // never inside the message: the user types them on the Floor and the
  // Factory never infers them.
  const body: {
    message: string
    vertical?: string
    country?: string
    currency?: string
    action?: FloorAction
    value?: string
  } = { message }
  if (typed) {
    body.action = typed.action
    if (typed.value !== undefined) body.value = typed.value
  }
  if (vertical !== undefined && vertical !== null) body.vertical = vertical
  if (locale) {
    body.country = locale.country
    body.currency = locale.currency
  }
  const res = await fetch(`${API_BASE}/v1/sessions/${sessionId}/chat`, {
    method: 'POST',
    headers: authHeaders(),
    credentials: 'include',
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) {
    const txt = await res.text().catch(() => '')
    throw new ApiError(res.status, txt || `chat failed (${res.status})`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const lines = block
        .split('\n')
        .map((l) => l.trimEnd())
        .filter(Boolean)
      let event = 'message'
      let data: unknown = ''
      for (const l of lines) {
        if (l.startsWith('event:')) {
          event = l.slice(6).trim()
        } else if (l.startsWith('data:')) {
          const raw = l.slice(5).trim()
          try {
            data = JSON.parse(raw)
          } catch {
            data = raw
          }
        }
      }
      onEvent({ event, data })
    }
  }
}

export function chatEventText(ev: ChatEvent): string | null {
  const textEvents = ['message', 'token', 'delta', 'text', 'chunk', 'content']
  if (!textEvents.includes(ev.event)) return null
  if (typeof ev.data === 'string') return ev.data
  const d = ev.data as Record<string, unknown> | null
  if (d && typeof d === 'object') {
    for (const k of ['token', 'text', 'delta', 'content', 'message']) {
      if (typeof d[k] === 'string') return d[k] as string
    }
  }
  return null
}

/**
 * The golden match OFFERED on a draft (H1, F-4). It is an OFFER, never a
 * substitution: the base blueprint stays the user's own draft.
 */
export interface GoldenSuggestion {
  golden_id: string
  golden_name: string
  overlap: number
  drafting_mode: string
  label: string
}

export interface ProductDesign {
  session_id?: string
  mode?: string
  brief?: string | null
  blueprint?: Record<string, unknown> | null
  blueprint_yaml?: string | null
  plan?: Record<string, unknown> | null
  intake_blueprint?: Record<string, unknown> | null
  blueprint_approved?: boolean
  brief_lint?: Record<string, unknown> | null
  /**
   * The golden OFFER surfaced on the draft (H1, F-4): the user's draft stays
   * the base and a matching golden is offered as accept/decline, never swapped
   * in. None below the overlap threshold / on no match.
   */
  golden_suggestion?: GoldenSuggestion | null
  generation?: {
    output_dir?: string
    inputs_hash?: string
    product_id?: string
    canonical_output?: string
    engine?: string
    triggered_by?: string
    build?: BuildStatus
  } | null
  last_error?: string | null
}

/** A vertical a Store kit DECLARES it serves (its own serves_verticals). */
export interface DeclaredVertical {
  vertical: string
  kit: string
  build_ready: boolean
}

/** The country (ISO 3166 alpha-2) and currency (ISO 4217) the user typed. */
export interface DeclaredLocale {
  country: string
  currency: string
}

export const product = {
  get: (sid: string) => req<ProductDesign>('GET', `/v1/sessions/${sid}/product`),
  /** The Floor's vertical picker: options from the Store kits' declarations,
   *  plus the user's current choice and declared country/currency. */
  verticals: (sid: string) =>
    req<{
      verticals: DeclaredVertical[]
      chosen: string | null
      default: string
      country?: string | null
      currency?: string | null
      build_level?: string | null
      intake?: IntakeState
    }>(
      'GET',
      `/v1/sessions/${sid}/product/verticals`,
    ),
  draft: (sid: string, brief: string, vertical_hint?: string) =>
    req<ProductDesign & { source?: string; yaml?: string; golden_suggestion?: GoldenSuggestion | null }>(
      'POST',
      `/v1/sessions/${sid}/product/draft`,
      {
        brief,
        vertical_hint,
      },
    ),
  // H1 (F-4): resolve the golden OFFERED on the current draft. Accept makes
  // the offered golden the session blueprint; decline keeps the user's draft
  // and clears the offer. Both return the resulting blueprint.
  acceptGolden: (sid: string) =>
    req<{ ok: boolean; blueprint: Record<string, unknown>; yaml?: string; source: string }>(
      'POST',
      `/v1/sessions/${sid}/product/draft/accept-golden`,
      {},
    ),
  declineGolden: (sid: string) =>
    req<{ ok: boolean; blueprint: Record<string, unknown>; source: string }>(
      'POST',
      `/v1/sessions/${sid}/product/draft/decline-golden`,
      {},
    ),
  approve: (sid: string, approve: boolean, blueprint?: Record<string, unknown>) =>
    req<{ ok: boolean; blueprint_approved: boolean }>('POST', `/v1/sessions/${sid}/product/approve`, {
      approve,
      blueprint,
    }),
  generate: (sid: string) =>
    req<ProductDesign & { ok: boolean }>('POST', `/v1/sessions/${sid}/product/generate`, {}),
  buildStatus: (sid: string) =>
    req<{ ok: boolean; product_id?: string; build: BuildStatus }>(
      'GET',
      `/v1/sessions/${sid}/product/build-status`,
    ),
  coderControl: (sid: string, action: 'pause' | 'stop' | 'resume') =>
    req<{ ok: boolean; control: { action: string } }>(
      'POST',
      `/v1/sessions/${sid}/product/coder-control`,
      { action },
    ),
}

export type AcceptanceScore = {
  passed?: number
  total?: number
  ok?: boolean
  missing?: boolean
  score?: string
  lines?: { name?: string; status?: string; detail?: string }[]
}

export type BuildAuthorship = {
  artifacts?: number
  agent_written?: number
  templated?: number
  action_py?: number
  agent_artifacts?: string[]
  cli_authored_ids?: string[]
  kept_handler_ids?: string[]
  /** Required capability count from the brief/blueprint. Scales the floor. */
  n_required?: number
  coder_failures?: Record<string, string>
}

export type BuildPhaseRef = {
  id: string
  label?: string
}

export type BuildPhaseProgress = {
  done: number
  total: number
  fraction?: number
  stage?: string
}

/** F3: one row per build phase, from the ledger — never a concatenated string. */
export type PhaseTrailEntry = {
  phase: string
  outcome: 'not_reached' | 'running' | 'passed' | 'failed' | 'aborted'
  reason?: string
  location?: string
  timestamp?: string | null
  detail?: string
}

/** F3: the first failing/aborted phase, with its named reason and location. */
export type BuildFailure = {
  phase?: string
  outcome?: string
  reason?: string
  location?: string
  timestamp?: string | null
  detail?: string
}

/** One line of the coding agent's own narration. */
export interface BuildActivityLine {
  ts?: string | null
  role?: string | null
  text: string
}

/** Where a queued writer stands in the build-slot FIFO. */
export interface BuildQueuePlace {
  position?: number | null
  ahead?: number | null
  since?: string | null
  waited_s?: number | null
}

/** One check the foreman proposed. `floor_entry` is a pre-filled
 *  acceptance_floor.v2.json entry for a human to review and paste; the
 *  Floor only copies it -- nothing writes the floor file from the UI. */
export interface SuggestedCheck {
  name: string
  rule: string
  evidence_count: number
  source: string
  floor_entry: Record<string, unknown>
}

export interface BuildDecision {
  class: 'REWORK' | 'ADVISORY' | 'REGENERATE_TEST' | 'STOP' | string
  gate: string
  gate_name?: string
  round_gate: number
  gate_budget: number
  round_build: number
  build_ceiling: number
  check: string
  finding: string
  reason?: string
}

export type BuildStatus = {
  state: 'not_started' | 'unknown' | 'building' | 'succeeded' | 'failed' | 'stalled' | 'waiting'
  detail?: string
  findings?: string[]
  cycle?: string
  outcome?: string
  /** True only after a SUCCESS that closed a pilot cycle (Store-green). */
  pilot_ready?: boolean
  /** True when the Floor will (or did) auto-open a pilot cycle after code SUCCESS. */
  auto_pilot?: boolean
  /** The build level the user chose and the gate that level stops at, as the
   *  run recorded it in its own ledger. Null for a run that declared none. */
  build_level?: { build_level: string; stop_gate: string } | null
  /** Fail-closed founding-customer grade. SCAFFOLD when pilot_ready is false. */
  level_grade?: {
    level?: string
    pilot_ready?: boolean
    full_pilot?: boolean
    founding_customer_ready?: boolean
    blockers?: string[]
    three_gate?: Record<string, string>
    acceptance?: AcceptanceScore
  }
  /** Store-green measured score. Authorship is not acceptance. */
  acceptance?: AcceptanceScore
  phases?: string[]
  completed?: string[]
  phases_done?: number
  phases_total?: number
  current_phase?: BuildPhaseRef | null
  phase_index?: number
  phase_total?: number
  phase_progress?: BuildPhaseProgress
  /** F3: per-phase outcome trail; the red stage the user is watching. */
  phase_trail?: PhaseTrailEntry[]
  /** F3: exact location + named reason of the failure, if any. */
  failure?: BuildFailure | null
  /** A FAILED build is still a product: never certified, never green. */
  certified?: boolean
  /** FAILED(gate, check, finding), read once from the run's terminal event. */
  failed?: { gate: string; check: string; finding: string } | null
  failed_label?: string | null
  /** One line: what the next Continue will try to fix. */
  next_continue?: string
  /** F5: what was tried, where it stopped, why -- read from the ledger;
   *  ``source`` is ``ledger`` (rendered by code) or ``model`` (validated
   *  against the ledger). */
  failure_narrative?: { text: string; source: string; cites?: string[] } | null
  /** The runner rule's decisions, in order: gate, class (REWORK / ADVISORY /
   *  REGENERATE_TEST / STOP), this gate's round of its budget, the build's
   *  round of its ceiling, check, finding. */
  decisions?: BuildDecision[]
  /** The STOP decision that ended the build, when the rule stopped it. */
  stopped?: BuildDecision | null
  /** Checks the foreman PROPOSED (audit pass after TESTER green, or a rework
   *  round) for a human to accept into the acceptance floor. Read-only
   *  suggestions -- never a verdict. */
  suggested_checks?: SuggestedCheck[]
  /** The first failure of a run that went on to SUCCEED -- history, not an alert. */
  recovered_failure?: BuildFailure | null
  /** Client's delivery choice + repo URL when github_repo was delivered. */
  delivery_format?: string
  repo_url?: string
  last_event?: string | null
  /** Set while the writer waits its turn for a build slot (a full slot
   *  queues, never fails): its place in the FIFO and how long it has waited. */
  queued?: BuildQueuePlace | null
  /** Tail of the agent's own narration, newest last. The Floor renders
   *  it as a live log: a single replaced sentence cannot distinguish a
   *  working agent from a wedged one. */
  activity_log?: BuildActivityLine[]
  last_event_at?: string | null
  last_event_age_s?: number
  next_phase?: BuildPhaseRef | null
  stale?: boolean
  /** True while the last ledger NOTE is an in-flight coder LLM call. */
  model_call_in_progress?: boolean
  /** Wall-clock seconds the current coder LLM call may occupy. */
  model_call_deadline_s?: number
  activity?: string
  activity_stage?: string
  activity_done?: number
  activity_total?: number
  authorship?: BuildAuthorship
  /** Required-capability count when authorship is missing (failed re-eval). */
  n_required?: number
  honesty?: string
  /** Owner Pause / Stop / Resume for the live coder session. */
  coder_control?: 'run' | 'pause' | 'stop' | string
  /** Tail of docs/coder_session.log — the live coder session. */
  coder_log?: string
  coder_brief_present?: boolean
  coder_log_present?: boolean
  coder_receipt?: {
    via?: string
    ok?: boolean
    detail?: string
    blocker?: string | null
    honesty_class?: string | null
    model?: string
    factory_llm_generate_fallthrough?: boolean
    cli_authored_ids?: string[]
  }
  brief_dispatch?: string | null
  /**
   * Client-only: when this ledger event was first observed in the UI.
   * Lets the heartbeat advance even if the server keeps returning a frozen
   * ``last_event_age_s`` on every poll.
   */
  client_observed_at_ms?: number
  /** Client-only: ``last_event_age_s`` at ``client_observed_at_ms``. */
  client_base_age_s?: number
  /**
   * Client-only: phase_progress.done stepped down for the same stage
   * (e.g. handlers 3/5 → 1/5 on a new WRITER pass).
   */
  client_wave_reset?: boolean
}

export async function awaitBuild(
  sid: string,
  onProgress?: (s: BuildStatus) => void,
  { intervalMs = 4000, timeoutMs = 180 * 60 * 1000 } = {},
): Promise<BuildStatus> {
  const deadline = Date.now() + timeoutMs
  for (;;) {
    const { build } = await product.buildStatus(sid)
    onProgress?.(build)
    if (build.state === 'succeeded') return build
    if (build.state === 'failed' || build.state === 'stalled') {
      return {
        ...build,
        state: 'failed',
        detail: build.detail ?? 'build stalled',
      }
    }
    if (build.state === 'unknown') {
      return { ...build, state: 'succeeded' }
    }
    if (Date.now() > deadline) {
      return { ...build, state: 'failed', detail: 'build timed out client-side' }
    }
    await new Promise((r) => setTimeout(r, intervalMs))
  }
}

/**
 * Keep polling across succeed → building (pilot reopen). Stops only on
 * failed/stalled or abort. Floor/Platforms use this so a FINISHED banner
 * cannot stay pinned while a pilot cycle is live.
 */
export async function watchBuildStatus(
  sid: string,
  onProgress: (s: BuildStatus) => void,
  { intervalMs = 4000, signal }: { intervalMs?: number; signal?: AbortSignal } = {},
): Promise<void> {
  while (!signal?.aborted) {
    const { build } = await product.buildStatus(sid)
    if (signal?.aborted) return
    onProgress(build)
    // Sticky pre-#392 THIN_AUTHORSHIP may still unlock after live
    // n_required re-eval — keep polling so Platforms cannot freeze on
    // a cached RUN_FAILED / need≥5 tick. Other terminal failures stop.
    const stickyThin = /FACTORY_CODE_CLI_THIN_AUTHORSHIP/i.test(build.detail || '')
    if ((build.state === 'failed' && !stickyThin) || build.state === 'stalled') return
    await new Promise<void>((resolve) => {
      const t = setTimeout(resolve, intervalMs)
      const onAbort = () => {
        clearTimeout(t)
        resolve()
      }
      if (signal) {
        if (signal.aborted) {
          onAbort()
          return
        }
        signal.addEventListener('abort', onAbort, { once: true })
      }
    })
  }
}

export async function downloadProductPackage(
  sid: string,
  opts?: { asIs?: boolean },
): Promise<void> {
  const qs = opts?.asIs ? '?as_is=1' : ''
  const res = await fetch(`${API_BASE}/v1/sessions/${sid}/product/package${qs}`, {
    credentials: 'include',
    headers: authHeaders(false),
  })
  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new ApiError(res.status, txt || `export failed (${res.status})`)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  const cd = res.headers.get('content-disposition') || ''
  const m = cd.match(/filename="?([^"";]+)?"?/)
  a.href = url
  a.download = m ? m[1] : 'cerebrumdev-product.zip'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export interface BillingStatus {
  plan?: string
  subscription_status?: string
  trial_ends_at?: string | null
  trial_days_left?: number | null
  entitled?: boolean
  checkout_available?: boolean
  enforcement?: boolean
  [k: string]: unknown
}

/**
 * Factory access is paused only when the backend would actually refuse.
 *
 * The backend gate reads:
 *
 *     if not enforcement_enabled(): return principal   # off => always pass
 *     if not is_entitled(...):      raise 402
 *
 * but `entitled` in the status payload is computed from the subscription
 * alone and never consults enforcement. Pausing on `entitled` by itself meant
 * that with BILLING_ENFORCEMENT off the API served the account while this flag
 * hard-disabled the composer -- locked out of a UI whose backend was open.
 * Mirror the backend: enforcement off => never paused.
 *
 * `enforcement` undefined (older backend, or a status call that failed and
 * resolved to null) keeps the previous behaviour: paused iff entitled is
 * explicitly false.
 */
export function factoryAccessPaused(
  status: Pick<BillingStatus, 'entitled' | 'enforcement'> | null | undefined,
): boolean {
  if (status?.enforcement === false) return false
  return status?.entitled === false
}

export function trialDaysLeft(status: BillingStatus): number | null {
  if (typeof status.trial_days_left === 'number') return status.trial_days_left
  if (!status.trial_ends_at) return null
  const end = Date.parse(status.trial_ends_at)
  if (Number.isNaN(end)) return null
  return Math.max(0, Math.ceil((end - Date.now()) / 86_400_000))
}

export type SubscriptionDisplay = {
  planLabel: string
  statusLabel: string
  showTrialDays: boolean
  trialDaysLeft: number | null
  accessLabel: 'Active' | 'Paused'
  /** Which plan card is the live one — never both. */
  currentPlan: 'trial' | 'factory'
}

/**
 * One coherent read of /v1/billing/status.
 * The store keeps subscription_status='trialing' after the clock runs out
 * (entitled=false, trial_days_left=0). Do not paint that as a live trial.
 */
export function subscriptionDisplay(status: BillingStatus): SubscriptionDisplay {
  const raw = String(status.subscription_status ?? status.status ?? 'none')
    .toLowerCase()
    .replace(/_/g, ' ')
  const days = trialDaysLeft(status)
  const paused = factoryAccessPaused(status)
  const planFromApi = status.plan ? String(status.plan) : ''
  const expiredTrial = raw === 'trialing' && status.entitled === false

  if (expiredTrial) {
    return {
      planLabel: planFromApi || 'trial',
      statusLabel: 'expired',
      showTrialDays: false,
      trialDaysLeft: days,
      accessLabel: 'Paused',
      currentPlan: 'trial',
    }
  }

  let planLabel = planFromApi
  if (!planLabel) {
    if (raw === 'active') planLabel = 'factory'
    else if (raw === 'trialing') planLabel = 'trial'
    else planLabel = raw
  }

  const currentPlan: 'trial' | 'factory' =
    planLabel.toLowerCase() === 'factory' || raw === 'active' ? 'factory' : 'trial'

  return {
    planLabel,
    statusLabel: raw,
    showTrialDays: raw === 'trialing' && days !== null && days > 0,
    trialDaysLeft: days,
    accessLabel: paused ? 'Paused' : 'Active',
    currentPlan,
  }
}

export const billing = {
  status: async () => {
    const raw = await req<BillingStatus & { billing?: BillingStatus; ok?: boolean }>(
      'GET',
      '/v1/billing/status',
    )
    if (raw.billing && typeof raw.billing === 'object') {
      return { ...raw.billing, ok: raw.ok }
    }
    return raw
  },
  checkout: () =>
    req<{ url?: string; checkout_url?: string }>('POST', '/v1/billing/checkout', {}),
  portal: () => req<{ url?: string; portal_url?: string }>('POST', '/v1/billing/portal', {}),
}

export const domains = {
  list: () => req<{ domains?: unknown[] }>('GET', '/v1/domains/'),
}

/** Named /health blocker when Kimi Code CLI is on PATH but config.toml is missing. */
export const FACTORY_CODE_CLI_CREDENTIALS_MISSING = 'FACTORY_CODE_CLI_CREDENTIALS_MISSING'
/** Named /health blocker when config.toml exists but default_model is unusable. */
export const FACTORY_CODE_CLI_NO_MODEL = 'FACTORY_CODE_CLI_NO_MODEL'
/** Coder receipt / WRITER honesty when FACTORY_CODE_CLI exited non-zero. */
export const FACTORY_CODE_CLI_FAILED = 'FACTORY_CODE_CLI_FAILED'
/** Moonshot 429 / insufficient-balance / account-suspended CLI miss. */
export const FACTORY_CODE_CLI_BILLING = 'FACTORY_CODE_CLI_BILLING'

export type FactoryCodeCliProbe = {
  command?: string | null
  provider?: string | null
  available?: boolean
  resolved?: string | null
  credentials_file_present?: boolean
  deepseek_key_present?: boolean
  default_model?: string | null
  default_model_configured?: boolean
  requires_cli?: boolean
  requires_kimi_credentials?: boolean
  requires_deepseek_credentials?: boolean
  cursor_ba_available?: boolean
  blocker?: string | null
  error?: string
}

export type HealthStatus = {
  status?: string
  factory_code_cli?: FactoryCodeCliProbe
  [k: string]: unknown
}

/** Public /health — operators read factory_code_cli.blocker from here. */
export function getHealth(): Promise<HealthStatus> {
  return req<HealthStatus>('GET', '/health')
}
