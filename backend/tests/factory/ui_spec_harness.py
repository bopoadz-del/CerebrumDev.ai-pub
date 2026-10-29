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
from typing import Dict, Iterator, Mapping, Optional

PRELUDE = r"""
const __tests: [string, any][] = []; const __before: any[] = [];
class __Fail extends Error {}
function expect(v: any) {
  const fail = (m: string) => { throw new __Fail(m); };
  return {
    toBe: (x: any) => { if (v !== x) fail(`expected ${x} got ${v}`); },
    toEqual: (x: any) => { if (JSON.stringify(v) !== JSON.stringify(x)) fail(`expected ${JSON.stringify(x)} got ${JSON.stringify(v)}`); },
    toContain: (x: any) => { if (!v.includes(x)) fail(`${JSON.stringify(v)} does not contain ${x}`); },
  };
}
const __resp = (r: Response) => ({ status: () => r.status });
const __request = {
  get: async (u: string) => __resp(await fetch(u)),
  post: async (u: string, o: any) => __resp(await fetch(u, { method: 'POST', body: o.data, headers: o.headers })),
};
const __page = { goto: async (u: string) => __resp(await fetch(u)), waitForLoadState: async () => {}, on: () => {} };
const test: any = (name: string, fn: any) => { __tests.push([name, fn]); };
test.describe = { configure: () => {} };
test.beforeAll = (fn: any) => { __before.push(fn); };
"""

RUNNER = r"""
for (const b of __before) await b({ browser: { newPage: async () => __page } });
const __only = (process.env.ONLY_KINDS || '').split(',').filter(Boolean);
const __out: Record<string, string> = {};
for (const [name, fn] of __tests) {
  const step = PLAN.find((s: any) => s.name === name);
  if (__only.length && !__only.includes(step.kind)) continue;
  try { await fn({ request: __request }); __out[name] = 'passed'; }
  catch (e: any) { __out[name] = 'failed: ' + String(e && e.message || e); }
}
console.log('@@UI@@' + JSON.stringify(__out));
"""


def node_available() -> bool:
    return shutil.which("node") is not None


def run_spec(spec: str, tmp: Path, base_url: Optional[str], only_kinds=()) -> Dict[str, str]:
    body = "\n".join(line for line in spec.splitlines() if not line.startswith("import "))
    f = tmp / "spec_run.mts"
    f.write_text(PRELUDE + body + RUNNER, encoding="utf-8")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "ONLY_KINDS": ",".join(only_kinds)}
    if base_url is not None:
        env["BASE_URL"] = base_url
    p = subprocess.run(["node", str(f)], capture_output=True, text=True, timeout=60, env=env)
    for line in p.stdout.splitlines():
        if line.startswith("@@UI@@"):
            return json.loads(line[6:])
    return {"__error__": p.stderr.strip() or "no output"}


@contextmanager
def server(statuses: Mapping[str, int], default: int = 200) -> Iterator[str]:
    """An HTTP server answering each path with its status (``POST <path>``
    keys a POST); the base URL is yielded."""

    class H(BaseHTTPRequestHandler):
        def _answer(self, key):
            code = statuses.get(key, default)
            self.send_response(code)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_GET(self):  # noqa: N802
            self._answer(self.path)

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length") or 0))
            self._answer("POST " + self.path)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
