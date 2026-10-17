import { useEffect, useRef, useState } from 'react'
import {
  auth,
  billing,
  clearSession,
  domains,
  factoryAccessPaused,
  isDomainStoreUnreachable,
  isEmailNotVerifiedError,
  isTransientBootError,
  isUnauthenticatedError,
  sessions,
  setSession,
  signOut,
  type AccountInfo,
} from './api/factory'
import { AuthGate, VerifyEmailGate } from './authGates'
import { Account, Floor, Platforms, Subscription } from './factoryViews'

export { AuthGate, VerifyEmailGate } from './authGates'
export { Account, BlueprintCard, Floor, Platforms, Subscription } from './factoryViews'

export type View = 'floor' | 'platforms' | 'subscription' | 'account'

const VIEW_PATHS: Record<View, string> = {
  floor: '/',
  platforms: '/platforms',
  subscription: '/subscription',
  account: '/account',
}

/** Map a browser pathname to the signed-in shell view (deep-link / reload safe). */
export function viewFromPath(pathname: string): View {
  if (pathname === '/account') return 'account'
  if (pathname === '/subscription') return 'subscription'
  if (pathname === '/platforms' || pathname.startsWith('/platforms/')) return 'platforms'
  if (pathname === '/floor' || pathname.startsWith('/floor/')) return 'floor'
  return 'floor'
}

export function pathFromView(view: View, sessionId?: string | null): string {
  if (sessionId && (view === 'floor' || view === 'platforms')) {
    return `/${view}/${encodeURIComponent(sessionId)}`
  }
  return VIEW_PATHS[view]
}

const SESSION_PATH = /^\/(?:floor|platforms)\/([^/]+)\/?$/

/** Session id from `/floor/{sessionId}` or `/platforms/{sessionId}`. */
export function sessionIdFromPath(pathname: string): string | null {
  const match = pathname.match(SESSION_PATH)
  if (!match) return null
  try {
    const trimmed = decodeURIComponent(match[1]).trim()
    return trimmed || null
  } catch {
    return null
  }
}

/** Path param wins over `?session=` so a stale query cannot override the URL. */
export function requestedSessionFromLocation(pathname: string, search: string): string | null {
  return sessionIdFromPath(pathname) ?? sessionQueryParam(search)
}

/** Floor / Platforms surfaces that may carry a session occupant. */
export function isSessionSurfacePath(pathname: string): boolean {
  return (
    pathname === '/' ||
    pathname === '/floor' ||
    pathname === '/platforms' ||
    pathname.startsWith('/floor/') ||
    pathname.startsWith('/platforms/')
  )
}

/**
 * Durable Floor / Platforms URLs are `/floor/{id}` and `/platforms/{id}`.
 * Legacy `?session=` on `/` is a second surface that can diverge from
 * canonical honesty — rewrite it before first paint.
 * Returns null when the location is already canonical.
 */
export function canonicalSessionLocation(pathname: string, search: string): string | null {
  if (!isSessionSurfacePath(pathname)) return null
  const params = new URLSearchParams(search.startsWith('?') ? search.slice(1) : search)
  const queryId = sessionQueryParam(search)
  const pathId = sessionIdFromPath(pathname)
  if (pathId) {
    if (!params.has('session')) return null
    params.delete('session')
    const q = params.toString()
    const path = pathname.replace(/\/$/, '') || `/${viewFromPath(pathname)}/${encodeURIComponent(pathId)}`
    return q ? `${path}?${q}` : path
  }
  if (!queryId) return null
  const view = viewFromPath(pathname)
  if (view !== 'floor' && view !== 'platforms') return null
  params.delete('session')
  const q = params.toString()
  const path = `/${view}/${encodeURIComponent(queryId)}`
  return q ? `${path}?${q}` : path
}

/** Rewrite `?session=` onto `/floor/{id}` (or `/platforms/{id}`). Idempotent. */
export function ensureCanonicalSessionUrl(): string | null {
  if (typeof window === 'undefined') return null
  const next = canonicalSessionLocation(window.location.pathname, window.location.search)
  if (!next) return null
  const dest = `${next}${window.location.hash}`
  const cur = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (cur === dest) return null
  window.history.replaceState(null, '', dest)
  return dest
}

/**
 * `/floor/{id}` (or `/platforms/{id}`) is the occupant the shell must paint.
 * A live WRITER bind / list[0] must not replace a path id.
 */
export function pathSessionWins(
  pathSessionId: string | null,
  boundSessionId: string | null,
): string | null {
  return pathSessionId ?? boundSessionId
}

/** Public auth URLs must not become a full-page "Factory unreachable" on a race. */
export function isPublicAuthPath(pathname: string): boolean {
  return (
    pathname === '/register' ||
    pathname === '/login' ||
    pathname === '/forgot-password' ||
    pathname === '/reset-password' ||
    pathname === '/verify-email'
  )
}

/** Signed-in visits here stay on the floor — do not show the auth form or sign out. */
export function isSignedInAuthRedirectPath(pathname: string): boolean {
  return pathname === '/login' || pathname === '/register'
}

/**
 * Password-reset URLs must mount AuthGate even when a ``cdt`` cookie exists.
 * ``/login`` and ``/register`` redirect to Floor when already signed in;
 * a reset email click must never do that — the token would be ignored.
 */
export function isForcedPublicAuthPath(pathname: string): boolean {
  return pathname === '/forgot-password' || pathname === '/reset-password'
}

/** `?session=` from a Floor / Platforms deep-link. Empty or whitespace is absent. */
export function sessionQueryParam(search: string): string | null {
  const raw = new URLSearchParams(search.startsWith('?') ? search : `?${search}`).get('session')
  const trimmed = raw?.trim() ?? ''
  return trimmed || null
}

export type SessionListItem = {
  session_id?: string
  title?: string
  stage?: 'empty' | 'talking' | 'blueprint' | 'build'
  updated_at?: string | null
}

const STAGE_LABEL: Record<string, string> = {
  empty: 'new',
  talking: 'talking',
  blueprint: 'blueprint',
  build: 'build',
}

/** The sessions a person has, reachable. They were always kept server-side;
 *  the rail showed only the current one's id, so starting a new session made
 *  the previous one -- and any build running in it -- impossible to get back
 *  to. */
export function SessionList({
  items,
  currentId,
  onOpen,
  onDelete,
  open = false,
}: {
  items: SessionListItem[]
  currentId: string | null
  onOpen: (id: string) => void
  /** Resolves when the session is gone; rejects with a message to show. */
  onDelete?: (id: string) => Promise<void>
  /** On phones the list is a drawer; this opens it. Ignored on desktop. */
  open?: boolean
}) {
  // Two-step, inline: a browser confirm() blocks the page, and a one-click
  // delete of a session holding a generated platform is too easy to misfire.
  const [confirming, setConfirming] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const rows = items.filter((s): s is SessionListItem & { session_id: string } => !!s.session_id)
  if (rows.length === 0) return null
  return (
    <section
      className={'rail-sessions' + (open ? ' rail-sessions--open' : '')}
      aria-label="Your sessions"
    >
      <h2 className="rail-sessions-head">Sessions</h2>
      <ul>
        {rows.map((s) => {
          const current = s.session_id === currentId
          return (
            <li key={s.session_id} className="rail-session-row">
              {confirming === s.session_id ? (
                <div className="rail-session-confirm" role="group" aria-label={`Delete ${s.title || 'session'}?`}>
                  <span className="rail-session-confirm-q">Delete for good?</span>
                  <button
                    type="button"
                    className="rail-session-yes"
                    disabled={deleting === s.session_id}
                    onClick={async () => {
                      if (!onDelete) return
                      setDeleting(s.session_id)
                      setError(null)
                      try {
                        await onDelete(s.session_id)
                        setConfirming(null)
                      } catch (e) {
                        setError(e instanceof Error ? e.message : 'Could not delete this session.')
                      } finally {
                        setDeleting(null)
                      }
                    }}
                  >
                    {deleting === s.session_id ? 'Deleting…' : 'Delete'}
                  </button>
                  <button
                    type="button"
                    className="rail-session-no"
                    onClick={() => {
                      setConfirming(null)
                      setError(null)
                    }}
                  >
                    Keep
                  </button>
                  {error ? <p className="rail-session-error" role="alert">{error}</p> : null}
                </div>
              ) : null}
              {confirming === s.session_id ? null : onDelete ? (
                <button
                  type="button"
                  className="rail-session-delete"
                  aria-label={`Delete session: ${s.title || 'Untitled session'}`}
                  onClick={() => {
                    setConfirming(s.session_id)
                    setError(null)
                  }}
                >
                  ×
                </button>
              ) : null}
              <button
                type="button"
                hidden={confirming === s.session_id}
                className={'rail-session' + (current ? ' active' : '')}
                aria-current={current ? 'page' : undefined}
                onClick={() => onOpen(s.session_id)}
                title={s.title || s.session_id}
              >
                <span className="rail-session-title">{s.title || 'Untitled session'}</span>
                <span className="rail-session-meta">
                  {STAGE_LABEL[s.stage ?? ''] ?? s.session_id.slice(5, 13)}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

export type BootSessionResult =
  | { status: 'selected'; sessionId: string }
  | { status: 'create' }
  | { status: 'missing'; requested: string }

/**
 * Pick the boot session from `/floor/{id}`, `/platforms/{id}`, or `?session=`
 * plus the account list. A requested id that is not in the list is missing —
 * never fall through to list[0], which would paint another build's Floor.
 */
export function resolveBootSession(
  requested: string | null,
  list: SessionListItem[],
): BootSessionResult {
  if (requested) {
    const match = list.find((s) => s.session_id === requested)
    if (match?.session_id) return { status: 'selected', sessionId: match.session_id }
    return { status: 'missing', requested }
  }
  const first = list[0]?.session_id
  if (first) return { status: 'selected', sessionId: first }
  return { status: 'create' }
}

const NAV_ITEMS: { view: View; label: string; shortLabel: string; icon: string }[] = [
  { view: 'floor', label: 'Factory Floor', shortLabel: 'Floor', icon: '⌂' },
  { view: 'platforms', label: 'Your Platforms', shortLabel: 'Platforms', icon: '▣' },
  { view: 'subscription', label: 'Subscription', shortLabel: 'Plan', icon: '◈' },
  { view: 'account', label: 'Account', shortLabel: 'Account', icon: '○' },
]

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(() => {
    if (typeof window === 'undefined') return null
    return isForcedPublicAuthPath(window.location.pathname) ? false : null
  })
  const [view, setView] = useState<View>(() => {
    if (typeof window !== 'undefined') ensureCanonicalSessionUrl()
    return typeof window === 'undefined' ? 'floor' : viewFromPath(window.location.pathname)
  })
  const [sessionId, setSessionId] = useState<string | null>(() =>
    typeof window === 'undefined' ? null : sessionIdFromPath(window.location.pathname),
  )
  const [bootError, setBootError] = useState<string | null>(null)
  const [needsEmailVerify, setNeedsEmailVerify] = useState(false)
  const [pendingDevToken, setPendingDevToken] = useState<string | null>(null)
  const [domainStoreNotice, setDomainStoreNotice] = useState<string | null>(null)
  const [bootNonce, setBootNonce] = useState(0)
  const [accountMe, setAccountMe] = useState<AccountInfo | null>(null)
  const [accessPaused, setAccessPaused] = useState(false)
  const [alreadySignedInNotice, setAlreadySignedInNotice] = useState(false)
  const [sessionLinkError, setSessionLinkError] = useState<string | null>(null)
  // Phones hide the sessions list by default; this toggles it as a drawer.
  const [sessionsOpen, setSessionsOpen] = useState(false)
  const [sessionList, setSessionList] = useState<SessionListItem[]>([])
  const [locationEpoch, setLocationEpoch] = useState(0)
  const sessionListRef = useRef<SessionListItem[]>([])
  sessionListRef.current = sessionList

  // Titles and stages move as a conversation does; the boot-time list would
  // leave a session reading "New session" long after it has a blueprint.
  // Refresh on every switch. A failed refresh keeps the list already shown.
  useEffect(() => {
    if (!authed || !sessionId) return
    let cancelled = false
    sessions
      .list()
      .then((list) => {
        if (cancelled) return
        const arr = Array.isArray(list) ? list : list.sessions ?? []
        if (arr.length === 0) return
        const known = new Set(arr.map((s) => s.session_id))
        const unsynced = sessionListRef.current.filter(
          (s) => s.session_id && !known.has(s.session_id),
        )
        const next = [...unsynced, ...arr]
        sessionListRef.current = next
        setSessionList(next)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [authed, sessionId])

  function rememberSession(id: string) {
    const next = [{ session_id: id }, ...sessionListRef.current.filter((s) => s.session_id !== id)]
    sessionListRef.current = next
    setSessionList(next)
  }

  function go(next: View, sid: string | null = sessionId) {
    if ((next === 'floor' || next === 'platforms') && sid && sid !== sessionId) {
      setSessionId(sid)
    }
    setView(next)
    setAlreadySignedInNotice(false)
    const bound = next === 'floor' || next === 'platforms' ? sid : null
    const path = pathFromView(next, bound)
    if (typeof window !== 'undefined' && window.location.pathname !== path) {
      window.history.pushState(null, '', path)
    }
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const params = new URLSearchParams(window.location.search)
      const linkToken = params.get('token')
      const requestedSession = requestedSessionFromLocation(
        window.location.pathname,
        window.location.search,
      )
      let verifyLinkOk = false
      if (linkToken && window.location.pathname === '/verify-email') {
        // Keep the public-auth path while the token is consumed so a
        // transient /me race is not painted as "Factory unreachable".
        window.history.replaceState(null, '', '/verify-email')
        try {
          await auth.verifyEmail(linkToken)
          verifyLinkOk = true
        } catch {
          /* still unverified; boot surfaces the verify gate */
        }
      }
      if (isForcedPublicAuthPath(window.location.pathname)) {
        if (!cancelled) {
          setNeedsEmailVerify(false)
          setBootError(null)
          setAuthed(false)
        }
        return
      }
      try {
        const me = await auth.me()
        if (me.email) setSession(String(me.email))
        const [list, bill] = await Promise.all([
          sessions.list(),
          billing.status().catch(() => null),
        ])
        const arr = Array.isArray(list) ? list : list.sessions ?? []
        const pathId = sessionIdFromPath(window.location.pathname)
        const choice = resolveBootSession(requestedSession, arr)
        if (choice.status === 'missing') {
          if (!cancelled) {
            setAccountMe(me)
            setAccessPaused(factoryAccessPaused(bill))
            setNeedsEmailVerify(false)
            setBootError(null)
            sessionListRef.current = arr
            setSessionList(arr)
            setSessionLinkError(choice.requested)
            setSessionId(null)
            setAuthed(true)
            setView(viewFromPath(window.location.pathname))
          }
          return
        }
        let sid = choice.status === 'selected' ? choice.sessionId : undefined
        // Path id stays the occupant even if list[0] is a live WRITER run.
        if (pathId && sid && sid !== pathId) {
          const pathChoice = resolveBootSession(pathId, arr)
          if (pathChoice.status === 'selected') sid = pathChoice.sessionId
        }
        let nextList = arr
        if (!sid) {
          const created = await sessions.create()
          sid = created.session_id
          if (sid) nextList = [{ session_id: sid }, ...arr]
        }
        if (!cancelled) {
          setAccountMe(me)
          setAccessPaused(factoryAccessPaused(bill))
          setNeedsEmailVerify(false)
          setBootError(null)
          sessionListRef.current = nextList
          setSessionList(nextList)
          setSessionLinkError(null)
          setSessionId(pathSessionWins(pathId, sid ?? null))
          setAuthed(true)
          if (isSignedInAuthRedirectPath(window.location.pathname)) {
            window.history.replaceState(null, '', '/')
            setView('floor')
            setAlreadySignedInNotice(true)
          } else if (window.location.pathname === '/verify-email') {
            window.history.replaceState(null, '', '/')
            setView('floor')
          } else {
            setView(viewFromPath(window.location.pathname))
          }
        }
      } catch (e) {
        if (!cancelled) {
          if (isUnauthenticatedError(e)) {
            clearSession()
            setNeedsEmailVerify(false)
            setSessionLinkError(null)
            setSessionId(null)
            if (verifyLinkOk && window.location.pathname === '/verify-email') {
              try {
                sessionStorage.setItem(
                  'cerebrum.factory.authNotice',
                  'Email verified. Sign in to enter the factory.',
                )
              } catch {
                /* ignore quota / private-mode */
              }
              window.history.replaceState(null, '', '/login')
            }
            setAuthed(false)
          } else if (isEmailNotVerifiedError(e)) {
            setBootError(null)
            setNeedsEmailVerify(true)
            setAuthed(true)
          } else if (isTransientBootError(e) && isPublicAuthPath(window.location.pathname)) {
            clearSession()
            setNeedsEmailVerify(false)
            setSessionId(null)
            setBootError(null)
            setAuthed(false)
          } else {
            setBootError(e instanceof Error ? e.message : 'backend unreachable')
            setAuthed(true)
          }
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [bootNonce])

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    domains
      .list()
      .then(() => {
        if (!cancelled) setDomainStoreNotice(null)
      })
      .catch((e) => {
        if (!cancelled && isDomainStoreUnreachable(e)) {
          setDomainStoreNotice('Domain store unreachable')
        }
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  // pushState (in-app go / live deep-link tools) does not fire popstate.
  // Rebind from the URL on every history write so a live WRITER occupant
  // cannot keep the Floor after the path changes to another session.
  useEffect(() => {
    const bump = () => setLocationEpoch((n) => n + 1)
    const hist = window.history
    const origPush = hist.pushState.bind(hist)
    const origReplace = hist.replaceState.bind(hist)
    hist.pushState = ((...args: Parameters<History['pushState']>) => {
      origPush(...args)
      bump()
    }) as History['pushState']
    hist.replaceState = ((...args: Parameters<History['replaceState']>) => {
      origReplace(...args)
      bump()
    }) as History['replaceState']
    window.addEventListener('popstate', bump)
    return () => {
      hist.pushState = origPush
      hist.replaceState = origReplace
      window.removeEventListener('popstate', bump)
    }
  }, [])

  useEffect(() => {
    if (!authed) return
    if (ensureCanonicalSessionUrl()) return
    const path = window.location.pathname
    setView(viewFromPath(path))
    const requested = requestedSessionFromLocation(path, window.location.search)
    if (!requested) return
    const choice = resolveBootSession(requested, sessionListRef.current)
    if (choice.status === 'selected') {
      setSessionLinkError(null)
      setSessionId(choice.sessionId)
    } else if (choice.status === 'missing' && sessionListRef.current.length > 0) {
      setSessionLinkError(choice.requested)
      setSessionId(null)
    }
  }, [authed, locationEpoch, sessionList])

  if (authed === null)
    return (
      <div className="center-screen">
        <div className="loader">Opening your factory floor…</div>
      </div>
    )
  if (!authed)
    return (
      <AuthGate
        onAuthed={(info) => {
          setPendingDevToken(info?.devVerificationToken ?? null)
          setAuthed(true)
          setBootNonce((n) => n + 1)
        }}
      />
    )
  if (needsEmailVerify)
    return (
      <VerifyEmailGate
        initialDevToken={pendingDevToken}
        onVerified={() => {
          setNeedsEmailVerify(false)
          setPendingDevToken(null)
          setBootError(null)
          setSessionId(null)
          setBootNonce((n) => n + 1)
        }}
        onLogout={() => {
          void signOut().then(() => {
            setNeedsEmailVerify(false)
            setPendingDevToken(null)
            setAuthed(false)
          })
        }}
      />
    )
  if (bootError)
    return (
      <div className="center-screen">
        <div className="panel narrow">
          <h2>Factory unreachable</h2>
          <p className="dim">{bootError}</p>
          <button
            onClick={() => {
              setBootError(null)
              setSessionId(null)
              setBootNonce((n) => n + 1)
            }}
          >
            Retry
          </button>
        </div>
      </div>
    )
  if (sessionLinkError)
    return (
      <div className="center-screen">
        <div className="panel narrow">
          <h2>Session not found</h2>
          <p className="dim">
            Session <code>{sessionLinkError}</code> is not in your list. The Floor
            did not open a different build, so a failed or unfinished export is
            never shown as Finished or Downloadable.
          </p>
          <button
            type="button"
            onClick={() => {
              if (typeof window !== 'undefined') {
                const url = new URL(window.location.href)
                url.searchParams.delete('session')
                if (sessionIdFromPath(url.pathname)) url.pathname = '/'
                const next = `${url.pathname}${url.search}${url.hash}`
                window.history.replaceState(null, '', next || '/')
              }
              setSessionLinkError(null)
              setSessionId(null)
              setBootNonce((n) => n + 1)
            }}
          >
            Open Factory Floor
          </button>
        </div>
      </div>
    )
  if (!sessionId)
    return (
      <div className="center-screen">
        <div className="loader">Opening your factory floor…</div>
      </div>
    )

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true">
            C
          </div>
          <div className="brand-text">
            <div className="brand-name">CerebrumDev.ai</div>
            <div className="brand-sub">the factory</div>
          </div>
        </div>
        <nav aria-label="Factory navigation">
          {NAV_ITEMS.map((item) => (
            <NavBtn
              key={item.view}
              label={item.label}
              shortLabel={item.shortLabel}
              icon={item.icon}
              active={view === item.view}
              onClick={() => go(item.view)}
            />
          ))}
        </nav>
        <button
          type="button"
          className="rail-sessions-toggle"
          aria-expanded={sessionsOpen}
          aria-label="Your sessions"
          onClick={() => setSessionsOpen((o) => !o)}
        >
          <span aria-hidden="true">☰</span> Sessions
        </button>
        {sessionsOpen && (
          <button
            type="button"
            className="rail-sessions-backdrop"
            aria-label="Close sessions"
            onClick={() => setSessionsOpen(false)}
          />
        )}
        <SessionList
          open={sessionsOpen}
          items={sessionList}
          currentId={sessionId}
          onOpen={(id) => {
            setSessionsOpen(false)
            go('floor', id)
          }}
          onDelete={async (id) => {
            await sessions.remove(id)
            const next = sessionListRef.current.filter((s) => s.session_id !== id)
            sessionListRef.current = next
            setSessionList(next)
            if (id !== sessionId) return
            // The open session is the one deleted: land on another, or a fresh one.
            const fallback = next.find((s) => s.session_id)?.session_id
            if (fallback) {
              go('floor', fallback)
              return
            }
            const created = await sessions.create()
            if (created.session_id) {
              rememberSession(created.session_id)
              go('floor', created.session_id)
            }
          }}
        />
        <div className="rail-foot">
          <span className="dot" aria-hidden="true" />
          <span className="rail-foot-text">session {sessionId.slice(0, 12)}…</span>
        </div>
      </aside>
      <main>
        {view === 'floor' && (
          <Floor
            key={sessionId}
            sessionId={sessionId}
            goPlatforms={() => go('platforms')}
            accessPaused={accessPaused}
            notice={alreadySignedInNotice ? 'Already signed in.' : null}
            onNewSession={
              accessPaused
                ? undefined
                : async () => {
                    const created = await sessions.create()
                    const fresh = created.session_id
                    if (fresh) rememberSession(fresh)
                    go('floor', fresh)
                  }
            }
          />
        )}
        {view === 'platforms' && (
          <Platforms sessionId={sessionId} goFloor={() => go('floor')} />
        )}
        {view === 'subscription' && <Subscription />}
        {view === 'account' && (
          <Account
            initialMe={accountMe}
            onLogout={() => {
              void signOut().then(() => setAuthed(false))
            }}
          />
        )}
      </main>
      {domainStoreNotice && (
        <div className="toast" role="status">
          {domainStoreNotice}
        </div>
      )}
    </div>
  )
}

function NavBtn({
  label,
  shortLabel,
  icon,
  active,
  onClick,
}: {
  label: string
  shortLabel: string
  icon: string
  active: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`nav-btn ${active ? 'active' : ''}`}
      onClick={onClick}
      aria-label={label}
      title={label}
      aria-current={active ? 'page' : undefined}
    >
      <span className="nav-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="nav-label nav-label-full">{label}</span>
      <span className="nav-label nav-label-short" aria-hidden="true">
        {shortLabel}
      </span>
    </button>
  )
}
