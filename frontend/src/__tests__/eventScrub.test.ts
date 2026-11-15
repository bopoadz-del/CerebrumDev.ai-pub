/**
 * SEC-1: an event carrying each sensitive kind goes in; none comes out.
 * Every value is synthetic and stands for one kind.
 */
import { describe, expect, it } from 'vitest'
import { REDACTED, isSecretKind, scrubEvent, scrubText } from '../eventScrub'

const SECRET_CASES: Array<[string, Record<string, unknown>, string]> = [
  ['authorization header', { request: { headers: { Authorization: 'Bearer a1b2c3d4e5f6g7' } } }, 'a1b2c3d4e5f6g7'],
  ['cookie header', { request: { headers: { Cookie: 'sid=q9w8e7r6t5' } } }, 'q9w8e7r6t5'],
  ['request cookies', { request: { cookies: { sid: 'z1x2c3v4b5' } } }, 'z1x2c3v4b5'],
  ['header pair list', { request: { headers: [['X-Api-Key', 'k7j6h5g4f3']] } }, 'k7j6h5g4f3'],
  ['token field', { extra: { refresh_token: 't0k3nv4lu3x' } }, 't0k3nv4lu3x'],
  ['password field', { extra: { form: { password: 'hunter2-xyz' } } }, 'hunter2-xyz'],
  ['uri connection string', { message: 'connect postgres://admin:s3cr3tpw@db.internal:5432/x' }, 's3cr3tpw'],
  ['kv connection string', { message: 'Server=db;User=u;Password=kvpass123;' }, 'kvpass123'],
  ['email in text', { exception: { values: [{ value: 'no user jane.doe@example.org' }] } }, 'jane.doe@example.org'],
  ['email field', { user: { email: 'ops@example.net' } }, 'ops@example.net'],
  ['bearer in text', { breadcrumbs: [{ message: 'sent bearer qwe.rty.uio' }] }, 'qwe.rty.uio'],
  ['token in url query', { request: { url: 'https://h.example/cb?token=qs-tok-321&x=1' } }, 'qs-tok-321'],
  ['request body', { request: { data: { note: 'body-text-555' } } }, 'body-text-555'],
  [
    'frame locals',
    { exception: { values: [{ stacktrace: { frames: [{ function: 'f', vars: { x: 'local-var-777' } }] } }] } },
    'local-var-777',
  ],
  ['provider key shape', { message: 'used sk-ABCDEFGHIJKLMNOPQRSTUV' }, 'sk-ABCDEFGHIJKLMNOPQRSTUV'],
]

describe('scrubEvent', () => {
  it.each(SECRET_CASES)('removes %s', (_label, event, secret) => {
    const out = scrubEvent(event)
    expect(out).not.toBeNull()
    expect(JSON.stringify(out)).not.toContain(secret)
  })

  it.each([
    [{ exception: { values: [{ type: 'TypeError', value: 'x is undefined' }] } }, 'x is undefined'],
    [{ release: '0123456789abcdef0123456789abcdef01234567', level: 'error' }, '0123456789abcdef0123456789abcdef01234567'],
    [{ tags: { keywords: 'a,b', author: 'team-ui' } }, 'team-ui'],
    [{ request: { headers: [['Accept', 'text/html']] } }, 'text/html'],
  ])('keeps an ordinary error reportable (%#)', (event, kept) => {
    expect(JSON.stringify(scrubEvent(event))).toContain(kept)
  })

  it.each([null, undefined])('drops the event when it cannot be scrubbed (%s)', (event) => {
    expect(scrubEvent(event)).toBeNull()
  })

  it.each([20, 40])('cuts pathological nesting at depth %i', (depth) => {
    const root: Record<string, unknown> = {}
    let node = root
    for (let i = 0; i < depth; i += 1) {
      const next: Record<string, unknown> = {}
      node.n = next
      node = next
    }
    node.leaf = 'deep-leaf-888'
    const out = JSON.stringify(scrubEvent({ extra: root }))
    expect(out).not.toContain('deep-leaf-888')
    expect(out).toContain(REDACTED)
  })
})

describe('isSecretKind', () => {
  it.each([
    ['Authorization', true],
    ['X-CSRF-Token', true],
    ['session', true],
    ['key', true],
    ['keywords', false],
    ['author', false],
    ['value', false],
  ])('%s -> %s', (name, secret) => {
    expect(isSecretKind(name)).toBe(secret)
  })
})

describe('scrubText', () => {
  it.each([
    ['redis://u:p4ss@cache:6379/0', `redis://${REDACTED}@cache:6379/0`],
    ['plain words stay', 'plain words stay'],
  ])('%s', (text, kept) => {
    expect(scrubText(text)).toBe(kept)
  })
})
