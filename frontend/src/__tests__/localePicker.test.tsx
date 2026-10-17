import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { LocalePicker } from '../localePicker'

describe('LocalePicker', () => {
  it('keeps only the shape: letters, upper-cased, 2 and 3 long', () => {
    const onChange = vi.fn()
    render(<LocalePicker country="" currency="" onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('Country'), { target: { value: 'zq9x' } })
    expect(onChange).toHaveBeenLastCalledWith({ country: 'ZQ', currency: '' })
    fireEvent.change(screen.getByLabelText('Currency'), { target: { value: 'zrb-k' } })
    expect(onChange).toHaveBeenLastCalledWith({ country: '', currency: 'ZRB' })
  })
})
