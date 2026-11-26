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
    answer is told apart from a sent-null one."""
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not installed: the console logic cannot run (never a skip)")
    script = (
        _block()
        + "\nconst CALLS = " + json.dumps(calls) + ";\n"
        + "const FNS = { capIdOf, resolveRef, pathMatches, fieldsFromSchema, declaredFields,"
        + " choicesOf, kindOf, valueOf };\n"
        + "const out = CALLS.map((c) => { const r = FNS[c.fn](...c.args);"
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


class TestEachControlSendsTheDeclaredType:
    @pytest.mark.parametrize(
        "field, kind",
        [
            ({"name": "a", "type": "int"}, "number"),
            ({"name": "b", "type": "Number"}, "number"),
            ({"name": "c", "type": "bool"}, "boolean"),
            ({"name": "d", "type": "str", "allowed_values": ["x"]}, "choice"),
            ({"name": "e", "type": "int", "options": [1, 2]}, "choice"),
            ({"name": "f", "type": "str", "enum": []}, "text"),
            ({"name": "g"}, "text"),
        ],
    )
    def test_the_kind_follows_the_declaration(self, field, kind, tmp_path):
        assert _run([{"fn": "kindOf", "args": [field]}], tmp_path) == [kind]

    @pytest.mark.parametrize(
        "field, raw, sent",
        [
            ({"name": "n", "type": "int"}, "42", 42),
            ({"name": "n", "type": "int"}, " 7.5 ", 7.5),
            ({"name": "n", "type": "int"}, "abc", "abc"),
            ({"name": "n", "type": "int"}, "", "<undefined>"),
            ({"name": "n", "type": "int"}, "   ", "<undefined>"),
            ({"name": "f", "type": "bool"}, True, True),
            ({"name": "f", "type": "bool"}, False, "<undefined>"),
            ({"name": "m", "type": "bool", "required": True}, False, False),
            ({"name": "p", "type": "int", "allowed_values": [1, 2]}, "2", 2),
            ({"name": "p", "type": "int", "allowed_values": [1, 2]}, "3", "3"),
            ({"name": "t"}, "hello", "hello"),
            ({"name": "t"}, "", "<undefined>"),
        ],
    )
    def test_a_control_sends_the_declared_type(self, field, raw, sent, tmp_path):
        assert _run([{"fn": "valueOf", "args": [field, raw]}], tmp_path) == [sent]
