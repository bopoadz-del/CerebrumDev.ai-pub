import { useEffect, useState } from 'react'
import { getHealth } from './api/factory'
import { factoryCodeCliHonesty, factoryCodeCliStatusTitle } from './buildProgress'

/** Live /health factory_code_cli honesty — null when the probe is clean or unreachable. */
export function useFactoryCodeCliHonesty(): string | null {
  const [message, setMessage] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    getHealth()
      .then((health) => {
        if (!cancelled) setMessage(factoryCodeCliHonesty(health.factory_code_cli))
      })
      .catch(() => {
        if (!cancelled) setMessage(null)
      })
    return () => {
      cancelled = true
    }
  }, [])
  return message
}

export function FactoryCodeCliStatus({
  message,
  testId = 'factory-code-cli-status',
}: {
  message: string | null
  testId?: string
}) {
  if (!message) return null
  return (
    <div className="panel dim error-box factory-cli-status" role="status" data-testid={testId}>
      <span className="status-pill status-pill-failed">{factoryCodeCliStatusTitle(message)}</span>
      <p>{message}</p>
    </div>
  )
}
