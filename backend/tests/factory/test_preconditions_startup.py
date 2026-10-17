"""PRECONDITIONS BECOME STARTUP CODE (owner's ruling R1c, 2026-09-01).

The obligation used to reach the coder as prose in its prompt. On
residential-lettings three of four handlers did not obey it, and the one
that used the correct calling convention still answered::

    team: Team access denied

because nothing had ever created the team. A rule that must be re-obeyed in
every handler is a rule that will be missed in one, so the ensure step is
now generated code that runs once at boot, before any capability can be
called.

Two halves have to hold, and a test that proves only one is not worth its
line count:

  * the RIGHT obligations are emitted -- platform-scoped only. A per_record
    ensure step run at boot would have to invent the caller's data, which is
    F18, and would be a worse defect than the one being fixed.
  * the emitted module is JOINED to the product -- written by the WRITER,
    called by the lifespan after migrations and before the first request.

Each test names the mutation it kills, so a later reader can judge whether
the assertion still earns its place.
"""

import ast
import sys
import types

import pytest

from app.factory.build.block_obligations import (
    BlockObligationError,
    resource_obligations,
    _platform_ensure_input,
    describe_resource_obligations,
    platform_obligations_for,
    render_preconditions_module,
    resource_obligations_for,
)

ALL_BLOCKS = sorted(resource_obligations())


# -- which obligations may run at startup ---------------------------------


def test_every_resource_obligation_declares_a_scope():
    """Mutation killed: adding an obligation with no scope.

    platform_obligations_for filters on scope == "platform", so an
    obligation that forgets the key is silently excluded from startup and
    nothing anywhere says so. Absence must be a test failure, not a shrug.
    """
    for block_id, rule in resource_obligations().items():
        assert rule.get("scope") in {"platform", "per_record"}, (
            "%s declares scope=%r; startup emission is decided by this key"
            % (block_id, rule.get("scope"))
        )


def test_only_platform_scoped_obligations_reach_startup():
    """Mutation killed: platform_obligations_for returning everything."""
    got = platform_obligations_for(["team", "storage"])
    assert set(got) == {"team"}
    assert got["team"]["ensure"] == "create_team"


def test_storage_is_left_to_the_handler_because_its_inputs_are_caller_data():
    """The reason storage is excluded, asserted as a property rather than
    as a hardcoded name.

    store's ensure inputs are content and filename -- the caller's file. A
    boot step would have to invent one. Mutation killed: flipping storage to
    scope "platform", which would make the platform fabricate a record at
    every boot and still pass every other test in this file.
    """
    assert resource_obligations()["storage"]["scope"] == "per_record"
    with pytest.raises(BlockObligationError) as exc:
        _platform_ensure_input(resource_obligations()["storage"], "Lettings")
    message = str(exc.value)
    assert "per_record" in message
    # It names the field that has no platform-level value rather than
    # guessing one.
    assert "content" in message or "filename" in message


def test_every_platform_scoped_rule_can_actually_be_fed_at_boot():
    """The pairing must be self-consistent in both directions.

    Mutation killed: marking a rule "platform" whose ensure inputs are
    domain data. This test fails at declaration time instead of at the
    first boot of a shipped product.
    """
    for block_id, rule in platform_obligations_for(ALL_BLOCKS).items():
        values = _platform_ensure_input(rule, "Lettings Manager")
        assert set(values) == set(rule["ensure_input"]), block_id


def test_the_ensure_inputs_are_about_the_platform_not_about_a_record():
    """Mutation killed: filling a missing field with a plausible domain
    value ("Flat 3B") instead of raising."""
    values = _platform_ensure_input(resource_obligations()["team"], "Lettings Manager")
    assert values["user_id"] == "system"
    assert values["name"] == "Lettings Manager system"
    assert values["slug"] == "lettings-manager-system"


def test_the_slug_survives_a_product_name_that_is_not_url_safe():
    """Mutation killed: slugifying with .replace(" ", "-") only. A slug with
    an ampersand or an accent in it is refused by the block that receives
    it, at boot, where nobody is watching."""
    values = _platform_ensure_input(
        resource_obligations()["team"], "Ridge & Fell Lettings (UK)"
    )
    slug = values["slug"]
    assert slug == "ridge---fell-lettings--uk-system", slug
    assert all(c.isalnum() or c == "-" for c in slug), slug
    assert not slug.startswith("-") and not slug.endswith("-"), slug
    # The name keeps the punctuation; only the slug is narrowed.
    assert values["name"] == "Ridge & Fell Lettings (UK) system"


def test_an_unknown_ensure_field_is_named_rather_than_invented():
    """Mutation killed: `out[field] = known.get(field, "")` -- an empty
    string is a value, and the block would refuse it at boot with a message
    nobody reads."""
    rule = dict(resource_obligations()["team"])
    rule["ensure_input"] = ["user_id", "tenancy_reference"]
    with pytest.raises(BlockObligationError) as exc:
        _platform_ensure_input(rule, "Lettings")
    assert "tenancy_reference" in str(exc.value)


# -- the emitted module ----------------------------------------------------


def _load(block_ids, execute, monkeypatch, product_name="Lettings Manager"):
    """Execute the rendered module the way the shipped product would."""
    source = render_preconditions_module(block_ids, product_name)
    fake = types.ModuleType("app.dispatch")
    fake.execute = execute
    # setitem, never sys.modules[...] = -- a direct assignment here leaked
    # into unrelated tests once already and cost an afternoon.
    monkeypatch.setitem(sys.modules, "app.dispatch", fake)
    module = types.ModuleType("preconditions_under_test")
    exec(compile(source, "preconditions.py", "exec"), module.__dict__)
    return module


def test_the_rendered_module_is_valid_python():
    """Mutation killed: any renderer edit that emits a module the product
    cannot import. The product would boot, log an exception from the
    lifespan's own except branch, and serve every request without its
    preconditions."""
    ast.parse(render_preconditions_module(ALL_BLOCKS, "Lettings Manager"))


def test_the_rendered_module_carries_only_the_platform_blocks(monkeypatch):
    module = _load(["team", "storage"], lambda *a, **k: {}, monkeypatch)
    assert set(module.PRECONDITIONS) == {"team"}


def test_a_build_with_no_platform_obligations_renders_a_working_no_op(monkeypatch):
    """Most products vendor no platform-scoped block at all. The module is
    still written, so the lifespan's import cannot fail.

    Mutation killed: emitting nothing (or a syntax error) for an empty rule
    set, which turns every such boot into the except branch.
    """
    calls = []
    module = _load(["storage"], lambda *a, **k: calls.append(a), monkeypatch)
    assert module.PRECONDITIONS == {}
    assert module.ensure_all() == {"ids": {}, "errors": {}}
    assert calls == []


def test_ensure_all_calls_the_declared_action_with_a_flat_payload(monkeypatch):
    """R1c meets R1a/R1d: the startup step is a block call like any other and
    obeys the same invocation contract.

    Mutation killed: wrapping the payload as {"input": {...}} -- the exact
    envelope defect #255 exists to catch, reintroduced by the one call site
    no coder writes.
    """
    seen = []

    def execute(block_id, payload, action=None):
        seen.append((block_id, payload, action))
        return {"team_id": "t-1"}

    _load(["team"], execute, monkeypatch).ensure_all()
    assert len(seen) == 1
    block_id, payload, action = seen[0]
    assert block_id == "team"
    assert action == "create_team"
    assert "input" not in payload
    assert payload["user_id"] == "system"


def test_ensure_all_records_the_id_it_was_given(monkeypatch):
    module = _load(["team"], lambda *a, **k: {"team_id": "t-42"}, monkeypatch)
    out = module.ensure_all()
    assert out["ids"] == {"team": "t-42"}
    assert module.resource_id("team") == "t-42"


def test_the_id_is_read_from_a_nested_result_envelope_too(monkeypatch):
    """Blocks answer both shapes. Mutation killed: reading only the top
    level, which yields "no team_id" against a call that succeeded."""
    module = _load(
        ["team"], lambda *a, **k: {"ok": True, "result": {"team_id": "t-9"}}, monkeypatch
    )
    module.ensure_all()
    assert module.resource_id("team") == "t-9"


def test_ensure_all_is_idempotent(monkeypatch):
    """Called twice -- by a re-entrant lifespan, a test client, a worker --
    it must not mint a second team. Mutation killed: dropping the
    already-have-it guard, which produces two teams and a race over which
    id the handlers read."""
    calls = []

    def execute(block_id, payload, action=None):
        calls.append(action)
        return {"team_id": "t-1"}

    module = _load(["team"], execute, monkeypatch)
    module.ensure_all()
    module.ensure_all()
    assert calls == ["create_team"]
    assert module.resource_id("team") == "t-1"


def test_a_block_that_raises_does_not_stop_the_boot(monkeypatch):
    """Mutation killed: letting the exception out. A platform that cannot
    reach one block at boot must still start and say so -- refusing to serve
    anything is a worse answer than serving with a named gap."""
    def execute(*a, **k):
        raise RuntimeError("connection refused")

    module = _load(["team"], execute, monkeypatch)
    out = module.ensure_all()
    assert out["ids"] == {}
    assert "connection refused" in out["errors"]["team"]
    assert "RuntimeError" in out["errors"]["team"]
    assert module.resource_id("team") is None


def test_a_refusal_that_is_not_an_exception_is_recorded_too(monkeypatch):
    """The live failure mode is not a crash, it is a polite refusal
    envelope. Mutation killed: treating any non-raising call as success,
    which records no id and no error and leaves resource_id() silently
    None."""
    module = _load(
        ["team"], lambda *a, **k: {"error": "Team access denied"}, monkeypatch
    )
    out = module.ensure_all()
    assert out["ids"] == {}
    assert "Team access denied" in out["errors"]["team"]


def test_a_later_success_clears_the_earlier_error(monkeypatch):
    """Mutation killed: never popping RESOURCE_ERRORS, which leaves a
    product reporting a failure it has since recovered from."""
    answers = [{"error": "not ready"}, {"team_id": "t-7"}]

    def execute(*a, **k):
        return answers.pop(0)

    module = _load(["team"], execute, monkeypatch)
    module.ensure_all()
    assert module.RESOURCE_ERRORS["team"]
    module.ensure_all()
    assert module.resource_id("team") == "t-7"
    assert "team" not in module.RESOURCE_ERRORS


def test_resource_id_is_none_before_ensure_all_has_run(monkeypatch):
    """A handler that reads the id at import time must get None rather than
    a KeyError."""
    module = _load(["team"], lambda *a, **k: {"team_id": "x"}, monkeypatch)
    assert module.resource_id("team") is None
    assert module.resource_id("no_such_block") is None


# -- the wiring: the lifespan --------------------------------------------


def _lifespan_tree():
    from app.factory.build.deploy import render_main

    return ast.parse(render_main("Lettings Manager"))


def _first_call_line(tree, func_name):
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == func_name
    ]
    assert lines, "no call to %s() in the rendered main" % func_name
    return min(lines)


def test_the_rendered_main_is_valid_python():
    _lifespan_tree()


def test_preconditions_run_after_migrations_and_before_the_first_request():
    """Order is the whole point. Mutation killed: moving ensure_all() above
    upgrade_head() (the tables it writes to do not exist yet) or below the
    yield (every request before shutdown runs without it)."""
    tree = _lifespan_tree()
    upgrade = _first_call_line(tree, "upgrade_head")
    ensure = _first_call_line(tree, "ensure_all")
    yields = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Yield)]
    assert yields, "the lifespan must yield"
    assert upgrade < ensure < min(yields)


def test_a_failing_precondition_step_cannot_take_the_boot_down():
    """Asserted on the structure, not on a phrase in a comment.

    Mutation killed: deleting the try/except around the import and call --
    which an ImportError from a product that never wrote preconditions.py
    would turn into a platform that will not start at all.
    """
    tree = _lifespan_tree()
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        body_calls = {
            n.func.id
            for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        if "ensure_all" not in body_calls:
            continue
        catches = {
            h.type.id
            for h in node.handlers
            if isinstance(h.type, ast.Name)
        }
        if "Exception" in catches or "BaseException" in catches:
            guarded = True
    assert guarded, "ensure_all() must run inside an except Exception guard"


# -- the wiring: the WRITER ----------------------------------------------


def test_the_writer_renders_the_module_from_the_blocks_it_vendored(
    tmp_path, monkeypatch, stub_coder
):
    """Mutation killed: run_writer calling render_preconditions_module(())
    or writing it to the wrong path -- the feature disconnected at the
    consuming end with every unit test above still green.

    A spy rather than a fixture assertion, because what has to be proved is
    the JOIN: the ids come from the build's own state, the name from its
    blueprint, and the bytes land at app/preconditions.py.
    """

    import app.factory.build.block_obligations as obligations
    import app.factory.build.roles_handlers as handlers
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext, run_writer
    from app.factory.build.workspace import RoleWorkspace

    seen = {}
    calls: list[list[str]] = []

    def spy(block_ids, product_name="platform"):
        calls.append(list(block_ids))
        seen["block_ids"] = list(block_ids)
        seen["product_name"] = product_name
        return "# rendered by the spy\n"

    monkeypatch.setattr(handlers, "render_preconditions_module", spy)
    monkeypatch.setattr(obligations, "render_preconditions_module", spy)

    class _Cap:
        capability_id = "alpha_cap"
        block_ids = ()

    class _Plan:
        capabilities = (_Cap(),)

    class _Blueprint:
        product_name = "Lettings Manager"
        product_id = "lettings-manager"
        vertical = "property"

    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    state = {"vendored_blocks": ("team", "storage")}
    result = run_writer(
        RoleContext(
            role=BuildRole.WRITER,
            workspace=ws,
            blueprint=_Blueprint(),
            plan=_Plan(),
            state=state,
        )
    )
    assert result.ok, result.detail
    assert calls == [["storage", "team"]], calls
    assert seen["block_ids"] == ["storage", "team"]
    assert seen["product_name"] == "Lettings Manager"
    assert ws.read_text("app/preconditions.py") == "# rendered by the spy\n"


# -- the wiring: what the coder is told -----------------------------------


def test_the_coder_is_told_to_read_the_id_not_to_create_it():
    """Two rules that must not contradict each other. If the prompt still
    says "call create_team first" while startup already did, the handler
    creates a second team and reads a different id than the platform holds.

    Mutation killed: leaving describe_resource_obligations unchanged when
    the startup step was added.
    """
    prose = describe_resource_obligations(["team"])
    assert "resource_id" in prose
    assert "from app.preconditions import resource_id" in prose
    assert "Do NOT call create_team yourself" in prose
    assert "team_id" in prose


def test_the_per_record_rule_still_tells_the_handler_to_do_it_itself():
    """Mutation killed: applying the platform prose to every obligation, so
    a handler waits for a stored file the platform never stores."""
    prose = describe_resource_obligations(["storage"])
    assert "call store" in prose
    assert "resource_id" not in prose
    assert "ALREADY created" not in prose


def test_the_two_rules_are_told_apart_within_one_prompt():
    """A product vendoring both must get both messages, each against its own
    block. Mutation killed: emitting the first rule's prose for every rule.
    """
    prose = describe_resource_obligations(["team", "storage"])
    team_line = next(l for l in prose.splitlines() if l.startswith("- team:"))
    storage_line = next(l for l in prose.splitlines() if l.startswith("- storage:"))
    assert "ALREADY created" in team_line
    assert "ALREADY created" not in storage_line
    assert "call store" in storage_line


def test_resource_obligations_for_still_returns_both_scopes():
    """platform_obligations_for narrows; the coder-facing view must not.

    Mutation killed: making resource_obligations_for itself platform-only,
    which would silently drop storage's rule from every coder prompt.
    """
    assert set(resource_obligations_for(["team", "storage"])) == {"team", "storage"}
