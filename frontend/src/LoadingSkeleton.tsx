/** Shared loading panel — never show empty-state copy while a fetch is in flight. */

export function LoadingSkeleton({
  label = 'Loading',
  lines = 4,
}: {
  label?: string
  lines?: number
}) {
  return (
    <div
      className="panel skeleton-panel"
      aria-busy="true"
      aria-label={label}
      data-testid="loading-skeleton"
    >
      <div className="skeleton skeleton-title" />
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="skeleton skeleton-line" style={{ width: `${88 - i * 8}%` }} />
      ))}
    </div>
  )
}
