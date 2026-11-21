/**
 * PR-03: the design tokens and the stylesheet rules that read them
 * (UI revamp spec, section 4.6).
 *
 * - Token contrast: reading text at least 4.5:1 and control edges at least
 *   3:1 on all four surfaces; each tone's text reads on its own fill.
 * - Stylesheet scan: no font size under 12px; no hover rule outside a
 *   hover media query; no reference to a custom property nobody defines.
 */
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'

// Read from disk (vitest runs from frontend/): vitest blanks `?raw` CSS
// imports while `css: false`.
const indexCss = readFileSync('src/index.css', 'utf8')
const verifyCss = readFileSync('src/verifyGate.css', 'utf8')

const SHEETS: Record<string, string> = { 'index.css': indexCss, 'verifyGate.css': verifyCss }

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** Every custom property definition, wherever it appears. */
function customProps(css: string): Map<string, string> {
  const out = new Map<string, string>()
  for (const m of stripComments(css).matchAll(/(--[\w-]+)\s*:\s*([^;}]+)[;}]/g)) {
    if (!out.has(m[1])) out.set(m[1], m[2].trim())
  }
  return out
}

const TOKENS = customProps(indexCss)

function resolve(value: string, depth = 0): string {
  const m = value.match(/^var\((--[\w-]+)\)$/)
  if (!m || depth > 8) return value
  const next = TOKENS.get(m[1])
  if (next === undefined) throw new Error(`undefined token ${m[1]}`)
  return resolve(next, depth + 1)
}

function hex(name: string): [number, number, number] {
  const v = resolve(`var(${name})`)
  const m = v.match(/^#([0-9a-f]{6})$/i)
  if (!m) throw new Error(`${name} is not a 6-digit colour: ${v}`)
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function luminance([r, g, b]: [number, number, number]): number {
  const lin = (c: number) => {
    const s = c / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

function contrast(a: string, b: string): number {
  const la = luminance(hex(a))
  const lb = luminance(hex(b))
  const [hi, lo] = la > lb ? [la, lb] : [lb, la]
  return (hi + 0.05) / (lo + 0.05)
}

const SURFACES = ['--bg', '--surface-1', '--surface-2', '--surface-3']
const TONES = ['success', 'info', 'warning', 'danger']

/** Each declaration with the at-rule preludes that enclose it. */
function declarations(css: string): { selector: string; media: string[]; prop: string; value: string }[] {
  const src = stripComments(css)
  const out: { selector: string; media: string[]; prop: string; value: string }[] = []
  const stack: string[] = []
  let buf = ''
  for (const ch of src) {
    if (ch === '{') {
      stack.push(buf.trim())
      buf = ''
    } else if (ch === '}') {
      const selector = stack.pop() ?? ''
      for (const d of buf.split(';')) addDecl(d, selector)
      buf = ''
    } else if (ch === ';' && stack.length && !stack[stack.length - 1].startsWith('@')) {
      addDecl(buf, stack[stack.length - 1])
      buf = ''
    } else {
      buf += ch
    }
  }
  return out

  function addDecl(raw: string, selector: string) {
    const i = raw.indexOf(':')
    if (i < 0 || selector.startsWith('@')) return
    out.push({
      selector,
      media: stack.filter((s) => s.startsWith('@')),
      prop: raw.slice(0, i).trim(),
      value: raw.slice(i + 1).trim(),
    })
  }
}

function px(value: string): number | null {
  const v = resolve(value)
  const m = v.match(/^([\d.]+)(px|rem|em)$/)
  if (!m) return null
  return m[2] === 'px' ? Number(m[1]) : Number(m[1]) * 16
}

describe('design tokens', () => {
  it('reads the real stylesheets', () => {
    expect(indexCss).toContain('--control-border')
    expect(TOKENS.size).toBeGreaterThan(40)
  })

  it('reading text is at least 4.5:1 on all four surfaces', () => {
    for (const s of SURFACES) {
      expect(contrast('--text', s), `--text on ${s}`).toBeGreaterThanOrEqual(4.5)
      expect(contrast('--text-muted', s), `--text-muted on ${s}`).toBeGreaterThanOrEqual(4.5)
    }
    expect(contrast('--text', '--bubble-user')).toBeGreaterThanOrEqual(4.5)
    expect(contrast('--on-accent', '--accent')).toBeGreaterThanOrEqual(4.5)
    expect(contrast('--on-accent', '--accent-pressed')).toBeGreaterThanOrEqual(4.5)
  })

  it('a control edge is at least 3:1 on all four surfaces', () => {
    for (const s of SURFACES) {
      expect(contrast('--control-border', s), `--control-border on ${s}`).toBeGreaterThanOrEqual(3)
    }
  })

  it('each tone: its text reads on its fill and on every surface; its edge is at least 3:1 on its fill', () => {
    for (const t of TONES) {
      expect(contrast(`--${t}-text`, `--${t}-fill`), `${t} text on fill`).toBeGreaterThanOrEqual(4.5)
      expect(contrast('--text', `--${t}-fill`), `body text in a ${t} box`).toBeGreaterThanOrEqual(4.5)
      for (const s of SURFACES) {
        expect(contrast(`--${t}-text`, s), `${t} text on ${s}`).toBeGreaterThanOrEqual(4.5)
      }
      expect(contrast(`--${t}-edge`, `--${t}-fill`), `${t} edge on fill`).toBeGreaterThanOrEqual(3)
    }
  })

  it('a disabled label is still readable (a colour, not opacity)', () => {
    expect(contrast('--text-disabled', '--disabled-fill')).toBeGreaterThanOrEqual(3)
  })

  it('the type scale has five steps and nothing under 12px', () => {
    const steps = ['xs', 'sm', 'md', 'lg', 'xl'].map((k) => px(`var(--text-${k})`))
    expect(steps).toEqual([12, 14, 16, 18, 22])
  })
})

describe('stylesheet scan', () => {
  for (const [name, css] of Object.entries(SHEETS)) {
    const decls = declarations(css)

    it(`${name}: no font size under 12px`, () => {
      const small: string[] = []
      for (const d of decls) {
        const size = d.prop === 'font-size' ? d.value : d.prop === 'font' ? d.value.split(/\s+/).find((p) => /\d/.test(p)) : undefined
        if (!size || size === 'inherit') continue
        const n = px(size.replace(/\/.*$/, ''))
        if (n === null) throw new Error(`${d.selector}: unreadable font size ${size}`)
        if (n < 12) small.push(`${d.selector} ${n}px`)
      }
      expect(small).toEqual([])
    })

    it(`${name}: every hover rule sits inside a hover media query`, () => {
      const loose = decls
        .filter((d) => d.selector.includes(':hover'))
        .filter((d) => !d.media.some((m) => /\(\s*hover\s*:\s*hover\s*\)/.test(m)))
        .map((d) => d.selector)
      expect([...new Set(loose)]).toEqual([])
    })

    it(`${name}: every custom property it reads is defined`, () => {
      const used = [...stripComments(css).matchAll(/var\((--[\w-]+)/g)].map((m) => m[1])
      const missing = used.filter((u) => !TOKENS.has(u))
      expect([...new Set(missing)]).toEqual([])
    })
  }

  it('the outline button carries no built-in left margin', () => {
    const ghostMargins = Object.values(SHEETS)
      .flatMap((css) => declarations(css))
      .filter((d) => /button\.ghost/.test(d.selector) && /^margin/.test(d.prop))
    expect(ghostMargins).toEqual([])
  })

  it('the scan sees what it is looking for', () => {
    // Guard against a parser that silently finds nothing.
    const sample = declarations('a{font-size:10px}@media (hover: hover){b:hover{color:red}}c:hover{color:red}')
    expect(sample.find((d) => d.selector === 'a')?.value).toBe('10px')
    expect(sample.find((d) => d.selector === 'b:hover')?.media).toEqual(['@media (hover: hover)'])
    expect(sample.find((d) => d.selector === 'c:hover')?.media).toEqual([])
  })
})
