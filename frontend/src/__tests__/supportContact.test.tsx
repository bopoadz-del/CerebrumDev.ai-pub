import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { SupportContact } from '../supportContact'

describe('SupportContact', () => {
  it('renders a mailto link for the configured address', () => {
    render(<SupportContact email="support@cerebrum-dev.com" />)
    const link = screen.getByRole('link', { name: 'Contact support' })
    expect(link).toHaveAttribute('href', 'mailto:support@cerebrum-dev.com')
  })

  it('renders a different address when configured differently', () => {
    render(<SupportContact email="help@acme.test" />)
    const link = screen.getByRole('link', { name: 'Contact support' })
    expect(link).toHaveAttribute('href', 'mailto:help@acme.test')
  })

  it('renders nothing when no address is known', () => {
    render(<SupportContact email={null} />)
    expect(screen.queryByRole('link', { name: 'Contact support' })).toBeNull()
  })
})
