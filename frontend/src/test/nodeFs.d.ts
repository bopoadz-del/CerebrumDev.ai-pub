// Test-only: the one Node API the stylesheet scan (tokens.test.ts) reads.
// The app is typed for the browser only (tsconfig "types": ["vite/client"]),
// and vitest blanks `?raw` CSS imports while `css: false`, so the scan reads
// the files from disk.
declare module 'node:fs' {
  export function readFileSync(path: URL | string, encoding: 'utf8'): string
}
