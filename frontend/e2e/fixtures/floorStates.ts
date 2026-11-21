/**
 * Mocked Floor states for the phone layout specs (spec section 5, standing
 * check B). Every /v1, /health and /version request is answered here; no real
 * backend is called. Each state is one session id whose saved design, chat
 * history, intake and build status put the Floor in that state on load.
 *
 * The data is test data only (an invented contracts platform). Product code
 * never reads any of these values.
 */
import type { Page, Route } from '@playwright/test'
import type {
  BillingStatus,
  BuildStatus,
  DeclaredVertical,
  IntakeFields,
  IntakeState,
  ProductDesign,
  SessionInfo,
  UploadAllowed,
} from '../../src/api/factory'

export const FLOOR_STATES = [
  'new-session',
  'suggestion-pending',
  'feature-list-pending',
  'building',
  'fix-round',
  'stopped',
  'stalled',
  'finished-prototype',
  'finished-pilot',
] as const
export type FloorState = (typeof FLOOR_STATES)[number]

/** States in which a build card exists. */
export const BUILD_STATES: FloorState[] = [
  'building',
  'fix-round',
  'stopped',
  'stalled',
  'finished-prototype',
  'finished-pilot',
]

export const SID: Record<FloorState, string> = {
  'new-session': 'sess_state_new',
  'suggestion-pending': 'sess_state_suggest',
  'feature-list-pending': 'sess_state_draft',
  building: 'sess_state_building',
  'fix-round': 'sess_state_fixround',
  stopped: 'sess_state_stopped',
  stalled: 'sess_state_stalled',
  'finished-prototype': 'sess_state_okproto',
  'finished-pilot': 'sess_state_okpilot',
}

const EMAIL = 'e2e.phone@factory.dev'

/* ------------------------------ payloads ------------------------------ */

const BRIEF =
  'Build me a contracts platform for my contracting company — register contracts, track variation orders and payment certificates, with approvals.'
const ANSWER = 'We are in the UAE and we work in dirhams. A prototype is enough for now.'
const DRAFT_ASK =
  'Register contracts, raise variation orders and issue payment certificates, each with an approval step. Draft it.'
const ASKS =
  'Got it — a contracts platform. Three things I need before I draft it: which country do you operate in, which currency do you work in, and which build level do you want — prototype, light, pilot or production?'
const PROPOSES =
  'Thanks. I have put country AE, currency AED and build level prototype on the intake line as a proposal — press Confirm to store them, or Change to edit. Then tell me what the platform must do and I will draft the feature list.'
const CONFIRMED =
  'Confirmed: country AE, currency AED, build level prototype. Build level prototype — the build is done when the code passes (CODE_GREEN); the pilot suite and the Store gate do not run.'

interface Cap {
  id: string
  description: string
  strategy_hint: string
  block_ids?: string[]
}
const CAPS: Cap[] = [
  { id: 'contract_register', description: 'Register a contract with its parties, value and dates.', strategy_hint: 'GENERATE' },
  { id: 'contract_settings', description: 'Set the country, currency and tax defaults contracts are priced in.', strategy_hint: 'GENERATE' },
  { id: 'variation_orders', description: 'Raise a variation order against a contract and route it for approval.', strategy_hint: 'COMPOSE', block_ids: ['workflow', 'notification'] },
  { id: 'payment_certificates', description: 'Issue an interim payment certificate and track what is certified to date.', strategy_hint: 'COMPOSE', block_ids: ['document_engine', 'workflow'] },
  { id: 'audit', description: 'Audit trail of every change.', strategy_hint: 'REUSE', block_ids: ['audit'] },
]
function blueprint(name: string): Record<string, unknown> {
  return {
    product_name: name,
    vertical: 'construction',
    build_level: 'prototype',
    summary: 'Contract register, variation orders and payment certificates with an approval step.',
    drafting_mode: 'architect_llm',
    capabilities: CAPS,
  }
}
const INTAKE_BP = {
  users: { value: ['contracts managers', 'quantity surveyors'] },
  roles: { value: ['admin', 'approver', 'viewer'] },
  done_when: { value: ['A variation order cannot be approved by the person who raised it'] },
}
const DECLARED: IntakeFields = { vertical: 'construction', country: 'AE', currency: 'AED', build_level: 'prototype' }
const VERTICALS: DeclaredVertical[] = [
  { vertical: 'construction', kit: 'construction', build_ready: true },
  { vertical: 'retail', kit: 'retailops', build_ready: true },
]
const UPLOAD: UploadAllowed = {
  extensions: ['.pdf', '.docx', '.xlsx'],
  accept: '.pdf,.docx,.xlsx',
  max_files: 5,
  max_file_bytes: 10 * 1024 * 1024,
  entitled: true,
}
const BILLING: BillingStatus = {
  plan: 'trial',
  subscription_status: 'trialing',
  trial_days_left: 3,
  entitled: true,
  checkout_available: false,
  enforcement: false,
}
const PHASES = ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']
const LEVEL_PROTO = { build_level: 'prototype', stop_gate: 'CODE' }

const BUILD_WRITER: BuildStatus = {
  state: 'building',
  cycle: 'code',
  phases: PHASES,
  completed: ['COLLECTOR', 'CLONER'],
  phases_done: 2,
  phases_total: 5,
  current_phase: { id: 'WRITER', label: 'Platform manufacturer' },
  phase_index: 3,
  phase_total: 5,
  next_phase: { id: 'TESTER', label: 'Acceptance inspector' },
  phase_progress: { done: 2, total: 5, fraction: 0.4, stage: 'handlers' },
  last_event: 'wrote handler variation_orders',
  last_event_age_s: 8,
  stale: false,
  activity: 'wrote handler variation_orders',
  activity_log: [
    { role: 'writer', text: 'Reading the brief and the contracts of the bound blocks' },
    { role: 'writer', text: 'wrote handler contract_register' },
    { role: 'writer', text: 'wrote handler contract_settings' },
    { role: 'writer', text: 'wrote handler variation_orders' },
  ],
  coder_control: 'run',
}

const REWORK_DECISION = {
  class: 'REWORK',
  gate: 'WRITER',
  round_gate: 1,
  gate_budget: 1,
  round_build: 1,
  build_ceiling: 6,
  check: 'writer_contract',
  finding: "app/actions/contract_settings.py:47: the declared code is a literal -- read it from app.money_settings",
}

const BUILD_FIX_ROUND: BuildStatus = {
  ...BUILD_WRITER,
  phase_progress: { done: 1, total: 5, fraction: 0.2, stage: 'handlers' },
  last_event: 'rework round 1: rewriting contract_settings',
  activity: 'rework round 1: rewriting contract_settings',
  failure: {
    phase: 'WRITER',
    location: 'WRITER',
    reason: 'writer_contract',
    code: 'writer_contract',
    summary: 'WRITER was refused during the build; the full detail is in the build record.',
    ref: '7461cb639b',
  },
  decisions: [REWORK_DECISION],
}

const TIMEOUT =
  'codewhale_worker_failed: worker_timed_out: headless job exceeded its 1800s wall (dispatched with 1800.0s)'

const BUILD_STOPPED: BuildStatus = {
  state: 'failed',
  cycle: 'code',
  outcome: 'FAILED',
  pilot_ready: false,
  certified: false,
  detail: 'WRITER failed: ' + TIMEOUT,
  build_level: LEVEL_PROTO,
  level_grade: {
    level: 'SCAFFOLD',
    pilot_ready: false,
    founding_customer_ready: false,
    three_gate: { CODE: 'FAIL', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' },
  },
  phases: PHASES,
  completed: ['COLLECTOR', 'CLONER'],
  phases_done: 2,
  phases_total: 5,
  failure: {
    phase: 'WRITER',
    location: 'WRITER',
    outcome: 'failed',
    reason: 'codewhale_worker_failed',
    code: 'codewhale_worker_failed',
    summary: 'WRITER was refused during the build; the full detail is in the build record.',
    ref: '7461cb639b',
  },
  failed: { gate: 'WRITER', check: 'codewhale_worker_failed', finding: TIMEOUT },
  failed_label: `FAILED(WRITER, codewhale_worker_failed, ${TIMEOUT})`,
  next_continue:
    "Continue will resume this platform's branch at WRITER and send the writer the failing check codewhale_worker_failed: " +
    TIMEOUT,
  failure_narrative: {
    text:
      'What was tried: the run passed COLLECTOR and CLONER. It ran 1 rework round (WRITER/writer_contract). Where it stopped: WRITER. Why: WRITER failed: ' +
      TIMEOUT,
    source: 'ledger',
  },
  decisions: [REWORK_DECISION],
}

/** The server still says building, but the last event is 42 minutes old: the client calls it stalled. */
const BUILD_STALLED: BuildStatus = { ...BUILD_WRITER, last_event_age_s: 2520, stale: true }

const BUILD_OK_PROTO: BuildStatus = {
  state: 'succeeded',
  outcome: 'SUCCESS',
  cycle: 'code',
  pilot_ready: false,
  auto_pilot: false,
  build_level: LEVEL_PROTO,
  phases: PHASES,
  completed: PHASES,
  authorship: { artifacts: 24, agent_written: 11, templated: 13, action_py: 5 },
  level_grade: {
    level: 'CODE_GREEN',
    pilot_ready: false,
    founding_customer_ready: false,
    three_gate: { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' },
  },
}

const BUILD_OK_PILOT: BuildStatus = {
  state: 'succeeded',
  outcome: 'SUCCESS',
  cycle: 'pilot',
  pilot_ready: true,
  build_level: { build_level: 'pilot', stop_gate: 'STORE' },
  phases: PHASES,
  completed: PHASES,
  acceptance: { passed: 12, total: 12, ok: true },
  authorship: { artifacts: 24, agent_written: 8, templated: 16, action_py: 8 },
  level_grade: {
    level: 'STORE_GREEN',
    founding_customer_ready: false,
    pilot_ready: true,
    acceptance: { passed: 12, total: 12, ok: true },
    full_pilot: true,
    three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
  },
}

/* ------------------------------ sessions ------------------------------ */

interface SessionWorld {
  design: ProductDesign
  history: { role: string; content: string }[]
  intake: IntakeState
  build: BuildStatus | null
  productId: string
}

function emptySession(sid: string): SessionWorld {
  return { design: { session_id: sid }, history: [], intake: { declared: {}, proposal: null }, build: null, productId: 'contracts-hub' }
}

const TALK = [
  { role: 'user', content: BRIEF },
  { role: 'assistant', content: ASKS },
  { role: 'user', content: ANSWER },
  { role: 'assistant', content: PROPOSES },
]
const DRAFTED = [
  ...TALK,
  { role: 'user', content: '' },
  { role: 'assistant', content: CONFIRMED },
  { role: 'user', content: DRAFT_ASK },
  { role: 'assistant', content: 'Blueprint drafted: Contracts Hub (construction). Drafted by the architect LLM.' },
]

function builtSession(sid: string, build: BuildStatus): SessionWorld {
  return {
    design: {
      session_id: sid,
      brief: BRIEF,
      blueprint: blueprint('Contracts Hub'),
      blueprint_approved: true,
      intake_blueprint: INTAKE_BP,
      generation: { engine: 'runner', product_id: 'contracts-hub', triggered_by: 'approve' },
    },
    history: [
      ...DRAFTED,
      { role: 'user', content: '' },
      {
        role: 'assistant',
        content:
          'Build started for contracts-hub: the coding agent has taken over the floor and is writing 5 capability(ies) against the real block contracts.',
      },
    ],
    intake: { declared: { ...DECLARED }, proposal: null },
    build,
    productId: 'contracts-hub',
  }
}

function makeSessions(): Record<string, SessionWorld> {
  const suggest = emptySession(SID['suggestion-pending'])
  suggest.history = TALK
  suggest.intake = { declared: {}, proposal: { country: 'AE', currency: 'AED', build_level: 'prototype' } }
  const draft = emptySession(SID['feature-list-pending'])
  draft.design = {
    session_id: draft.design.session_id,
    brief: BRIEF,
    blueprint: blueprint('Contracts Hub'),
    blueprint_approved: false,
    intake_blueprint: INTAKE_BP,
  }
  draft.history = DRAFTED
  draft.intake = { declared: { ...DECLARED }, proposal: null }
  return {
    [SID['new-session']]: emptySession(SID['new-session']),
    [SID['suggestion-pending']]: suggest,
    [SID['feature-list-pending']]: draft,
    [SID.building]: builtSession(SID.building, BUILD_WRITER),
    [SID['fix-round']]: builtSession(SID['fix-round'], BUILD_FIX_ROUND),
    [SID.stopped]: builtSession(SID.stopped, BUILD_STOPPED),
    [SID.stalled]: builtSession(SID.stalled, BUILD_STALLED),
    [SID['finished-prototype']]: builtSession(SID['finished-prototype'], BUILD_OK_PROTO),
    [SID['finished-pilot']]: builtSession(SID['finished-pilot'], BUILD_OK_PILOT),
  }
}

function sse(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

/** Answer every request the Floor makes, from the fixtures above. */
export async function mockFloorStates(page: Page): Promise<void> {
  const by = makeSessions()
  const stageOf = (s: FloorState): SessionInfo['stage'] =>
    s === 'new-session' ? 'empty' : s === 'suggestion-pending' ? 'talking' : s === 'feature-list-pending' ? 'blueprint' : 'build'
  const sessions: SessionInfo[] = FLOOR_STATES.map((s) => ({ session_id: SID[s], title: s, stage: stageOf(s) }))
  await page.addInitScript((email) => {
    localStorage.removeItem('cerebrum.factory.token')
    localStorage.setItem('cerebrum.factory.email', email)
  }, EMAIL)
  await page.route(
    (url) => url.pathname.startsWith('/v1/') || url.pathname === '/health' || url.pathname === '/version',
    async (route: Route) => {
      const req = route.request()
      const p = new URL(req.url()).pathname
      const method = req.method()
      const json = (body: unknown, status = 200) =>
        route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
      if (p === '/health') {
        return json({
          status: 'ok',
          factory_code_cli: { available: true, credentials_file_present: true, requires_kimi_credentials: true },
        })
      }
      if (p === '/version') return json({})
      if (p === '/v1/auth/me') {
        return json({ email: EMAIL, email_verified: true, account_id: 'acct_e2e_phone' })
      }
      if (p === '/v1/billing/status') return json({ ok: true, billing: BILLING })
      if (/^\/v1\/domains\/?$/.test(p)) return json({ domains: [] })
      if (/^\/v1\/sessions\/?$/.test(p)) {
        return json(method === 'GET' ? { sessions } : { session_id: SID['new-session'] })
      }
      const m = p.match(/^\/v1\/sessions\/([^/]+)(\/.*)?$/)
      if (m) {
        const sid = decodeURIComponent(m[1])
        const rest = m[2] ?? ''
        const w = by[sid] ?? (by[sid] = emptySession(sid))
        if (rest === '') return json({ session_id: sid, chat_history: w.history })
        if (rest === '/product') return json(w.design)
        if (rest === '/product/verticals') {
          return json({
            verticals: VERTICALS,
            chosen: w.intake.declared.vertical ?? null,
            default: 'product',
            country: w.intake.declared.country ?? null,
            currency: w.intake.declared.currency ?? null,
            build_level: w.intake.declared.build_level ?? null,
            intake: w.intake,
          })
        }
        if (rest === '/product/build-status') {
          return json({ ok: true, product_id: w.productId, build: w.build ?? { state: 'not_started' } })
        }
        if (rest === '/product/coder-control') return json({ ok: true, control: { action: 'run' } })
        if (rest === '/upload/allowed') return json(UPLOAD)
        if (rest === '/chat') {
          const summary = 'Noted.'
          return route.fulfill({
            status: 200,
            contentType: 'text/event-stream',
            body:
              sse('intake', w.intake) +
              sse('info', { sse: 'info', ok: true, summary }) +
              sse('delta', summary) +
              sse('done', ''),
          })
        }
      }
      return json({ detail: `not mocked: ${method} ${p}` }, 404)
    },
  )
}
