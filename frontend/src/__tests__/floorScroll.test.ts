import { createElement } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { NotYetNotice } from '../floorView'
import {
  FOLLOW_WITHIN_PX,
  distanceFromBottom,
  scrollBehavior,
  settledKey,
  shouldFollow,
} from '../floorScroll'

describe('the Floor follow rule', () => {
  it('follows a new message only when the reader is within 80px of the bottom', () => {
    expect(FOLLOW_WITHIN_PX).toBe(80)
    expect(shouldFollow('new-message', 0)).toBe(true)
    expect(shouldFollow('new-message', 80)).toBe(true)
    expect(shouldFollow('new-message', 81)).toBe(false)
    expect(shouldFollow('new-message', 1200)).toBe(false)
  })

  it('never follows a status update, wherever the reader is', () => {
    for (const d of [0, 1, 40, 80, 81, 5000]) {
      expect(shouldFollow('status-update', d)).toBe(false)
    }
  })

  it('measures the distance between the visible bottom and the content end', () => {
    expect(distanceFromBottom({ scrollHeight: 2000, scrollTop: 1000, clientHeight: 600 })).toBe(400)
    expect(distanceFromBottom({ scrollHeight: 600, scrollTop: 0, clientHeight: 600 })).toBe(0)
    // Overscroll (rubber band) never gives a negative distance.
    expect(distanceFromBottom({ scrollHeight: 600, scrollTop: 30, clientHeight: 600 })).toBe(0)
  })
})

describe('bringing the build card into view once', () => {
  it('has a key only for states that end a run', () => {
    expect(settledKey('failed')).toBe('failed')
    expect(settledKey('stalled')).toBe('stalled')
    expect(settledKey('succeeded')).toBe('succeeded')
    expect(settledKey('building')).toBeNull()
    expect(settledKey('not_started')).toBeNull()
    expect(settledKey(undefined)).toBeNull()
    expect(settledKey(null)).toBeNull()
  })
})

describe('scrolling honours reduced motion', () => {
  it('is instant when the system asks for reduced motion, smooth otherwise', () => {
    expect(scrollBehavior((q) => ({ matches: q.includes('reduce') }))).toBe('auto')
    expect(scrollBehavior(() => ({ matches: false }))).toBe('smooth')
  })
})

describe('"What this is not yet" is a one-line link', () => {
  beforeEach(() => window.localStorage.clear())

  it('shows only the link until it is opened', () => {
    render(createElement(NotYetNotice))
    const link = screen.getByTestId('floor-not-yet-link')
    expect(link).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('floor-not-yet')).toBeNull()
    fireEvent.click(link)
    expect(link).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByTestId('floor-not-yet')).toHaveTextContent('not a finished production system')
  })

  it('remembers that it was closed, across a reload', () => {
    const first = render(createElement(NotYetNotice))
    fireEvent.click(screen.getByTestId('floor-not-yet-link'))
    fireEvent.click(screen.getByTestId('floor-not-yet-link'))
    first.unmount()
    render(createElement(NotYetNotice))
    expect(screen.getByTestId('floor-not-yet-link')).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByTestId('floor-not-yet')).toBeNull()
  })

  it('stays open across a reload until it is closed', () => {
    const first = render(createElement(NotYetNotice))
    fireEvent.click(screen.getByTestId('floor-not-yet-link'))
    first.unmount()
    render(createElement(NotYetNotice))
    expect(screen.getByTestId('floor-not-yet')).toBeInTheDocument()
  })
})
