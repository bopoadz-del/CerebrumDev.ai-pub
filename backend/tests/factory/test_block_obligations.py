"""IF YOU ASSIGN IT, YOU FEED IT.

Three precondition classes, all found by running the platform the factory
shipped rather than by any gate, and all now checked before codegen:

  schema      the block needs a value only the caller can supply
  resource    the block needs an id only the block can mint
  dependency  the block needs a distribution nothing declared

Each test below is written against the actual observed failure, not against
the implementation. The mutation each one kills is named in its docstring so
a later reader can tell whether the assertion still earns its place.
"""

import pytest

from app.factory.build.block_obligations import (
    BlockObligationError,
    DISTRIBUTIONS,
    ENVELOPE_STATUS_VALUES,
    resource_obligations,
    schema_obligations,
    assert_feedable,
    audit_capability,
    augment_model_spec,
    dependency_obligations,
    describe_resource_obligations,
    distribution_for,
    ensure_record_envelope,
    render_dependency_lines,
    resource_obligations_for,
    schema_obligations_for,
    third_party_imports,
)


def _spec(*names):
    return {"name": "DailyLog", "fields": [{"name": n, "type": "str"} for n in names]}


# -- schema obligation ----------------------------------------------------


def test_daily_log_without_a_file_field_is_refused_before_codegen():
    """The live failure: 'document_engine: No input files provided'.

    Mutation killed: dropping the audit and letting the WRITER proceed.
    """
    with pytest.raises(BlockObligationError) as exc:
        assert_feedable(
            "daily_log_management",
            ["document_engine", "workflow"],
            _spec("log_date", "weather", "crew_count", "notes"),
        )
    message = str(exc.value)
    assert "daily_log_management" in message
    assert "document_engine" in message
    # The point of failing at plan time is that it NAMES the missing field.
    assert "attachment_path" in message


def test_a_spec_that_already_carries_any_accepted_field_passes():
    """any_of, not all_of. A capability that designed pdf_path itself is fed.

    Mutation killed: requiring the one canonical name and re-adding a field
    the agent already designed under a different name.
    """
    for field in schema_obligations()["document_engine"]["any_of"]:
        assert audit_capability("cap", ["document_engine"], _spec("x", field)) == []


def test_augment_adds_the_field_and_records_why():
    spec = augment_model_spec(_spec("log_date", "notes"), ["document_engine"])
    names = [f["name"] for f in spec["fields"]]
    assert "attachment_path" in names
    assert any("document_engine" in note for note in spec["obligated_fields"])
    # Closing the obligation must not disturb the agent's own design.
    assert names[:2] == ["log_date", "notes"]
    # And the added field is optional: an obligation is not a demand that
    # every row carry a document.
    added = [f for f in spec["fields"] if f["name"] == "attachment_path"][0]
    assert added["required"] is False


def test_augment_then_audit_is_always_clean():
    """The two halves must agree, or the WRITER raises on its own output.

    Mutation killed: an 'add' whose name is absent from its own any_of.
    """
    for block_id in schema_obligations():
        spec = augment_model_spec(_spec("unrelated"), [block_id])
        assert audit_capability("cap", [block_id], spec) == []


def test_augment_is_idempotent():
    once = augment_model_spec(_spec("notes"), ["document_engine"])
    twice = augment_model_spec(once, ["document_engine"])
    assert [f["name"] for f in twice["fields"]] == [f["name"] for f in once["fields"]]


def test_a_block_with_no_obligation_changes_nothing():
    spec = _spec("a", "b")
    assert augment_model_spec(spec, ["workflow", "auth"]) is spec
    assert audit_capability("cap", ["workflow", "auth"], spec) == []


def test_ensure_record_envelope_adds_reference_when_llm_spec_omits_it():
    """Live sess_66a387b: veterinary-care LLM specs omitted ``reference``.

    PRODUCT then refused schema-built payloads. Envelope is not a block
    obligation — augment_model_spec still changes nothing for workflow-only.
    """
    spec = _spec("pet_name", "appointment_date")
    assert augment_model_spec(spec, ["workflow"]) is spec
    enveloped, added = ensure_record_envelope(spec)
    assert added == ["reference", "status"]
    names = [f["name"] for f in enveloped["fields"]]
    assert names[:2] == ["pet_name", "appointment_date"]
    assert "reference" in names
    assert "status" in names
    again, added_again = ensure_record_envelope(enveloped)
    assert added_again == []
    assert [f["name"] for f in again["fields"]] == names


def test_ensure_record_envelope_rewrites_llm_status_vocab():
    """sess_1fd1d54c: envelope kept scheduled; route guard used factory."""
    spec = {
        "entity": "appointment",
        "fields": [
            {
                "name": "status",
                "type": "str",
                "required": True,
                "allowed_values": ["scheduled", "completed", "cancelled"],
            },
            {"name": "reference", "type": "str", "required": True},
        ],
    }
    out, added = ensure_record_envelope(spec)
    assert added == []
    by_name = {f["name"]: f for f in out["fields"]}
    assert by_name["status"]["allowed_values"] == list(ENVELOPE_STATUS_VALUES)
    again, added_again = ensure_record_envelope(out)
    assert added_again == []
    assert again["fields"] == out["fields"]


def test_unassigned_blocks_impose_nothing():
    """F11 is about DECLARED blocks. An obligation for a block the capability
    never declared would be the factory inventing schema."""
    assert schema_obligations_for(["auth"]) == {}
    assert audit_capability("cap", ["auth"], _spec("only_this")) == []


# -- resource obligation --------------------------------------------------


def test_crew_dashboard_gets_told_where_the_team_id_comes_from():
    """The live failure: 'team: Team access denied' -- for the owner.

    REWRITTEN for R1c (2026-09-01). The instruction genuinely changed: the
    platform now creates the team at STARTUP (app/preconditions.py), so
    telling the handler to call create_team first would produce a second
    team and a race over which id the handlers read. What must survive is
    the part that prevents the bug -- the id comes from the block, never
    from a domain value like payload['primary_crew'] -- and the handler must
    be told exactly where to read it.

    Mutation killed: leaving the old "call create_team FIRST" prose in place
    after the startup step landed, so two rules contradict each other and
    the coder follows the wrong one.
    """
    text = describe_resource_obligations(["team", "document_engine"])
    assert "team_id" in text
    assert "get_team_context" in text
    # The instruction that actually prevents the bug, unchanged in substance.
    assert "never pass a domain value" in text
    assert "mint their own id" in text
    # Where the id now comes from, and the explicit stop on the old advice.
    assert "from app.preconditions import resource_id" in text
    assert "resource_id('team')" in text
    assert "Do NOT call create_team yourself" in text


def test_the_per_record_obligation_still_says_do_it_yourself_and_first():
    """R1c moved ONLY the platform-scoped obligation to startup. storage's
    ensure inputs are the caller's file, so a boot step would have to invent
    one (F18) -- the handler still owns it, and still owns the ordering.

    Mutation killed: applying the startup prose to every obligation, which
    leaves a handler waiting for a stored file the platform never stores.
    """
    text = describe_resource_obligations(["storage"])
    assert "FIRST" in text
    assert "resource_id" not in text
    assert "ALREADY created" not in text


def test_storage_obligation_is_stated_because_it_fails_quietly():
    text = describe_resource_obligations(["storage"])
    assert "file_id" in text and "store" in text
    assert "file_not_found" in text


def test_no_resource_blocks_yields_no_prompt_noise():
    assert describe_resource_obligations(["workflow"]) == ""
    assert describe_resource_obligations([]) == ""
    assert resource_obligations_for(["team"]).keys() == {"team"}


def test_every_resource_rule_carries_its_id_into_at_least_one_action():
    for block_id, rule in resource_obligations().items():
        assert rule["into"], block_id
        assert rule["ensure"] not in rule["into"], block_id
        assert rule["carry"], block_id


# -- dependency obligation ------------------------------------------------


def test_a_lazy_import_is_found_and_marked_as_action_reach():
    """document_engine imports yaml inside a method, so the block imports,
    reports healthy, and dies only on the action that parses YAML.

    Mutation killed: scanning module-level imports only, which is exactly
    what made this class invisible -- the noisy ones were already declared.
    """
    source = "import json\n\n\ndef parse(self):\n    import yaml\n    return yaml\n"
    assert third_party_imports(source) == {"yaml": "lazy"}
    obligations = dependency_obligations({"vendor/x.py": source})
    assert obligations["PyYAML"]["reach"] == "lazy"
    assert obligations["PyYAML"]["module"] == "yaml"


def test_a_module_level_import_outranks_a_lazy_one_elsewhere():
    """storage imports aiofiles at module level; the block cannot import at
    all. Reporting that as 'action' would understate it."""
    files = {
        "vendor/a.py": "def f():\n    import httpx\n",
        "vendor/b.py": "import httpx\n",
    }
    assert dependency_obligations(files)["httpx"]["reach"] == "module"
    assert sorted(dependency_obligations(files)["httpx"]["files"]) == [
        "vendor/a.py",
        "vendor/b.py",
    ]


def test_within_one_file_a_module_level_import_outranks_an_earlier_lazy_one():
    """notification.py is exactly this shape: httpx appears first inside a
    method and later at module level. Reporting it as 'action' would say the
    block loads fine when it cannot load at all.

    Mutation killed: dropping the never-downgrade guard in _ImportScan, which
    the cross-file test cannot reach -- dependency_obligations does its own
    upgrade when merging files.
    """
    source = (
        "def send():\n"
        "    import httpx\n"
        "    return httpx\n"
        "\n"
        "import httpx\n"
    )
    assert third_party_imports(source) == {"httpx": "module"}
    # and the reverse order, so this is not passing on statement order
    reverse = "import httpx\n\n\ndef send():\n    import httpx\n"
    assert third_party_imports(reverse) == {"httpx": "module"}


def test_stdlib_and_local_imports_are_not_dependencies():
    source = (
        "import json\nimport asyncio\nimport sqlite3\n"
        "from app.core import x\nfrom vendor.cerebrum.blocks import y\n"
        "from . import sibling\n"
    )
    assert third_party_imports(source) == {}


def test_an_unrecorded_import_raises_instead_of_being_dropped():
    """The failure mode this exists to prevent is a requirements.txt that is
    quietly incomplete. An unknown import is a build error, not a shrug.

    Mutation killed: distribution_for falling back to the import name, which
    would emit 'pkg:pypi/fitz' -- a package that is not PyMuPDF.
    """
    with pytest.raises(BlockObligationError) as exc:
        distribution_for("some_package_nobody_recorded")
    assert "some_package_nobody_recorded" in str(exc.value)
    assert "DISTRIBUTIONS" in str(exc.value)
    with pytest.raises(BlockObligationError):
        dependency_obligations({"vendor/x.py": "import some_package_nobody_recorded\n"})


def test_import_names_that_differ_from_their_distribution_are_recorded():
    """Every one of these has bitten a Dockerfile somewhere."""
    assert DISTRIBUTIONS["yaml"] == "PyYAML"
    assert DISTRIBUTIONS["bs4"] == "beautifulsoup4"
    assert DISTRIBUTIONS["fitz"] == "PyMuPDF"
    assert DISTRIBUTIONS["PIL"] == "Pillow"
    assert DISTRIBUTIONS["sklearn"] == "scikit-learn"
    assert DISTRIBUTIONS["docx"] == "python-docx"
    assert DISTRIBUTIONS["psycopg2"] == "psycopg2-binary"


def test_unparseable_vendored_source_still_declares_what_it_imports():
    """The CLONER vendors truncated and malformed Store source on purpose --
    that is what the Store-unwired adapters are for. Reporting such a file as
    importing nothing is the accident-not-declaration hole reopening.

    Mutation killed: returning {} on SyntaxError, which silently drops
    aiofiles for any block the CLONER had to patch.
    """
    broken = (
        "import aiofiles\n"
        "class StorageBlock:\n"
        "    def store(self):\n"
        "        except Exception:\n"
        "            import httpx\n"
    )
    found = third_party_imports(broken)
    assert found["aiofiles"] == "module"
    assert found["httpx"] == "lazy"
    assert dependency_obligations({"vendor/s.py": broken})["aiofiles"]["reach"] == "module"


def test_the_line_scanner_still_ignores_stdlib_and_local_imports():
    broken = (
        "import json\n"
        "from app.core import x\n"
        "import yaml\n"
        "def f(:\n"
    )
    assert third_party_imports(broken) == {"yaml": "module"}


def test_rendered_lines_dedupe_against_the_runtime_lane():
    """pydantic is already declared. A second unversioned line for it would
    override the >=2.0 floor the runtime needs."""
    obligations = dependency_obligations(
        {"vendor/n.py": "import pydantic\nimport yaml\n"}
    )
    text = render_dependency_lines(obligations, already=("pydantic", "fastapi"))
    assert "PyYAML" in text
    assert not any(
        line.strip().lower().startswith("pydantic") for line in text.splitlines()
    )


def test_rendered_lines_say_which_reach_and_which_file():
    text = render_dependency_lines(
        dependency_obligations(
            {"vendor/cerebrum/blocks/document_engine.py": "def f():\n    import yaml\n"}
        )
    )
    line = [l for l in text.splitlines() if l.startswith("PyYAML")][0]
    assert "action" in line
    assert "document_engine.py" in line
    # Unversioned on purpose: an invented floor is a guess that fails at
    # pip install rather than an honestly unpinned dependency.
    assert "==" not in line and ">=" not in line


def test_nothing_to_declare_renders_nothing():
    assert render_dependency_lines({}) == ""
    assert render_dependency_lines(
        dependency_obligations({"vendor/x.py": "import json\n"})
    ) == ""


def test_the_four_dependencies_that_shipped_undeclared_are_all_covered():
    """The report that opened this class, as a regression fixture."""
    for module, dist in [
        ("yaml", "PyYAML"),
        ("httpx", "httpx"),
        ("aiosmtplib", "aiosmtplib"),
        ("psycopg2", "psycopg2-binary"),
    ]:
        assert distribution_for(module) == dist


def test_pypdf2_legacy_import_has_a_recorded_distribution():
    """Live sess_d1cb9d51c5354bea / CEREBRUMDEV-BACKEND-A (2026-09-04).

    Store ``document_engine`` still imports the legacy name ``PyPDF2``.
    DISTRIBUTIONS already mapped ``pypdf`` -> ``pypdf`` but not ``PyPDF2``,
    so CLONER raised BlockObligationError and the build thread crashed
    (Sentry CEREBRUMDEV-BACKEND-A).

    Mutation killed: adding only the modern ``pypdf`` key and leaving the
    legacy import unrecorded.
    """
    assert distribution_for("PyPDF2") == "PyPDF2"
    assert distribution_for("pypdf") == "pypdf"
    obligations = dependency_obligations(
        {"vendor/cerebrum/blocks/document_engine.py": "import PyPDF2\n"}
    )
    assert "PyPDF2" in obligations
    assert obligations["PyPDF2"]["module"] == "PyPDF2"
    assert obligations["PyPDF2"]["reach"] == "module"


# -- end to end: the obligation must reach requirements.txt ---------------
#
# The unit tests above prove the scanner and the renderer. This proves the
# wiring, which is the part that was actually broken: the factory already had
# an honest requirements.txt header explaining that a module which imports a
# package must DECLARE it -- and then generated the file from app/ alone,
# while the CLONER copied in block source importing yaml, httpx, aiofiles and
# psycopg2. The rule existed; it was never applied to the vendored lane.

from pathlib import Path

from app.factory.build.roles_handlers import _render_requirements


def test_render_requirements_declares_what_the_cloner_vendored():
    """The whole chain, from vendored source text to the shipped file."""
    shipped = {
        "vendor/cerebrum/blocks/storage.py": "import aiofiles\n\n\nclass S:\n    pass\n",
        "vendor/cerebrum/blocks/document_engine.py": (
            "class D:\n    def parse(self):\n        import yaml\n        return yaml\n"
        ),
        "vendor/cerebrum/blocks/web.py": "import httpx\nfrom bs4 import BeautifulSoup\n",
    }
    text = _render_requirements(dependency_obligations(shipped))

    # The runtime lane is untouched, pins and all (dependency_pins).
    from app.factory.build.dependency_pins import pin

    assert pin("fastapi") + "\n" in text
    assert pin("pydantic") + "\n" in text

    # And the vendored lane is now declared, under its PyPI name.
    assert "aiofiles" in text
    assert "PyYAML" in text
    assert "httpx" in text
    assert "beautifulsoup4" in text
    # Not the import names, which pip cannot install.
    assert "\nyaml" not in text
    assert "\nbs4" not in text

    # Severity is stated, because the two failure modes look different in
    # production: a module-level import means the block cannot load; a lazy
    # one means it loads, reports healthy, and fails on one action.
    aiofiles_line = [l for l in text.splitlines() if l.startswith("aiofiles")][0]
    pyyaml_line = [l for l in text.splitlines() if l.startswith("PyYAML")][0]
    assert "module:" in aiofiles_line
    assert "action:" in pyyaml_line


def test_render_requirements_is_unchanged_when_nothing_was_vendored():
    """A platform with no blocks must not grow a stray comment block."""
    assert _render_requirements(None) == _render_requirements({})
    assert "Vendored block dependencies" not in _render_requirements(None)


def test_the_runtime_lane_is_never_double_declared():
    """A vendored block importing pydantic must not emit a second,
    unversioned pydantic line that shadows the Factory's pin."""
    text = _render_requirements(
        dependency_obligations({"vendor/x.py": "import pydantic\nimport fastapi\n"})
    )
    assert len([l for l in text.splitlines() if l.startswith("pydantic")]) == 1
    assert len([l for l in text.splitlines() if l.startswith("fastapi")]) == 1
    from app.factory.build.dependency_pins import pin

    assert pin("pydantic") + "\n" in text


def test_the_declared_set_parses_as_requirements():
    """Whatever is emitted has to survive the SBOM's own parser, or the
    supply-chain artifact silently loses components."""
    from app.factory.build.supply_chain import parse_requirement_lines

    text = _render_requirements(
        dependency_obligations({
            "vendor/a.py": "import aiofiles\n",
            "vendor/b.py": "def f():\n    import yaml\n",
        })
    )
    names = {row["name"] for row in parse_requirement_lines(text)}
    assert {"fastapi", "pydantic", "aiofiles", "PyYAML"} <= names
    # An unversioned line is recorded as unspecified, not dropped.
    rows = {r["name"]: r["version"] for r in parse_requirement_lines(text)}
    assert rows["PyYAML"] == "unspecified"
    from app.factory.build.dependency_pins import FACTORY_PINS

    assert rows["fastapi"] == "==" + FACTORY_PINS["fastapi"]


# -- the wiring, driven through the real CLONER ---------------------------
#
# Everything above can pass with the feature completely disconnected: the
# scanner works, the renderer works, and requirements.txt still ships without
# them because run_cloner never records what it vendored or run_writer never
# reads it. That gap is the whole defect class, so it gets a test that runs
# the real role against a faux Store.

import json

from app.factory.build.authority import BuildRole
from app.factory.build.roles import RoleContext, run_cloner
from app.factory.build.workspace import RoleWorkspace


_BLOCKS_INIT = '''\
import importlib

_EXTENDED_BLOCK_DEFS = {
    "reporter": ("app.blocks.reporter", "ReporterBlock"),
}


def get_block(name):
    module_path, class_name = _EXTENDED_BLOCK_DEFS[name]
    return getattr(importlib.import_module(module_path), class_name)
'''

# Shaped like the real document_engine and storage: one third-party import at
# module level (the block cannot load without it) and one inside a method (the
# block loads, reports healthy, and fails on that action only).
_REPORTER = '''\
import aiofiles

from app.core.universal_base import UniversalBlock


class ReporterBlock(UniversalBlock):
    async def execute(self, input_data, params):
        import yaml

        return {"status": "ok", "result": {"parsed": yaml.safe_load("a: 1")}}
'''

_UNIVERSAL_BASE = '''\
class UniversalBlock:
    def __init__(self, hal_block=None, config=None):
        self.hal_block = hal_block
        self.config = config or {}
'''

_SHIM = '''\
"""Auto-generated adapter for Cerebrum block: reporter."""

import asyncio
from app.blocks import get_block


def run(**kwargs):
    block_cls = get_block("reporter")
    instance = block_cls()
    envelope = asyncio.run(instance.execute(kwargs.get("input", kwargs), {}))
    return envelope.get("result", envelope)
'''


def _store_with_third_party_imports(root: Path) -> Path:
    store = root / "store"
    reg = store / "block_registry" / "reporter"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(
        json.dumps({"id": "reporter", "name": "Reporter"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_SHIM, encoding="utf-8")
    # A helper beside the shim. The CLONER rglobs the registry directory and
    # rewrites every .py in it, so a third-party import here ships too -- and
    # is scanned on a different code path from the app/blocks slice.
    (reg / "formatting.py").write_text(
        "import pandas\n\n\ndef to_frame(rows):\n    return pandas.DataFrame(rows)\n",
        encoding="utf-8",
    )

    blocks = store / "app" / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "__init__.py").write_text(_BLOCKS_INIT, encoding="utf-8")
    (blocks / "reporter.py").write_text(_REPORTER, encoding="utf-8")

    core = store / "app" / "core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "universal_base.py").write_text(_UNIVERSAL_BASE, encoding="utf-8")
    return store


def test_the_cloner_records_what_the_blocks_it_vendored_import(tmp_path, monkeypatch):
    """Mutation killed: run_cloner recording {} -- the feature disconnected at
    the producing end, with every unit test still green."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    store = _store_with_third_party_imports(tmp_path)
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    state = {"resolved_blocks": ("reporter",)}
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=store,
        state=state,
    )
    result = run_cloner(ctx)
    assert result.ok, result.detail

    deps = state.get("vendored_dependencies")
    assert deps, "the CLONER must record the vendored lane's dependencies"
    assert deps["aiofiles"]["reach"] == "module"
    assert deps["PyYAML"]["reach"] == "lazy"
    # Named against the file that ships, so a reviewer can check the claim.
    assert any("reporter.py" in f for f in deps["aiofiles"]["files"])
    # The shim directory is rewritten on its own code path, after the runtime
    # slice. A file that ships from there is a dependency exactly the same way.
    assert "pandas" in deps, "shim-directory imports must be recorded too"
    assert any("formatting.py" in f for f in deps["pandas"]["files"])


class _Cap:
    def __init__(self, cid):
        self.capability_id = cid
        self.block_ids = ()


class _Plan:
    def __init__(self, *cids):
        self.capabilities = tuple(_Cap(c) for c in cids)


class _Blueprint:
    product_name = "Obligation Probe"
    product_id = "obligation-probe"
    vertical = "testing"


def test_the_writer_declares_what_the_cloner_recorded(tmp_path, monkeypatch, stub_coder):
    """Mutation killed: run_writer calling _render_requirements(None) -- the
    feature disconnected at the consuming end, with every other test green.

    Driven through the real run_writer and asserted on the file it WRITES,
    because requirements.txt is what pip installs and what the SBOM is built
    from. Asserting on the renderer with the cloner's dict would prove the
    two halves work and say nothing about whether they are joined.
    """
    from app.factory.build.roles import run_writer

    store = _store_with_third_party_imports(tmp_path)
    clone_ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    state = {"resolved_blocks": ("reporter",)}
    assert run_cloner(RoleContext(
        role=BuildRole.CLONER, workspace=clone_ws, blueprint=None, plan=None,
        blocks_root=store, state=state,
    )).ok

    write_ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    assert run_writer(RoleContext(
        role=BuildRole.WRITER, workspace=write_ws, blueprint=_Blueprint(),
        plan=_Plan("alpha_cap"), state=state,
    )).ok

    text = write_ws.read_text("requirements.txt")
    assert "aiofiles" in text
    assert "PyYAML" in text
    assert "pandas" in text
    # The runtime lane still ships with its pins.
    from app.factory.build.dependency_pins import pin

    assert pin("fastapi") + "\n" in text
    assert pin("pydantic") + "\n" in text
