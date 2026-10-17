/**
 * awaitBuild must not spin for 45 minutes on a finished template product
 * (status "unknown", no ledger) or on a dead runner thread ("stalled").
 * watchBuildStatus keeps polling through succeed → building (pilot reopen).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { awaitBuild, product, watchBuildStatus, type BuildStatus } from '../api/factory'

describe('awaitBuild', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('returns succeeded builds as-is', async () => {
    vi.spyOn(product, 'buildStatus').mockResolvedValue({
      ok: true,
      build: { state: 'succeeded', authorship: { agent_written: 4, artifacts: 4 } },
    })
    const result = await awaitBuild('sess', undefined, { intervalMs: 1, timeoutMs: 50 })
    expect(result.state).toBe('succeeded')
    expect(result.authorship?.agent_written).toBe(4)
  })

  it('treats stalled as a failed build so the UI cannot download a torn tree', async () => {
    vi.spyOn(product, 'buildStatus').mockResolvedValue({
      ok: true,
      build: { state: 'stalled', detail: 'no build activity for 40 min' },
    })
    const result = await awaitBuild('sess', undefined, { intervalMs: 1, timeoutMs: 50 })
    expect(result.state).toBe('failed')
    expect(result.detail).toMatch(/no build activity/)
  })

  it('treats unknown (template, no ledger) as already finished', async () => {
    vi.spyOn(product, 'buildStatus').mockResolvedValue({
      ok: true,
      build: { state: 'unknown', detail: 'no build ledger for this product' },
    })
    const result = await awaitBuild('sess', undefined, { intervalMs: 1, timeoutMs: 50 })
    expect(result.state).toBe('succeeded')
  })

  it('reports coding-agent progress while building, then succeeds', async () => {
    const seen: BuildStatus[] = []
    vi.spyOn(product, 'buildStatus')
      .mockResolvedValueOnce({
        ok: true,
        build: { state: 'building', phases_done: 1, phases_total: 5, activity: 'WRITER' },
      })
      .mockResolvedValueOnce({
        ok: true,
        build: {
          state: 'succeeded',
          authorship: { agent_written: 2, artifacts: 5 },
        },
      })
    const result = await awaitBuild('sess', (s) => seen.push(s), { intervalMs: 1, timeoutMs: 500 })
    expect(seen[0]?.state).toBe('building')
    expect(seen[0]?.activity).toBe('WRITER')
    expect(result.state).toBe('succeeded')
    expect(result.authorship?.agent_written).toBe(2)
  })
})

describe('watchBuildStatus', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('keeps polling after succeeded so a pilot reopen can replace FINISHED', async () => {
    const seen: BuildStatus[] = []
    const ac = new AbortController()
    vi.spyOn(product, 'buildStatus')
      .mockResolvedValueOnce({
        ok: true,
        build: { state: 'succeeded', authorship: { agent_written: 11, templated: 19 } },
      })
      .mockResolvedValueOnce({
        ok: true,
        build: {
          state: 'building',
          phases_done: 2,
          phases_total: 5,
          activity: 'pilot WRITER',
        },
      })
      .mockImplementation(async () => {
        ac.abort()
        return {
          ok: true,
          build: { state: 'building', phases_done: 2, phases_total: 5 },
        }
      })
    await watchBuildStatus('sess', (s) => seen.push(s), { intervalMs: 1, signal: ac.signal })
    expect(seen.some((s) => s.state === 'succeeded')).toBe(true)
    expect(seen.some((s) => s.state === 'building' && s.activity === 'pilot WRITER')).toBe(true)
  })

  it('keeps polling sticky THIN_AUTHORSHIP so live n_required can unlock', async () => {
    const seen: BuildStatus[] = []
    const ac = new AbortController()
    vi.spyOn(product, 'buildStatus')
      .mockResolvedValueOnce({
        ok: true,
        build: {
          state: 'failed',
          detail:
            'FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the full-pilot floor (written=4, cli_authored_ids=4, need ≥5)',
        },
      })
      .mockResolvedValueOnce({
        ok: true,
        build: {
          state: 'succeeded',
          outcome: 'SUCCESS',
          pilot_ready: true,
          authorship: { n_required: 4, action_py: 4, agent_written: 4 },
        },
      })
      .mockImplementation(async () => {
        ac.abort()
        return {
          ok: true,
          build: {
            state: 'succeeded',
            outcome: 'SUCCESS',
            pilot_ready: true,
          },
        }
      })
    await watchBuildStatus('sess_4591d5cc45d04fe1', (s) => seen.push(s), {
      intervalMs: 1,
      signal: ac.signal,
    })
    expect(seen.some((s) => s.state === 'failed')).toBe(true)
    expect(seen.some((s) => s.state === 'succeeded')).toBe(true)
  })

  it('stops on stalled', async () => {
    vi.spyOn(product, 'buildStatus').mockResolvedValue({
      ok: true,
      build: { state: 'stalled', detail: 'no build activity for 40 min' },
    })
    const seen: BuildStatus[] = []
    await watchBuildStatus('sess', (s) => seen.push(s), { intervalMs: 1 })
    expect(seen).toHaveLength(1)
    expect(seen[0]?.state).toBe('stalled')
  })
})
