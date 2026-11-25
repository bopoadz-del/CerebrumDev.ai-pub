"""Run a rendered F-8b Playwright spec for real under node, with a minimal
``@playwright/test`` stand-in whose ``page`` / ``request`` do real HTTP
against BASE_URL. Used by the UI grader tests so status assertions are
exercised end to end (two servers, two outcomes), not pinned as substrings."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from app.factory.build.product_grader.ui_grader import UiTenants, new_tenants

#: The tenants a run is given when a test names none: new per test session.
TENANTS = new_tenants()

PRELUDE = r"""
const __tests: [string, any, any][] = []; const __before: any[] = [];
// Playwright's serial mode: a describe block (or the file) configured
// serial skips every test of that block after its first failure.
const __root: any = { serial: false, failed: false, parent: null };
let __group: any = __root;
const __serialOf = (g: any) => { let s: any = null; for (let x = g; x; x = x.parent) if (x.serial) s = x; return s; };
class __Fail extends Error {}
function expect(v: any) {
  const fail = (m: string) => { throw new __Fail(m); };
  return {
    toBe: (x: any) => { if (v !== x) fail(`expected ${x} got ${v}`); },
    toEqual: (x: any) => { if (JSON.stringify(v) !== JSON.stringify(x)) fail(`expected ${JSON.stringify(x)} got ${JSON.stringify(v)}`); },
    toContain: (x: any) => { if (!v.includes(x)) fail(`${JSON.stringify(v)} does not contain ${x}`); },
    toBeVisible: async () => { if (!v || !v.__visible || !v.__visible()) fail('not visible'); },
  };
}
const __resp = (r: Response) => ({ status: () => r.status });
const __request = {
  get: async (u: string, o: any = {}) => __resp(await fetch(u, { headers: o.headers })),
  post: async (u: string, o: any) => __resp(await fetch(u, { method: 'POST', body: o.data, headers: o.headers })),
};
// The page is the text the product served: the last page loaded plus every
// upload / chat reply. getByText(re) is visible iff re matches that text.
let __html = '';
let __url = 'about:blank';
const __base = () => process.env.BASE_URL || '';
const __loc = (find: (t: string) => boolean, act: any = {}) => {
  const l: any = { first: () => l, last: () => l, __visible: () => find(__html), ...act };
  return l;
};
const __page = {
  goto: async (u: string) => { const r = await fetch(u); __html = await r.text(); __url = u; return __resp(r); },
  url: () => __url,
  waitForLoadState: async () => {}, on: () => {},
  getByText: (re: RegExp) => __loc((t) => re.test(t)),
  getByRole: (role: string, o: any = {}) => __loc(() => true, {
    click: async () => { const r = await fetch(__base() + '/__click', { method: 'POST', body: role + ':' + (o.name || '') }); __html += '\n' + await r.text(); },
    fill: async (v: string) => { (__page as any).__typed = v; },
    press: async (k: string) => {
      if (k !== 'Enter') return;
      const r = await fetch(__base() + '/__ask', { method: 'POST', body: (__page as any).__typed || '' });
      __html += '\n' + await r.text();
    },
  }),
  locator: (sel: string) => __loc(() => true, {
    setInputFiles: async (p: string) => {
      const fs = await import('node:fs');
      const r = await fetch(__base() + '/__upload?name=' + encodeURIComponent(p.split('/').pop() || ''), { method: 'POST', body: fs.readFileSync(p) });
      __html += '\n' + await r.text();
    },
  }),
};
const test: any = (name: string, fn: any) => { __tests.push([name, fn, __group]); };
test.describe = (_name: any, fn?: any) => {
  if (typeof fn !== 'function') return;
  const parent = __group; __group = { serial: false, failed: false, parent };
  try { fn(); } finally { __group = parent; }
};
test.describe.configure = (o: any = {}) => { if (o.mode === 'serial') __group.serial = true; };
test.beforeAll = (fn: any) => { __before.push(fn); };
"""

RUNNER = r"""
for (const b of __before) await b({ browser: { newPage: async () => __page } });
const __only = (process.env.ONLY_KINDS || '').split(',').filter(Boolean);
const __out: Record<string, string> = {};
for (const [name, fn, group] of __tests) {
  const step = PLAN.find((s: any) => s.name === name);
  if (__only.length && !__only.includes(step.kind)) continue;
  const serial = __serialOf(group);
  if (serial && serial.failed) { __out[name] = 'skipped'; continue; }
  try { await fn({ request: __request }); __out[name] = 'passed'; }
  catch (e: any) { __out[name] = 'failed: ' + String(e && e.message || e); if (serial) serial.failed = true; }
}
console.log('@@UI@@' + JSON.stringify(__out));
"""


def require_node() -> str:
    """The node binary. Missing node FAILS the calling test (CI installs it);
    a UI assertion that did not run is never a skip."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not installed: the generated UI spec cannot run (never a skip)")
    return node


def run_spec(
    spec: str, tmp: Path, base_url: Optional[str], only_kinds=(), fixtures: Optional[Path] = None,
    tenants: Optional[UiTenants] = None,
) -> Dict[str, str]:
    body = "\n".join(line for line in spec.splitlines() if not line.startswith("import "))
    f = tmp / "spec_run.mts"
    f.write_text(PRELUDE + body + RUNNER, encoding="utf-8")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "ONLY_KINDS": ",".join(only_kinds)}
    env.update((tenants or TENANTS).run_env())
    if fixtures is not None:
        env["GRADE_FIXTURES"] = str(fixtures)
    if base_url is not None:
        env["BASE_URL"] = base_url
    p = subprocess.run([require_node(), str(f)], capture_output=True, text=True, timeout=60, env=env)
    for line in p.stdout.splitlines():
        if line.startswith("@@UI@@"):
            return json.loads(line[6:])
    return {"__error__": p.stderr.strip() or "no output"}


class Server(str):
    """The base URL, carrying what the product received (``seen``)."""

    seen: List[Tuple[str, bytes]]


@contextmanager
def server(
    statuses: Mapping[str, int], default: int = 200, bodies: Optional[Mapping[str, str]] = None,
    bound: Optional[Iterable[str]] = None,
) -> Iterator[Server]:
    """An HTTP server answering each path with its status and body
    (``POST <path>`` keys a POST; the query string is ignored for keys).
    Every POST body is recorded in ``.seen`` as ``(key, bytes)``.

    ``bound``: the bearer tokens it serves under ``/v1/``, as a product
    built to the tenancy contract does; any other caller gets 401. None
    serves every caller."""
    tokens = None if bound is None else set(bound)
    bodies = bodies or {}
    seen: List[Tuple[str, bytes]] = []

    class H(BaseHTTPRequestHandler):
        def _answer(self, key):
            code = statuses.get(key, default)
            if tokens is not None and self.path.startswith("/v1/"):
                auth = str(self.headers.get("authorization") or "")
                if not (auth.startswith("Bearer ") and auth[7:] in tokens):
                    code = 401
            body = bodies.get(key, "ok").encode()
            self.send_response(code)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._answer(self.path)

        def do_POST(self):  # noqa: N802
            data = self.rfile.read(int(self.headers.get("content-length") or 0))
            key = "POST " + self.path.split("?")[0]
            seen.append((self.path, data))
            self._answer(key)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        base = Server(f"http://127.0.0.1:{srv.server_port}")
        base.seen = seen
        yield base
    finally:
        srv.shutdown()
