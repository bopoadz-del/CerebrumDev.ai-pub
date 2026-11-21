import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { BriefCard } from '../briefCard'
import type { IntakeState } from '../api/factory'

// The intake used to be one line under the chat; since PR-04 it lives in the
// "Your brief" card. The behaviour these tests pin is unchanged: the chat
// proposes, only the typed Confirm stores, the edit controls appear only on
// request, the levels come from the shared spec, and a declared intake reads
// back as declared. Only the wording moved (label and value, not one line).
function renderBrief(intake: IntakeState, onTyped = vi.fn(), editControls: React.ReactNode = null) {
  render(
    <BriefCard
      intake={intake}
      status={intake.proposal ? 'suggested by chat' : 'draft'}
      features={null}
      featuresApproved={false}
      onTyped={onTyped}
      editControls={editControls}
    />,
  )
  return onTyped
}

describe('the intake in the brief card: the chat proposes, the user confirms', () => {
  it('shows a proposal as its own strip with Confirm these, which sends the typed action', () => {
    const onTyped = renderBrief({
      declared: {},
      proposal: { country: 'ZQ', currency: 'ZQX', build_level: 'pilot' },
    })
    const strip = screen.getByTestId('intake-proposal')
    expect(strip).toHaveTextContent('ZQ')
    expect(strip).toHaveTextContent('ZQX')
    expect(strip).toHaveTextContent('Pilot')
    // The suggestion does not replace the confirmed values.
    expect(screen.getByTestId('intake-declared')).toHaveTextContent(
      'Not set: country, currency, build level',
    )
    fireEvent.click(screen.getByTestId('intake-confirm'))
    expect(onTyped).toHaveBeenLastCalledWith({ action: 'confirm_intake' })
  })

  it('has no always-visible boxes: the edit controls appear only on Edit brief', () => {
    const onTyped = renderBrief(
      { declared: {}, proposal: null },
      vi.fn(),
      <span data-testid="pickers">pickers</span>,
    )
    expect(screen.getByTestId('intake-declared')).toHaveTextContent(
      'Not set: country, currency, build level',
    )
    expect(screen.queryByTestId('pickers')).toBeNull()
    expect(screen.queryByTestId('intake-build-level')).toBeNull()
    fireEvent.click(screen.getByTestId('intake-change'))
    expect(screen.getByTestId('pickers')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('intake-build-level'), { target: { value: 'prototype' } })
    expect(onTyped).toHaveBeenLastCalledWith({ action: 'set_build_level', value: 'prototype' })
  })

  it('offers exactly the levels the shared spec defines, with no level preselected', () => {
    renderBrief({ declared: {}, proposal: null })
    fireEvent.click(screen.getByTestId('intake-change'))
    const select = screen.getByTestId('intake-build-level') as HTMLSelectElement
    expect(select.value).toBe('')
    const offered = Array.from(select.options)
      .filter((o) => !o.disabled)
      .map((o) => o.value)
    expect(offered).toEqual(['prototype', 'light', 'pilot', 'production'])
  })

  it('a declared intake reads back as the user declared it', () => {
    renderBrief({
      declared: { country: 'ZQ', currency: 'ZQX', build_level: 'light' },
      proposal: null,
    })
    const line = screen.getByTestId('intake-declared')
    expect(line).toHaveTextContent('ZQ · ZQX · Light')
    expect(line).not.toHaveTextContent('Not set')
  })
})
