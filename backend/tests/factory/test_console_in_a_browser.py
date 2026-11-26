"""The stamped console, driven in a real headless Chromium.

The node tests in ``test_console_declared_inputs.py`` judge the console's
pure form logic. These judge what a user actually gets: the page
``render_ui_index`` renders is served next to a stand-in product (its
``/v1/capabilities``, ``/openapi.json`` and record routes), a browser types
into the controls and clicks Create, and the test reads the POST the stand-in
received. A browser number input empties text that does not parse, and a
select with no blank option preselects its first value -- both only show up
here.

CI installs Playwright and Chromium for the backend job, so a missing browser
FAILS these tests (never a skip).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from app.factory.build.store_acceptance import render_ui_index

REPO = Path(__file__).resolve().parents[3]

DRIVER = r"""
const path = require("path");
const dirs = JSON.parse(process.env.PW_DIRS || "[]");
let pw = null;
for (const name of ["@playwright/test", "playwright-core", "playwright"]) {
  try { pw = require(require.resolve(name, { paths: dirs })); break; } catch (e) { /* next */ }
}
if (!pw) { console.error("playwright is not installed"); process.exit(3); }
const plan = JSON.parse(process.env.PLAN);
(async () => {
  const opts = process.env.CONSOLE_CHROMIUM ? { executablePath: process.env.CONSOLE_CHROMIUM } : {};
  const browser = await pw.chromium.launch(opts);
  const page = await browser.newPage();
  await page.goto(process.env.BASE_URL + "/");
  await page.fill("#tok", "t");
  await page.click("text=Connect");
  await page.waitForSelector("#caps .card");
  const out = [];
  for (const step of plan) {
    const card = page.locator("#caps .card", { has: page.locator("strong", { hasText: step.cap }) }).first();
    const controls = await card.locator("[data-f]").evaluateAll((els) => els.map((el) => ({
      name: el.dataset.f, tag: el.tagName.toLowerCase(), type: el.type || "",
      placeholder: el.placeholder || "", required: !!el.required,
      value: el.type === "checkbox" ? String(el.checked) : el.value,
      options: el.tagName === "SELECT" ? Array.from(el.options).map((o) => o.value) : null,
    })));
    for (const [name, value] of Object.entries(step.type || {})) {
      await card.locator('[data-f="' + name + '"]').pressSequentially(value);
    }
    for (const [name, value] of Object.entries(step.pick || {})) {
      await card.locator('[data-f="' + name + '"]').selectOption(value);
    }
    if (step.raw !== undefined) await card.locator("textarea").first().fill(step.raw);
    const pre = card.locator("[data-out]");
    await card.locator('[data-act="create"]').click();
    await page.waitForFunction((el) => el.textContent !== "—", await pre.elementHandle());
    out.push({ controls, shown: await pre.textContent() });
  }
  await browser.close();
  process.stdout.write("@@CONSOLE@@" + JSON.stringify(out));
})().catch((e) => { console.error(String(e && e.stack || e)); process.exit(1); });
"""


@contextmanager
def _product(caps: List[Any], doc: Dict[str, Any]) -> Iterator[Tuple[str, List[Tuple[str, Any]]]]:
    """Serve the console page plus a stand-in product; yield (base, posts)."""
    page = render_ui_index("Any Platform").encode("utf-8")
    posts: List[Tuple[str, Any]] = []

    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/":
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path == "/v1/capabilities":
                self._send(200, json.dumps({"items": caps}).encode())
            elif self.path == "/openapi.json":
                self._send(200, json.dumps(doc).encode())
            else:
                self._send(200, b"[]")

        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            posts.append((self.path, json.loads(self.rfile.read(n) or b"null")))
            self._send(201, b'{"created": true}')

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", posts
    finally:
        srv.shutdown()
        srv.server_close()


def _module_dirs() -> List[str]:
    """Where Playwright may live: this repo's frontend, an extra directory a
    developer names in ``PLAYWRIGHT_NODE_MODULES``, then npm's global root
    (where CI installs it)."""
    dirs = [str(REPO / "frontend" / "node_modules")]
    if os.environ.get("PLAYWRIGHT_NODE_MODULES"):
        dirs.append(os.environ["PLAYWRIGHT_NODE_MODULES"])
    npm = shutil.which("npm")
    if npm:
        got = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=60)
        if got.returncode == 0 and got.stdout.strip():
            dirs.append(got.stdout.strip())
    return dirs


def _drive(caps: List[Any], doc: Dict[str, Any], plan: List[Dict[str, Any]], tmp_path: Path):
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not installed: the console cannot be driven (never a skip)")
    script = tmp_path / "drive_console.cjs"
    script.write_text(DRIVER, encoding="utf-8")
    with _product(caps, doc) as (base, posts):
        env = dict(os.environ, BASE_URL=base, PLAN=json.dumps(plan), PW_DIRS=json.dumps(_module_dirs()))
        done = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=120, env=env)
        assert done.returncode == 0, done.stderr
        line = next(x for x in done.stdout.splitlines() if x.startswith("@@CONSOLE@@"))
        return json.loads(line[len("@@CONSOLE@@"):]), list(posts)


@pytest.mark.parametrize("typed", ["abc", "1e"])
def test_a_number_that_does_not_parse_is_sent_as_typed(typed, tmp_path):
    caps = [{"id": "orders", "fields": [{"name": "amount", "type": "number", "required": True}]}]
    [step], posts = _drive(caps, {}, [{"cap": "orders", "type": {"amount": typed}}], tmp_path)
    assert posts == [("/v1/orders", {"amount": typed})]
    assert step["controls"][0]["tag"] == "input" and step["controls"][0]["type"] == "text"


@pytest.mark.parametrize("typed, sent", [("12.5", 12.5), (" 7 ", 7)])
def test_a_number_that_parses_is_sent_as_a_number(typed, sent, tmp_path):
    caps = [{"id": "orders", "fields": [{"name": "amount", "type": "float"}]}]
    _, posts = _drive(caps, {}, [{"cap": "orders", "type": {"amount": typed}}], tmp_path)
    assert posts == [("/v1/orders", {"amount": sent})]


@pytest.mark.parametrize("choices", [["a", "b"], ["x", "y", "z"]])
def test_an_untouched_required_choice_sends_nothing(choices, tmp_path):
    caps = [{"id": "visits", "fields": [
        {"name": "room", "type": "str", "allowed_values": choices, "required": True},
        {"name": "nights", "type": "int"},
    ]}]
    [step], posts = _drive(caps, {}, [{"cap": "visits", "type": {"nights": "2"}}], tmp_path)
    assert posts == [("/v1/visits", {"nights": 2})]
    room = step["controls"][0]
    assert room["options"] == [""] + choices and room["value"] == "" and room["required"] is True


@pytest.mark.parametrize("required", [True, False])
def test_a_picked_choice_is_sent(required, tmp_path):
    caps = [{"id": "visits", "fields": [
        {"name": "room", "type": "str", "allowed_values": ["a", "b"], "required": required},
    ]}]
    _, posts = _drive(caps, {}, [{"cap": "visits", "pick": {"room": "b"}}], tmp_path)
    assert posts == [("/v1/visits", {"room": "b"})]


@pytest.mark.parametrize(
    "typed, sent",
    [
        ({"labels": '["x","y"]', "meta": '{"k":1}'}, {"labels": ["x", "y"], "meta": {"k": 1}}),
        ({"labels": "[]", "meta": "not json"}, {"labels": [], "meta": "not json"}),
    ],
)
def test_list_and_object_fields_are_sent_as_json(typed, sent, tmp_path):
    caps = [{"id": "tags", "fields": [{"name": "labels", "type": "list"}, {"name": "meta", "type": "dict"}]}]
    [step], posts = _drive(caps, {}, [{"cap": "tags", "type": typed}], tmp_path)
    assert posts == [("/v1/tags", sent)]
    assert [c["tag"] for c in step["controls"]] == ["textarea", "textarea"]


@pytest.mark.parametrize("fields", [
    [{"name": "room", "allowed_values": ["a", "b"]}, {"name": "note"}],
    [{"name": "n", "type": "int"}, {"name": "paid", "type": "bool"}, {"name": "tags", "type": "list"}],
])
def test_empty_optional_controls_send_no_key(fields, tmp_path):
    caps = [{"id": "notes", "fields": fields}]
    _, posts = _drive(caps, {}, [{"cap": "notes"}], tmp_path)
    assert posts == [("/v1/notes", {})]


@pytest.mark.parametrize("raw", ["{bad", "[1,"])
def test_a_raw_body_that_is_not_json_is_shown_and_not_sent(raw, tmp_path):
    [step], posts = _drive(["plain"], {}, [{"cap": "plain", "raw": raw}], tmp_path)
    assert posts == []
    assert "The body is not valid JSON" in step["shown"]


@pytest.mark.parametrize("raw, sent", [('{"a": 1}', {"a": 1}), ("", {})])
def test_a_valid_raw_body_is_sent(raw, sent, tmp_path):
    _, posts = _drive(["plain"], {}, [{"cap": "plain", "raw": raw}], tmp_path)
    assert posts == [("/v1/plain", sent)]


@pytest.mark.parametrize("bounds, hint", [({"min": 0}, "at least 0"), ({"min": 1, "max": 5}, "1 to 5")])
def test_a_number_box_shows_the_declared_bounds(bounds, hint, tmp_path):
    caps = [{"id": "orders", "fields": [dict({"name": "units", "type": "int"}, **bounds)]}]
    [step], _ = _drive(caps, {}, [{"cap": "orders", "type": {"units": "3"}}], tmp_path)
    assert step["controls"][0]["placeholder"] == hint


OPTIONAL_OPENAPI = {
    "paths": {"/v1/{capability_id}": {"post": {"requestBody": {"content": {"application/json": {
        "schema": {"allOf": [{"$ref": "#/components/schemas/Body"}]}}}}}}},
    "components": {"schemas": {
        "Kind": {"type": "string", "enum": ["open", "closed"]},
        "Body": {"type": "object", "properties": {
            "k": {"anyOf": [{"$ref": "#/components/schemas/Kind"}, {"type": "null"}]},
            "n": {"anyOf": [{"type": "integer", "maximum": 9}, {"type": "null"}]},
        }},
    }},
}


@pytest.mark.parametrize("cap, pick", [("orders", "open"), ("visits", "closed")])
def test_optional_pydantic_fields_get_their_declared_controls(cap, pick, tmp_path):
    [step], posts = _drive([cap], OPTIONAL_OPENAPI, [{"cap": cap, "pick": {"k": pick}, "type": {"n": "4"}}],
                           tmp_path)
    by = {c["name"]: c for c in step["controls"]}
    assert by["k"]["tag"] == "select" and by["k"]["options"] == ["", "open", "closed"]
    assert by["n"]["placeholder"] == "at most 9"
    assert posts == [("/v1/" + cap, {"k": pick, "n": 4})]
