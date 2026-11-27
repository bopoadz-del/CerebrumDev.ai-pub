/**
 * The grader's findings on the build card: the server's headline and one plain
 * line per finding on the face; rule and quoted words only in Technical detail.
 */
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import type { BuildGraderFinding, BuildStatus } from '../api/factory'
import { BuildCard, FACE_FINDINGS } from '../buildCard'

afterEach(cleanup)

const PHASES = ['COLLECTOR', 'CLONER', 'WRITER', 'TESTER', 'STORE_MANAGER']

function row(where: string, owner: BuildGraderFinding['owner'] = 'writer'): BuildGraderFinding {
  return {
    rule: 'function_check',
    what: 'This feature did not give a correct answer when the quality check called it.',
    where,
    place: where === '-' ? '' : where.replace(/_/g, ' '),
    owner,
    quote: `function grade FAIL: ${where}`,
  }
}

function sentBack(findings: BuildGraderFinding[], headline: string): BuildStatus {
  return {
    state: 'building',
    phases: PHASES,
    completed: ['COLLECTOR', 'CLONER'],
    current_phase: { id: 'WRITER' },
    grader: { state: 'sent_back', tier: 'NOT_CERTIFIED', round: 1, budget: 1, findings, sent_to_writer: findings.length, headline },
  } as BuildStatus
}

describe('grader findings on the card face', () => {
  it.each([
    ['zorblat_total', 'The quality check found 1 problem and sent it back to the writer to fix:'],
    ['plinket_count', 'The quality check found 1 problem and sent it back to the writer to fix:'],
  ])('a send-back shows the headline and where (%s)', (where, headline) => {
    render(<BuildCard build={sentBack([row(where)], headline)} sessionId="sess_x" />)
    const box = screen.getByTestId('floor-grader-findings')
    expect(within(box).getByText(headline)).toBeTruthy()
    const items = within(box).getAllByTestId('floor-grader-finding')
    expect(items).toHaveLength(1)
    expect(items[0].textContent).toContain(`(${where.replace(/_/g, ' ')})`)
    expect(items[0].textContent).not.toContain(where)
    // machine text stays out of the face
    expect(box.textContent).not.toContain('function grade FAIL')
    expect(box.textContent).not.toContain('function_check')
  })

  it.each([
    ['stopped', 'The quality check did not pass the build. It found 1 problem:'],
    ['refused', 'The quality check did not pass the build. It found 1 problem:'],
  ])('a refusal (%s) shows its findings on a stopped card', (state, headline) => {
    const build = {
      state: 'failed',
      phases: PHASES,
      completed: ['COLLECTOR', 'CLONER'],
      grader: { state, tier: 'NOT_CERTIFIED', findings: [row('-', 'run')], sent_to_writer: 0, headline },
    } as BuildStatus
    render(<BuildCard build={build} sessionId="sess_x" />)
    const box = screen.getByTestId('floor-grader-findings')
    expect(box.getAttribute('data-state')).toBe(state)
    expect(within(box).getByTestId('floor-grader-finding').textContent).not.toContain('(')
  })

  it.each([
    [{ state: 'certified', tier: 'CERTIFIED', findings: [], headline: '' }],
    [{ state: 'sent_back', tier: 'NOT_CERTIFIED', findings: [], headline: '' }],
  ])('nothing to show shows nothing', (grader) => {
    render(<BuildCard build={{ state: 'building', phases: PHASES, grader } as BuildStatus} sessionId="sess_x" />)
    expect(screen.queryByTestId('floor-grader-findings')).toBeNull()
  })

  it.each([[FACE_FINDINGS + 1], [FACE_FINDINGS + 4]])('past %i rows the rest are counted, all are in Technical detail', (n) => {
    const rows = Array.from({ length: n }, (_, i) => row(`cap_${i}`))
    render(<BuildCard build={sentBack(rows, 'h:')} sessionId="sess_x" />)
    expect(screen.getAllByTestId('floor-grader-finding')).toHaveLength(FACE_FINDINGS)
    expect(screen.getByTestId('floor-grader-findings-more').textContent).toBe(
      `${n - FACE_FINDINGS} more in Technical detail.`,
    )
    const raw = screen.getByTestId('floor-grader-findings-raw')
    expect(raw.querySelectorAll('li')).toHaveLength(n)
    expect(raw.textContent).toContain(`function_check (writer) at cap_${n - 1}: function grade FAIL: cap_${n - 1}`)
  })
})
