# 2026-11-17 — picomatch 4.0.7 (npm audit, front-end build and test tooling)

**Date:** 2026-11-17
**Expires:** 2026-12-01 (14 days). `scripts/dep_audit.py` fails the audit
the day after this date. Do not extend it silently: ship the fix or write
a fresh dated note.
**Package:** `picomatch==4.0.7` (one copy in `frontend/package-lock.json`,
`"dev": true`; latest published on this date is 4.0.7)
**Decision:** `accept_until_expiry` for GHSA-fc5q-39xc-vjpf only. Build and
test tooling only; no user-controlled string reaches a glob pattern.

## GHSA-fc5q-39xc-vjpf

- **Alias:** CVE-2026-112366
- **Scanner:** picomatch vulnerable to ReDoS through single-branch repeated
  extglobs. Severity high. Vulnerable range `<= 4.0.7`.
- **Patched release:** none. GitHub advisory API on 2026-11-17:
  `first_patched_version: null` (advisory updated 2026-11-17T15:56:44Z).
  `npm view picomatch dist-tags`: `latest` is `4.0.7`.
- **What npm calls a fix:** `fixAvailable` names a major DOWNGRADE of the
  parents (vite 8.3.5 -> 6.2.7, vitest 5.0.5 -> 3.1.4, typescript-eslint
  8.72.0 -> 8.47.0, @vitejs/plugin-react 6.1.2 -> 5.2.0). That is not a
  patched picomatch. Because npm reports it as truthy, the script would call
  an `accept_until_fix` row stale, so this row uses a dated expiry instead.

### Dependency path (every node is `"dev": true` in the lockfile)

- `vite` -> `picomatch`; `vite` -> `tinyglobby` -> `picomatch` and
  `tinyglobby` -> `fdir` -> `picomatch`
- `vitest` -> `picomatch`; `vitest` -> `tinyglobby`
- `typescript-eslint` -> `@typescript-eslint/typescript-estree` ->
  `tinyglobby` -> `picomatch`

The root runtime `dependencies` are `@sentry/react`, `react` and
`react-dom`. None of them reaches picomatch (the test twin walks the lock
graph to check this).

### Not shipped

- `frontend/Dockerfile.prod` runtime stage copies only `dist/`,
  `serve.mjs`, `security-headers.json` and `package.json`; it runs no
  `npm install`. `serve.mjs` imports only `node:` built-ins.
- No non-test file under `frontend/src` imports picomatch, tinyglobby,
  fdir or vite, and the built `dist/assets` carry no picomatch code.

### No user input reaches a glob pattern

The patterns picomatch compiles come from repo config only
(`vite.config.ts` test `exclude`, eslint file globs, vitest defaults). None
of them contains an extglob (`*(`, `+(`, `?(`, `@(`, `!(`). The vite dev
server is not deployed.

## Revisit

- The day a patched picomatch is published: upgrade the lockfile and delete
  this row and note.
- 2026-12-01 at the latest: the audit goes red by itself on 2026-12-02.
