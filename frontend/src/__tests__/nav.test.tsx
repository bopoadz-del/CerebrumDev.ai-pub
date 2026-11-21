import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { FactoryNav, SessionList } from '../App'

// UI revamp PR-02: on a phone the four destinations are a bottom tab bar with
// whole labels (L1-04: they were cut to "Fl…", "Pl…", "Pl…", "A…").
describe('FactoryNav', () => {
  const tabs = [
    { name: 'Factory Floor', short: 'Floor', view: 'floor' },
    { name: 'Your Platforms', short: 'Platforms', view: 'platforms' },
    { name: 'Subscription', short: 'Plan', view: 'subscription' },
    { name: 'Account', short: 'Account', view: 'account' },
  ] as const

  it('renders four tabs, each with its whole label and its short label', () => {
    render(<FactoryNav view="floor" onGo={() => {}} />)
    const nav = screen.getByRole('navigation', { name: 'Factory navigation' })
    const buttons = within(nav).getAllByRole('button')
    expect(buttons).toHaveLength(4)
    tabs.forEach((tab, i) => {
      const btn = buttons[i]
      expect(btn).toHaveAccessibleName(tab.name)
      expect(btn.querySelector('.nav-label-full')?.textContent).toBe(tab.name)
      // The phone label is the whole word, never a cut-down stub.
      expect(btn.querySelector('.nav-label-short')?.textContent).toBe(tab.short)
    })
  })

  it('marks only the open tab as the current page', () => {
    render(<FactoryNav view="subscription" onGo={() => {}} />)
    const current = screen.getAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'page')
    expect(current).toHaveLength(1)
    expect(current[0]).toHaveAccessibleName('Subscription')
  })

  it('goes to the tab that was pressed', () => {
    const onGo = vi.fn()
    render(<FactoryNav view="floor" onGo={onGo} />)
    fireEvent.click(screen.getByRole('button', { name: 'Account' }))
    expect(onGo).toHaveBeenCalledWith('account')
    fireEvent.click(screen.getByRole('button', { name: 'Your Platforms' }))
    expect(onGo).toHaveBeenLastCalledWith('platforms')
  })
})

// L6-29: the confirm names the session, and the row it replaces is hidden.
describe('SessionList delete confirm (phone)', () => {
  const items = [
    { session_id: 'sess_a00000000000', title: 'Harbour Ferry Desk', stage: 'build' as const },
    { session_id: 'sess_b00000000000', title: '', stage: 'empty' as const },
  ]

  it('names the session it is about to delete and hides that row', () => {
    render(<SessionList items={items} currentId={null} onOpen={() => {}} onDelete={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delete session: Harbour Ferry Desk' }))
    const confirm = screen.getByRole('group', { name: 'Delete Harbour Ferry Desk?' })
    expect(within(confirm).getByText('Harbour Ferry Desk')).toBeInTheDocument()
    expect(within(confirm).getByRole('button', { name: 'Delete' })).toBeInTheDocument()
    expect(within(confirm).getByRole('button', { name: 'Keep' })).toBeInTheDocument()
    const row = confirm.closest('li')
    const openRow = row?.querySelector('button.rail-session')
    expect(openRow).toHaveAttribute('hidden')
  })

  it('names an untitled session the way the list does', () => {
    render(<SessionList items={items} currentId={null} onOpen={() => {}} onDelete={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delete session: Untitled session' }))
    const confirm = screen.getByRole('group', { name: 'Delete session?' })
    expect(within(confirm).getByText('Untitled session')).toBeInTheDocument()
  })

  it('shows the support address once, at the foot of the list, only when known', () => {
    const { rerender } = render(
      <SessionList items={items} currentId={null} onOpen={() => {}} supportEmail="help@example.test" />,
    )
    const links = screen.getAllByRole('link', { name: 'Contact support' })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', 'mailto:help@example.test')
    rerender(<SessionList items={items} currentId={null} onOpen={() => {}} supportEmail={null} />)
    expect(screen.queryByRole('link', { name: 'Contact support' })).toBeNull()
  })
})
