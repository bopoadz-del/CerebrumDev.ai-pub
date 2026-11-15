import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { scrubEvent } from './eventScrub'
import './index.css'
import './verifyGate.css'

// Observability: Sentry activates only when the deployment provides a DSN.
// The dynamic import keeps it out of the bundle when unconfigured.
if (import.meta.env.VITE_SENTRY_DSN) {
  import('@sentry/react')
    .then((Sentry) => {
      Sentry.init({
        dsn: import.meta.env.VITE_SENTRY_DSN,
        environment: import.meta.env.MODE,
        release: import.meta.env.VITE_APP_VERSION as string | undefined,
        // @sentry/react 11 removed `sendDefaultPii` (v10: false = no cookies,
        // no user info, no auth headers, no query strings). The v11
        // replacement, `dataCollection`, DEFAULTS TO COLLECT-EVERYTHING --
        // including stack-frame variable VALUES, which the browser SDK never
        // captured before -- so deleting the old flag would silently flip PII
        // collection on. This block states the v10 posture explicitly (equal
        // or more restrictive). Loosening it is a deliberate edit here, never
        // an upgrade side effect.
        dataCollection: {
          userInfo: false,
          cookies: false,
          httpHeaders: false,
          httpBodies: [],
          urlQueryParams: false,
          stackFrameVariables: false,
        },
        // SEC-1: every event and transaction is scrubbed before it leaves
        // the browser (credentials, e-mails, bodies, frame locals).
        beforeSend: scrubEvent,
        beforeSendTransaction: scrubEvent,
        tracesSampleRate: 0.1,
      })
    })
    .catch(() => {
      // Never let observability block the app.
    })
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
