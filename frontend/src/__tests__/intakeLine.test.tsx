import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { IntakeLine } from '../intakeLine'

describe('the intake line: the chat proposes, the user confirms', () => {
  it('shows a proposal as one line with Confirm, and Confirm sends the typed action', () => {
    const onTyped = vi.fn()
    render(
      <IntakeLine
        intake={{
          declared: {},
          proposal: { country: 'ZQ', currency: 'ZQX', build_level: 'pilot' },
        }}
        onTyped={onTyped}
        editControls={<span data-testid="pickers">pickers</span>}
      />,
    )
    expect(screen.getByTestId('intake-proposal')).toHaveTextContent(
      'country ZQ · currency ZQX · build level pilot',
    )
    fireEvent.click(screen.getByTestId('intake-confirm'))
    expect(onTyped).toHaveBeenLastCalledWith({ action: 'confirm_intake' })
  })

  it('has no always-visible boxes: the edit controls appear only on Change', () => {
    const onTyped = vi.fn()
    render(
      <IntakeLine
        intake={{ declared: {}, proposal: null }}
        onTyped={onTyped}
        editControls={<span data-testid="pickers">pickers</span>}
      />,
    )
    expect(screen.getByTestId('intake-declared')).toHaveTextContent(
      'not declared: country, currency, build level',
    )
    expect(screen.queryByTestId('pickers')).toBeNull()
    expect(screen.queryByTestId('intake-build-level')).toBeNull()
    fireEvent.click(screen.getByTestId('intake-change'))
    expect(screen.getByTestId('pickers')).toBeInTheDocument()
    fireEvent.change(screen.getByTestId('intake-build-level'), { target: { value: 'prototype' } })
    expect(onTyped).toHaveBeenLastCalledWith({ action: 'set_build_level', value: 'prototype' })
  })

  it('offers exactly the levels the shared spec defines, with no level preselected', () => {
    render(
      <IntakeLine
        intake={{ declared: {}, proposal: null }}
        onTyped={vi.fn()}
        editControls={null}
      />,
    )
    fireEvent.click(screen.getByTestId('intake-change'))
    const select = screen.getByTestId('intake-build-level') as HTMLSelectElement
    expect(select.value).toBe('')
    const offered = Array.from(select.options)
      .filter((o) => !o.disabled)
      .map((o) => o.value)
    expect(offered).toEqual(['prototype', 'light', 'pilot', 'production'])
  })

  it('a declared intake reads back as the user declared it', () => {
    render(
      <IntakeLine
        intake={{
          declared: { country: 'ZQ', currency: 'ZQX', build_level: 'light' },
          proposal: null,
        }}
        onTyped={vi.fn()}
        editControls={null}
      />,
    )
    const line = screen.getByTestId('intake-declared')
    expect(line).toHaveTextContent('country ZQ · currency ZQX · build level light')
    expect(line).not.toHaveTextContent('not declared')
  })
})
