/**
 * The vertical is the user's choice on the Floor: picked from what the
 * Store's kits declare, or typed. It travels as a typed field -- never inside
 * the chat message -- and is sent only when the user changed it.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { chatStream } from '../api/factory'
import { VerticalPicker } from '../verticalPicker'

const verticalsMock = vi.fn()

vi.mock('../api/factory', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/factory')>()
  return {
    ...actual,
    product: { ...actual.product, verticals: (...a: unknown[]) => verticalsMock(...a) },
  }
})

describe('VerticalPicker', () => {
  beforeEach(() => {
    verticalsMock.mockReset()
  })

  it('offers the verticals the Store kits declare, and loads the saved choice without marking it a user change', async () => {
    verticalsMock.mockResolvedValue({
      verticals: [
        { vertical: 'zorblat_yards', kit: 'zorblat', build_ready: true },
        { vertical: 'quux_depots', kit: 'quux', build_ready: false },
      ],
      chosen: 'zorblat_yards',
      default: 'product',
    })
    const onChange = vi.fn()
    const onLoaded = vi.fn()
    const { container } = render(
      <VerticalPicker sessionId="s1" value="" onChange={onChange} onLoaded={onLoaded} />,
    )
    await waitFor(() => expect(onLoaded).toHaveBeenCalledWith('zorblat_yards'))
    expect(onChange).not.toHaveBeenCalled()
    const values = Array.from(container.querySelectorAll('datalist option')).map(
      (o) => (o as HTMLOptionElement).value,
    )
    expect(values).toEqual(['zorblat_yards', 'quux_depots'])
  })

  it('reports what the user picks or types', async () => {
    verticalsMock.mockResolvedValue({ verticals: [], chosen: null, default: 'product' })
    const onChange = vi.fn()
    render(<VerticalPicker sessionId="s2" value="" onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('Vertical'), { target: { value: 'my_own_trade' } })
    expect(onChange).toHaveBeenCalledWith('my_own_trade')
  })
})

describe('chatStream', () => {
  it('sends the vertical as its own typed field, never inside the message', async () => {
    const bodies: unknown[] = []
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)))
      return new Response('', { status: 200 })
    })
    vi.stubGlobal('fetch', fetchMock)
    await chatStream('s3', 'build me something', () => {}, 'zorblat_yards')
    await chatStream('s3', 'and more', () => {})
    expect(bodies[0]).toEqual({ message: 'build me something', vertical: 'zorblat_yards' })
    expect(bodies[1]).toEqual({ message: 'and more' })
    vi.unstubAllGlobals()
  })
})
