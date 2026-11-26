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

    def test_an_input_schema_on_the_entry_is_read(self, tmp_path):
        items = [
            {"id": "a", "input_schema": {"properties": {"x": {"type": "integer"}}, "required": ["x"]}},
            {"id": "b", "input_schema": {"$ref": "#/components/schemas/Record"}},
        ]
        got = _run([{"fn": "declaredFields", "args": [i, TEMPLATED_DOC, i["id"]]} for i in items], tmp_path)
        assert _names(got[0]) == ["x"] and got[0][0]["required"] is True
        assert _names(got[1]) == ["amount", "kind"]

    def test_a_literal_route_still_wins_over_a_template(self, tmp_path):
        doc = json.loads(json.dumps(TEMPLATED_DOC))
        doc["paths"]["/v1/orders"] = {"post": {"requestBody": {"content": {"application/json": {
            "schema": {"properties": {"only_here": {"type": "string"}}}}}}}}
        got = _run([
            {"fn": "declaredFields", "args": ["orders", doc, "orders"]},
            {"fn": "declaredFields", "args": ["visits", doc, "visits"]},
        ], tmp_path)
        assert _names(got[0]) == ["only_here"]
        assert _names(got[1]) == ["amount", "kind"]


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

    def test_a_template_does_not_match_a_different_depth_or_an_empty_segment(self, tmp_path):
        got = _run([
            {"fn": "pathMatches", "args": ["/v1/{capability_id}", "/v1/orders"]},
            {"fn": "pathMatches", "args": ["/v1/{capability_id}", "/v1/orders/7"]},
            {"fn": "pathMatches", "args": ["/v1/{capability_id}", "/v1/"]},
            {"fn": "pathMatches", "args": ["/v2/{capability_id}", "/v1/orders"]},
            {"fn": "pathMatches", "args": ["/v1/{}", "/v1/orders"]},
        ], tmp_path)
        assert got == [True, False, False, False, False]

    def test_a_ref_loop_or_a_foreign_ref_resolves_to_nothing(self, tmp_path):
        loop = {"components": {"schemas": {"A": {"$ref": "#/components/schemas/A"}}}}
        got = _run([
            {"fn": "resolveRef", "args": [loop, {"$ref": "#/components/schemas/A"}]},
            {"fn": "resolveRef", "args": [{}, {"$ref": "other.json#/X"}]},
            {"fn": "resolveRef", "args": [{}, {"type": "string"}]},
        ], tmp_path)
        assert got[0] is None and got[1] is None
        assert got[2] == {"type": "string"}

    def test_entries_without_an_id_are_not_cards(self, tmp_path):
        got = _run([
            {"fn": "capIdOf", "args": [{"capability_id": "orders"}]},
            {"fn": "capIdOf", "args": ["visits"]},
            {"fn": "capIdOf", "args": [{"label": "no id"}]},
            {"fn": "capIdOf", "args": [None]},
        ], tmp_path)
        assert got == ["orders", "visits", "", ""]


class TestEachControlSendsTheDeclaredType:
    def test_the_kind_follows_the_declaration(self, tmp_path):
        fields = [
            {"name": "a", "type": "int"},
            {"name": "b", "type": "number"},
            {"name": "c", "type": "bool"},
            {"name": "d", "type": "str", "allowed_values": ["x"]},
            {"name": "e", "type": "str", "enum": []},
            {"name": "f"},
        ]
        got = _run([{"fn": "kindOf", "args": [f]} for f in fields], tmp_path)
        assert got == ["number", "number", "boolean", "choice", "text", "text"]

    def test_values_are_typed_and_empty_optional_sends_nothing(self, tmp_path):
        num = {"name": "n", "type": "int"}
        flag = {"name": "f", "type": "bool"}
        must = {"name": "m", "type": "bool", "required": True}
        pick = {"name": "p", "type": "int", "allowed_values": [1, 2]}
        got = _run([
            {"fn": "valueOf", "args": [num, "42"]},
            {"fn": "valueOf", "args": [num, " 7.5 "]},
            {"fn": "valueOf", "args": [num, "abc"]},
            {"fn": "valueOf", "args": [num, ""]},
            {"fn": "valueOf", "args": [num, "   "]},
            {"fn": "valueOf", "args": [flag, True]},
            {"fn": "valueOf", "args": [flag, False]},
            {"fn": "valueOf", "args": [must, False]},
            {"fn": "valueOf", "args": [pick, "2"]},
            {"fn": "valueOf", "args": [pick, "3"]},
            {"fn": "valueOf", "args": [{"name": "t"}, "hello"]},
        ], tmp_path)
        assert got == [42, 7.5, "abc", "<undefined>", "<undefined>", True, "<undefined>",
                       False, 2, "3", "hello"]
