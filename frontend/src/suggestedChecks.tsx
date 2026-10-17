import { useState } from 'react'
import type { SuggestedCheck } from './api/factory'

/**
 * Checks the foreman PROPOSED for this build -- read-only suggestions for a
 * person to consider adding to the acceptance floor. Never a verdict: they do
 * not change the build, and nothing here writes the floor file. "Copy floor
 * entry" only copies a pre-filled acceptance_floor.v2.json entry (gate_fn left
 * null on purpose) to the clipboard so a person can review it and open a PR.
 */
export function SuggestedChecks({ checks }: { checks: SuggestedCheck[] }) {
  const [copied, setCopied] = useState<string | null>(null)

  const copy = async (check: SuggestedCheck) => {
    const text = JSON.stringify(check.floor_entry, null, 2)
    try {
      await navigator.clipboard.writeText(text)
      setCopied(check.name)
    } catch {
      setCopied(null)
    }
  }

  return (
    <section className="coder-suggested-checks" data-testid="floor-suggested-checks">
      <h4>Suggested checks</h4>
      <p className="coder-suggested-note">
        Proposed by the foreman for a person to review. They do not change this build.
      </p>
      <ul>
        {checks.map((c) => (
          <li key={c.name} data-testid="floor-suggested-check" data-source={c.source}>
            <strong>{c.name}</strong> — {c.rule}{' '}
            <span className="coder-suggested-meta">
              ({c.evidence_count} evidence, from {c.source})
            </span>{' '}
            <button
              type="button"
              data-testid="floor-suggested-copy"
              onClick={() => void copy(c)}
            >
              {copied === c.name ? 'Copied — paste after review' : 'Copy floor entry'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
