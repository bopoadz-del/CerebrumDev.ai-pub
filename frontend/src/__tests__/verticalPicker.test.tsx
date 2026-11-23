/**
 * The industry is the user's choice on the Floor: picked from what the
 * Store's kits declare, "General", or typed under "Other". It travels as a
 * typed field -- never inside the chat message. Since PR-06 it is a row of
 * chips in the edit sheet instead of a free box with a datalist, and the
 * sheet's one Save sends it with the other three answers.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { chatStream } from '../api/factory'
import { IndustryChips } from '../verticalPicker'

const KITS = [
  { vertical: 'zorblat_yards', kit: 'zorblat', build_ready: true },
  { vertical: 'quux_depots', kit: 'quux', build_ready: false },
]

describe('IndustryChips', () => {
  it('offers the industries the Store kits declare, plus General and Other', () => {
    render(<IndustryChips options={KITS} value="zorblat_yards" other={false} onChange={vi.fn()} />)
    const chips = screen.getAllByRole('button').map((b) => b.textContent)
    expect(chips).toEqual(['General', 'Zorblat yards', 'Quux depotsnot certified', 'Other'])
    expect(screen.getByRole('button', { name: 'Zorblat yards' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('reports what the user picks or types', () => {
    const onChange = vi.fn()
    const { rerender } = render(<IndustryChips options={KITS} value="" other={false} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: /Quux depots/ }))
    expect(onChange).toHaveBeenLastCalledWith({ value: 'quux_depots', other: false })
    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    expect(onChange).toHaveBeenLastCalledWith({ value: '', other: true })
    rerender(<IndustryChips options={KITS} value="" other={true} onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('Your industry'), { target: { value: 'my_own_trade' } })
    expect(onChange).toHaveBeenLastCalledWith({ value: 'my_own_trade', other: true })
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
