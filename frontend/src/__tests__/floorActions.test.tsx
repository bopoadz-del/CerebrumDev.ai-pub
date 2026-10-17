import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { BlueprintCard, typedActionLabel } from '../floorView'

const BLUEPRINT = {
  product_name: 'Zorblat Yard',
  vertical: 'product',
  capabilities: [
    { id: 'zorblat_core', strategy_hint: 'GENERATE' },
    { id: 'quux_audit', strategy_hint: 'REUSE' },
  ],
}

function renderCard() {
  const onRefine = vi.fn()
  const onApprove = vi.fn()
  render(<BlueprintCard blueprint={BLUEPRINT} busy={false} onApprove={onApprove} onRefine={onRefine} />)
  return { onRefine, onApprove }
}

describe('Floor controls send typed actions, never chat text', () => {
  it('add and remove a capability by id', () => {
    const { onRefine } = renderCard()
    fireEvent.change(screen.getByTestId('bp-cap-id'), { target: { value: 'quux_audit' } })
    fireEvent.click(screen.getByTestId('bp-remove-cap'))
    expect(onRefine).toHaveBeenLastCalledWith({ action: 'remove_capability', value: 'quux_audit' })
    fireEvent.change(screen.getByTestId('bp-cap-id'), { target: { value: 'zorblat_payments' } })
    fireEvent.click(screen.getByTestId('bp-add-cap'))
    expect(onRefine).toHaveBeenLastCalledWith({ action: 'add_capability', value: 'zorblat_payments' })
  })

  it('rename is typed; the build level is intake, not a card refinement', () => {
    const { onRefine } = renderCard()
    fireEvent.change(screen.getByTestId('bp-rename'), { target: { value: 'Quux Hub' } })
    fireEvent.click(screen.getByTestId('bp-rename-apply'))
    expect(onRefine).toHaveBeenLastCalledWith({ action: 'rename', value: 'Quux Hub' })
    expect(screen.queryByTestId('bp-rigor')).toBeNull()
  })

  it('list capabilities is a typed action with no value', () => {
    const { onRefine } = renderCard()
    fireEvent.click(screen.getByRole('button', { name: 'list capabilities' }))
    expect(onRefine).toHaveBeenLastCalledWith({ action: 'list_capabilities' })
  })

  it('empty inputs cannot send a value-less add or rename', () => {
    renderCard()
    expect(screen.getByTestId('bp-add-cap')).toBeDisabled()
    expect(screen.getByTestId('bp-rename-apply')).toBeDisabled()
  })

  it('labels a typed action for the chat log', () => {
    expect(typedActionLabel({ action: 'remove_capability', value: 'quux_audit' })).toBe(
      '[remove capability: quux_audit]',
    )
    expect(typedActionLabel({ action: 'approve' })).toBe('[approve]')
  })
})
