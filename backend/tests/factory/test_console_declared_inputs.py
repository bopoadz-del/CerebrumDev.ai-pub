"""The stamped console builds each capability's form from what the product declares.

The console looked up ``paths["/v1/" + cap]`` in openapi.json only. A product
that serves one templated route (``/v1/{capability_id}``) never has that key,
so every capability card fell back to two invented inputs and could only
create empty records -- although the product's own GET /v1/capabilities
already listed each capability's fields, types and allowed values.

These tests run the console's own form logic (the ``declared-inputs`` block of
the rendered page) under node, so they judge the shipped JavaScript, not a
copy of it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

from app.factory.build.store_acceptance import render_ui_index

START = "// <<declared-inputs>>"
END = "// <</declared-inputs>>"


def _block() -> str:
    html = render_ui_index("Any Platform")
    assert html.count(START) == 1 and html.count(END) == 1, "the declared-inputs block is marked once"
    return html[html.index(START): html.index(END)]


def _run(calls: List[Dict[str, Any]], tmp_path: Path) -> List[Any]:
    """Evaluate each call ``{"fn": name, "args": [...]}`` in node; return results.

    ``undefined`` comes back as the string "<undefined>" so a sent-nothing
    answer is told apart from a sent-null one; a thrown error comes back as
    ``{"error": message}``."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not installed: the console logic cannot run (never a skip)")
    script = (
        _block()
        + "\nconst CALLS = " + json.dumps(calls) + ";\n"
        + "const FNS = { capIdOf, resolveRef, pathMatches, fieldsFromSchema, declaredFields,"
        + " choicesOf, kindOf, valueOf, unwrap, rangeOf, shownOf, choiceTexts, notSetLabel, textFrom,"
        + " bodyFrom: (fields, values) => bodyFrom(fields, (f) => values[f.name]),"
        + " formFromText: (t) => { const d = parseExact(t); return fieldsFromSchema(d, d)"
        + ".map((f) => [f.name, kindOf(f), shownOf(f), rangeOf(f)]); },"
        + " capsFromText: (t) => { const l = parseExact(t); const it = (l.items || l.capabilities)[0];"
        + " return declaredFields(it, {}, capIdOf(it)).map((f) => [f.name, kindOf(f), shownOf(f)]); },"
        + " choicesFromText: (t) => { const d = parseExact(t); return choiceTexts(fieldsFromSchema(d, d)[0]); } };\n"
        + "const out = CALLS.map((c) => { let r; try { r = FNS[c.fn](...c.args); }"
        + " catch (e) { r = { error: e.message }; }"
        + " return r === undefined ? '<undefined>' : r; });\n"
        + "process.stdout.write(JSON.stringify(out, (k, v) => (v === undefined ? '<undefined>' : v)));\n"
    )
    f = tmp_path / "console_inputs.js"
    f.write_text(script, encoding="utf-8")
    done = subprocess.run(
        [node, str(f)], capture_output=True, text=True, timeout=60, env=dict(os.environ)
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _sent(text: Any) -> Any:
    """What the product reads from one control's JSON text (Python keeps every digit)."""
    return text if text == "<undefined>" else json.loads(text)


def _names(fields: List[Dict[str, Any]]) -> List[str]:
    return [f["name"] for f in fields]


TEMPLATED_DOC = {
    "paths": {
        "/v1/capabilities": {"get": {}},
        "/v1/{capability_id}": {
            "post": {"requestBody": {"$ref": "#/components/requestBodies/Record"}}
        },
        "/v1/{capability_id}/{record_id}": {"patch": {}},
    },
    "components": {
        "requestBodies": {
            "Record": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/Record"}}
                }
            }
        },
        "schemas": {
            "Record": {
                "type": "object",
                "required": ["amount"],
                "properties": {
                    "amount": {"type": "number", "minimum": 0},
                    "kind": {"$ref": "#/components/schemas/Kind"},
                },
            },
            "Kind": {"type": "string", "enum": ["open", "closed"]},
        },
    },
}


class TestTheFormComesFromTheProductsOwnDeclaration:
    @pytest.mark.parametrize(
        "item, expected",
        [
            (
                {"id": "orders", "fields": [
                    {"name": "customer", "type": "str", "required": True},
                    {"name": "units", "type": "int", "min": 1, "max": 9},
                ]},
                ["customer", "units"],
            ),
            (
                {"id": "visits", "fields": [
                    {"name": "room", "type": "str", "allowed_values": ["a", "b"]},
                    {"name": "nights", "type": "float"},
                    {"name": "paid", "type": "bool"},
                ]},
                ["room", "nights", "paid"],
            ),
        ],
    )
    def test_the_capabilities_entry_fields_are_the_form(self, item, expected, tmp_path):
        # The openapi document declares other fields: the product's own entry wins.
        [fields] = _run([{"fn": "declaredFields", "args": [item, TEMPLATED_DOC, item["id"]]}], tmp_path)
        assert _names(fields) == expected
        assert "reference" not in _names(fields)

    @pytest.mark.parametrize("cap", ["orders", "site_visits"])
    def test_a_templated_route_is_matched_and_refs_followed(self, cap, tmp_path):
        [fields] = _run([{"fn": "declaredFields", "args": [cap, TEMPLATED_DOC, cap]}], tmp_path)
        by_name = {f["name"]: f for f in fields}
        assert sorted(by_name) == ["amount", "kind"]
        assert by_name["amount"]["required"] is True and by_name["amount"]["min"] == 0
        assert by_name["kind"]["required"] is False
        assert by_name["kind"]["allowed_values"] == ["open", "closed"]

    @pytest.mark.parametrize(
        "item, expected, required",
        [
            ({"id": "a", "input_schema": {"properties": {"x": {"type": "integer"}}, "required": ["x"]}},
             ["x"], [True]),
            ({"id": "b", "input_schema": {"$ref": "#/components/schemas/Record"}},
             ["amount", "kind"], [True, False]),
        ],
    )
    def test_an_input_schema_on_the_entry_is_read(self, item, expected, required, tmp_path):
        [got] = _run([{"fn": "declaredFields", "args": [item, TEMPLATED_DOC, item["id"]]}], tmp_path)
        assert _names(got) == expected
        assert [f["required"] for f in got] == required

    @pytest.mark.parametrize("cap, expected", [("orders", ["only_here"]), ("visits", ["amount", "kind"])])
    def test_a_literal_route_still_wins_over_a_template(self, cap, expected, tmp_path):
        doc = json.loads(json.dumps(TEMPLATED_DOC))
        doc["paths"]["/v1/orders"] = {"post": {"requestBody": {"content": {"application/json": {
            "schema": {"properties": {"only_here": {"type": "string"}}}}}}}}
        [got] = _run([{"fn": "declaredFields", "args": [cap, doc, cap]}], tmp_path)
        assert _names(got) == expected


class TestNothingIsInvented:
    @pytest.mark.parametrize(
        "item, doc",
        [
            ("orders", {}),
            ("orders", {"paths": {"/v1/{capability_id}": {"post": {}}}}),
            ({"id": "orders", "fields": []}, TEMPLATED_DOC),
            ("orders", {"paths": {"/v1/{capability_id}": {"get": {}}}}),
        ],
    )
    def test_no_declaration_gives_no_fields(self, item, doc, tmp_path):
        [fields] = _run([{"fn": "declaredFields", "args": [item, doc, "orders"]}], tmp_path)
        assert fields == []

    def test_the_old_invented_inputs_are_gone(self):
        html = render_ui_index("Any Platform")
        assert '["reference", "status"]' not in html
        assert "No inputs declared for this capability" in html

    @pytest.mark.parametrize(
        "template, path, expected",
        [
            ("/v1/{capability_id}", "/v1/orders", True),
            ("/v1/{id}", "/v1/site_visits", True),
            ("/v1/{capability_id}", "/v1/orders/7", False),
            ("/v1/{capability_id}", "/v1/", False),
            ("/v2/{capability_id}", "/v1/orders", False),
            ("/v1/{}", "/v1/orders", False),
        ],
    )
    def test_a_template_matches_only_one_whole_segment(self, template, path, expected, tmp_path):
        assert _run([{"fn": "pathMatches", "args": [template, path]}], tmp_path) == [expected]

    @pytest.mark.parametrize(
        "doc, node, expected",
        [
            ({"components": {"schemas": {"A": {"$ref": "#/components/schemas/A"}}}},
             {"$ref": "#/components/schemas/A"}, None),
            ({}, {"$ref": "other.json#/X"}, None),
            ({}, {"$ref": "#/components/schemas/Missing"}, None),
            ({}, {"type": "string"}, {"type": "string"}),
            ({"components": {"schemas": {"B": {"type": "integer"}}}},
             {"$ref": "#/components/schemas/B"}, {"type": "integer"}),
        ],
    )
    def test_a_ref_resolves_or_gives_nothing(self, doc, node, expected, tmp_path):
        assert _run([{"fn": "resolveRef", "args": [doc, node]}], tmp_path) == [expected]

    @pytest.mark.parametrize(
        "item, expected",
        [
            ({"capability_id": "orders"}, "orders"),
            ({"id": "visits", "capability_id": "other"}, "visits"),
            ("rooms", "rooms"),
            ({"label": "no id"}, ""),
            (None, ""),
        ],
    )
    def test_entries_without_an_id_are_not_cards(self, item, expected, tmp_path):
        assert _run([{"fn": "capIdOf", "args": [item]}], tmp_path) == [expected]


def _err(sent: Any) -> str:
    """The reason a refused control gave, or "" when it was not refused."""
    return sent["error"] if isinstance(sent, dict) and "error" in sent else ""


# The form has exactly three controls. Each row: a declared field, and the
# kind of control it gets.
KINDS = [
    ({"name": "s", "type": "string"}, "text"),
    ({"name": "s", "type": "str", "default": None}, "text"),
    ({"name": "s", "type": "String", "default": [1, 2]}, "text"),
    ({"name": "s", "type": "str", "enum": []}, "text"),
    ({"name": "b", "type": "bool"}, "choice"),
    ({"name": "b", "type": "boolean", "required": True}, "choice"),
    ({"name": "b", "type": "Boolean", "default": True}, "choice"),
    ({"name": "b", "type": "bool", "default": None}, "choice"),
    ({"name": "e", "type": "str", "allowed_values": ["x"]}, "choice"),
    ({"name": "e", "type": "int", "options": [1, 2]}, "choice"),
    ({"name": "e", "enum": ["a", 1]}, "choice"),
    ({"name": "n", "type": "int"}, "json"),
    ({"name": "n", "type": "Number"}, "json"),
    ({"name": "n", "type": "integer", "default": 5}, "json"),
    ({"name": "n", "type": "int", "enum": []}, "json"),
    ({"name": "l", "type": "list"}, "json"),
    ({"name": "l", "type": "array"}, "json"),
    ({"name": "o", "type": "Dict"}, "json"),
    ({"name": "o", "type": "object"}, "json"),
    ({"name": "u"}, "json"),
    ({"name": "u", "default": 5}, "json"),
    ({"name": "u", "type": None, "default": None}, "json"),
]


class TestThreeControlsOnly:
    @pytest.mark.parametrize("field, kind", KINDS)
    def test_the_kind_follows_the_declaration(self, field, kind, tmp_path):
        assert _run([{"fn": "kindOf", "args": [field]}], tmp_path) == [kind]

    @pytest.mark.parametrize(
        "field, choices",
        [
            ({"name": "b", "type": "bool"}, [True, False]),
            ({"name": "b", "type": "Boolean", "default": True}, [True, False]),
            ({"name": "b", "type": "bool", "required": True}, [True, False]),
            ({"name": "b", "type": "bool", "allowed_values": [True]}, [True]),
            ({"name": "e", "type": "str", "enum": ["a", "b"]}, ["a", "b"]),
            ({"name": "s", "type": "str"}, []),
            ({"name": "n", "type": "int"}, []),
        ],
    )
    def test_a_boolean_is_always_a_choice_of_its_two_values(self, field, choices, tmp_path):
        assert _run([{"fn": "choicesOf", "args": [field]}], tmp_path) == [choices]

    @pytest.mark.parametrize(
        "field, texts",
        [
            ({"name": "b", "type": "bool"}, ["true", "false"]),
            ({"name": "e", "allowed_values": ["open", "closed"]}, ['"open"', '"closed"']),
            ({"name": "e", "enum": [1, 2.5, None]}, ["1", "2.5", "null"]),
        ],
    )
    def test_each_choice_is_its_own_json_text(self, field, texts, tmp_path):
        assert _run([{"fn": "choiceTexts", "args": [field]}], tmp_path) == [texts]

    @pytest.mark.parametrize(
        "field, label",
        [
            ({"name": "b", "type": "bool"}, "not set"),
            ({"name": "b", "type": "bool", "default": True}, "not set (default true)"),
            ({"name": "b", "type": "bool", "default": None}, "not set (default null)"),
            ({"name": "e", "enum": ["open", "closed"], "default": "open"}, 'not set (default "open")'),
            ({"name": "e", "enum": [1, 2], "default": 12345678901234567890}, "not set"),
        ],
    )
    def test_not_set_names_the_declared_default(self, field, label, tmp_path):
        assert _run([{"fn": "notSetLabel", "args": [field]}], tmp_path) == [label]


# Every kind of control walked through what a user can leave in it. Each row:
# a field, what is in its control, and the JSON text sent (MISSING = no key;
# an {"error": ...} = refused in the page, nothing sent).
MISSING = "<undefined>"
SENT = [
    # text: a declared string is sent as typed, as a JSON string
    ({"name": "t", "type": "string"}, "hello", '"hello"'),
    ({"name": "t", "type": "str"}, "  5 ", '"  5 "'),
    ({"name": "t", "type": "string"}, "", MISSING),
    ({"name": "t", "type": "string", "default": "memo"}, "memo", MISSING),
    ({"name": "t", "type": "string", "default": "memo"}, "", '""'),
    ({"name": "t", "type": "string", "default": "memo"}, "other", '"other"'),
    ({"name": "t", "type": "string", "default": None}, '{"a":1}', json.dumps('{"a":1}')),
    ({"name": "t", "type": "string", "default": None}, "", MISSING),
    # choice: not set sends nothing; a pick sends the value's own text
    ({"name": "f", "type": "bool"}, "true", "true"),
    ({"name": "f", "type": "bool"}, "false", "false"),
    ({"name": "f", "type": "bool", "default": True}, "", MISSING),
    ({"name": "f", "type": "bool", "default": True}, "false", "false"),
    ({"name": "f", "type": "bool", "required": True}, "", MISSING),
    ({"name": "c", "allowed_values": ["open", "closed"]}, '"closed"', '"closed"'),
    ({"name": "c", "allowed_values": [1, 2]}, "2", "2"),
    # json: the typed text itself, digit for digit, once it is valid JSON
    ({"name": "n", "type": "int"}, "42", "42"),
    ({"name": "n", "type": "int"}, " 7.5 ", "7.5"),
    ({"name": "n", "type": "int"}, "12345678901234567890", "12345678901234567890"),
    ({"name": "n", "type": "float"}, "1e400", "1e400"),
    ({"name": "n", "type": "int"}, "", MISSING),
    ({"name": "n", "type": "int"}, "   ", MISSING),
    ({"name": "n", "type": "int", "default": 3}, "3", MISSING),
    ({"name": "n", "type": "int", "default": 3}, " 3 ", MISSING),
    ({"name": "n", "type": "int", "default": 3}, "4", "4"),
    ({"name": "n", "type": "int", "default": None}, "null", MISSING),
    ({"name": "n", "type": "int", "default": None}, "5", "5"),
    ({"name": "l", "type": "list"}, "[12345678901234567890]", "[12345678901234567890]"),
    ({"name": "l", "type": "list"}, "5", "5"),
    ({"name": "o", "type": "dict"}, "null", "null"),
    ({"name": "o", "type": "dict", "default": {"a": 1}}, '{"a":1}', MISSING),
    ({"name": "o", "type": "dict", "default": {"a": 1}}, '{"a":1,"b":2}', '{"a":1,"b":2}'),
    ({"name": "u"}, "[1,2]", "[1,2]"),
    ({"name": "u", "default": 5}, "6", "6"),
    ({"name": "u", "default": 5}, '"6"', '"6"'),
    ({"name": "i", "type": "array", "default": [12345678901234567000],
      "default_text": "[12345678901234567890]"}, "[12345678901234567890]", MISSING),
    ({"name": "i", "type": "array", "default": [12345678901234567000],
      "default_text": "[12345678901234567890]"}, "[12345678901234567890,7]", "[12345678901234567890,7]"),
]


REFUSED = [
    ({"name": "n", "type": "int"}, "abc"),
    ({"name": "n", "type": "integer"}, "0x10"),
    ({"name": "n", "type": "float"}, "Infinity"),
    ({"name": "u"}, "hello"),
    ({"name": "l", "type": "list"}, "x, y"),
    ({"name": "o", "type": "object", "default": {"a": 1}}, "{a:1}"),
]


class TestEachControlSendsWhatItHolds:
    @pytest.mark.parametrize("field, raw, text", SENT)
    def test_the_text_sent(self, field, raw, text, tmp_path):
        assert _run([{"fn": "valueOf", "args": [field, raw]}], tmp_path) == [text]

    @pytest.mark.parametrize("field, raw", REFUSED)
    def test_text_that_is_not_json_is_refused_with_the_field_and_reason(self, field, raw, tmp_path):
        [got] = _run([{"fn": "valueOf", "args": [field, raw]}], tmp_path)
        assert _err(got).startswith(field["name"] + " is not valid JSON: ")

    @pytest.mark.parametrize("field, raw", [
        ({"name": "c", "allowed_values": [1, 2]}, "3"),
        ({"name": "f", "type": "bool"}, "yes"),
    ])
    def test_a_choice_outside_the_allowed_values_is_refused(self, field, raw, tmp_path):
        [got] = _run([{"fn": "valueOf", "args": [field, raw]}], tmp_path)
        assert _err(got) == field["name"] + ": not one of the allowed values"

    @pytest.mark.parametrize(
        "field, shown",
        [
            ({"name": "t", "type": "string", "default": "memo"}, "memo"),
            ({"name": "t", "type": "string"}, ""),
            ({"name": "t", "type": "string", "default": None}, ""),
            ({"name": "t", "type": "str", "default": 5}, "5"),
            ({"name": "b", "type": "bool", "default": True}, ""),
            ({"name": "c", "allowed_values": ["a", "b"], "default": "b"}, ""),
            ({"name": "n", "type": "int", "default": 0}, "0"),
            ({"name": "n", "type": "int", "default": None}, "null"),
            ({"name": "n", "type": "int"}, ""),
            ({"name": "e", "default": {"k": 1}}, '{"k":1}'),
            ({"name": "l", "type": "list", "default": [1, "x"]}, '[1,"x"]'),
            ({"name": "g", "type": "integer", "default": 9007199254740991}, "9007199254740991"),
            ({"name": "g", "type": "integer", "default": 9007199254740992}, ""),
            ({"name": "g", "type": "integer", "default": 12345678901234567890}, ""),
            ({"name": "g", "type": "list", "default": [1, 12345678901234567890]}, ""),
            ({"name": "g", "type": "dict", "default": {"a": 12345678901234567890}}, ""),
            ({"name": "g", "type": "integer", "default": 12345678901234567890,
              "default_text": "12345678901234567890"}, "12345678901234567890"),
            ({"name": "g", "type": "number", "default": 7, "default_text": "7.0"}, "7.0"),
        ],
    )
    def test_a_control_starts_at_the_schemas_own_text(self, field, shown, tmp_path):
        assert _run([{"fn": "shownOf", "args": [field]}], tmp_path) == [shown]

    @pytest.mark.parametrize(
        "field, hint",
        [
            ({"name": "n", "type": "int", "min": 0}, "at least 0"),
            ({"name": "n", "type": "int", "max": 9}, "at most 9"),
            ({"name": "n", "type": "int", "min": 1, "max": 5}, "1 to 5"),
            ({"name": "n", "type": "int", "min": None, "max": ""}, "JSON"),
            ({"name": "n", "type": "list"}, "JSON"),
            ({"name": "n", "type": "int", "max": 12345678901234567000,
              "max_text": "12345678901234567890"}, "at most 12345678901234567890"),
        ],
    )
    def test_a_json_box_hints_its_declared_bounds_in_their_own_text(self, field, hint, tmp_path):
        assert _run([{"fn": "rangeOf", "args": [field]}], tmp_path) == [hint]

    @pytest.mark.parametrize(
        "fields, values, body",
        [
            (
                [{"name": "room", "allowed_values": ["a", "b"], "required": True},
                 {"name": "note", "type": "str"}, {"name": "n", "type": "int"}],
                {"room": "", "note": "", "n": " 3 "},
                '{"n":3}',
            ),
            (
                [{"name": "room", "allowed_values": ["a", "b"]}, {"name": "paid", "type": "bool"},
                 {"name": "tags", "type": "list"}],
                {"room": '"b"', "paid": "false", "tags": '["x"]'},
                '{"room":"b","paid":false,"tags":["x"]}',
            ),
            ([{"name": "ids", "type": "array"}], {"ids": "[12345678901234567890,7]"},
             '{"ids":[12345678901234567890,7]}'),
            ([{"name": "gone"}], {}, "{}"),
        ],
    )
    def test_a_body_is_the_controls_texts_spliced(self, fields, values, body, tmp_path):
        assert _run([{"fn": "bodyFrom", "args": [fields, values]}], tmp_path) == [body]

    @pytest.mark.parametrize("bad", ["n", "m"])
    def test_one_refused_box_refuses_the_whole_body(self, bad, tmp_path):
        fields = [{"name": "n", "type": "int"}, {"name": "m", "type": "list"}]
        values = {"n": "1", "m": "[2]"}
        values[bad] = "not json"
        [got] = _run([{"fn": "bodyFrom", "args": [fields, values]}], tmp_path)
        assert _err(got).startswith(bad + " is not valid JSON")

    @pytest.mark.parametrize("hops, leaf", [(3, "q"), (4, "w")])
    def test_a_chain_of_refs_reaches_the_last_schema(self, hops, leaf, tmp_path):
        names = ["S%d" % i for i in range(hops + 1)]
        schemas = {a: {"$ref": "#/components/schemas/" + b} for a, b in zip(names, names[1:])}
        schemas[names[-1]] = {"type": "object", "required": [leaf],
                              "properties": {leaf: {"type": "integer", "minimum": 1}}}
        doc = {"components": {"schemas": schemas}}
        [fields] = _run([{"fn": "fieldsFromSchema", "args": [doc, {"$ref": "#/components/schemas/S0"}]}],
                        tmp_path)
        assert [(f["name"], f["type"], f["required"], f["min"]) for f in fields] == [(leaf, "integer", True, 1)]


OPTIONAL_DOC = {
    "components": {
        "schemas": {
            "Kind": {"type": "string", "enum": ["open", "closed"]},
            "Body": {
                "type": "object",
                "required": ["n"],
                "properties": {
                    "n": {"anyOf": [{"type": "integer", "maximum": 9}, {"type": "null"}]},
                    "k": {"anyOf": [{"$ref": "#/components/schemas/Kind"}, {"type": "null"}]},
                    "t": {"type": ["number", "null"], "minimum": 2},
                    "either": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                },
            },
            "Extra": {"type": "object", "required": ["z"], "properties": {"z": {"type": "boolean"}}},
        }
    }
}


class TestTheStandardSchemaShapesAreRead:
    @pytest.mark.parametrize(
        "schema",
        [{"$ref": "#/components/schemas/Body"}, {"allOf": [{"$ref": "#/components/schemas/Body"}]}],
    )
    def test_optional_fields_keep_their_type_bounds_and_choices(self, schema, tmp_path):
        [fields] = _run([{"fn": "fieldsFromSchema", "args": [OPTIONAL_DOC, schema]}], tmp_path)
        by = {f["name"]: f for f in fields}
        assert sorted(by) == ["either", "k", "n", "t"]
        assert by["n"]["type"] == "integer" and by["n"]["max"] == 9 and by["n"]["required"] is True
        assert by["k"]["allowed_values"] == ["open", "closed"] and by["k"]["required"] is False
        assert by["t"]["type"] == "number" and by["t"]["min"] == 2
        # Two real alternatives are not resolved into either one.
        # It keeps no type at all (not "string"), so it gets the JSON box.
        assert by["either"]["type"] == "<undefined>" and by["either"]["allowed_values"] == "<undefined>"

    @pytest.mark.parametrize(
        "schema, names, required",
        [
            ({"allOf": [{"$ref": "#/components/schemas/Body"}, {"$ref": "#/components/schemas/Extra"}]},
             ["either", "k", "n", "t", "z"], ["n", "z"]),
            ({"allOf": [{"$ref": "#/components/schemas/Extra"}],
              "properties": {"own": {"type": "string"}}, "required": ["own"]},
             ["own", "z"], ["own", "z"]),
        ],
    )
    def test_all_of_members_are_merged(self, schema, names, required, tmp_path):
        [fields] = _run([{"fn": "fieldsFromSchema", "args": [OPTIONAL_DOC, schema]}], tmp_path)
        assert sorted(f["name"] for f in fields) == names
        assert sorted(f["name"] for f in fields if f["required"]) == required

    @pytest.mark.parametrize(
        "node, expected",
        [
            ({"anyOf": [{"type": "null"}, {"type": "integer"}], "title": "N"},
             {"title": "N", "type": "integer"}),
            ({"type": ["string", "null"]}, {"type": "string"}),
            ({"type": ["string", "integer"]}, {"type": "<undefined>"}),
            ({"anyOf": [{"type": "null"}]}, {"anyOf": [{"type": "null"}]}),
        ],
    )
    def test_only_a_null_branch_is_dropped(self, node, expected, tmp_path):
        assert _run([{"fn": "unwrap", "args": [{}, node]}], tmp_path) == [expected]

    @pytest.mark.parametrize(
        "fields, expected",
        [
            ([{"type": "int"}, {"name": ""}, None, {"name": "ok"}], ["ok"]),
            (["x", "y"], ["x", "y"]),
            ([{"name": 3}, "z", ""], ["z"]),
        ],
    )
    def test_named_entries_only_and_plain_names_count(self, fields, expected, tmp_path):
        [got] = _run([{"fn": "declaredFields", "args": [{"id": "c", "fields": fields}, {}, "c"]}], tmp_path)
        assert _names(got) == expected


BIG = 12345678901234567890


def _form(schema_text: str, tmp_path: Path) -> List[Any]:
    """[name, kind, shown, hint] of each field, read from an openapi text by
    parseExact (as boot reads the product's own text)."""
    [got] = _run([{"fn": "formFromText", "args": [schema_text]}], tmp_path)
    return got


class TestTheSchemasOwnTextIsKept:
    @pytest.mark.parametrize(
        "prop, shown",
        [
            ({"type": "array", "items": {"type": "integer"}, "default": [BIG]}, "[%d]" % BIG),
            ({"type": "object", "default": {"a": BIG, "b": [1, BIG]}}, '{"a":%d,"b":[1,%d]}' % (BIG, BIG)),
            ({"anyOf": [{"type": "integer"}, {"type": "null"}], "default": BIG}, str(BIG)),
            ({"type": "integer", "default": -9999999999999999}, "-9999999999999999"),
            ({"type": "number", "default": 1e20}, "1e+20"),
            ({"anyOf": [{"type": "array"}, {"type": "object"}], "default": None}, "null"),
            ({"default": {"default": {"x": BIG}}}, '{"default":{"x":%d}}' % BIG),
        ],
    )
    def test_a_nested_or_top_level_default_shows_every_digit(self, prop, shown, tmp_path):
        text = json.dumps({"type": "object", "properties": {"f": prop}})
        [[name, kind, got, _hint]] = _form(text, tmp_path)
        assert (name, kind, got) == ("f", "json", shown)

    @pytest.mark.parametrize(
        "prop, hint",
        [
            ({"type": "integer", "maximum": BIG}, "at most %d" % BIG),
            ({"type": "integer", "minimum": -BIG, "maximum": 3}, "%d to 3" % -BIG),
        ],
    )
    def test_a_declared_bound_beyond_2_53_is_hinted_exactly(self, prop, hint, tmp_path):
        text = json.dumps({"type": "object", "properties": {"f": prop}})
        assert _form(text, tmp_path)[0][3] == hint

    @pytest.mark.parametrize("cap_text", [
        json.dumps({"items": [{"id": "c", "fields": [{"name": "n", "type": "int", "default": BIG}]}]}),
        json.dumps({"capabilities": [{"id": "c", "fields": [{"name": "n", "default": [BIG, 1]}]}]}),
    ])
    def test_the_capabilities_text_keeps_its_digits_too(self, cap_text, tmp_path):
        [got] = _run([{"fn": "capsFromText", "args": [cap_text]}], tmp_path)
        want = json.loads(cap_text)
        default = (want.get("items") or want.get("capabilities"))[0]["fields"][0]["default"]
        assert got == [["n", "json", json.dumps(default, separators=(",", ":"))]]

    @pytest.mark.parametrize("text", ["12345678901234567890", "-9999999999999999"])
    def test_the_kept_digits_of_a_default_reach_the_field(self, text, tmp_path):
        schema = {"type": "object", "properties": {
            "g": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": int(text), "default_text": text},
            "h": {"type": "integer", "default": 3}}}
        [fields] = _run([{"fn": "fieldsFromSchema", "args": [{}, schema]}], tmp_path)
        assert [(f["name"], f.get("default_text", "<undefined>")) for f in fields] == [
            ("g", text), ("h", "<undefined>")]

    @pytest.mark.parametrize("prop", [
        {"enum": [BIG, 1]},
        {"type": "integer", "enum": [1, BIG]},
    ])
    def test_a_choice_is_sent_as_the_declared_digits(self, prop, tmp_path):
        text = json.dumps({"type": "object", "properties": {"f": prop}})
        [got] = _run([{"fn": "choicesFromText", "args": [text]}], tmp_path)
        assert got == [str(v) for v in prop["enum"]]

    @pytest.mark.parametrize("value, text", [
        ([1, 2], None),
        ([1, BIG], None),
        ({"a": 1}, None),
        ({}, "{}"),
        (5, "5"),
        ("x", '"x"'),
        (BIG, None),
    ])
    def test_a_value_whose_member_texts_were_not_kept_has_no_text(self, value, text, tmp_path):
        # Outside parseExact no member text is kept and no source text is
        # given: a list then has no text at all (never one missing a member),
        # and an integer beyond 2^53 has none (never a rounded one).
        assert _run([{"fn": "textFrom", "args": [value]}], tmp_path) == [text]
