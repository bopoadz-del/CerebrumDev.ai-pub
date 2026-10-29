import { afterEach, describe, expect, it, vi } from 'vitest'

// H1 (F-4): the Floor's accept/decline of the golden OFFER must POST to the
// dedicated resolve routes — the wiring the graders scan for outside the
// router. These assert the exact URLs and that a blueprint comes back.

describe('product.acceptGolden / declineGolden', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('acceptGolden POSTs to .../product/draft/accept-golden', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({ ok: true, blueprint: { product_id: 'projectsix' }, source: 'golden' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { product } = await import('../api/factory')
    const resp = await product.acceptGolden('sess_1')
    expect(fetchMock).toHaveBeenCalled()
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/v1/sessions/sess_1/product/draft/accept-golden')
    expect((init as RequestInit).method).toBe('POST')
    expect(resp.source).toBe('golden')
    expect(resp.blueprint.product_id).toBe('projectsix')
  })

  it('declineGolden POSTs to .../product/draft/decline-golden', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () =>
        JSON.stringify({ ok: true, blueprint: { product_id: 'estate' }, source: 'drafted' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { product } = await import('../api/factory')
    const resp = await product.declineGolden('sess_2')
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toContain('/v1/sessions/sess_2/product/draft/decline-golden')
    expect((init as RequestInit).method).toBe('POST')
    expect(resp.source).toBe('drafted')
    expect(resp.blueprint.product_id).toBe('estate')
  })
})
