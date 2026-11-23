import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import {
  CodePicker,
  currencyOptions,
  matchOptions,
  regionOptions,
  resolveCode,
  type CodeOption,
} from '../localePicker'

// Since PR-06 country and currency are searchable pickers with name and code
// (the browser's own Intl data) instead of two shape-only code boxes. The
// shape rule stays as the fallback for a browser with no such data.
const OPTIONS: CodeOption[] = [
  { code: 'ZQ', name: 'Zorblat Isles' },
  { code: 'ZR', name: 'Quux Reach' },
]

describe('resolveCode', () => {
  it.each([
    ['zq', 'ZQ'],
    ['Zorblat Isles (ZQ)', 'ZQ'],
    ['quux reach', 'ZR'],
    ['ZX', null],
    ['', null],
  ])('reads %j as %j against the option list', (text, code) => {
    expect(resolveCode(text, OPTIONS, 2)).toBe(code)
  })

  it.each([
    ['zrb', 3, 'ZRB'],
    ['zq9', 3, null],
    ['zq', 2, 'ZQ'],
    ['zqx', 2, null],
  ])('keeps only the shape when the browser has no list: %j', (text, length, code) => {
    expect(resolveCode(text, [], length)).toBe(code)
  })
})

describe('the browser lists', () => {
  it('names regions and currencies from Intl, with their codes', () => {
    const regions = regionOptions()
    const currencies = currencyOptions()
    expect(regions.length).toBeGreaterThan(100)
    expect(regions.every((o) => /^[A-Z]{2}$/.test(o.code) && o.name.toUpperCase() !== o.code)).toBe(true)
    expect(currencies.every((o) => /^[A-Z]{3}$/.test(o.code))).toBe(true)
  })

  it.each([
    ['zor', ['ZQ']],
    ['reach', ['ZR']],
  ])('matches %j by name or code', (text, codes) => {
    expect(matchOptions(text, OPTIONS).map((o) => o.code)).toEqual(codes)
  })
})

describe('CodePicker', () => {
  it('labels the field above it, lists matches and fills "Name (CODE)" on a pick', () => {
    const onText = vi.fn()
    const { rerender } = render(<CodePicker label="Country" text="" options={OPTIONS} onText={onText} />)
    const box = screen.getByLabelText('Country')
    rerender(<CodePicker label="Country" text="zor" options={OPTIONS} onText={onText} />)
    fireEvent.focus(box)
    fireEvent.click(screen.getByRole('option', { name: 'Zorblat Isles (ZQ)' }))
    expect(onText).toHaveBeenLastCalledWith('Zorblat Isles (ZQ)')
  })

  it('shows its error inline', () => {
    render(<CodePicker label="Currency" text="zz" options={OPTIONS} onText={vi.fn()} error="Not known." />)
    expect(screen.getByRole('alert')).toHaveTextContent('Not known.')
    expect(screen.getByLabelText('Currency')).toHaveAttribute('aria-invalid', 'true')
  })
})
