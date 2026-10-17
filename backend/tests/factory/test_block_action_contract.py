"""Blocks are action-dispatched, and the WRITER must be told their contract.

New-shape tests for the second failure of the first real build. With the
runtime slice vendored, every block imported -- and then rejected every
payload: handlers sent raw domain dicts to blocks that answer
``Unknown action: None`` or ``Input validation failed``, because nothing
carried the block's contract (default action, declared inputs, schema
required fields) from the vendored source to the code that calls it.

No LLM key and no network anywhere here: the one coder test stubs the wire
call and inspects the prompt it would have sent.
"""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.roles import (
    _DISPATCH_RUNTIME,
    RoleContext,
    _block_contract,
    _ensure_handler_fails_closed,
    _handler_module,
    _templated_body,
)
from app.factory.build.workspace import RoleWorkspace


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _platform_with_recording_block(tmp_path: Path) -> Path:
    """A minimal built-platform layout whose one block records its kwargs."""
    root = tmp_path / "platform"
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(_DISPATCH_RUNTIME, encoding="utf-8")
    block = root / "vendor" / "blocks" / "recorder"
    block.mkdir(parents=True)
    (block / "block.py").write_text(
        "CALLS = []\n"
        "def run(**kwargs):\n"
        "    CALLS.append(kwargs)\n"
        "    return {'status': 'ok', 'seen': sorted(kwargs)}\n",
        encoding="utf-8",
    )
    return root


def test_dispatch_forwards_action_and_params(tmp_path):
    """The first build could not even express an action: execute() had no
    parameter for it, so every real block answered "Unknown action: None"."""
    root = _platform_with_recording_block(tmp_path)
    dispatch = _load("dispatch_action_probe", root / "app" / "dispatch.py")

    out = dispatch.execute(
        "recorder", {"name": "x"}, action="create_team", params={"limit": 3}
    )
    assert out["status"] == "ok"
    block = dispatch.load_block("recorder")
    assert block.CALLS[-1] == {
        "input": {"name": "x"},
        "action": "create_team",
        "limit": 3,
    }


def test_dispatch_without_action_stays_bare(tmp_path):
    """No action means none is sent -- inventing one would silently change
    behaviour for blocks that dispatch on its absence."""
    root = _platform_with_recording_block(tmp_path)
    dispatch = _load("dispatch_bare_probe", root / "app" / "dispatch.py")

    dispatch.execute("recorder", {"name": "x"})
    block = dispatch.load_block("recorder")
    assert block.CALLS[-1] == {"input": {"name": "x"}}


def test_dispatch_turns_a_block_raise_into_a_named_envelope(tmp_path):
    """The Store shim raises bare "Input validation failed" -- no block, no
    field list -- and three rework rounds burned without converging because
    that was the whole finding. Dispatch must return the failure as data,
    with the block named, so handlers and tests can report something
    actionable."""
    root = _platform_with_recording_block(tmp_path)
    raiser = root / "vendor" / "blocks" / "raiser"
    raiser.mkdir(parents=True)
    (raiser / "block.py").write_text(
        "def run(**kwargs):\n    raise RuntimeError('Input validation failed')\n",
        encoding="utf-8",
    )
    dispatch = _load("dispatch_envelope_probe", root / "app" / "dispatch.py")

    out = dispatch.execute("raiser", {"x": 1}, action="run")
    assert out["status"] == "error"
    assert out["block"] == "raiser"
    assert "Input validation failed" in out["error"]

    # Structural failures still raise: a missing block is a build defect,
    # not a runtime outcome to report politely.
    with pytest.raises(dispatch.BlockNotVendored):
        dispatch.execute("not_vendored", {})


def test_dispatch_refuses_missing_required_fields_instead_of_fabricating(tmp_path):
    """F18 inverted: missing metric/value/topic is an error envelope, not invented."""
    from app.factory.build.roles import _render_dispatch

    root = tmp_path / "platform"
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(
        _render_dispatch(
            {
                "analytics": {"input_required_fields": ["metric", "value"]},
                "event_bus": {"input_required_fields": ["topic"]},
            }
        ),
        encoding="utf-8",
    )
    for bid in ("analytics", "event_bus"):
        block = root / "vendor" / "blocks" / bid
        block.mkdir(parents=True)
        (block / "block.py").write_text(
            "SEEN = []\n"
            "def run(**kwargs):\n"
            "    SEEN.append(kwargs.get('input'))\n"
            "    return {'status': 'ok', 'input': kwargs.get('input')}\n",
            encoding="utf-8",
        )
    dispatch = _load("dispatch_adapt_probe", root / "app" / "dispatch.py")
    out = dispatch.execute("analytics", {"party_size": 4}, action="record")
    assert out["status"] == "error"
    assert out["ok"] is False
    assert out["block"] == "analytics"
    assert "metric" in out["error"]
    assert dispatch.load_block("analytics").SEEN == [], "block must not run on a fabricated payload"
    bus = dispatch.execute("event_bus", {"party_size": 4})
    assert bus["status"] == "error"
    assert "topic" in bus["error"]
    assert "topic" not in (bus.get("input") or {})


def test_dispatch_does_not_invent_offline_fields_without_a_harvested_contract(tmp_path):
    """F18 inverted: empty harvest must not invent topic/channel/block/steps."""
    from app.factory.build.roles import _render_dispatch

    root = tmp_path / "platform"
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(
        _render_dispatch({}),
        encoding="utf-8",
    )
    for bid in ("event_bus", "notification", "team", "workflow"):
        block = root / "vendor" / "blocks" / bid
        block.mkdir(parents=True)
        (block / "block.py").write_text(
            "SEEN = []\n"
            "def run(**kwargs):\n"
            "    SEEN.append(kwargs.get('input'))\n"
            "    return {'status': 'ok', 'input': kwargs.get('input')}\n",
            encoding="utf-8",
        )
    dispatch = _load("dispatch_always_fill_probe", root / "app" / "dispatch.py")
    bus = dispatch.execute("event_bus", {"party_size": 4})
    assert bus["status"] == "ok"
    assert "topic" not in bus["input"]
    note = dispatch.execute("notification", {"party_size": 4})
    assert note["input"] == {"party_size": 4}
    assert "channel" not in note["input"]
    assert "block" not in note["input"]
    team = dispatch.execute("team", {"party_size": 4})
    assert team["input"] == {"party_size": 4}
    flow = dispatch.execute("workflow", {"party_size": 4})
    assert "steps" not in flow["input"]
    assert "result" not in flow["input"]


def test_dispatch_does_not_rewrite_notification_channel_or_invent_mcp_targets(tmp_path):
    """F18 inverted: in_process stays in_process; block/tool are not invented."""
    from app.factory.build.roles import _render_dispatch

    root = tmp_path / "platform"
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(
        _render_dispatch({"database": {"input_required_fields": ["query"]}}),
        encoding="utf-8",
    )
    block = root / "vendor" / "blocks" / "notification"
    block.mkdir(parents=True)
    (block / "block.py").write_text(
        "def run(**kwargs):\n"
        "    return {'status': 'ok', 'input': kwargs.get('input')}\n",
        encoding="utf-8",
    )
    dispatch = _load("dispatch_mcp_target_probe", root / "app" / "dispatch.py")
    out = dispatch.execute("notification", {"channel": "in_process", "message": "hi"})
    assert out["status"] == "ok"
    assert out["input"]["channel"] == "in_process"
    assert "block" not in out["input"]
    assert "tool" not in out["input"]
    assert out["input"]["message"] == "hi"
    kept = dispatch.execute("notification", {"channel": "mcp", "message": "hi"})
    assert kept["input"]["channel"] == "mcp"
    assert "block" not in kept["input"]


def test_dispatch_rejects_unknown_fields_when_the_contract_names_them(tmp_path):
    """Negative space: a harvested field list is a closed set, not a hint."""
    from app.factory.build.roles import _render_dispatch

    root = tmp_path / "platform"
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(
        _render_dispatch({"analytics": {"input_required_fields": ["metric", "value"]}}),
        encoding="utf-8",
    )
    block = root / "vendor" / "blocks" / "analytics"
    block.mkdir(parents=True)
    (block / "block.py").write_text(
        "SEEN = []\n"
        "def run(**kwargs):\n"
        "    SEEN.append(kwargs.get('input'))\n"
        "    return {'status': 'ok', 'input': kwargs.get('input')}\n",
        encoding="utf-8",
    )
    dispatch = _load("dispatch_unknown_field_probe", root / "app" / "dispatch.py")
    out = dispatch.execute(
        "analytics", {"metric": "visits", "value": 1, "party_size": 4}
    )
    assert out["status"] == "error"
    assert out["ok"] is False
    assert "party_size" in out["error"]
    assert dispatch.load_block("analytics").SEEN == []


def test_dispatch_rejects_unknown_params_when_the_contract_declares_inputs(tmp_path):
    """Negative space: params not in declared_inputs are not forwarded."""
    from app.factory.build.roles import _render_dispatch

    root = _platform_with_recording_block(tmp_path)
    (root / "app" / "dispatch.py").write_text(
        _render_dispatch(
            {
                "recorder": {
                    "declared_inputs": [
                        {"name": "input", "type": "json", "required": False},
                        {"name": "action", "type": "string"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    dispatch = _load("dispatch_unknown_param_probe", root / "app" / "dispatch.py")
    out = dispatch.execute(
        "recorder", {"name": "x"}, action="create_team", params={"limit": 3}
    )
    assert out["status"] == "error"
    assert out["ok"] is False
    assert "limit" in out["error"]
    assert dispatch.load_block("recorder").CALLS == []


def test_dispatch_does_not_pass_on_a_block_refusal(tmp_path):
    """A block status=error must stay an error envelope, never ok: True."""
    root = _platform_with_recording_block(tmp_path)
    refuser = root / "vendor" / "blocks" / "refuser"
    refuser.mkdir(parents=True)
    (refuser / "block.py").write_text(
        "def run(**kwargs):\n"
        "    return {'status': 'error', 'error': 'metric and value required'}\n",
        encoding="utf-8",
    )
    dispatch = _load("dispatch_refusal_probe", root / "app" / "dispatch.py")
    out = dispatch.execute("refuser", {"party_size": 4}, action="track")
    assert out["status"] == "error"
    assert out["ok"] is False
    assert out["block"] == "refuser"
    assert "metric and value required" in out["error"]


def test_the_templated_handler_sends_each_blocks_default_action(tmp_path):
    """The deterministic path must at least reach a real action -- the
    block.json default -- instead of the guaranteed 'Unknown action: None'."""
    module_text = _handler_module(
        "crew_assignment",
        ["team"],
        _templated_body(["team"]),
        "deterministic contract template",
        {"team": "create_team"},
    )
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    calls = []

    # The handler does ``from app.dispatch import execute``; inject a fake
    # module under that name for the duration of the exec.
    import sys
    import types

    fake = types.ModuleType("app.dispatch")

    def _fake_execute(block_id, payload, action=None, params=None):
        calls.append((block_id, action))
        return {"status": "ok"}

    fake.execute = _fake_execute
    sys.modules["app.dispatch"] = fake
    try:
        spec = importlib.util.spec_from_file_location("handler_probe", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.handle({"crew": "C1"})
    finally:
        del sys.modules["app.dispatch"]

    assert calls == [("team", "create_team")]


def test_the_templated_handler_does_not_report_ok_around_a_failed_block(tmp_path):
    """The ninth live build's fake green: three template handlers answered
    status ok while every nested block call had failed. A block error must
    surface as ok=False with the block named."""
    module_text = _handler_module(
        "defect_register",
        ["workflow"],
        _templated_body(["workflow"]),
        "deterministic contract template",
        {"workflow": "run"},
    )
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    import sys
    import types

    fake = types.ModuleType("app.dispatch")
    fake.execute = lambda block_id, payload, action=None, params=None: {
        "status": "error",
        "block": block_id,
        "error": "RuntimeError: Input validation failed",
    }
    sys.modules["app.dispatch"] = fake
    try:
        spec = importlib.util.spec_from_file_location("handler_honesty_probe", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.handle({"defect_id": "D1"})
    finally:
        del sys.modules["app.dispatch"]

    assert out["ok"] is False
    assert "workflow" in out["error"]
    assert "Input validation failed" in out["error"]


def test_handler_module_refuses_success_over_a_failed_block(tmp_path):
    """Regression: coder body that ignores execute() errors must not ship ok:True.

    Live invoice-management handlers (moonshot-v1-8k / kimi-k2.7-code) returned
    success over a failed block; writer_behaviour then halted the whole run.
    """
    dishonest = (
        "    res = execute(BLOCK_IDS[0], payload, action='insert')\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID, 'ignored': res}\n"
    )
    module_text = _handler_module(
        "invoice_management",
        ["database"],
        dishonest,
        "coder LLM (moonshot-v1-8k)",
        {"database": "insert"},
    )
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    import sys
    import types

    fake = types.ModuleType("app.dispatch")
    fake.execute = lambda block_id, payload, action=None, params=None: {
        "status": "error",
        "block": block_id,
        "error": "writer_behaviour gate: forced block failure",
    }
    sys.modules["app.dispatch"] = fake
    try:
        spec = importlib.util.spec_from_file_location("coder_honesty_probe", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.handle({"reference": "INV-1"})
    finally:
        del sys.modules["app.dispatch"]

    assert out["ok"] is False, out
    assert "database" in str(out.get("error")), out
    assert "forced block failure" in str(out.get("error")), out
    wrapped = _ensure_handler_fails_closed(dishonest)
    assert "_block_errors" in wrapped
    assert "_watched" in wrapped
    assert "execute=_watched" in wrapped


def test_the_smoke_scans_nested_results_for_block_errors(tmp_path):
    """Defence in depth: whoever wrote the handler, a green that wraps a
    failed block call must fail the generated suite."""
    from app.factory.build.roles import run_tester

    _workspace_with_contract(tmp_path)

    class _Cap:
        capability_id = "crew_assignment"
        block_ids = ("team",)

    class _Plan:
        capabilities = (_Cap(),)

    tester_ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=RoleWorkspace(BuildRole.TESTER, tmp_path / "build"),
        blueprint=None,
        plan=_Plan(),
        state={"vendored_blocks": ("team",), "model_specs": {}},
    )
    assert run_tester(tester_ctx).ok

    smoke = (tmp_path / "build" / "tests" / "test_smoke.py").read_text(encoding="utf-8")
    assert "@pytest.mark.pilot" in smoke
    assert "def test_every_capability_executes_end_to_end():" in smoke
    assert "reported ok around a failed block call" in smoke
    assert '\\"status\\": \\"error\\"' in smoke
    # Nested scan is pilot coverage, not the factory code-phase gate.
    marker_at = smoke.index("@pytest.mark.pilot")
    scan_at = smoke.index("reported ok around a failed block call")
    assert marker_at < scan_at


def _workspace_with_contract(tmp_path: Path) -> RoleContext:
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER, workspace=ws, blueprint=None, plan=None
    )
    ws.write_text(
        Path("vendor") / "blocks" / "team" / "block.json",
        json.dumps(
            {
                "id": "team",
                "inputs": [
                    {"name": "input", "type": "json", "required": False},
                    {
                        "name": "action",
                        "type": "string",
                        "default": "create_team",
                        "options": ["create_team", "invite_member"],
                    },
                    {"name": "max_members_per_team", "type": "number"},
                ],
            }
        ),
    )
    ws.write_text(
        Path("vendor") / "cerebrum" / "blocks" / "team.py",
        textwrap.dedent(
            '''
            from vendor.cerebrum.core.typed_block import TypedBlock, Schema, ContentType

            class TeamBlock(TypedBlock):
                input_schema = Schema(
                    content_type=ContentType.JSON,
                    required_fields=["name", "members"],
                    optional_fields=["org"],
                )
            '''
        ),
    )
    return ctx


def test_block_contract_harvests_runtime_error_literals(tmp_path):
    """Blocks self-document per-action requirements in the error literals
    they answer with ("user_id and name required"). Three live builds
    discovered these one paid round at a time; the contract must carry them
    up front."""
    ctx = _workspace_with_contract(tmp_path)
    module_rel = Path("vendor") / "cerebrum" / "blocks" / "team.py"
    source = ctx.workspace.read_text(module_rel)
    ctx.workspace.write_text(
        module_rel,
        source
        + textwrap.dedent(
            '''
            async def _create_team(self, data):
                if not data.get("user_id") or not data.get("name"):
                    return {"error": "user_id and name required"}
                if data.get("role") not in ("member", "admin"):
                    return {"error": f"Invalid role: {data.get('role')}"}
                return {"team_id": "t1"}
            '''
        ),
    )

    contract = _block_contract(ctx, "team")
    harvested = contract["runtime_error_contracts"]
    assert "user_id and name required" in harvested
    # f-string interpolation fragments are excluded -- a half-message with a
    # dangling brace would mislead more than it informs.
    assert not any("{" in item for item in harvested)


def test_block_contract_lists_the_input_keys_the_block_reads(tmp_path):
    """A pipeline step written as {"block_id": ...} failed with "No block
    specified" because the block reads step.get("block") -- vocabulary that
    sat in the vendored source the whole time."""
    ctx = _workspace_with_contract(tmp_path)
    module_rel = Path("vendor") / "cerebrum" / "blocks" / "team.py"
    ctx.workspace.write_text(
        module_rel,
        ctx.workspace.read_text(module_rel)
        + '\ndef _run_steps(self, steps):\n'
        '    for step in steps:\n'
        '        name = step.get("block")\n'
        '        data = step.get("input", {})\n',
    )

    contract = _block_contract(ctx, "team")
    keys = contract["input_keys_read_by_block"]
    assert "block" in keys
    assert "input" in keys


def test_the_coder_prompt_carries_the_vendored_roster(monkeypatch):
    """defect_register's pipeline step referenced the workflow block itself
    (recursion) because the prompt only listed the capability's own blocks --
    a pipeline block needs the whole roster to build steps."""
    from app.factory import coder

    captured = {}

    def _capture(messages):
        captured["messages"] = messages
        # Must invoke every declared block: the static F11 coverage gate
        # rejects a body that declares blocks it never calls (live
        # sess_6400b6c halted on exactly that). Iterating BLOCK_IDS
        # satisfies it; these tests assert prompt contents, not the body.
        return (
            "results = {}\n"
            "for block_id in BLOCK_IDS:\n"
            "    results[block_id] = execute(block_id, payload)\n"
            'return {"ok": True, "capability": CAPABILITY_ID, "results": results}',
            "stub-model",
        )

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr(coder, "_llm_code_call", _capture)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {"model": "stub"},
    )

    coder.generate_platform_handler(
        capability_id="defect_register",
        description="register defects",
        block_ids=["workflow"],
        product_name="Field Ops",
        vertical="field_operations",
        vendored_roster=["analytics", "team", "validation", "workflow"],
    )

    user_message = captured["messages"][-1]["content"]
    assert "validation" in user_message
    assert "never reference the pipeline block itself" in user_message


def test_block_contract_reads_json_and_schema(tmp_path):
    """default action + options from block.json; required input fields from
    the vendored module's Schema. Both existed on disk during the failed
    build -- nothing read them."""
    ctx = _workspace_with_contract(tmp_path)
    contract = _block_contract(ctx, "team")

    assert contract["default_action"] == "create_team"
    assert contract["action_options"] == ["create_team", "invite_member"]
    assert contract["input_required_fields"] == ["name", "members"]
    names = [item["name"] for item in contract["declared_inputs"]]
    assert "max_members_per_team" in names
    assert "action" not in names


def test_block_contract_survives_a_bare_block(tmp_path):
    """A mirror stub has no block.json worth reading and no runtime module.
    The contract degrades to just the id instead of raising."""
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(role=BuildRole.CLONER, workspace=ws, blueprint=None, plan=None)
    ws.write_text(
        Path("vendor") / "blocks" / "solo" / "block.py", "def run(**k):\n    return {}\n"
    )
    assert _block_contract(ctx, "solo") == {"block_id": "solo"}


def test_a_body_that_only_returns_from_a_nested_def_is_rejected():
    """Seen live: the model wrapped its logic in ``def endpoint(...)`` that
    nothing calls, the route fell through to None, and every request answered
    ResponseValidationError. The gate must reject that shape, not ship it."""
    from app.factory.coder import CoderError, _validate_body

    nested = (
        "def endpoint(payload):\n"
        '    return {"ok": True}\n'
    )
    with pytest.raises(CoderError, match="never returns"):
        _validate_body(nested, "cap")


def test_a_return_inside_try_except_still_counts():
    """The gate must not over-reject: a body whose returns live inside
    try/except (or any compound statement) does return from the function."""
    from app.factory.coder import _validate_body

    body = (
        "try:\n"
        "    value = payload\n"
        "except Exception as exc:\n"
        '    return {"ok": False, "error": str(exc)}\n'
        'return {"ok": True}\n'
    )
    assert _validate_body(body, "cap")

    only_in_handler = (
        "try:\n"
        '    return {"ok": True}\n'
        "except Exception as exc:\n"
        '    return {"ok": False, "error": str(exc)}\n'
    )
    assert _validate_body(only_in_handler, "cap")


def test_the_tester_smoke_speaks_the_contract(tmp_path):
    """The generated smoke must probe blocks with their default action and
    drive handlers with the spec-derived payload -- the canned junk payload
    is what made every real block reject the suite."""
    from app.factory.build.roles import run_tester

    # Side effect wanted: writes the vendored block.json + runtime module
    # the tester's contract lookup reads.
    _workspace_with_contract(tmp_path)

    class _Cap:
        capability_id = "crew_assignment"
        block_ids = ("team",)

    class _Plan:
        capabilities = (_Cap(),)

    tester_ws = RoleWorkspace(BuildRole.TESTER, tmp_path / "build")
    tester_ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=tester_ws,
        blueprint=None,
        plan=_Plan(),
        state={
            "vendored_blocks": ("team",),
            "model_specs": {
                "crew_assignment": {
                    "entity": "crew_assignment",
                    "fields": [
                        {"name": "crew", "type": "str", "required": True},
                        {"name": "headcount", "type": "int", "required": True},
                    ],
                }
            },
        },
    )
    result = run_tester(tester_ctx)
    assert result.ok, result.detail

    smoke = (tmp_path / "build" / "tests" / "test_smoke.py").read_text(encoding="utf-8")
    assert "'team': 'create_team'" in smoke, "block default action missing"
    assert "load_block" in smoke, "import failures are no longer hard-asserted"
    assert "'crew'" in smoke, "handler payload is not built from the spec"
    assert "'reference': 'probe'" not in smoke, "canned junk payload is back"
    assert "def test_every_capability_handle_returns_mapping():" in smoke
    from app.factory.build.roles_constants import CONFTEST_REL

    conftest = (tmp_path / "build" / CONFTEST_REL).read_text(encoding="utf-8")
    assert "pilot:" in conftest
    assert "pytest_configure" in conftest


def test_the_coder_prompt_carries_the_block_contract(monkeypatch):
    """The agent cannot honour a contract it was never shown. The prompt must
    contain the actions and required fields the handler has to satisfy."""
    from app.factory import coder

    captured = {}

    def _capture(messages):
        captured["messages"] = messages
        # Must invoke every declared block: the static F11 coverage gate
        # rejects a body that declares blocks it never calls (live
        # sess_6400b6c halted on exactly that). Iterating BLOCK_IDS
        # satisfies it; these tests assert prompt contents, not the body.
        return (
            "results = {}\n"
            "for block_id in BLOCK_IDS:\n"
            "    results[block_id] = execute(block_id, payload)\n"
            'return {"ok": True, "capability": CAPABILITY_ID, "results": results}',
            "stub-model",
        )

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr(coder, "_llm_code_call", _capture)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {"model": "stub"},
    )

    coder.generate_platform_handler(
        capability_id="crew_assignment",
        description="assign a crew",
        block_ids=["team"],
        product_name="Field Ops",
        vertical="field_operations",
        block_contracts={
            "team": {
                "block_id": "team",
                "default_action": "create_team",
                "input_required_fields": ["name", "members"],
            }
        },
    )

    user_message = captured["messages"][-1]["content"]
    assert "create_team" in user_message
    assert "input_required_fields" in user_message
    assert "members" in user_message


def test_a_transient_connect_error_is_retried_not_templated(monkeypatch):
    """Two live runs were lost to intermittent DNS: one getaddrinfo failure
    permanently degraded the artifact to the template path. Connection-class
    errors retry with backoff; the third success answers."""
    import httpx

    from app.factory import coder

    attempts = []

    def _flaky(url, json=None, headers=None, timeout=None):
        attempts.append(url)
        if len(attempts) < 3:
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {"content": 'return {"ok": True}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"completion_tokens": 5},
                }

        return _Resp()

    monkeypatch.setattr(coder.httpx, "post", _flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {
            "provider": "kimi",
            "model": "kimi-k2.7-code-highspeed",
            "fallback_model": "moonshot-v1-8k",
            "base_url": "https://api.moonshot.ai/v1",
            "api_key": "test-key-not-real",
        },
    )

    out, model_used = coder._llm_code_call([{"role": "user", "content": "u"}])
    assert out == 'return {"ok": True}'
    assert model_used == "kimi-k2.7-code-highspeed", "the answering leg was misreported"
    assert len(attempts) == 3, "the transient failures were not retried"


def test_an_http_status_error_is_not_retried(monkeypatch):
    """A 4xx/5xx is the model answering; retrying spends money on the same
    answer. It must fall to the fallback leg after ONE attempt per model."""
    import httpx

    from app.factory import coder

    attempts = []

    def _refuse(url, json=None, headers=None, timeout=None):
        attempts.append(json.get("model") if isinstance(json, dict) else None)

        class _Resp:
            status_code = 404

            def raise_for_status(self):
                raise httpx.HTTPStatusError(
                    "404", request=None, response=None
                )

        return _Resp()

    monkeypatch.setattr(coder.httpx, "post", _refuse)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {
            "provider": "kimi",
            "model": "primary-model",
            "fallback_model": "fallback-model",
            "base_url": "https://api.moonshot.ai/v1",
            "api_key": "test-key-not-real",
        },
    )

    with pytest.raises(coder.CoderError, match="primary-model"):
        coder._llm_code_call([{"role": "user", "content": "u"}])
    assert attempts == ["primary-model", "fallback-model"], attempts


def test_a_rejected_body_gets_one_repair_retry(monkeypatch):
    """The tenth live build lost the run to a single never-returning body
    that went straight to the template. The gate now hands the validation
    error back to the model once; a second rejection still raises."""
    from app.factory import coder

    outputs = [
        'def endpoint(payload):\n    return {"ok": True}\n',  # rejected: no return
        'return {"ok": True, "capability": CAPABILITY_ID}',
    ]
    calls = []

    def _stub(messages):
        calls.append(messages)
        return outputs[len(calls) - 1], "stub-model"

    monkeypatch.setattr(coder, "_llm_code_call", _stub)
    body, model_used = coder._call_validate_retry(
        [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], "cap"
    )
    assert "ok" in body
    assert model_used == "stub-model"
    assert len(calls) == 2
    # The retry conversation carries the rejected code and the gate's reason.
    retry_messages = calls[1]
    assert any("rejected by a static gate" in m["content"] for m in retry_messages)
    assert any("never returns" in m["content"] for m in retry_messages)


def test_a_second_rejection_still_raises(monkeypatch):
    from app.factory import coder

    bad = 'def endpoint(payload):\n    return {"ok": True}\n'
    monkeypatch.setattr(coder, "_llm_code_call", lambda messages: (bad, "stub-model"))
    with pytest.raises(coder.CoderError, match="never returns"):
        coder._call_validate_retry([{"role": "user", "content": "u"}], "cap")


def test_a_rework_prompt_carries_the_previous_attempt(monkeypatch):
    """Eight live rounds proved regeneration from the same prompt converges
    to the same wrong code, verbatim. A rework is an EDIT: the coder must see
    what it wrote last time next to what failed."""
    from app.factory import coder

    captured = {}

    def _capture(messages):
        captured["messages"] = messages
        # Must invoke every declared block: the static F11 coverage gate
        # rejects a body that declares blocks it never calls (live
        # sess_6400b6c halted on exactly that). Iterating BLOCK_IDS
        # satisfies it; these tests assert prompt contents, not the body.
        return (
            "results = {}\n"
            "for block_id in BLOCK_IDS:\n"
            "    results[block_id] = execute(block_id, payload)\n"
            'return {"ok": True, "capability": CAPABILITY_ID, "results": results}',
            "stub-model",
        )

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr(coder, "_llm_code_call", _capture)
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: {"model": "stub"},
    )

    coder.generate_platform_handler(
        capability_id="operations_dashboard",
        description="rollup",
        block_ids=["analytics"],
        product_name="Field Ops",
        vertical="field_operations",
        work_list=["analytics rejected: metric and value required"],
        previous_attempt='result = execute("analytics", data, "track_event")\nreturn result',
    )

    user_message = captured["messages"][-1]["content"]
    assert "YOUR PREVIOUS ATTEMPT" in user_message
    assert 'execute("analytics", data, "track_event")' in user_message
    assert "metric and value required" in user_message
