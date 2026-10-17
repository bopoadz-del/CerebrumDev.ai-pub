import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SuggestedChecks } from '../suggestedChecks'

const CHECK = {
  name: 'handler_persists_what_it_accepts',
  rule: 'a handler that answers ok:true writes the record it returned',
  evidence_count: 2,
  source: 'audit',
  floor_entry: {
    id: 'handler_persists_what_it_accepts',
    requirement_text: 'a handler that answers ok:true writes the record it returned',
    brief_render: '- handler_persists_what_it_accepts: a handler that answers ok:true writes the record it returned',
    gate_fn: null,
    check: 'a handler that answers ok:true writes the record it returned',
    universal: false,
    subject: 'runtime',
  },
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('suggested checks: read-only proposals, never a verdict', () => {
  it('lists each proposal with its rule, evidence count and source', () => {
    render(<SuggestedChecks checks={[CHECK]} />)
    const row = screen.getByTestId('floor-suggested-check')
    expect(row).toHaveTextContent(CHECK.name)
    expect(row).toHaveTextContent(CHECK.rule)
    expect(row).toHaveTextContent('2 evidence, from audit')
    expect(row).toHaveAttribute('data-source', 'audit')
  })

  it('accept only copies the pre-filled floor entry; nothing is sent anywhere', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    render(<SuggestedChecks checks={[CHECK]} />)
    await act(async () => {
      fireEvent.click(screen.getByTestId('floor-suggested-copy'))
    })
    expect(writeText).toHaveBeenCalledTimes(1)
    expect(JSON.parse(writeText.mock.calls[0][0] as string)).toEqual(CHECK.floor_entry)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(screen.getByTestId('floor-suggested-copy')).toHaveTextContent('Copied')
  })
})
