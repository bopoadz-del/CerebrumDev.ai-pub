/**
 * PR-08: the action bar. A destructive press asks first; the safe choice is
 * the default and sends nothing; every action shows its line.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { BuildStatus } from '../api/factory'
import { ActionBar } from '../actionBar'
import { buildActions } from '../buildActions'

const STOPPED: BuildStatus = {
  state: 'failed',
  phases: ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER'],
  completed: ['COLLECTOR', 'CLONER'],
  failure: { phase: 'WRITER', location: 'WRITER', outcome: 'failed', reason: 'x', code: 'x' },
}
const RUNNING: BuildStatus = { state: 'building' }

afterEach(cleanup)

function renderBar(build: BuildStatus, extra: { accessPaused?: boolean } = {}) {
  const onRun = vi.fn()
  const onSeePlans = vi.fn()
  render(
    <ActionBar
      plan={buildActions({ build, ...extra })}
      placement="bar"
      onRun={onRun}
      onSeePlans={onSeePlans}
    />,
  )
  return { onRun, onSeePlans }
}

describe('ActionBar', () => {
  it('Start from scratch then Cancel sends nothing; the confirm sends start_over', () => {
    const { onRun } = renderBar(STOPPED)
    fireEvent.click(screen.getByTestId('floor-action-more'))
    fireEvent.click(screen.getByTestId('floor-start-over'))
    const sheet = screen.getByTestId('floor-confirm-sheet')
    expect(sheet).toHaveTextContent('Start from scratch?')
    expect(screen.getByTestId('floor-confirm-keep')).toHaveTextContent('Cancel')
    expect(screen.getByTestId('floor-confirm-keep')).toHaveFocus()
    fireEvent.click(screen.getByTestId('floor-confirm-keep'))
    expect(screen.queryByTestId('floor-confirm-sheet')).not.toBeInTheDocument()
    expect(onRun).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId('floor-action-more'))
    fireEvent.click(screen.getByTestId('floor-start-over'))
    fireEvent.click(screen.getByTestId('floor-confirm-go'))
    expect(onRun).toHaveBeenCalledExactlyOnceWith('start_over')
  })

  it('Stop build then Keep building sends nothing; Escape is Keep building too', () => {
    const { onRun } = renderBar(RUNNING)
    fireEvent.click(screen.getByTestId('floor-coder-stop'))
    expect(screen.getByTestId('floor-confirm-sheet')).toHaveTextContent('Stop this build?')
    fireEvent.click(screen.getByTestId('floor-confirm-keep'))
    expect(onRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('floor-coder-stop'))
    fireEvent.keyDown(screen.getByTestId('floor-confirm-sheet'), { key: 'Escape' })
    expect(screen.queryByTestId('floor-confirm-sheet')).not.toBeInTheDocument()
    expect(onRun).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('floor-coder-stop'))
    fireEvent.click(screen.getByTestId('floor-confirm-go'))
    expect(onRun).toHaveBeenCalledExactlyOnceWith('stop')
  })

  it('Try again runs at once and shows its line under the label', () => {
    const { onRun } = renderBar(STOPPED)
    expect(screen.getByTestId('floor-try-again-sub')).toHaveTextContent(
      'Picks up at step 3. Finished steps are kept.',
    )
    fireEvent.click(screen.getByTestId('floor-try-again'))
    expect(onRun).toHaveBeenCalledExactlyOnceWith('try_again')
  })

  it('access paused: a See plans banner, the reason in words, downloads still enabled', () => {
    const { onRun, onSeePlans } = renderBar(STOPPED, { accessPaused: true })
    expect(screen.getByTestId('floor-access-banner')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'See plans' }))
    expect(onSeePlans).toHaveBeenCalledOnce()
    expect(screen.getByTestId('floor-try-again')).toBeDisabled()
    expect(screen.getByTestId('floor-try-again-sub')).toHaveTextContent('Access is paused.')
    expect(screen.getByTestId('floor-take-copy')).toBeEnabled()
    fireEvent.click(screen.getByTestId('floor-take-copy'))
    expect(onRun).toHaveBeenCalledExactlyOnceWith('download_copy')
  })

  it('has no Pause and no Resume on a running build', () => {
    renderBar(RUNNING)
    expect(screen.queryByRole('button', { name: /pause|resume/i })).not.toBeInTheDocument()
  })
})
