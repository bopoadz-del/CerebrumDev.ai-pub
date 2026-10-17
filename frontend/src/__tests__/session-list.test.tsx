import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { SessionList } from '../App'

// Owner: "new session deletes the ongoing one, the sessions should be saved on
// the left panel, not disappear". They were always kept server-side; the rail
// simply never rendered them.
describe('SessionList', () => {
  const items = [
    { session_id: 'sess_new0000000000', title: '', stage: 'empty' as const },
    { session_id: 'sess_bakery00000000', title: 'Bakery Chain Operations', stage: 'build' as const },
    { session_id: 'sess_vet00000000000', title: 'i run a vet clinic', stage: 'talking' as const },
  ]

  it('lists every session by a title a person can recognise', () => {
    render(<SessionList items={items} currentId="sess_new0000000000" onOpen={() => {}} />)
    expect(screen.getByRole('region', { name: 'Your sessions' })).toBeInTheDocument()
    expect(screen.getByText('Bakery Chain Operations')).toBeInTheDocument()
    expect(screen.getByText('i run a vet clinic')).toBeInTheDocument()
    expect(screen.getByText('Untitled session')).toBeInTheDocument()
  })

  it('opens the session that was left behind', () => {
    const onOpen = vi.fn()
    render(<SessionList items={items} currentId="sess_new0000000000" onOpen={onOpen} />)
    fireEvent.click(screen.getByText('Bakery Chain Operations'))
    expect(onOpen).toHaveBeenCalledWith('sess_bakery00000000')
  })

  it('marks the session you are in, and only that one', () => {
    render(<SessionList items={items} currentId="sess_bakery00000000" onOpen={() => {}} />)
    const current = screen.getAllByRole('button').filter((b) => b.getAttribute('aria-current') === 'page')
    expect(current).toHaveLength(1)
    expect(current[0]).toHaveTextContent('Bakery Chain Operations')
  })

  it('shows the stage so a running build is findable', () => {
    render(<SessionList items={items} currentId={null} onOpen={() => {}} />)
    expect(screen.getByText('build')).toBeInTheDocument()
    expect(screen.getByText('talking')).toBeInTheDocument()
  })

  it('renders nothing when there are no sessions', () => {
    const { container } = render(<SessionList items={[]} currentId={null} onOpen={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('SessionList delete', () => {
  const items = [
    { session_id: 'sess_a00000000000', title: 'FleetOps Back-Office Platform', stage: 'build' as const },
    { session_id: 'sess_b00000000000', title: 'Bakery Chain Operations', stage: 'build' as const },
  ]

  it('asks first, and deletes only on the second click', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined)
    render(<SessionList items={items} currentId={null} onOpen={() => {}} onDelete={onDelete} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delete session: FleetOps Back-Office Platform' }))
    expect(onDelete).not.toHaveBeenCalled()
    expect(screen.getByText('Delete for good?')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    await vi.waitFor(() => expect(onDelete).toHaveBeenCalledWith('sess_a00000000000'))
  })

  it('"Keep" backs out without deleting', () => {
    const onDelete = vi.fn()
    render(<SessionList items={items} currentId={null} onOpen={() => {}} onDelete={onDelete} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delete session: Bakery Chain Operations' }))
    fireEvent.click(screen.getByRole('button', { name: 'Keep' }))
    expect(onDelete).not.toHaveBeenCalled()
    expect(screen.queryByText('Delete for good?')).not.toBeInTheDocument()
  })

  it('shows why a delete was refused (a build is running)', async () => {
    const onDelete = vi.fn().mockRejectedValue(new Error('a build is running in this session'))
    render(<SessionList items={items} currentId={null} onOpen={() => {}} onDelete={onDelete} />)
    fireEvent.click(screen.getByRole('button', { name: 'Delete session: FleetOps Back-Office Platform' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('a build is running')
  })

  it('offers no delete control when no handler is given', () => {
    render(<SessionList items={items} currentId={null} onOpen={() => {}} />)
    expect(screen.queryByRole('button', { name: /Delete session/ })).not.toBeInTheDocument()
  })
})
