"""A block payload key must reach the block, not be folded into ``input``.

Measured on the booted zip of session sess_6400b6c (construction-management,
149 files, all five phases green, seventeen routes served):

    daily_log_management  document_engine: No input files provided
                          (pdf/docx/xlsx). Pass file_path as pdf_path, ...
    photo_documentation   database: Insert failed: near ")": syntax error
    punch_list_tracking   block or tool name required for MCP channel
    crew_dashboard        team: Team access denied
                          database: Insert failed: near ")": syntax error

Four capabilities, four different messages, one defect. ``_known_fields``
built its allow-list from ``declared_inputs`` (the block's CONFIG params in
block.json) and ignored ``input_keys_read_by_block`` (the keys the block's
code actually reads at run time). So for ``database``, whose declared inputs
are only {input, backend, connection_string}, a correct call

    execute("database", {"table": "crew_logs", "values": record}, action="insert")

had BOTH keys counted as stray, folded into ``input`` by ``_adapt_input``,
and handed to the block as ``{"input": {"table": ..., "values": ...}}``.
``DatabaseBlock._insert`` read ``table=None`` and ``values={}`` off the outer
dict and emitted ``INSERT INTO None () VALUES ()``.

The platform built, shipped, booted, migrated, answered /health with four
green checks -- and could not persist a single row.

These tests exec the emitted template and call the real functions, because
the defect was in behaviour, not in text a substring assertion would catch.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from app.factory.build.roles import _DISPATCH_RUNTIME

# The harvested contracts as they appear in the shipped platform, trimmed to
# the fields these tests exercise. Verbatim key lists -- do not "tidy" them.
LIVE_CONTRACTS: Dict[str, Any] = {
    "database": {
        "block_id": "database",
        "default_action": "query",
        "declared_inputs": [
            {"name": "input", "type": "json", "required": False},
            {"name": "backend", "type": "string", "required": False},
            {"name": "connection_string", "type": "string", "required": False},
        ],
        "input_keys_read_by_block": [
            "action", "backend", "connection_string", "filters", "params",
            "schema", "sql", "table", "table_name", "values", "where",
            "where_params",
        ],
    },
    "document_engine": {
        "block_id": "document_engine",
        "declared_inputs": [
            {"name": "input", "type": "string", "required": False},
            {"name": "output_format", "type": "string", "required": False},
        ],
        "input_keys_read_by_block": [
            "bytes", "docx", "docx_path", "file_path", "input", "path", "pdf",
            "pdf_path", "status", "text", "xlsx", "xlsx_path",
        ],
    },
    "notification": {
        "block_id": "notification",
        "default_action": "send",
        "declared_inputs": [{"name": "input", "type": "json", "required": False}],
        "input_required_fields": ["channel", "message"],
        "input_keys_read_by_block": [
            "action", "block", "blocks", "body", "channel", "channels",
            "input", "message", "params", "payload", "tool", "to", "url",
        ],
    },
    "team": {
        "block_id": "team",
        "default_action": "create_team",
        "declared_inputs": [{"name": "input", "type": "json", "required": False}],
        "input_keys_read_by_block": [
            "action", "email", "name", "permission", "plan", "role", "slug",
            "target_user_id", "team_id", "token", "user_id",
        ],
    },
    # A block with no harvested runtime keys: the allow-list is just its
    # declared config, and the fold must still work for domain records.
    "dashboard": {
        "block_id": "dashboard",
        "default_action": "render",
        "declared_inputs": [
            {"name": "input", "type": "json", "required": False},
            {"name": "theme", "type": "string", "required": False},
        ],
        "input_keys_read_by_block": ["action", "theme", "title", "widgets"],
    },
}


@pytest.fixture()
def dispatch(tmp_path):
    """The emitted dispatch module, with the live contracts installed.

    The template resolves its vendor directory off ``__file__``; exec gets no
    module of its own, so hand it the path the file would have in a shipped
    platform. Nothing here loads a block -- these tests are about the payload
    the block would have been given.
    """
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    ns: Dict[str, Any] = {"__file__": str(app_dir / "dispatch.py")}
    exec(compile(_DISPATCH_RUNTIME, "<dispatch_runtime>", "exec"), ns)
    ns["BLOCK_CONTRACTS"] = LIVE_CONTRACTS
    return ns


# ── the live failures, one test each ─────────────────────────────────────────


def test_database_insert_payload_stays_flat(dispatch):
    """The exact call crew_dashboard and photo_documentation make."""
    record = {"worker_name": "A", "hours_worked": 8}
    adapted = dispatch["_adapt_input"](
        "database", {"table": "crew_logs", "values": record}, "insert"
    )
    assert adapted == {"table": "crew_logs", "values": record}
    # The shape that produced INSERT INTO None () VALUES ()
    assert "input" not in adapted


def test_document_engine_file_path_stays_flat(dispatch):
    adapted = dispatch["_adapt_input"](
        "document_engine", {"pdf_path": "/tmp/a.pdf"}, None
    )
    assert adapted == {"pdf_path": "/tmp/a.pdf"}


def test_notification_block_and_tool_stay_flat(dispatch):
    adapted = dispatch["_adapt_input"](
        "notification",
        {"channel": "mcp", "message": "m", "block": "database", "tool": "insert"},
        "send",
    )
    assert adapted["block"] == "database"
    assert adapted["tool"] == "insert"
    assert "input" not in adapted


def test_team_context_keys_stay_flat(dispatch):
    adapted = dispatch["_adapt_input"](
        "team", {"team_id": "crew-a", "user_id": "worker-1", "name": "Crew A"},
        "get_team_context",
    )
    assert adapted == {"team_id": "crew-a", "user_id": "worker-1", "name": "Crew A"}


@pytest.mark.parametrize(
    "block_id,payload",
    [
        ("database", {"table": "t", "values": {"a": 1}}),
        ("document_engine", {"pdf_path": "/tmp/a.pdf"}),
        ("team", {"team_id": "t", "user_id": "u", "name": "n"}),
    ],
)
def test_no_block_payload_is_double_wrapped(dispatch, block_id, payload):
    """Regression on the shape itself: never {"input": {<block keys>}}."""
    adapted = dispatch["_adapt_input"](block_id, payload, None)
    assert adapted.get("input") != payload


# ── the fold that must survive ───────────────────────────────────────────────


def test_a_domain_record_is_still_folded_into_input(dispatch):
    """The warehouse `audit` case _adapt_input was written for.

    A key the block never reads IS a domain record and still belongs in the
    block's declared ``input`` slot. Fixing the allow-list must not undo it.
    """
    adapted = dispatch["_adapt_input"](
        "dashboard",
        {"theme": "construction", "reference": "R-1", "quantity": 3},
        "render",
    )
    assert adapted["theme"] == "construction"
    assert adapted["input"] == {"reference": "R-1", "quantity": 3}


def test_caller_supplied_input_is_never_moved(dispatch):
    adapted = dispatch["_adapt_input"](
        "dashboard", {"input": {"a": 1}, "theme": "x"}, "render"
    )
    assert adapted == {"input": {"a": 1}, "theme": "x"}


# ── the allow-list itself ────────────────────────────────────────────────────


def test_known_fields_unions_all_three_contract_sources(dispatch):
    ns = dispatch
    known = ns["_known_fields"]("database")
    assert "backend" in known                 # declared_inputs (config)
    assert {"table", "values", "where"} <= known   # input_keys_read_by_block
    assert "action" not in known              # action travels as a kwarg

    known_notif = ns["_known_fields"]("notification")
    assert {"channel", "message"} <= known_notif   # input_required_fields


def test_an_unharvested_key_is_still_refused(dispatch):
    """The allow-list must stay closed -- widening it is not disabling it."""
    with pytest.raises(dispatch["DispatchContractError"]) as exc:
        dispatch["_adapt_input"]("team", {"user_id": "u", "input": {}, "nope": 1}, None)
    assert "nope" in str(exc.value)


def test_missing_required_field_is_still_refused(dispatch):
    with pytest.raises(dispatch["DispatchContractError"]) as exc:
        dispatch["_adapt_input"]("notification", {"channel": "email"}, "send")
    assert "message" in str(exc.value)
