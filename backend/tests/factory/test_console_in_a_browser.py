"""The stamped console, driven in a real headless Chromium.

The node tests in ``test_console_declared_inputs.py`` judge the console's
pure form logic. These judge what a user actually gets: the page
``render_ui_index`` renders is served next to a stand-in product (its
``/v1/capabilities``, ``/openapi.json`` and record routes), a browser types
into the controls and clicks Create, and the test reads the POST the stand-in
received. The form has three controls (a text box for a declared string, a
not set / allowed values choice for a boolean or an enum, a JSON box for
everything else); what a JSON box holds is spliced into the body as typed, so
a rounded or stringified value, or a select that preselects its first value,
only shows up here.

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
      value: el.value,
      options: el.tagName === "SELECT" ? Array.from(el.options).map((o) => o.value) : null,
      labels: el.tagName === "SELECT" ? Array.from(el.options).map((o) => o.text) : null,
    })));
    for (const [name, value] of Object.entries(step.type || {})) {
      await card.locator('[data-f="' + name + '"]').pressSequentially(value);
    }
    for (const [name, value] of Object.entries(step.pick || {})) {
      await card.locator('[data-f="' + name + '"]').selectOption(value);
    }
    for (const [name, value] of step.fill || []) {
      await card.locator('[data-f="' + name + '"]').fill(value);
    }
    for (const name of step.click || []) {
      await card.locator('[data-f="' + name + '"]').click();
    }
    if (step.raw !== undefined) await card.locator("textarea").first().fill(step.raw);
    const pre = card.locator("[data-out]");
    // Each step waits for its own answer, not one an earlier step left.
    await pre.evaluate((el) => { el.textContent = "—"; });
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
            sent = self.rfile.read(n) or b"null"
            posts.append((self.path, json.loads(sent)))
            self._send(201, sent)  # the stand-in stores and answers with what it received

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


BIG = 12345678901234567890


def _doc_with(props: Dict[str, Any]) -> Dict[str, Any]:
    """openapi.json whose one templated POST declares ``props``."""
    return {"paths": {"/v1/{capability_id}": {"post": {"requestBody": {"content": {
        "application/json": {"schema": {"type": "object", "properties": props}}}}}}}}


@pytest.mark.parametrize("typed", ["abc", "1e"])
def test_a_number_that_is_not_json_is_refused_and_nothing_is_sent(typed, tmp_path):
    caps = [{"id": "orders", "fields": [{"name": "amount", "type": "number", "required": True}]}]
    [step], posts = _drive(caps, {}, [{"cap": "orders", "type": {"amount": typed}}], tmp_path)
    assert posts == []
    assert step["shown"].startswith("Not sent. amount is not valid JSON: ")
    assert step["controls"][0]["tag"] == "textarea"


@pytest.mark.parametrize("typed, sent", [("12.5", 12.5), (" 7 ", 7)])
def test_a_number_that_is_json_is_sent_as_typed(typed, sent, tmp_path):
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
    assert room["options"] == [""] + [json.dumps(c) for c in choices]
    assert room["labels"] == ["not set"] + choices
    assert room["value"] == "" and room["required"] is True


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
        ({"labels": "[]", "meta": "{}"}, {"labels": [], "meta": {}}),
    ],
)
def test_list_and_object_fields_are_sent_as_json(typed, sent, tmp_path):
    caps = [{"id": "tags", "fields": [{"name": "labels", "type": "list"}, {"name": "meta", "type": "dict"}]}]
    [step], posts = _drive(caps, {}, [{"cap": "tags", "type": typed}], tmp_path)
    assert posts == [("/v1/tags", sent)]
    assert [c["tag"] for c in step["controls"]] == ["textarea", "textarea"]


@pytest.mark.parametrize("fields", [
    [{"name": "room", "allowed_values": ["a", "b"]}, {"name": "note", "type": "str"}],
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
    assert by["k"]["tag"] == "select" and by["k"]["options"] == ["", '"open"', '"closed"']
    assert by["n"]["placeholder"] == "at most 9"
    assert posts == [("/v1/" + cap, {"k": pick, "n": 4})]


# The table of types x states. Every declared type is walked through the four
# states a control can be left in. Each row: name, schema with a default, what
# its control shows for that default, its control ("fill" a box or "pick" a
# choice), the step that changes it and what is then sent, and what a cleared
# control sends (MISSING = no key at all).
MISSING = object()
TYPES = [
    ("note", {"type": "string", "default": "memo"}, "memo", "fill",
     {"fill": [["note", "second memo"]]}, "second memo", ""),
    ("rate", {"type": "number", "default": 1.5}, "1.5", "fill",
     {"fill": [["rate", "2.25"]]}, 2.25, MISSING),
    ("ref", {"type": "integer", "default": 3}, "3", "fill",
     {"fill": [["ref", str(BIG)]]}, BIG, MISSING),
    ("notify", {"type": "boolean", "default": True}, "", "pick",
     {"pick": {"notify": "false"}}, False, MISSING),
    ("state", {"type": "string", "enum": ["open", "closed"], "default": "open"}, "", "pick",
     {"pick": {"state": "closed"}}, "closed", MISSING),
    ("ids", {"type": "array", "items": {"type": "integer"}, "default": [1, BIG]}, "[1,%d]" % BIG, "fill",
     {"fill": [["ids", "[2, 9007199254740993]"]]}, [2, 9007199254740993], MISSING),
    ("meta", {"type": "object", "default": {"a": BIG}}, '{"a":%d}' % BIG, "fill",
     {"fill": [["meta", '{"b": 2}']]}, {"b": 2}, MISSING),
    ("anything", {"default": 5}, "5", "fill",
     {"fill": [["anything", "[6]"]]}, [6], MISSING),
]


def _doc(optional: bool) -> Dict[str, Any]:
    """One POST body declaring every type: with its default, or as an Optional
    (anyOf the type and null, default null) the way pydantic writes it."""
    props = {}
    for name, schema, *_ in TYPES:
        if optional:
            bare = {k: v for k, v in schema.items() if k != "default"}
            props[name] = {"anyOf": [bare, {"type": "null"}], "default": None}
        else:
            props[name] = schema
    return _doc_with(props)


def _shown(optional: bool) -> Dict[str, str]:
    """What each control shows untouched. An Optional's default is null: a
    text box shows it empty, a choice starts at not set, a JSON box shows null."""
    if not optional:
        return {name: shown for name, _s, shown, *_ in TYPES}
    return {name: ("null" if how == "fill" and s.get("type") != "string" else "")
            for name, s, _shown, how, *_ in TYPES}


def _steps(state: str, optional: bool) -> Dict[str, Any]:
    """The browser steps that leave every control in ``state``."""
    plan: Dict[str, Any] = {"fill": [], "pick": {}}
    start = _shown(optional)
    for name, _schema, _shown_value, how, changed, *_ in TYPES:
        if state == "kept":  # touched, then left showing what it started with
            if how == "pick":
                plan["pick"][name] = start[name]
            else:
                plan["fill"] += [[name, "x"], [name, start[name]]]
        elif state == "changed":
            plan["fill"] += changed.get("fill", [])
            plan["pick"].update(changed.get("pick", {}))
        elif state == "cleared":
            if how == "pick":
                plan["pick"][name] = ""
            else:
                plan["fill"] += [[name, "x"], [name, ""]]
    return plan


def _expected(state: str, optional: bool) -> Dict[str, Any]:
    want: Dict[str, Any] = {}
    for name, _schema, shown, _how, _changed, changed_sent, cleared_sent in TYPES:
        if state == "changed":
            want[name] = changed_sent
        elif state == "cleared" and cleared_sent is not MISSING and not optional:
            want[name] = cleared_sent
    return want


@pytest.mark.parametrize("optional", [False, True])
@pytest.mark.parametrize("state", ["untouched", "kept", "changed", "cleared"])
def test_every_declared_type_sends_exactly_what_the_control_holds(state, optional, tmp_path):
    step = dict({"cap": "orders"}, **(_steps(state, optional) if state != "untouched" else {}))
    [seen], posts = _drive(["orders"], _doc(optional), [step], tmp_path)
    assert posts == [("/v1/orders", _expected(state, optional))]
    if state == "untouched":
        assert {c["name"]: c["value"] for c in seen["controls"]} == _shown(optional)
        assert {c["name"]: c["tag"] for c in seen["controls"]} == {
            name: ("select" if how == "pick" else "input" if s.get("type") == "string" else "textarea")
            for name, s, _sh, how, *_ in TYPES}


@pytest.mark.parametrize("typed", ["12345678901234567890", "9007199254740993", "-9999999999999999"])
def test_a_typed_integer_is_sent_digit_for_digit(typed, tmp_path):
    caps = [{"id": "orders", "fields": [{"name": "ref", "type": "int", "required": True}]}]
    [step], posts = _drive(caps, {}, [{"cap": "orders", "type": {"ref": typed}}], tmp_path)
    assert posts == [("/v1/orders", {"ref": int(typed)})]
    assert step["shown"] == '201  {"ref":%s}' % typed  # the answer is shown as the product sent it


@pytest.mark.parametrize("typed", ["0x10", "0b101", "Infinity", "+5", ".5"])
def test_a_number_json_does_not_read_is_refused(typed, tmp_path):
    caps = [{"id": "orders", "fields": [{"name": "ref", "type": "integer"}]}]
    [step], posts = _drive(caps, {}, [{"cap": "orders", "type": {"ref": typed}}], tmp_path)
    assert posts == []
    assert step["shown"].startswith("Not sent. ref is not valid JSON: ")


@pytest.mark.parametrize("names", [["must"], ["agreed", "signed"]])
def test_an_untouched_required_boolean_sends_nothing(names, tmp_path):
    caps = [{"id": "forms", "fields": [{"name": n, "type": "bool", "required": True} for n in names]}]
    [step], posts = _drive(caps, {}, [{"cap": "forms"}], tmp_path)
    assert posts == [("/v1/forms", {})]
    assert [(c["tag"], c["options"]) for c in step["controls"]] == [("select", ["", "true", "false"])] * len(names)


@pytest.mark.parametrize("pick, sent", [("false", False), ("true", True)])
def test_a_boolean_is_sent_as_either_value_or_not_at_all(pick, sent, tmp_path):
    doc = _doc_with({"paid": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "default": None},
                     "flag": {"type": "boolean", "default": True},
                     "ref": {"type": "integer"}})
    plan = [{"cap": "orders", "fill": [["ref", "5"]], "pick": {"paid": pick, "flag": pick}},
            {"cap": "orders", "pick": {"paid": "", "flag": ""}}]
    seen, posts = _drive(["orders"], doc, plan, tmp_path)
    by = {c["name"]: c for c in seen[0]["controls"]}
    assert (by["paid"]["tag"], by["paid"]["options"], by["paid"]["value"]) == ("select", ["", "true", "false"], "")
    assert by["paid"]["labels"][0] == "not set (default null)"
    assert by["flag"]["labels"][0] == "not set (default true)"
    assert posts == [("/v1/orders", {"ref": 5, "paid": sent, "flag": sent}), ("/v1/orders", {"ref": 5})]


@pytest.mark.parametrize("schema, shown, typed, sent", [
    ({"default": {"k": 1}}, '{"k":1}', '{"k": 2}', {"k": 2}),
    ({"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "object"}], "default": [1, 2]},
     "[1,2]", "[3, 9007199254740993]", [3, 9007199254740993]),
])
def test_a_structured_default_is_shown_and_sent_as_json(schema, shown, typed, sent, tmp_path):
    plan = [{"cap": "orders"}, {"cap": "orders", "fill": [["extra", typed]]}]
    seen, posts = _drive(["orders"], _doc_with({"extra": schema}), plan, tmp_path)
    [extra] = seen[0]["controls"]
    assert (extra["tag"], extra["value"]) == ("textarea", shown)
    assert posts == [("/v1/orders", {}), ("/v1/orders", {"extra": sent})]


@pytest.mark.parametrize("default", [BIG, -9999999999999999])
def test_an_integer_default_beyond_2_53_is_shown_digit_for_digit(default, tmp_path):
    doc = _doc_with({"big": {"type": "integer", "default": default}})
    plan = [{"cap": "orders"}, {"cap": "orders", "fill": [["big", str(default + 1)]]}]
    seen, posts = _drive(["orders"], doc, plan, tmp_path)
    assert [c["value"] for c in seen[0]["controls"]] == [str(default)]
    assert posts == [("/v1/orders", {}), ("/v1/orders", {"big": default + 1})]


@pytest.mark.parametrize("schema, edit, sent", [
    ({"type": "array", "items": {"type": "integer"}, "default": [BIG]},
     "[%d,7]" % BIG, [BIG, 7]),
    ({"type": "object", "additionalProperties": {"type": "integer"}, "default": {"a": BIG}},
     '{"a":%d,"b":1}' % BIG, {"a": BIG, "b": 1}),
])
def test_a_big_integer_inside_a_default_is_shown_and_sent_exactly(schema, edit, sent, tmp_path):
    shown = json.dumps(schema["default"], separators=(",", ":"))
    plan = [{"cap": "orders"}, {"cap": "orders", "fill": [["held", edit]]}]
    seen, posts = _drive(["orders"], _doc_with({"held": schema}), plan, tmp_path)
    assert [c["value"] for c in seen[0]["controls"]] == [shown]
    assert posts == [("/v1/orders", {}), ("/v1/orders", {"held": sent})]


@pytest.mark.parametrize("schema, shown, typed, sent", [
    ({"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "object"}, {"type": "null"}],
      "default": None}, "null", "[1,2]", [1, 2]),
    ({"default": 5}, "5", "6", 6),
    ({}, "", '{"k": [1]}', {"k": [1]}),
])
def test_a_field_with_no_single_type_is_sent_as_typed_json(schema, shown, typed, sent, tmp_path):
    plan = [{"cap": "orders"}, {"cap": "orders", "fill": [["loose", typed]]}]
    seen, posts = _drive(["orders"], _doc_with({"loose": schema}), plan, tmp_path)
    [loose] = seen[0]["controls"]
    assert (loose["tag"], loose["value"]) == ("textarea", shown)
    assert posts == [("/v1/orders", {}), ("/v1/orders", {"loose": sent})]


@pytest.mark.parametrize("field, shown", [
    ({"name": "n", "type": "int", "default": BIG}, str(BIG)),
    ({"name": "n", "type": "list", "default": [BIG, 1]}, "[%d,1]" % BIG),
])
def test_a_capabilities_entry_default_keeps_its_digits(field, shown, tmp_path):
    [step], posts = _drive([{"id": "orders", "fields": [field]}], {}, [{"cap": "orders"}], tmp_path)
    assert [c["value"] for c in step["controls"]] == [shown]
    assert posts == [("/v1/orders", {})]


@pytest.mark.parametrize("cap, path", [("my cap/x", "/v1/my%20cap%2Fx"), ("a?b#c", "/v1/a%3Fb%23c")])
def test_create_sends_to_the_encoded_capability_id(cap, path, tmp_path):
    caps = [{"id": cap, "fields": [{"name": "n", "type": "int"}]}]
    _, posts = _drive(caps, {}, [{"cap": cap, "type": {"n": "4"}}], tmp_path)
    assert posts == [(path, {"n": 4})]
