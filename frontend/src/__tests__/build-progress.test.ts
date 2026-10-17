import { describe, expect, it } from 'vitest'
import type { BuildStatus } from '../api/factory'
import {
  CLIENT_STALL_AFTER_S,
  eventAgeSeconds,
  exportAffordance,
  factoryCodeCliHonesty,
  factoryCodeCliStatusTitle,
  cliKeptHandlerCount,
  formatFinishedAuthorship,
  formatHeartbeat,
  formatPhaseCounts,
  formatPhaseHeadline,
  ACCEPTANCE_KK,
  ACCEPTANCE_REQUIRED,
  formatAcceptanceScore,
  isAcceptanceKk,
  isAcceptancePendingPrototype,
  FULL_PILOT_MIN_AUTHORED_ACTIONS,
  fullPilotAuthorshipCount,
  fullPilotAuthorshipNeed,
  honestLevel,
  isAuthoritativePilotReady,
  isBelowFullPilotAuthorshipFloor,
  isCoderCliFailed,
  isPilotZipReady,
  isScaffoldClaim,
  isThinTemplatedAuthorship,
  shouldDemoteFounding,
  levelGradeLabel,
  phaseBarFraction,
  nRequiredFromProductInputs,
  platformsLeadCopy,
  preferHonestBuild,
  reevaluateStickyThinAuthorship,
  shouldRefuseExport,
  outcomeFailed,
  stampBuildObservation,
  threeGateEntries,
  withClientStall,
  withExportHonesty,
  withResolvedNRequired,
} from '../buildProgress'

const cloner: BuildStatus = {
  state: 'building',
  current_phase: { id: 'CLONER', label: 'Block stocker' },
  phase_index: 2,
  phase_total: 5,
  phases_done: 1,
  phases_total: 5,
  next_phase: { id: 'WRITER', label: 'Platform manufacturer' },
  phase_progress: { done: 3, total: 7, fraction: 0.429, stage: 'blocks' },
  last_event: 'cloned audit',
  last_event_age_s: 12,
  stale: false,
}

describe('build progress copy', () => {
  it('names the current phase instead of a bare 2/5', () => {
    expect(formatPhaseHeadline(cloner)).toBe('RESEARCHER 2/5')
    expect(formatPhaseCounts(cloner)).toBe('3/7 blocks')
    expect(phaseBarFraction(cloner)).toBeCloseTo(0.429)
    expect(formatHeartbeat(cloner)).toBe('still working · 12s ago')
  })

  it('marks a quiet build so the Floor can say it may be stuck', () => {
    const quiet: BuildStatus = {
      ...cloner,
      stale: true,
      last_event_age_s: 240,
    }
    expect(formatHeartbeat(quiet)).toMatch(/quiet/)
    expect(formatHeartbeat(quiet)).toMatch(/4 min/)
    expect(formatHeartbeat(quiet)).toMatch(/no new progress/)
    expect(formatHeartbeat(quiet)).not.toMatch(/may still be running/)
  })

  it('bounds an in-flight coder call and does not claim handler progress', () => {
    const calling: BuildStatus = {
      ...cloner,
      last_event: 'calling coder LLM for clinical_treatment_notes',
      last_event_age_s: 12,
      model_call_in_progress: true,
      model_call_deadline_s: 390,
      phase_progress: undefined,
    }
    expect(formatHeartbeat(calling)).toMatch(/waiting on coder LLM/)
    expect(formatHeartbeat(calling)).not.toMatch(/still working/)
    expect(formatPhaseCounts(calling)).toBeNull()

    const staleCall: BuildStatus = {
      ...calling,
      stale: true,
      last_event_age_s: 240,
    }
    expect(formatHeartbeat(staleCall)).toMatch(/still inside 390s watchdog/)
    expect(formatHeartbeat(staleCall)).not.toMatch(/may still be running/)

    const overdue: BuildStatus = {
      ...calling,
      last_event_age_s: 400,
      stale: true,
    }
    expect(formatHeartbeat(overdue)).toMatch(/coder LLM timed out/)
    expect(formatHeartbeat(overdue)).toMatch(/deadline 390s/)
    expect(formatHeartbeat(overdue)).not.toMatch(/may still be running/)

    // Live sess_97a1bc6525924e8b: 1965s elapsed vs 480s deadline.
    const liveOvershoot: BuildStatus = {
      ...calling,
      last_event_age_s: 1965,
      model_call_deadline_s: 480,
      stale: true,
    }
    expect(formatHeartbeat(liveOvershoot)).toMatch(/coder LLM timed out after 1965s/)
    expect(formatHeartbeat(liveOvershoot)).toMatch(/deadline 480s/)
    expect(formatHeartbeat(liveOvershoot)).not.toMatch(/may still be running/)

    // Live sess_ab446de: 1085s elapsed vs 480s deadline (~2.3× after #297).
    const liveCloseOvershoot: BuildStatus = {
      ...calling,
      last_event_age_s: 1085,
      model_call_deadline_s: 480,
      stale: true,
    }
    expect(formatHeartbeat(liveCloseOvershoot)).toMatch(/coder LLM timed out after 1085s/)
    expect(formatHeartbeat(liveCloseOvershoot)).toMatch(/deadline 480s/)
    expect(formatHeartbeat(liveCloseOvershoot)).not.toMatch(/may still be running/)
    const failed = withClientStall(overdue)
    expect(failed?.state).toBe('failed')
    expect(failed?.detail).toMatch(/coder LLM timed out/)

    // Live MakersHub Leeds ~510s vs the old 480s wall. Production default
    // is 40 min — 510s is still coding, not STOPPED.
    const live510: BuildStatus = {
      ...calling,
      last_event_age_s: 510,
      stale: true,
      model_call_deadline_s: undefined,
    }
    expect(formatHeartbeat(live510)).toMatch(/still inside 2400s watchdog/)
    expect(formatHeartbeat(live510)).not.toMatch(/timed out/)
    expect(withClientStall(live510)?.state).toBe('building')

    const longWrite: BuildStatus = {
      ...calling,
      last_event_age_s: 2100,
      stale: true,
      model_call_deadline_s: 2400,
    }
    expect(formatHeartbeat(longWrite)).toMatch(/still inside 2400s watchdog/)
    expect(withClientStall(longWrite)?.state).toBe('building')
  })

  it('advances relative age from last_event_at so a frozen server snapshot cannot stall the ticker', () => {
    const at = '2026-09-03T16:00:00.000Z'
    const t0 = Date.parse(at)
    const build: BuildStatus = {
      ...cloner,
      last_event_at: at,
      last_event_age_s: 120, // stale snapshot that would stay "2 min ago"
      stale: false,
    }
    expect(eventAgeSeconds(build, t0 + 120_000)).toBeCloseTo(120, 0)
    expect(formatHeartbeat(build, t0 + 120_000)).toBe('still working · 2 min ago')
    expect(formatHeartbeat(build, t0 + 420_000)).toMatch(/quiet for 7 min/)
  })

  it('advances a frozen last_event_age_s across polls via client observation stamp', () => {
    const t0 = Date.parse('2026-09-03T16:00:00.000Z')
    const poll1 = stampBuildObservation(
      {
        ...cloner,
        last_event: 'wrote handler tenancy_application_pipeline',
        last_event_age_s: 120, // field recheck: stuck at "2 min ago"
        last_event_at: null,
      },
      null,
      t0,
    )
    // Same ledger event, same frozen age_s — must keep the first stamp.
    const poll2 = stampBuildObservation(
      {
        ...cloner,
        last_event: 'wrote handler tenancy_application_pipeline',
        last_event_age_s: 120,
        last_event_at: null,
      },
      poll1,
      t0 + 60_000,
    )
    expect(poll2.client_observed_at_ms).toBe(t0)
    expect(eventAgeSeconds(poll2, t0 + 300_000)).toBeCloseTo(420, 0) // 120 + 300s
    expect(formatHeartbeat(poll2, t0 + 300_000)).toMatch(/quiet for 7 min/)
    const stalled = withClientStall(poll2, t0 + (CLIENT_STALL_AFTER_S + 30) * 1000)
    expect(stalled?.state).toBe('stalled')
  })

  it('re-stamps when the ledger event identity changes', () => {
    const t0 = Date.parse('2026-09-03T16:00:00.000Z')
    const first = stampBuildObservation(
      { ...cloner, last_event: 'handler_a', last_event_age_s: 120 },
      null,
      t0,
    )
    const next = stampBuildObservation(
      { ...cloner, last_event: 'handler_b', last_event_age_s: 5 },
      first,
      t0 + 60_000,
    )
    expect(next.client_observed_at_ms).toBe(t0 + 60_000)
    expect(eventAgeSeconds(next, t0 + 65_000)).toBeCloseTo(10, 0)
  })

  it('labels a handler-wave reset so 3/5 → 1/5 does not read as a regression', () => {
    const t0 = Date.parse('2026-09-03T16:00:00.000Z')
    const atThree = stampBuildObservation(
      {
        ...cloner,
        last_event: 'tenancy_application_pipeline',
        phase_progress: { done: 3, total: 5, fraction: 0.6, stage: 'handlers' },
      },
      null,
      t0,
    )
    expect(formatPhaseCounts(atThree)).toBe('3/5 handlers')
    const wave = stampBuildObservation(
      {
        ...cloner,
        last_event: 'unit_registry_and_vacancy_tracking',
        phase_progress: { done: 1, total: 5, fraction: 0.2, stage: 'handlers' },
      },
      atThree,
      t0 + 60_000,
    )
    expect(wave.client_wave_reset).toBe(true)
    expect(formatPhaseCounts(wave)).toBe('1/5 handlers (new pass)')
  })

  it('promotes a forever-building snapshot to stalled after the stall window', () => {
    const at = '2026-09-03T16:00:00.000Z'
    const t0 = Date.parse(at)
    const build: BuildStatus = {
      ...cloner,
      last_event_at: at,
      last_event_age_s: 120,
    }
    const stalled = withClientStall(build, t0 + (CLIENT_STALL_AFTER_S + 60) * 1000)
    expect(stalled?.state).toBe('stalled')
    expect(stalled?.detail).toMatch(/no build activity/)
  })

  it('CLI keep-path authorship is not coder-idle / 0-artifact templated copy', () => {
    const keepPath = {
      artifacts: 27,
      agent_written: 4,
      templated: 23,
      kept_handler_ids: [
        'unit_registry_and_vacancy_tracking',
        'viewing_management',
        'maintenance_issue_tracking',
        'tenancy_application_pipeline',
      ],
    }
    expect(cliKeptHandlerCount(keepPath)).toBe(4)
    expect(
      formatFinishedAuthorship(keepPath, { pilotReady: true, demoteFounding: true }),
    ).toBe('Pilot-ready — 4 artifacts; 23 templated')
    expect(formatFinishedAuthorship(keepPath, { pilotReady: true })).not.toMatch(
      /coder idle or no LLM key/,
    )
    expect(formatFinishedAuthorship(keepPath)).not.toMatch(/wrote 0 artifacts/)
    // Residual backend 0 + kept_handler_ids still must not paint idle.
    const residualZero = { artifacts: 27, agent_written: 0, templated: 27, kept_handler_ids: keepPath.kept_handler_ids }
    expect(formatFinishedAuthorship(residualZero, { pilotReady: true })).not.toMatch(
      /coder idle or no LLM key/,
    )
    expect(formatFinishedAuthorship(residualZero, { pilotReady: true })).toMatch(
      /4 artifacts/,
    )
  })

  it('SUCCESS copy is finished only when pilot-ready, not hang-looking 22 of 28', () => {
    expect(
      formatFinishedAuthorship(
        { artifacts: 28, agent_written: 22, templated: 6 },
        { pilotReady: true },
      ),
    ).toBe('Finished — 22 artifacts; 6 templated')
    expect(
      formatFinishedAuthorship({ artifacts: 28, agent_written: 22, templated: 6 }),
    ).toBe('Code-cycle prototype — 22 artifacts; 6 templated. Not yet pilot-ready')
    expect(
      formatFinishedAuthorship({ artifacts: 28, agent_written: 22, templated: 6 }),
    ).not.toMatch(/22 of 28/)
    expect(
      formatFinishedAuthorship(
        { artifacts: 28, agent_written: 22, templated: 6 },
        { pilotReady: false },
      ),
    ).not.toMatch(/Finished/)
  })

  it('outcomeFailed scores named ledger outcomes, not success or absence', () => {
    expect(outcomeFailed({ outcome: 'FAILED_GATE' } as BuildStatus)).toBe(true)
    expect(outcomeFailed({ outcome: 'FAILED_BUDGET_SPENT' } as BuildStatus)).toBe(true)
    expect(outcomeFailed({ outcome: 'SUCCESS' } as BuildStatus)).toBe(false)
    expect(outcomeFailed({ outcome: 'HANDOFF_TO_N3' } as BuildStatus)).toBe(false)
    expect(outcomeFailed({} as BuildStatus)).toBe(false)
    expect(outcomeFailed(null)).toBe(false)
  })

  it('unreadable ledger is failed honesty — never Building / writing', () => {
    const torn = {
      state: 'unknown' as const,
      detail: "LEDGER_UNREADABLE: build_ledger.jsonl:4561 is not a readable ledger event: 'seq'",
    }
    expect(withClientStall(torn)?.state).toBe('failed')
    expect(platformsLeadCopy(torn, true)).toMatch(/unreadable ledger/)
    expect(platformsLeadCopy(torn, true)).toMatch(/Build failed — no certified download/)
    expect(platformsLeadCopy(torn, true)).not.toMatch(/writing this platform/)
    expect(exportAffordance(torn)).toMatchObject({
      label: 'Download as-is (failed gates)',
      disabled: false,
      ghost: true,
      asIs: true,
    })
  })

  it('platformsLeadCopy never invites Download when export is not pilot-ready', () => {
    expect(platformsLeadCopy(null, false)).not.toMatch(/Download the export/i)
    expect(platformsLeadCopy({ state: 'building' }, true)).not.toMatch(/Download the export/i)
    expect(platformsLeadCopy({ state: 'failed', pilot_ready: false }, true)).toMatch(
      /Build failed — no certified download/,
    )
    expect(platformsLeadCopy({ state: 'failed', pilot_ready: false }, true)).not.toMatch(
      /Download the export/i,
    )
    expect(platformsLeadCopy({ state: 'stalled' }, true)).toMatch(/Build stalled — no certified download/)
    expect(platformsLeadCopy({ state: 'succeeded', pilot_ready: false }, true)).not.toMatch(
      /Download the export/i,
    )
    expect(platformsLeadCopy({ state: 'succeeded', pilot_ready: true }, true)).not.toMatch(
      /Download the export/,
    )
    expect(platformsLeadCopy({ state: 'succeeded', pilot_ready: true }, true)).not.toMatch(
      /build failed/,
    )
    expect(platformsLeadCopy({ state: 'succeeded', pilot_ready: true }, true)).toMatch(
      /Acceptance is 0\/12/,
    )
    expect(
      platformsLeadCopy({ state: 'succeeded', pilot_ready: true, acceptance: ACCEPTANCE_KK }, true),
    ).toMatch(/Download the export/)
  })

  it('factoryCodeCliHonesty names the DeepSeek credential, not a vague credentials string', () => {
    expect(factoryCodeCliHonesty(null)).toBeNull()
    expect(
      factoryCodeCliHonesty({
        available: true,
        credentials_file_present: true,
      }),
    ).toBeNull()
    expect(
      factoryCodeCliHonesty({
        available: true,
        credentials_file_present: false,
        requires_kimi_credentials: false,
      }),
    ).toBeNull()
    const named = factoryCodeCliHonesty({
      available: true,
      credentials_file_present: false,
      blocker: 'FACTORY_CODE_CLI_CREDENTIALS_MISSING',
    })
    expect(named).toMatch(/coding agent has no credentials/)
    expect(named).toMatch(/FACTORY_CODE_CLI_CREDENTIALS_MISSING/)
    expect(named).toMatch(/DEEPSEEK_API_KEY/)
    expect(named).toMatch(/FACTORY_CODEWHALE_WRITER=1/)
    const fileOnly = factoryCodeCliHonesty({
      available: true,
      credentials_file_present: false,
      requires_kimi_credentials: true,
    })
    expect(fileOnly).toMatch(/FACTORY_CODE_CLI_CREDENTIALS_MISSING/)
    expect(fileOnly).toMatch(/DEEPSEEK_API_KEY/)
    const deepseek = factoryCodeCliHonesty({
      available: true,
      credentials_file_present: false,
      requires_kimi_credentials: false,
      requires_deepseek_credentials: true,
      blocker: 'FACTORY_CODE_CLI_CREDENTIALS_MISSING',
    })
    expect(deepseek).toMatch(/DEEPSEEK_API_KEY/)
    expect(deepseek).toMatch(/FACTORY_CODE_CLI_CREDENTIALS_MISSING/)
    expect(deepseek).toMatch(/OpenRouter \(OPENROUTER_API_KEY\) is the fallback/)
    expect(factoryCodeCliStatusTitle(deepseek)).toBe('Coding agent credentials missing')
  })

  it('factoryCodeCliHonesty names missing default_model as FACTORY_CODE_CLI_NO_MODEL', () => {
    const named = factoryCodeCliHonesty({
      available: true,
      credentials_file_present: true,
      default_model_configured: false,
      blocker: 'FACTORY_CODE_CLI_NO_MODEL',
    })
    expect(named).toMatch(/FACTORY_CODE_CLI_NO_MODEL/)
    expect(named).toMatch(/default_model/)
    expect(named).toMatch(/DEEPSEEK_API_KEY/)
    expect(named).not.toMatch(/FACTORY_CODE_CLI_CREDENTIALS_MISSING/)
    const fileOnly = factoryCodeCliHonesty({
      available: true,
      credentials_file_present: true,
      default_model_configured: false,
      requires_kimi_credentials: true,
    })
    expect(fileOnly).toMatch(/FACTORY_CODE_CLI_NO_MODEL/)
    expect(factoryCodeCliStatusTitle(named)).toBe('Coding agent has no model')
    expect(
      factoryCodeCliStatusTitle({
        blocker: 'FACTORY_CODE_CLI_CREDENTIALS_MISSING',
        credentials_file_present: false,
      }),
    ).toBe('Coding agent credentials missing')
  })

  it('factoryCodeCliHonesty is silent when no CLI credential is required', () => {
    expect(
      factoryCodeCliHonesty({
        available: true,
        credentials_file_present: false,
        blocker: 'FACTORY_CODE_CLI_CREDENTIALS_MISSING',
        requires_cli: false,
      }),
    ).toBeNull()
    expect(
      factoryCodeCliHonesty({
        available: true,
        credentials_file_present: false,
        requires_cli: false,
      }),
    ).toBeNull()
    expect(
      factoryCodeCliHonesty({
        available: false,
        credentials_file_present: false,
        requires_cli: false,
      }),
    ).toBeNull()
  })

  it('gold Download is only for a Store-green success', () => {
    expect(exportAffordance({ state: 'stalled', detail: 'gone' })).toMatchObject({
      label: 'Download as-is (failed gates)',
      disabled: false,
      ghost: true,
      asIs: true,
    })
    expect(exportAffordance({ state: 'failed', pilot_ready: false })).toMatchObject({
      label: 'Download as-is (failed gates)',
      disabled: false,
      ghost: true,
      asIs: true,
    })
    expect(
      exportAffordance({ state: 'succeeded', pilot_ready: false }),
    ).toMatchObject({
      label: 'Download code-cycle prototype (.zip)',
      disabled: false,
      ghost: true,
    })
    expect(
      exportAffordance({ state: 'succeeded', pilot_ready: true, acceptance: ACCEPTANCE_KK }),
    ).toEqual({
      label: 'Download platform export (.zip)',
      disabled: false,
      ghost: false,
    })
    expect(exportAffordance({ state: 'building' })).toEqual({
      label: 'Building…',
      disabled: true,
      ghost: true,
    })
    expect(
      exportAffordance({
        state: 'building',
        pilot_ready: true,
        level_grade: { level: 'FOUNDING_CUSTOMER_READY', founding_customer_ready: true },
      }),
    ).toEqual({
      label: 'Building…',
      disabled: true,
      ghost: true,
    })
    expect(
      isPilotZipReady({
        state: 'building',
        pilot_ready: true,
        level_grade: { level: 'STORE_GREEN', founding_customer_ready: false },
      }),
    ).toBe(false)
  })

  it('honestLevel fail-closes Store-green / founding when pilot_ready is false', () => {
    expect(
      honestLevel({
        state: 'succeeded',
        cycle: 'code',
        pilot_ready: false,
        level_grade: {
          level: 'CODE_GREEN',
          founding_customer_ready: false,
          three_gate: { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' },
        },
      }),
    ).toBe('CODE_GREEN')
    expect(
      honestLevel({
        state: 'succeeded',
        cycle: 'pilot',
        pilot_ready: true,
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
        acceptance: ACCEPTANCE_KK,
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
          three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
        },
      }),
    ).toBe('FOUNDING_CUSTOMER_READY')
    expect(
      honestLevel({
        state: 'succeeded',
        cycle: 'pilot',
        pilot_ready: false,
        level_grade: {
          level: 'FOUNDING_CUSTOMER_READY',
          founding_customer_ready: true,
        },
      }),
    ).toBe('CODE_GREEN')
    expect(
      honestLevel({
        state: 'failed',
        cycle: 'pilot',
        pilot_ready: false,
        level_grade: { level: 'SCAFFOLD', three_gate: { PRODUCT: 'FAIL' } },
      }),
    ).toBe('SCAFFOLD')
    expect(isPilotZipReady({ state: 'succeeded', pilot_ready: false, cycle: 'code' })).toBe(
      false,
    )
    expect(
      isPilotZipReady({
        state: 'succeeded',
        pilot_ready: true,
        authorship: { artifacts: 28, agent_written: 22, templated: 6 },
        acceptance: ACCEPTANCE_KK,
        level_grade: { level: 'FOUNDING_CUSTOMER_READY', founding_customer_ready: true },
      }),
    ).toBe(true)
    expect(levelGradeLabel('CODE_GREEN')).toBe('Code-green (prototype)')
    expect(levelGradeLabel('CODE_GREEN', false)).toBe('Code-cycle prototype')
    expect(
      threeGateEntries({
        state: 'succeeded',
        level_grade: { three_gate: { CODE: 'PASS', PRODUCT: 'NOT_RUN', STORE: 'NOT_RUN' } },
      }),
    ).toEqual([
      { name: 'CODE', verdict: 'PASS' },
      { name: 'PRODUCT', verdict: 'NOT RUN' },
      { name: 'STORE', verdict: 'NOT RUN' },
    ])
    expect(
      exportAffordance({
        state: 'succeeded',
        pilot_ready: false,
        level_grade: { level: 'FOUNDING_CUSTOMER_READY', founding_customer_ready: true },
      }),
    ).toMatchObject({
      label: 'Download code-cycle prototype (.zip)',
      ghost: true,
    })
  })

  it('CLI-failed + thin authorship refuses Store-green Download', () => {
    // Live sess_c220986f67914681 / 1206 VetCare: 1 agent-written is below
    // the full-pilot floor. #338 keep-path still demotes founding, but
    // gold Download is no longer honest.
    const vetCareKeepPath: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: { artifacts: 24, agent_written: 1, templated: 23, action_py: 1 },
      level_grade: {
        level: 'FOUNDING_CUSTOMER_READY',
        founding_customer_ready: true,
        pilot_ready: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
      coder_receipt: {
        ok: false,
        blocker: 'FACTORY_CODE_CLI_FAILED',
        detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
      },
    }
    expect(FULL_PILOT_MIN_AUTHORED_ACTIONS).toBe(5)
    expect(isCoderCliFailed(vetCareKeepPath)).toBe(true)
    expect(isThinTemplatedAuthorship(vetCareKeepPath.authorship)).toBe(true)
    expect(shouldDemoteFounding(vetCareKeepPath)).toBe(true)
    expect(isBelowFullPilotAuthorshipFloor(vetCareKeepPath)).toBe(true)
    expect(shouldRefuseExport(vetCareKeepPath)).toBe(true)
    expect(honestLevel(vetCareKeepPath)).toBe('CODE_GREEN')
    expect(honestLevel(vetCareKeepPath)).not.toBe('STORE_GREEN')
    expect(isPilotZipReady(vetCareKeepPath)).toBe(false)
    expect(exportAffordance(vetCareKeepPath)).toMatchObject({
      label: 'Export (.zip) — below full-pilot authorship floor',
      disabled: true,
      ghost: true,
    })
    expect(platformsLeadCopy(vetCareKeepPath, true)).toMatch(/full-pilot floor/)
    expect(platformsLeadCopy(vetCareKeepPath, true)).not.toMatch(/Download the export/)
    expect(
      formatFinishedAuthorship(vetCareKeepPath.authorship, {
        pilotReady: isPilotZipReady(vetCareKeepPath),
        demoteFounding: shouldDemoteFounding(vetCareKeepPath),
      }),
    ).not.toMatch(/Finished/)
  })

  it('CLI-failed keep-path with ≥5 authored actions still allows Export', () => {
    const billedButAuthored: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: { artifacts: 24, agent_written: 6, templated: 18, action_py: 6 },
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'STORE_GREEN',
        founding_customer_ready: false,
        pilot_ready: true,
        full_pilot: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
      coder_receipt: {
        ok: false,
        blocker: 'FACTORY_CODE_CLI_BILLING',
        honesty_class: 'FACTORY_CODE_CLI_FAILED',
        detail: 'FACTORY_CODE_CLI_BILLING: 429 — insufficient balance',
      },
    }
    expect(isBelowFullPilotAuthorshipFloor(billedButAuthored)).toBe(false)
    expect(shouldRefuseExport(billedButAuthored)).toBe(false)
    expect(honestLevel(billedButAuthored)).toBe('STORE_GREEN')
    expect(isPilotZipReady(billedButAuthored)).toBe(true)
    expect(exportAffordance(billedButAuthored)).toEqual({
      label: 'Download platform export (.zip)',
      disabled: false,
      ghost: false,
    })
    expect(levelGradeLabel('STORE_GREEN')).toBe('Store-green')
    expect(levelGradeLabel('STORE_GREEN', false)).toBe('Pilot-ready')
  })

  it('authorship-only Store-green does not enable Export', () => {
    const authoredOnly: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: { artifacts: 24, agent_written: 6, templated: 18, action_py: 6 },
      acceptance: { passed: 5, total: ACCEPTANCE_REQUIRED, ok: false },
      level_grade: {
        level: 'STORE_GREEN',
        founding_customer_ready: false,
        pilot_ready: true,
        full_pilot: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(isBelowFullPilotAuthorshipFloor(authoredOnly)).toBe(false)
    expect(shouldRefuseExport(authoredOnly)).toBe(true)
    expect(isPilotZipReady(authoredOnly)).toBe(false)
    expect(honestLevel(authoredOnly)).toBe('CODE_GREEN')
    expect(formatAcceptanceScore(authoredOnly)).toBe('5/12')
    expect(exportAffordance(authoredOnly)).toMatchObject({
      label: 'Export (.zip) — acceptance 5/12',
      disabled: true,
      ghost: true,
    })
    expect(isAcceptancePendingPrototype(authoredOnly)).toBe(true)
    expect(isAcceptanceKk(authoredOnly)).toBe(false)
    expect(platformsLeadCopy(authoredOnly, true)).toMatch(/Acceptance is 5\/12/)
    expect(platformsLeadCopy(authoredOnly, true)).not.toMatch(/build failed/)
    expect(platformsLeadCopy(authoredOnly, true)).not.toMatch(/Download the export/)
  })

  it('live sess_4591d5cc photograph is acceptance-pending, not a failed build', () => {
    const liveGolden: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      cycle: 'pilot',
      auto_pilot: true,
      pilot_ready: true,
      acceptance: { passed: 0, total: 12, ok: false, missing: true },
      authorship: { artifacts: 27, agent_written: 6, templated: 21 },
      level_grade: {
        level: 'CODE_GREEN',
        founding_customer_ready: false,
        pilot_ready: false,
        full_pilot: false,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(formatAcceptanceScore(liveGolden)).toBe('0/12')
    expect(isAcceptancePendingPrototype(liveGolden)).toBe(true)
    expect(shouldRefuseExport(liveGolden)).toBe(true)
    expect(isPilotZipReady(liveGolden)).toBe(false)
    expect(platformsLeadCopy(liveGolden, true)).toMatch(/Acceptance is 0\/12/)
    expect(platformsLeadCopy(liveGolden, true)).toMatch(/not a failed build/)
    expect(platformsLeadCopy(liveGolden, true)).not.toMatch(/build failed/)
    expect(exportAffordance(liveGolden)).toMatchObject({
      label: 'Export (.zip) — acceptance 0/12',
      disabled: true,
      ghost: true,
    })
  })

  it('enables Export once acceptance is k/k and the zip is pilot-ready', () => {
    const ready: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: { artifacts: 24, agent_written: 6, templated: 18, action_py: 6 },
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'STORE_GREEN',
        founding_customer_ready: false,
        pilot_ready: true,
        full_pilot: true,
        acceptance: ACCEPTANCE_KK,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(isAcceptancePendingPrototype(ready)).toBe(false)
    expect(isAcceptanceKk(ready)).toBe(true)
    expect(shouldRefuseExport(ready)).toBe(false)
    expect(isPilotZipReady(ready)).toBe(true)
    expect(exportAffordance(ready)).toEqual({
      label: 'Download platform export (.zip)',
      disabled: false,
      ghost: false,
    })
    expect(platformsLeadCopy(ready, true)).toMatch(/Download the export/)
    expect(platformsLeadCopy(ready, true)).not.toMatch(/build failed/)
  })

  it('FACTORY_CODE_CLI_BILLING honesty_class demotes founding and keeps Export when floor holds', () => {
    const billingKeepPath: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: { artifacts: 24, agent_written: 6, templated: 18, action_py: 6 },
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'FOUNDING_CUSTOMER_READY',
        founding_customer_ready: true,
        pilot_ready: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
      coder_receipt: {
        ok: false,
        blocker: 'FACTORY_CODE_CLI_BILLING',
        honesty_class: 'FACTORY_CODE_CLI_FAILED',
        detail: 'FACTORY_CODE_CLI_BILLING: 429 — insufficient balance',
      },
    }
    expect(isCoderCliFailed(billingKeepPath)).toBe(true)
    expect(shouldDemoteFounding(billingKeepPath)).toBe(true)
    expect(isAuthoritativePilotReady(billingKeepPath)).toBe(true)
    expect(shouldRefuseExport(billingKeepPath)).toBe(false)
    expect(honestLevel(billingKeepPath)).toBe('STORE_GREEN')
    expect(exportAffordance(billingKeepPath)).toMatchObject({
      label: 'Download platform export (.zip)',
      disabled: false,
      ghost: false,
    })
  })

  it('thin templated authorship below the floor refuses Store-green Download', () => {
    const thinKeep: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      authorship: { artifacts: 24, agent_written: 1, templated: 23 },
      level_grade: {
        level: 'FOUNDING_CUSTOMER_READY',
        founding_customer_ready: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(isCoderCliFailed(thinKeep)).toBe(false)
    expect(isThinTemplatedAuthorship(thinKeep.authorship)).toBe(true)
    expect(shouldDemoteFounding(thinKeep)).toBe(true)
    expect(honestLevel(thinKeep)).toBe('CODE_GREEN')
    expect(shouldRefuseExport(thinKeep)).toBe(true)
    expect(isPilotZipReady(thinKeep)).toBe(false)
  })

  it('sess_cec9a 1449 photograph: 6 written / 18 templated / action_py=3 refuses Store-green Download', () => {
    // Live 2026-09-07: /floor/sess_cec9a1345b2049bb is honest (Code-green,
    // export refused). Legacy ?session= painted Finished / Store-green /
    // Download enabled from the same API truth (CODE_GREEN, pilot_ready=false,
    // package 409 FACTORY_CODE_CLI_THIN_AUTHORSHIP).
    const sessCec9aPhoto: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      cycle: 'pilot',
      pilot_ready: false,
      authorship: {
        artifacts: 24,
        agent_written: 6,
        templated: 18,
        action_py: 3,
        agent_artifacts: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
        cli_authored_ids: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
      },
      level_grade: {
        level: 'CODE_GREEN',
        founding_customer_ready: false,
        pilot_ready: false,
        full_pilot: false,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(fullPilotAuthorshipCount(sessCec9aPhoto)).toBe(3)
    expect(isBelowFullPilotAuthorshipFloor(sessCec9aPhoto)).toBe(true)
    expect(honestLevel(sessCec9aPhoto)).toBe('CODE_GREEN')
    expect(isPilotZipReady(sessCec9aPhoto)).toBe(false)
    expect(shouldRefuseExport(sessCec9aPhoto)).toBe(true)
    expect(exportAffordance(sessCec9aPhoto)).toMatchObject({
      label: 'Export (.zip) — below full-pilot authorship floor',
      disabled: true,
      ghost: true,
    })
    expect(
      formatFinishedAuthorship(sessCec9aPhoto.authorship, {
        pilotReady: isPilotZipReady(sessCec9aPhoto),
        demoteFounding: shouldDemoteFounding(sessCec9aPhoto),
      }),
    ).toMatch(/Code-cycle prototype — 6 artifacts; 18 templated/)
    expect(honestLevel({ ...sessCec9aPhoto, pilot_ready: true, level_grade: {
      ...sessCec9aPhoto.level_grade,
      level: 'STORE_GREEN',
      pilot_ready: true,
    }})).toBe('CODE_GREEN')
  })

  it('4 of 4 required capabilities meets the scaled floor', () => {
    const lettingsFour: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: {
        artifacts: 27,
        agent_written: 4,
        templated: 23,
        action_py: 4,
        n_required: 4,
        cli_authored_ids: [
          'unit_registry_and_vacancy_tracking',
          'viewing_management',
          'maintenance_issue_tracking',
          'tenancy_application_pipeline',
        ],
      },
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'STORE_GREEN',
        founding_customer_ready: false,
        pilot_ready: true,
        full_pilot: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(fullPilotAuthorshipNeed(lettingsFour)).toBe(4)
    expect(
      fullPilotAuthorshipNeed({
        ...lettingsFour,
        authorship: { ...lettingsFour.authorship, n_required: undefined },
        n_required: 4,
      }),
    ).toBe(4)
    expect(fullPilotAuthorshipCount(lettingsFour)).toBe(4)
    expect(isBelowFullPilotAuthorshipFloor(lettingsFour)).toBe(false)
    expect(shouldRefuseExport(lettingsFour)).toBe(false)
    expect(exportAffordance(lettingsFour)).toMatchObject({
      label: 'Download platform export (.zip)',
      disabled: false,
    })

    const threeOfFour = {
      ...lettingsFour,
      authorship: {
        ...lettingsFour.authorship,
        agent_written: 3,
        action_py: 3,
        cli_authored_ids: lettingsFour.authorship!.cli_authored_ids!.slice(0, 3),
      },
      level_grade: {
        ...lettingsFour.level_grade,
        full_pilot: false,
        level: 'CODE_GREEN',
        pilot_ready: false,
      },
      pilot_ready: false,
    }
    expect(isBelowFullPilotAuthorshipFloor(threeOfFour)).toBe(true)
    expect(exportAffordance(threeOfFour).title).toMatch(/Need ≥4/)
  })

  it('sess_4591d5cc sticky need≥5 RUN_FAILED unlocks after live n_required=4', () => {
    const sticky: BuildStatus = {
      state: 'failed',
      outcome: 'FAILED_ROLE_ERROR',
      cycle: 'pilot',
      pilot_ready: false,
      detail:
        'FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the full-pilot floor (written=4, cli_authored_ids=4, need ≥5). Do not SUCCESS a Store-green pilot from thin authorship.',
      findings: [
        'FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the full-pilot floor (written=4, cli_authored_ids=4, need ≥5)',
      ],
      authorship: {
        artifacts: 25,
        agent_written: 4,
        templated: 21,
        action_py: 4,
        cli_authored_ids: [
          'unit_registry_and_vacancy_tracking',
          'viewing_management',
          'maintenance_issue_tracking',
          'tenancy_application_pipeline',
        ],
      },
      acceptance: ACCEPTANCE_KK,
    }
    const lettingsBp = {
      product_name: 'Residential Lettings Platform',
      capabilities: [
        { id: 'unit_registry_and_vacancy_tracking' },
        { id: 'viewing_management' },
        { id: 'maintenance_issue_tracking' },
        { id: 'tenancy_application_pipeline' },
      ],
    }
    expect(nRequiredFromProductInputs(lettingsBp)).toBe(4)
    const resolved = withResolvedNRequired(sticky, nRequiredFromProductInputs(lettingsBp))
    const unlocked = withClientStall(resolved)
    expect(unlocked?.state).toBe('succeeded')
    expect(unlocked?.pilot_ready).toBe(true)
    expect(unlocked?.honesty).toBe('full_pilot_authorship_reevaluated')
    expect(unlocked?.detail).not.toMatch(/need\s*≥\s*5/)
    expect(isPilotZipReady(unlocked)).toBe(true)
    expect(shouldRefuseExport(unlocked)).toBe(false)
    expect(exportAffordance(unlocked)).toMatchObject({
      label: 'Download platform export (.zip)',
      disabled: false,
    })
    expect(platformsLeadCopy(unlocked, true)).toMatch(/Download the export/)
    expect(honestLevel(unlocked)).toBe('STORE_GREEN')

    const stillUnknown = withClientStall(sticky)
    expect(stillUnknown?.state).toBe('failed')
    expect(shouldRefuseExport(stillUnknown)).toBe(true)
    expect(exportAffordance(stillUnknown).label).toMatch(/Download as-is \(failed gates\)/)

    const threeSticky = withResolvedNRequired(
      {
        ...sticky,
        authorship: {
          ...sticky.authorship,
          agent_written: 3,
          action_py: 3,
          cli_authored_ids: sticky.authorship!.cli_authored_ids!.slice(0, 3),
        },
      },
      4,
    )
    const threeHonest = withClientStall(threeSticky)
    expect(threeHonest?.state).toBe('failed')
    expect(shouldRefuseExport(threeHonest)).toBe(true)

    const liveSuccess: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      cycle: 'pilot',
      pilot_ready: true,
      authorship: { ...sticky.authorship, n_required: 4 },
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'STORE_GREEN',
        pilot_ready: true,
        full_pilot: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    const kept = preferHonestBuild(sticky, liveSuccess)
    expect(kept.state).toBe('succeeded')
    expect(isPilotZipReady(kept)).toBe(true)
    expect(reevaluateStickyThinAuthorship(sticky)?.state).toBe('failed')
  })

  it('VetCare action_py=3 is below the full-pilot floor', () => {
    const vetCare1206: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: {
        artifacts: 24,
        agent_written: 3,
        templated: 21,
        action_py: 3,
        agent_artifacts: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
        cli_authored_ids: ['audit', 'vetcare_hub_veterinary_core', 'workflow'],
      },
      level_grade: {
        level: 'STORE_GREEN',
        founding_customer_ready: false,
        pilot_ready: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(fullPilotAuthorshipCount(vetCare1206)).toBe(3)
    expect(isBelowFullPilotAuthorshipFloor(vetCare1206)).toBe(true)
    expect(honestLevel(vetCare1206)).toBe('CODE_GREEN')
    expect(isPilotZipReady(vetCare1206)).toBe(false)
    expect(shouldRefuseExport(vetCare1206)).toBe(true)
  })

  it('sess_45729bb 0639 photograph: template-majority Store-green is not founding', () => {
    // Live 2026-09-06 launching-ready cycle 0639: FINISHED / Store-green /
    // Download enabled / Pilot-ready thin authorship (factory-LLM 8/16),
    // but the UI painted Founding-customer-ready. c220 on the same pattern
    // already said NOT founding. Thin Store-green is not founding product.
    const sess45729Photo: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      cycle: 'pilot',
      authorship: { artifacts: 24, agent_written: 8, templated: 16 },
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'FOUNDING_CUSTOMER_READY',
        founding_customer_ready: true,
        pilot_ready: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(isCoderCliFailed(sess45729Photo)).toBe(false)
    expect(isThinTemplatedAuthorship(sess45729Photo.authorship)).toBe(true)
    expect(shouldDemoteFounding(sess45729Photo)).toBe(true)
    expect(isAuthoritativePilotReady(sess45729Photo)).toBe(true)
    expect(shouldRefuseExport(sess45729Photo)).toBe(false)
    expect(honestLevel(sess45729Photo)).toBe('STORE_GREEN')
    expect(honestLevel(sess45729Photo)).not.toBe('FOUNDING_CUSTOMER_READY')
    expect(isPilotZipReady(sess45729Photo)).toBe(true)
    expect(exportAffordance(sess45729Photo)).toEqual({
      label: 'Download platform export (.zip)',
      disabled: false,
      ghost: false,
    })
    expect(levelGradeLabel(honestLevel(sess45729Photo)!)).toBe('Store-green')
    expect(
      formatFinishedAuthorship(sess45729Photo.authorship, {
        pilotReady: isPilotZipReady(sess45729Photo),
        demoteFounding: shouldDemoteFounding(sess45729Photo),
      }),
    ).toMatch(/^Pilot-ready —/)
    expect(
      formatFinishedAuthorship(sess45729Photo.authorship, {
        pilotReady: isPilotZipReady(sess45729Photo),
        demoteFounding: shouldDemoteFounding(sess45729Photo),
      }),
    ).not.toMatch(/Finished/)
    expect(
      honestLevel({
        ...sess45729Photo,
        level_grade: {
          ...sess45729Photo.level_grade,
          level: 'STORE_GREEN',
          founding_customer_ready: true,
        },
      }),
    ).toBe('STORE_GREEN')
  })

  it('founding claim without authorship counts is demoted on the glass', () => {
    const unsourcedFounding: BuildStatus = {
      state: 'succeeded',
      pilot_ready: true,
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'FOUNDING_CUSTOMER_READY',
        founding_customer_ready: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
    }
    expect(shouldDemoteFounding(unsourcedFounding)).toBe(true)
    expect(honestLevel(unsourcedFounding)).toBe('STORE_GREEN')
    expect(isPilotZipReady(unsourcedFounding)).toBe(true)
  })

  it('factory-LLM GENERATE fallthrough demotes founding even with writer counts', () => {
    const fallthrough: BuildStatus = {
      state: 'succeeded',
      outcome: 'SUCCESS',
      pilot_ready: true,
      authorship: { artifacts: 28, agent_written: 22, templated: 6 },
      acceptance: ACCEPTANCE_KK,
      level_grade: {
        level: 'FOUNDING_CUSTOMER_READY',
        founding_customer_ready: true,
        three_gate: { CODE: 'PASS', PRODUCT: 'PASS', STORE: 'PASS' },
      },
      coder_receipt: { factory_llm_generate_fallthrough: true },
    }
    expect(shouldDemoteFounding(fallthrough)).toBe(true)
    expect(honestLevel(fallthrough)).toBe('STORE_GREEN')
    expect(shouldRefuseExport(fallthrough)).toBe(false)
  })

  it('CLI-failed + pilot_ready=false still refuses Export', () => {
    const cliMissNotReady: BuildStatus = {
      state: 'succeeded',
      pilot_ready: false,
      cycle: 'code',
      authorship: { artifacts: 24, agent_written: 1, templated: 23 },
      coder_receipt: {
        ok: false,
        blocker: 'FACTORY_CODE_CLI_FAILED',
        detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
      },
    }
    expect(isCoderCliFailed(cliMissNotReady)).toBe(true)
    expect(isAuthoritativePilotReady(cliMissNotReady)).toBe(false)
    expect(shouldRefuseExport(cliMissNotReady)).toBe(true)
    expect(isPilotZipReady(cliMissNotReady)).toBe(false)
    expect(withExportHonesty(cliMissNotReady)).toMatchObject({
      state: 'failed',
      pilot_ready: false,
      detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
    })
    expect(exportAffordance(cliMissNotReady)).toMatchObject({
      label: 'Download as-is (failed gates)',
      disabled: false,
      ghost: true,
      asIs: true,
    })
    expect(platformsLeadCopy(cliMissNotReady, true)).toMatch(/Build failed — no certified download/)
    expect(
      formatFinishedAuthorship(cliMissNotReady.authorship, {
        pilotReady: isPilotZipReady(cliMissNotReady),
      }),
    ).not.toMatch(/Finished/)
  })

  it('sess_45729bb claimed founding + PRODUCT fail refuses Export', () => {
    const vetCareCliFailed: BuildStatus = {
      state: 'succeeded',
      pilot_ready: false,
      cycle: 'pilot',
      authorship: { artifacts: 24, agent_written: 1, templated: 23 },
      level_grade: {
        level: 'FOUNDING_CUSTOMER_READY',
        founding_customer_ready: true,
        pilot_ready: false,
        three_gate: { CODE: 'PASS', PRODUCT: 'FAIL', STORE: 'NOT_RUN' },
      },
      coder_receipt: {
        ok: false,
        blocker: 'FACTORY_CODE_CLI_FAILED',
        detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
      },
    }
    expect(isCoderCliFailed(vetCareCliFailed)).toBe(true)
    expect(shouldRefuseExport(vetCareCliFailed)).toBe(true)
    expect(honestLevel(vetCareCliFailed)).toBe('SCAFFOLD')
    expect(isPilotZipReady(vetCareCliFailed)).toBe(false)
    expect(withExportHonesty(vetCareCliFailed)).toMatchObject({
      state: 'failed',
      pilot_ready: false,
      detail: 'FACTORY_CODE_CLI_FAILED: CLI exited 1',
    })
    expect(withClientStall(vetCareCliFailed)?.state).toBe('failed')
    expect(exportAffordance(vetCareCliFailed)).toMatchObject({
      label: 'Download as-is (failed gates)',
      disabled: false,
      ghost: true,
      asIs: true,
    })
    expect(platformsLeadCopy(vetCareCliFailed, true)).toMatch(/Build failed — no certified download/)
    expect(platformsLeadCopy(vetCareCliFailed, true)).not.toMatch(/Download the export/i)
    expect(
      formatFinishedAuthorship(vetCareCliFailed.authorship, {
        pilotReady: isPilotZipReady(vetCareCliFailed),
      }),
    ).not.toMatch(/Finished/)
  })

  it('SCAFFOLD grade refuses Export when the build is not actually pilot-ready', () => {
    const scaffold: BuildStatus = {
      state: 'succeeded',
      pilot_ready: false,
      level_grade: { level: 'SCAFFOLD', founding_customer_ready: true },
    }
    expect(isScaffoldClaim(scaffold)).toBe(true)
    expect(honestLevel(scaffold)).toBe('SCAFFOLD')
    expect(isPilotZipReady(scaffold)).toBe(false)
    expect(shouldRefuseExport(scaffold)).toBe(true)
    // Not pilot-ready => never a gold zip; the honest exit is the labeled
    // as-is download, not a dead button.
    expect(exportAffordance(scaffold)).toMatchObject({
      label: 'Download as-is (failed gates)',
      disabled: false,
      asIs: true,
    })
  })

  it('falls back to completed+1 when the API has no current_phase yet', () => {
    expect(
      formatPhaseHeadline({
        state: 'building',
        phases_done: 2,
        phases_total: 5,
      }),
    ).toBe('3/5')
  })
})
