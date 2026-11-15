/**
 * SEC-2: one rule for every secret the user types into the browser.
 *
 *  - A secret field is described by its PURPOSE, never by its screen:
 *      current  - a secret the user already has (sign in, confirm identity)
 *      new      - a secret the user is choosing now (register, reset, change)
 *      one-time - a secret that is entered, used, and must not be offered
 *                 or saved again by the browser
 *    The purpose alone decides the field's type and autocomplete hint, so two
 *    fields with the same purpose can never disagree.
 *  - A secret is never written to browser storage. `purgeStoredSecrets`
 *    removes, by key kind, any secret an older build left behind.
 */

export type SecretPurpose = 'current' | 'new' | 'one-time'

/** WHATWG autofill tokens, one per purpose. */
export const SECRET_AUTOCOMPLETE: Readonly<Record<SecretPurpose, string>> = {
  current: 'current-password',
  new: 'new-password',
  'one-time': 'one-time-code',
}

export interface SecretFieldProps {
  type: 'password'
  autoComplete: string
  autoCapitalize: 'off'
  autoCorrect: 'off'
  spellCheck: false
}

/** Input props shared by every secret field of the given purpose. */
export function secretFieldProps(purpose: SecretPurpose): SecretFieldProps {
  return {
    type: 'password',
    autoComplete: SECRET_AUTOCOMPLETE[purpose],
    autoCapitalize: 'off',
    autoCorrect: 'off',
    spellCheck: false,
  }
}

/** Storage-key kinds that hold a typed secret (passwords, pass phrases, keys). */
const STORED_SECRET_PARTS: readonly string[] = ['password', 'passwd', 'passphrase', 'pwd']
const STORED_SECRET_SUFFIX = 'key'

function kind(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '')
}

/** True when a browser-storage key, by its kind, names a typed secret. */
export function isStoredSecretKey(name: string): boolean {
  const k = kind(name)
  return STORED_SECRET_PARTS.some((part) => k.includes(part)) || k.endsWith(STORED_SECRET_SUFFIX)
}

function storages(): Storage[] {
  const out: Storage[] = []
  for (const pick of [() => window.localStorage, () => window.sessionStorage]) {
    try {
      out.push(pick())
    } catch {
      /* storage blocked (private window) - nothing to purge */
    }
  }
  return out
}

/** Remove every stored secret from local and session storage. Never throws. */
export function purgeStoredSecrets(): string[] {
  const removed: string[] = []
  for (const store of storages()) {
    try {
      const keys = Array.from({ length: store.length }, (_, i) => store.key(i)).filter(
        (k): k is string => typeof k === 'string',
      )
      for (const key of keys) {
        if (isStoredSecretKey(key)) {
          store.removeItem(key)
          removed.push(key)
        }
      }
    } catch {
      /* a broken store must not block the app */
    }
  }
  return removed
}
