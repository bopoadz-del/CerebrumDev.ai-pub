import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { BriefCard } from '../briefCard'
import { BuildLevelCards } from '../intakeLine'
import type { IntakeState } from '../api/factory'

// The intake used to be one line under the chat; since PR-04 it lives in the
// "Your brief" card. The behaviour these tests pin is unchanged: the chat
// proposes, only the typed Confirm stores, the edit controls appear only on
// request, the levels come from the shared spec, and a declared intake reads
// back as declared. Only the wording moved (label and value, not one line).
// Since PR-06 "Edit brief" opens the edit sheet (onEdit) and the level is
// four option cards there, not a select that sends set_build_level at once:
// the sheet's one Save sends it (briefSheet.test.tsx).
function renderBrief(intake: IntakeState, onTyped = vi.fn(), onEdit = vi.fn()) {
  render(
    <BriefCard
      intake={intake}
      status={intake.proposal ? 'suggested by chat' : 'draft'}
      features={null}
      featuresApproved={false}
      onTyped={onTyped}
      onEdit={onEdit}
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
    const onEdit = vi.fn()
    const onTyped = renderBrief({ declared: {}, proposal: null }, vi.fn(), onEdit)
    expect(screen.getByTestId('intake-declared')).toHaveTextContent(
      'Not set: country, currency, build level',
    )
    expect(screen.queryByRole('radio')).toBeNull()
    expect(screen.queryByRole('textbox')).toBeNull()
    fireEvent.click(screen.getByTestId('intake-change'))
    expect(onEdit).toHaveBeenCalledTimes(1)
    // Opening the sheet stores nothing.
    expect(onTyped).not.toHaveBeenCalled()
  })

  it('offers exactly the levels the shared spec defines, with no level preselected', () => {
    const onChange = vi.fn()
    render(<BuildLevelCards value="" onChange={onChange} />)
    const radios = screen.getAllByRole('radio') as HTMLInputElement[]
    expect(radios.map((r) => r.value)).toEqual(['prototype', 'light', 'pilot', 'production'])
    expect(radios.some((r) => r.checked)).toBe(false)
    fireEvent.click(radios[0])
    expect(onChange).toHaveBeenLastCalledWith('prototype')
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
