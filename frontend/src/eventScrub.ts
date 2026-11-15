/**
 * SEC-1: one scrub rule for every error-report event before it leaves the
 * browser. It is the same rule as the backend's app/core/event_scrub.py:
 *
 *  - payload subtrees (request bodies, cookies, stack-frame locals) are
 *    dropped whole;
 *  - a field whose normalised name is a credential kind has its value
 *    replaced with "[redacted]";
 *  - every remaining string loses e-mail addresses, URI or key=value
 *    credentials, bearer tokens, credential assignments and provider key
 *    shapes.
 *
 * Top-level SDK identifiers (event id, release, platform, level,
 * timestamps) pass through. A failed scrub drops the event (returns null),
 * so raw data never ships. No product, route or field of any one screen is
 * named here.
 */

export const REDACTED = '[redacted]'

export const DROPPED_KINDS: ReadonlySet<string> = new Set(['data', 'body', 'cookies', 'vars'])

export const SECRET_KIND_PARTS: readonly string[] = [
  'authorization',
  'cookie',
  'token',
  'secret',
  'password',
  'passwd',
  'passphrase',
  'apikey',
  'accesskey',
  'privatekey',
  'credential',
  'connectionstring',
  'databaseurl',
  'dsn',
  'signature',
  'bearer',
  'sessionid',
  'email',
]

export const SECRET_KIND_EXACT: ReadonlySet<string> = new Set([
  'key',
  'auth',
  'pwd',
  'session',
  'otp',
  'pin',
  'mail',
])

export const ENVELOPE_FIELDS: ReadonlySet<string> = new Set([
  'event_id',
  'release',
  'dist',
  'environment',
  'platform',
  'level',
  'timestamp',
  'start_timestamp',
  'type',
])

const VALUE_PATTERNS: ReadonlyArray<[RegExp, string]> = [
  [/\b([a-z][a-z0-9+.-]*:\/\/)[^\s/@]+@/gi, `$1${REDACTED}@`],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, REDACTED],
  [/\b(password|pwd)\s*=\s*[^;\s]+/gi, `$1=${REDACTED}`],
  [/\bbearer\s+[A-Za-z0-9._~+/=-]+/gi, `Bearer ${REDACTED}`],
  [
    /\b(api[_-]?key|apikey|secret|token|access[_-]?key|private[_-]?key)\s*[=:]\s*[^\s&;,]+/gi,
    `$1=${REDACTED}`,
  ],
  [/\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b/g, REDACTED],
  [/\bAKIA[0-9A-Z]{16}\b/g, REDACTED],
  [/\bgh[pousr]_[A-Za-z0-9]{20,}\b/g, REDACTED],
  [/\bxox[baprs]-[A-Za-z0-9-]{20,}\b/g, REDACTED],
]

const MAX_DEPTH = 16

function kind(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '')
}

/** True when a field named `name` holds credential material. */
export function isSecretKind(name: string): boolean {
  const k = kind(name)
  return SECRET_KIND_EXACT.has(k) || SECRET_KIND_PARTS.some((part) => k.includes(part))
}

/** Remove e-mails, credentials and secret-shaped values from `text`. */
export function scrubText(text: string): string {
  return VALUE_PATTERNS.reduce((out, [pattern, replacement]) => out.replace(pattern, replacement), text)
}

function scrub(value: unknown, depth: number): unknown {
  if (depth > MAX_DEPTH) return REDACTED
  if (Array.isArray(value)) {
    // A header list arrives as [name, value] pairs.
    if (value.length === 2 && typeof value[0] === 'string' && isSecretKind(value[0])) {
      return [value[0], REDACTED]
    }
    return value.map((item) => scrub(item, depth + 1))
  }
  if (value !== null && typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value)) {
      if (DROPPED_KINDS.has(kind(k))) continue
      out[k] = isSecretKind(k) ? REDACTED : scrub(v, depth + 1)
    }
    return out
  }
  if (typeof value === 'string') return scrubText(value)
  return value
}

/** Sentry `beforeSend` / `beforeSendTransaction` hook. Never throws. */
export function scrubEvent<T>(event: T): T | null {
  try {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(event as Record<string, unknown>)) {
      out[k] = ENVELOPE_FIELDS.has(k) ? v : scrub(v, 1)
    }
    return out as T
  } catch {
    return null
  }
}
