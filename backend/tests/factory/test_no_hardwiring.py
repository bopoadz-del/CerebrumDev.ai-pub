"""NO HARDWIRING -- the rule's one home in CI.

Owner's rule (2026-10-02): a failing case is never fixed by name. No per-case
branches, no literal document strings, no product/capability/session names, no
word lists, no rescue functions, no per-case switches. Fix the mechanism so
every case of that kind passes.

Each test below locks one mechanism that replaced a by-name fix, and each runs
on invented names the Factory has never seen:

  * a brief carries a kit's contract, never its provenance or another
    product's capabilities (kit-manifest leak);
  * a RAG surface is a whole token of the capability id ("storage" is not RAG);
  * the hardwiring gate refuses a word list -- a literal collection searched
    inside text -- and nothing that is a closed vocabulary or a key lookup;
  * the Store's stub audit treats typing.Protocol members as interface
    declarations, like abstractmethods, and still flags the same body
    anywhere else.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.writer_phases import inventory_needs_rag
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
GATE = ROOT / "scripts" / "scan_hardwiring.py"


def _compiled(path=SMOKE):
    bp = load_blueprint(path)
    return compile_brief(bp, plan_blueprint(bp))


def _gate():
    spec = importlib.util.spec_from_file_location("scan_hardwiring", GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# --- kit-manifest leak ---------------------------------------------------------


def test_kit_manifest_in_a_brief_is_identity_plus_claimed_contract_only():
    compiled = _compiled()
    kit = next(iter(compiled.kit_manifests.values()))
    assert not {"capabilities", "blueprint", "author", "artifacts"} & set(kit)
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_a_kit_provenance_key_planted_in_a_brief_is_refused():
    compiled = _compiled()
    kit_id = next(iter(compiled.kit_manifests))
    compiled.kit_manifests[kit_id] = dict(
        compiled.kit_manifests[kit_id], capabilities=["zorblat_intake"])
    assert any("non-contract keys: capabilities" in e for e in lint_brief(compiled).errors)


def test_a_kit_contract_naming_an_unclaimed_block_is_refused():
    compiled = _compiled()
    kit_id = next(iter(compiled.kit_manifests))
    kit = dict(compiled.kit_manifests[kit_id])
    kit["blocks"] = {"group": list(kit.get("product_blocks") or []) + ["zorblat_block"]}
    compiled.kit_manifests[kit_id] = kit
    assert any("did not claim" in e for e in lint_brief(compiled).errors)


# --- whole-token RAG ------------------------------------------------------------


class _Cap:
    def __init__(self, cid, block_ids):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = "REUSE"
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Zorblat Yard"
    product_id = "zorblat-yard"
    vertical = "zorblat_yards"
    summary = "Track zorblats."


def _store_root(tmp_path, reads_by_block):
    """An invented Store: each block's block.json declares the given reads."""
    import json

    root = tmp_path / "store"
    for bid, reads in reads_by_block.items():
        d = root / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps({"id": bid, "reads": reads}), encoding="utf-8")
    return root


@pytest.mark.parametrize("cap_id, block_id, owes_rag", [
    ("zorblat_rag_answers", "zorblat_ledger", False),  # "rag" in the name decides nothing
    ("storage_management", "zorblat_index", True),     # the bound block retrieves
    ("rag", "zorblat_ledger", False),
])
def test_a_rag_surface_follows_what_the_bound_block_declares(tmp_path, monkeypatch, cap_id, block_id, owes_rag):
    root = _store_root(tmp_path, {
        "zorblat_index": [{"kind": "database", "scope": "vector"}],
        "zorblat_ledger": [{"kind": "database", "scope": "sql"}],
    })
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
    compiled = compile_brief(_Blueprint(), _Plan(_Cap(cap_id, [block_id])), store_ids={block_id})
    assert inventory_needs_rag(compiled) is owes_rag

# --- the word-list form ---------------------------------------------------------


def test_a_two_word_list_searched_in_text_is_refused():
    found = _gate().word_lists(
        'WORDS = ("zorblat yard", "quillon")\n'
        "def route(brief):\n"
        "    return any(w in brief.lower() for w in WORDS)\n")
    assert found and found[0][1] == "WORDS"


def test_closed_vocabularies_and_key_lookups_are_not_word_lists():
    assert _gate().word_lists(
        "def f(mode, d, done):\n"
        "    if mode in ('zip', 'github_repo'):\n"
        "        pass\n"
        "    for k in ('alpha', 'beta'):\n"
        "        if k in d:\n"
        "            d[k] = 1\n"
        "    return all(r in done for r in ('A', 'B'))\n") == []


def test_members_loaded_from_data_are_not_literals():
    assert _gate().word_lists(
        "import json\n"
        "WORDS = tuple(json.load(open('store.json')))\n"
        "def f(t: str):\n    return any(w in t for w in WORDS)\n") == []


# --- Protocol members in the Store's stub audit ----------------------------------


def _store_audit() -> Path:
    root = os.environ.get("CEREBRUM_BLOCKS_ROOT")
    script = Path(root) / "scripts" / "audit_stubs.py" if root else None
    if script is None or not script.is_file():
        pytest.fail("CEREBRUM_BLOCKS_ROOT must name a Store checkout (CI sets it)")
    return script


def test_protocol_members_are_interfaces_and_the_same_body_elsewhere_is_a_stub(tmp_path):
    script = _store_audit()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "iface.py").write_text(
        "from typing import Protocol\n"
        "class ZorblatStore(Protocol):\n"
        "    def read(self, key: str) -> str: ...\n", encoding="utf-8")
    ok = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                        capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stdout
    (tmp_path / "pkg" / "impl.py").write_text(
        "class ZorblatImpl:\n    def read(self, key: str) -> str: ...\n", encoding="utf-8")
    bad = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "pkg/impl.py" in bad.stdout and "iface.py" not in bad.stdout


# --- the phrase-match form --------------------------------------------------------


@pytest.mark.parametrize("src", [
    'def f(message: str):\n    return "zorblat refused" in message\n',
    'def f(out: str):\n    return out.lower().startswith("zorblat")\n',
    'import re\ndef f(log: str):\n    return re.search("zorblat failed", log)\n',
    'def f(detail: str):\n    return detail == "zorblat went wrong"\n',
    'def f(e):\n    return "zorblat" in str(e)\n',
    # A phrase check inside a code template the Factory emits is still code.
    'TEMPLATE = """\nimport sys\n\ndef check(text: str):\n    return "zorblat" in text\n\n\nprint(1)\n"""\n',
])
def test_a_single_phrase_used_as_a_decision_on_text_is_caught(src):
    assert _gate().phrase_matches(src), src


@pytest.mark.parametrize("src", [
    '"""A docstring may say zorblat failed."""\n',
    '# a comment may say "zorblat" in message\nx = 1\n',
    'def f(p: str):\n    return p.endswith(".py") or "/" in p\n',
    'def f(rec: dict):\n    return rec.get("zorblat")\n',
    'def f(kind: str):\n    return kind == "zorblat"\n',  # one token: closed vocabulary
])
def test_prose_paths_keys_and_closed_vocabulary_are_not_phrases(src):
    assert _gate().phrase_matches(src) == [], src


def test_a_phrase_compiled_at_module_scope_and_applied_to_text_is_caught():
    """A phrase compiled once and applied to text later is the same decision
    as re.search("<phrase>", text): red at the use site, then green once the
    pattern is gone. The rewrite of 'FROM records' SQL was exactly this."""
    gate = _gate()
    injected = (
        "import re\n"
        "_ZORBLAT_TABLE = re.compile(r'\\b(FROM|INTO)\\s+zorblat_rows\\b')\n"
        "\n"
        "def retarget(sql: str) -> str:\n"
        "    return _ZORBLAT_TABLE.sub('FROM zorblat_entity', sql)\n"
    )
    hits = gate.phrase_matches(injected)
    assert hits and hits[0][0] == 5, hits
    removed = "def retarget(sql: str) -> str:\n    return sql\n"
    assert gate.phrase_matches(removed) == []
    # Shape patterns are structure, not phrases: a class and a count carry
    # no word.
    shape = (
        "import re\n"
        "_ZQ_SHAPE = re.compile(r'[A-Z]{3}')\n"
        "def ok(value: str) -> bool:\n"
        "    return bool(_ZQ_SHAPE.fullmatch(value))\n"
    )
    assert gate.phrase_matches(shape) == []


# -- group C: prose and log signals became typed ---------------------------


def test_a_model_call_closes_on_a_typed_field_not_a_sentence():
    from types import SimpleNamespace

    from app.factory.build.model_call import CLOSED, MODEL_CALL_STATE
    from app.factory.build_jobs import _open_model_call_note

    opened = SimpleNamespace(detail="zorblat started", payload={"model_call": True})
    worded = SimpleNamespace(detail="zorblat session finished", payload={})
    closed = SimpleNamespace(detail="", payload={MODEL_CALL_STATE: CLOSED})
    # The words of a NOTE close nothing; the typed field does.
    assert _open_model_call_note([opened, worded]) is opened
    assert _open_model_call_note([opened, closed]) is None


def test_a_provenance_stamp_is_classified_by_exact_vocabulary():
    from app.factory.build.authorship import (
        TEMPLATED_SOURCE,
        factory_grounded_sources,
        is_factory_grounded_source,
        is_templated_source,
    )

    for stamp in factory_grounded_sources():
        assert is_factory_grounded_source(stamp)
    assert is_templated_source(TEMPLATED_SOURCE)
    # A stamp that merely CONTAINS the words is not one of the Factory's.
    assert not is_factory_grounded_source("zorblat factory-grounded thing")
    assert not is_templated_source("zorblat deterministic template copy")


def test_a_named_blocker_is_read_by_position():
    from app.factory.build.coder_session import named_blocker_of

    assert named_blocker_of("ZORBLAT_BLOCKER: the reason, with a colon: here") == "ZORBLAT_BLOCKER"
    assert named_blocker_of("no blocker token in this sentence") == ""


def test_an_unreachable_github_is_typed_at_the_raise_site():
    from app.factory.build import n3_store_gate as n3
    from app.factory.build.builds_push import BuildsPushError

    assert n3.is_infrastructure_error(BuildsPushError("zorblat", unreachable=True))
    # The old message words carry no meaning on their own.
    assert not n3.is_infrastructure_error(BuildsPushError("GitHub API down: HTTP 401"))


def test_failure_ownership_parses_the_failing_assert_as_code():
    from app.factory.build.failure_owner import _asserts_a_comparison

    assert _asserts_a_comparison(">       assert zorblat(x) == quux\nE   AssertionError")
    assert _asserts_a_comparison(">   assert ok, (key, got, want)")
    assert not _asserts_a_comparison(">   assert False, 'zorblat broke'")
    assert not _asserts_a_comparison("prose saying assert x == y without source")


# -- the vertical is the user's choice, never inferred ------------------------


def _module_tree(rel: str):
    import ast

    src = (Path(__file__).resolve().parents[2] / "app" / rel).read_text(encoding="utf-8")
    return ast.parse(src), src


def test_every_drafted_vertical_comes_from_the_users_choice_only():
    """Owner order 2026-10-05: the Factory never infers a vertical from brief
    prose or from the block set. Structurally: every value the architect binds
    to ``vertical`` is ``chosen_vertical(vertical_hint)`` -- the user's field --
    and no model payload's "vertical" key is ever read."""
    import ast

    tree, _ = _module_tree("factory/product_architect.py")
    # The two functions that CREATE a draft's vertical (one per drafting path).
    drafters = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and n.name in {"_blueprint_from_llm_payload", "_draft_blueprint_from_brief_inner"}
    }
    assert set(drafters) == {"_blueprint_from_llm_payload", "_draft_blueprint_from_brief_inner"}
    for name, fn in drafters.items():
        bound = [
            node.value
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "vertical" for t in node.targets)
        ]
        assert bound, f"{name} binds no vertical"
        for value in bound:
            assert (
                isinstance(value, ast.Call)
                and getattr(value.func, "id", None) == "chosen_vertical"
                and [getattr(a, "id", None) for a in value.args] == ["vertical_hint"]
            ), f"{name}: {ast.unparse(value)}"
    # The model's payload never supplies one.
    for node in ast.walk(drafters["_blueprint_from_llm_payload"]):
        key = None
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key = node.args[0].value
        assert key != "vertical", f"a vertical is read from data: {ast.unparse(node)}"


def test_the_chat_model_picks_no_kit_and_the_notice_reads_the_session_choice():
    import ast

    tree, src = _module_tree("factory/platform_chat_llm.py")
    assert "kit_match" not in src
    notice = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_kit_notice"
    )
    assert [a.arg for a in notice.args.args] == ["state"], "the notice must take no message text"


# -- samples follow declarations; source questions go to the parser --------


def test_a_field_name_alone_implies_no_sample_shape():
    """Only what a field declares shapes its sample. Names that used to imply
    an address, a time, a status, a channel or an id imply nothing."""
    from app.factory.build.roles_handlers import _sample_value

    for name in ("zorblat_email", "zorblat_at", "zorblat_date", "zorblat_time",
                 "zorblat_status", "zorblat_channel", "zorblat_id", "is_zorblat"):
        assert _sample_value({"name": name, "type": "str"}) == "sample", name
    assert "@" in _sample_value({"name": "quux", "type": "str", "format": "email"})
    assert _sample_value({"name": "quux", "type": "time"}) == "10:00:00"
    assert _sample_value({"name": "quux", "type": "str", "allowed_values": ["b", "c"]}) == "b"


def test_source_questions_are_answered_by_the_syntax_tree():
    """A word in a comment or a string is not the code it names."""
    from app.factory.build.offline_adapters import (
        _defines,
        _import_is_guarded,
        _references_module,
        _tree,
    )
    from app.factory.build.roles_handlers import _dotted_mentions, _persists_directly
    from app.factory.build.workflow_accept import handler_has_prepared_event_bus_step

    prose = '"""import zorblat_mod; def zorblat_fn(): store.save(x)"""\n# zorblat_mod.helper\n'
    assert not _references_module(prose, "zorblat_mod")
    assert not _defines(prose, "zorblat_fn")
    assert not _persists_directly(prose)
    assert _references_module("import zorblat_mod.helper\n", "zorblat_mod")
    assert _defines("def zorblat_fn():\n    pass\n", "zorblat_fn")
    code, words = _dotted_mentions("import app.core.zorblat\nX = 'app.core.quux'\n")
    assert "app.core.zorblat" in code and "app.core.quux" in words
    # An import that is the whole body of an ImportError guard stays; one
    # that shares its try body with other statements does not count.
    guarded = "try:\n    from zorblat_pkg import thing\nexcept ImportError:\n    thing = None\n"
    shared = "try:\n    from zorblat_pkg import thing\n    use(thing)\nexcept ImportError:\n    thing = None\n"
    assert _import_is_guarded(_tree(guarded), 2)
    assert not _import_is_guarded(_tree(shared), 2)
    # The prepared-step keys must be BOUND in code, not mentioned in text.
    mention = "# 'topic' 'message' payload={ channel='mcp' action='publish' 'event_bus'\n"
    assert handler_has_prepared_event_bus_step(mention) is False


def test_the_secret_scrubber_passes_and_a_match_decision_on_its_patterns_is_rejected():
    """Owner ruling 2026-10-05: the scrubber's patterns are data
    (secret_patterns.json) used only to substitute. The same patterns used to
    DECIDE something are a phrase check."""
    gate = _gate()
    scrubber = (
        Path(__file__).resolve().parents[2] / "app" / "factory" / "build" / "sanitize.py"
    ).read_text(encoding="utf-8")
    assert gate.phrase_matches(scrubber) == []
    decision = scrubber + (
        "\n\ndef zorblat_leaks(text):\n"
        "    if _rules()['long_blob'].search(text):\n"
        "        return True\n"
        "    return False\n"
    )
    hits = gate.phrase_matches(decision)
    assert hits and "decision" in hits[0][1]


@pytest.mark.parametrize(
    "loader",
    [
        "P = ROOT / 'secret_patterns.json'\n",
        "F = 'secret_patterns.json'\nP = ROOT / F\n",
        "F = 'secret_patterns.json'\nP = open(F)\n",
        "P = Path('cfg/secret_patterns.json')\n",
    ],
)
def test_reaching_the_secret_patterns_file_by_any_path_is_a_load(loader):
    """A decision on a pattern match is refused in every module that REACHES
    the file -- directly or through a bound name."""
    gate = _gate()
    decision = "\n\ndef leaks(r, text):\n    if r.search(text):\n        return True\n    return False\n"
    hits = gate.phrase_matches(loader + decision)
    assert hits and "decision" in hits[0][1]


def test_binding_and_comparing_the_file_name_is_not_a_load():
    """The gate defines its own rule by naming the file and comparing with it;
    it never opens it, so its own regex decisions are not secret-pattern ones."""
    gate = _gate()
    src = (
        "F = 'secret_patterns.json'\n\n\n"
        "def names_it(s):\n    return s.endswith(F)\n\n\n"
        "def shaped(r, text):\n    if r.match(text):\n        return 1\n    return 0\n"
    )
    assert gate.phrase_matches(src) == []


# --- block_name_dispatch -------------------------------------------------------
#
# Owner ruling: a site branches on what a block DECLARES in its signed manifest,
# never on its id. The ids come from the Store registry at run time, so the
# test plants an invented id in a fixture Store and proves the gate reads it.


def _fixture_store(root, ids):
    import json

    for bid in ids:
        d = root / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps({"id": bid}), encoding="utf-8")
    return root


_DISPATCH_RED = [
    'def shape(bid, data):\n    if bid == "zq_relay":\n        return {}\n    return data\n',
    'def shape(block_ids):\n    return "zq_relay" in block_ids\n',
    'def shape(roster):\n    return [b for b in roster if b != "zq_relay"]\n',
    'def shape(vendored_blocks):\n    for mod in vendored_blocks:\n        if mod == "zq_relay":\n            return mod\n',
    'RELAY_BLOCK = "zq_relay"\n',
    'TABLE = {"zq_relay": {"ensure": "x"}}\n',
    'def shape(bid):\n    match bid:\n        case "zq_relay":\n            return 1\n',
]


@pytest.mark.parametrize("src", _DISPATCH_RED)
def test_an_invented_block_id_used_to_dispatch_is_red(tmp_path, src):
    gate = _gate()
    ids = gate.load_block_ids(str(_fixture_store(tmp_path, ["zq_relay", "zq_other"])))
    planted = tmp_path / "planted.py"
    planted.write_text(src, encoding="utf-8")
    hits = gate.scan_file(planted, ("block_name_dispatch",), block_ids=ids)
    assert [(form, tok) for _ln, form, tok in hits][:1] == [("block_name_dispatch", "zq_relay")]


@pytest.mark.parametrize(
    "src",
    [
        # the same site, converted: it reads the declaration
        'def shape(bid, data):\n    if block_capability_class(bid) == EVENTS:\n        return {}\n    return data\n',
        # the id flows through as a value -- never compared
        'def shape(bid):\n    return {"block": bid, "tool": bid}\n',
        # a word that happens to be an id, compared with something that is not a block
        'def shape(fmt):\n    return fmt == "zq_relay"\n',
        # a one-key dict built inside a function is a payload, not a table
        'def shape(x):\n    return {"zq_relay": x}\n',
    ],
)
def test_reading_the_declaration_is_green(tmp_path, src):
    gate = _gate()
    ids = gate.load_block_ids(str(_fixture_store(tmp_path, ["zq_relay"])))
    planted = tmp_path / "planted.py"
    planted.write_text(src, encoding="utf-8")
    assert gate.scan_file(planted, ("block_name_dispatch",), block_ids=ids) == []


def test_the_ids_come_from_the_store_not_a_list(tmp_path):
    """The same source is red against a Store that publishes the id and green
    against one that does not: the gate holds no id of its own."""
    gate = _gate()
    planted = tmp_path / "planted.py"
    planted.write_text(_DISPATCH_RED[0], encoding="utf-8")
    with_it = gate.load_block_ids(str(_fixture_store(tmp_path / "a", ["zq_relay"])))
    without = gate.load_block_ids(str(_fixture_store(tmp_path / "b", ["zq_other"])))
    assert gate.scan_file(planted, ("block_name_dispatch",), block_ids=with_it)
    assert gate.scan_file(planted, ("block_name_dispatch",), block_ids=without) == []


def test_no_store_fails_the_gate_closed(monkeypatch):
    gate = _gate()
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    with pytest.raises(gate.NoStoreRegistry):
        gate.load_block_ids()
    assert gate.main(["--root", "scripts", "--form", "block_name_dispatch"]) == 1


def test_block_name_dispatch_is_enforced_at_baseline_zero():
    import json

    gate = _gate()
    assert "block_name_dispatch" in gate.DEFAULT_FORMS
    baseline = json.loads(gate.BASELINE.read_text(encoding="utf-8"))
    assert not any(
        "block_name_dispatch" in forms for forms in (baseline.get("files") or {}).values()
    )
