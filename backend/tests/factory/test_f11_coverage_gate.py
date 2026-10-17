"""A declared block that is never invoked must be caught at coder time.

Live sess_6400b6c halted the whole P7 build at the WRITER behaviour gate:

    WRITER gate 'writer_behaviour' failed: a capability declares block(s)
    it never invokes

findings: crew_dashboard (dashboard + database), daily_log_management
(capture + document_engine), punch_list_tracking (workflow).

Root cause was a contract mismatch, not a model failure. writer_behaviour
requires EVERY id in BLOCK_IDS to be invoked, but the coder prompt said
"every block in BLOCK_IDS whose output the capability needs" -- which
licenses skipping one. Any capability declaring 2+ blocks was a coin flip.

The prompt now states the real rule, and _validate_body enforces it
statically so a non-covering body costs one bounded retry instead of the
whole run (WRITER halts before TESTER and STORE_MANAGER ever start).
"""

import pytest

from app.factory.coder import CoderError, _validate_body

LIVE_PAIRS = [
    ("crew_dashboard", ["dashboard", "database"]),
    ("daily_log_management", ["capture", "document_engine"]),
]


def _loop_body():
    return (
        "results = {}\n"
        "for block_id in BLOCK_IDS:\n"
        "    results[block_id] = execute(block_id, payload)\n"
        'return {"ok": True, "capability": CAPABILITY_ID, "results": results}'
    )


def _literal_body(block_ids):
    lines = [
        "r%d = execute(%r, payload)" % (i, b) for i, b in enumerate(block_ids)
    ]
    lines.append('return {"ok": True, "capability": CAPABILITY_ID}')
    return "\n".join(lines)


@pytest.mark.parametrize("cap,blocks", LIVE_PAIRS)
def test_iterating_block_ids_is_accepted(cap, blocks):
    assert _validate_body(_loop_body(), cap, blocks)


@pytest.mark.parametrize("cap,blocks", LIVE_PAIRS)
def test_naming_every_block_literally_is_accepted(cap, blocks):
    assert _validate_body(_literal_body(blocks), cap, blocks)


@pytest.mark.parametrize("cap,blocks", LIVE_PAIRS)
def test_skipping_one_declared_block_is_rejected(cap, blocks):
    """The exact live failure: only the first block gets invoked."""
    body = _literal_body(blocks[:1])
    with pytest.raises(CoderError) as exc:
        _validate_body(body, cap, blocks)
    assert "never invokes declared block" in str(exc.value)
    assert blocks[1] in str(exc.value)


def test_invoking_no_blocks_at_all_is_rejected():
    body = 'return {"ok": True, "capability": CAPABILITY_ID}'
    with pytest.raises(CoderError):
        _validate_body(body, "punch_list_tracking", ["workflow"])


def test_no_declared_blocks_means_no_coverage_requirement():
    body = 'return {"ok": True, "capability": CAPABILITY_ID}'
    assert _validate_body(body, "x", [])
    assert _validate_body(body, "x", None)


def test_coverage_runs_after_the_existing_gates():
    """A body that never returns must still fail for THAT reason first."""
    body = "x = execute('dashboard', payload)"
    with pytest.raises(CoderError) as exc:
        _validate_body(body, "crew_dashboard", ["dashboard", "database"])
    assert "never returns" in str(exc.value)


def test_prompt_states_the_rule_the_gate_enforces():
    """The prompt and the gate must not disagree again."""
    from app.factory.coder import _PLATFORM_SYSTEM

    assert "EVERY id in BLOCK_IDS" in _PLATFORM_SYSTEM
    assert "whose output the capability needs" not in _PLATFORM_SYSTEM


def test_prompt_forbids_action_inside_the_payload():
    """Live makerspace: the prompt must state the dispatch contract."""
    from app.factory.coder import _PLATFORM_SYSTEM

    assert 'NEVER put "action" inside the payload dict' in _PLATFORM_SYSTEM
    assert "action=BLOCK_DEFAULT_ACTIONS.get(block_id)" in _PLATFORM_SYSTEM


def test_action_inside_execute_payload_is_rejected():
    """Regression: the exact makerspace coder shape must fail statically."""
    body = (
        "body = dict(payload)\n"
        "body['action'] = 'render'\n"
        "res = execute('dashboard', {'action': 'render', 'name': payload.get('name')})\n"
        "return {'ok': True, 'capability': CAPABILITY_ID, 'res': res}\n"
    )
    with pytest.raises(CoderError) as exc:
        _validate_body(body, "dashboards_and_reports", ["dashboard"])
    assert "puts 'action' inside the execute() payload" in str(exc.value)
    assert "action=" in str(exc.value)


def test_merged_dict_with_action_key_is_rejected():
    body = (
        "res = execute(BLOCK_IDS[0], {**payload, 'action': 'insert'})\n"
        "return {'ok': True, 'capability': CAPABILITY_ID, 'res': res}\n"
    )
    with pytest.raises(CoderError) as exc:
        _validate_body(body, "equipment_inventory_and_maintenance", ["database"])
    assert "puts 'action' inside the execute() payload" in str(exc.value)


def test_action_as_keyword_is_accepted():
    body = (
        "results = {}\n"
        "for block_id in BLOCK_IDS:\n"
        "    results[block_id] = execute(\n"
        "        block_id, payload, action=BLOCK_DEFAULT_ACTIONS.get(block_id)\n"
        "    )\n"
        "return {'ok': True, 'capability': CAPABILITY_ID, 'results': results}\n"
    )
    assert _validate_body(body, "dashboards_and_reports", ["dashboard", "analytics"])
