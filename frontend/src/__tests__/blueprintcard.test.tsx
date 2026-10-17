/**
 * Capability picker contract (STANDING TEST DOCTRINE — pin the #116 feature):
 * the user determines what the platform includes. Unticked capabilities are
 * passed to onApprove as exclusions, the button reflects the selection, and
 * a zero-selection blueprint cannot be approved.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { BlueprintCard } from '../App'

const BLUEPRINT = {
  product_name: 'Retail Platform',
  vertical: 'retail',
  capabilities: [
    { id: 'retail_core', description: 'Core retail workflows', strategy_hint: 'GENERATE' },
    { id: 'audit', description: 'Audit capability', strategy_hint: 'REUSE', block_ids: ['audit'] },
    { id: 'voice_assistant', description: 'Voice UI', strategy_hint: 'GENERATE' },
  ],
}

function setup(busy = false) {
  const onApprove = vi.fn()
  const onRefine = vi.fn()
  render(
    <BlueprintCard blueprint={BLUEPRINT} busy={busy} onApprove={onApprove} onRefine={onRefine} />,
  )
  return { onApprove, onRefine }
}

describe('BlueprintCard capability picker', () => {
  it('renders one checkbox per capability, all ticked by default', () => {
    setup()
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes).toHaveLength(3)
    boxes.forEach((b) => expect(b).toBeChecked())
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeEnabled()
  })

  it('unticking passes exactly the excluded ids to onApprove', () => {
    const { onApprove } = setup()
    // Not every app needs voice — untick it, and audit too.
    fireEvent.click(screen.getAllByRole('checkbox')[2])
    fireEvent.click(screen.getAllByRole('checkbox')[1])
    const btn = screen.getByRole('button', { name: /Approve & build \(1 of 3\)/ })
    fireEvent.click(btn)
    expect(onApprove).toHaveBeenCalledWith(['audit', 'voice_assistant'], 'zip')
  })

  it('full selection approves with no exclusions', () => {
    const { onApprove } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))
    expect(onApprove).toHaveBeenCalledWith([], 'zip')
  })

  it('the delivery picker chooses zip by default and github_repo on change', () => {
    const { onApprove } = setup()
    const select = screen.getByTestId('bp-delivery-format')
    fireEvent.change(select, { target: { value: 'github_repo' } })
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))
    expect(onApprove).toHaveBeenCalledWith([], 'github_repo')
  })

  it('zero selection disables the approve button', () => {
    setup()
    screen.getAllByRole('checkbox').forEach((b) => fireEvent.click(b))
    expect(
      screen.getByRole('button', { name: /Approve & build \(0 of 3\)/ }),
    ).toBeDisabled()
  })

  it('re-ticking restores the capability to the build', () => {
    const { onApprove } = setup()
    const voice = screen.getAllByRole('checkbox')[2]
    fireEvent.click(voice) // out
    fireEvent.click(voice) // back in
    fireEvent.click(screen.getByRole('button', { name: 'Approve & build' }))
    expect(onApprove).toHaveBeenCalledWith([], 'zip')
  })

  it('busy state freezes checkboxes and the approve button', () => {
    setup(true)
    screen.getAllByRole('checkbox').forEach((b) => expect(b).toBeDisabled())
    expect(screen.getByRole('button', { name: 'Approve & build' })).toBeDisabled()
  })
})
