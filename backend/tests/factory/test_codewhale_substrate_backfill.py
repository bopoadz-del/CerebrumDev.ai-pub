"""The CodeWhale writer path must satisfy the contract TESTER stamps.

``run_writer`` returns at its CodeWhale branch, and
``FACTORY_CODEWHALE_WRITER=1`` makes that the only path production takes --
so ``emit_writer_artifacts``, further down that function, never runs. But
``run_tester`` still stamps ``tests/test_data_lifecycle.py``, whose first
line is ``from app import backup, store``. Live build
sess_b6d51f9089e14176 went red on exactly that:

    ImportError: cannot import name 'backup' from 'app'

The agent is never asked for those files -- the writer prompt names
``app/migrations/`` and ``scripts/release_gate.py`` and says nothing about
backup, alembic, or an entrypoint -- so without a backfill it has to
reverse-engineer the substrate contract from red tests, one rework round
per missing file.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.data_lifecycle import (
    REVISION_0002,
    backfill_platform_substrate,
    platform_substrate,
    render_product_tests,
)


class _Workspace:
    """Minimal RoleWorkspace stand-in: write_text + exists over one root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.written: list[str] = []

    def write_text(self, relpath, content: str) -> Path:
        dest = self.root / Path(relpath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        self.written.append(Path(relpath).as_posix())
        return dest

    def exists(self, relpath) -> bool:
        return (self.root / Path(relpath)).exists()

    def read_text(self, relpath) -> str:
        return (self.root / Path(relpath)).read_text(encoding="utf-8")


SPECS = {"booking": {"entity": "booking", "fields": [{"name": "reference", "type": "string"}]}}


def test_the_module_the_emitted_suite_imports_is_written(tmp_path):
    """The exact live failure: tests/test_data_lifecycle.py imports app.backup."""
    suite = render_product_tests(SPECS)
    assert "from app import backup, store" in suite, "test contract changed"

    ws = _Workspace(tmp_path)
    result = backfill_platform_substrate(ws)

    assert "app/backup.py" in result["written"]
    assert (tmp_path / "app" / "backup.py").is_file()
    assert (tmp_path / "app" / "migrations.py").is_file()


def test_a_stub_that_breaks_a_stamped_import_is_repaired(tmp_path):
    """D2 (live 2026-09-29, contractor platform): with stub_rate=1.0 the agent
    left a stub app/domain_ops.py. The backfill was 'gaps only' -- file exists
    → skip -- so the stub survived, and the factory's OWN stamped test
    (from app.domain_ops import OUTCOMES, perform_all) died at collection:
    'suite_red: missing module -- FAILED tests.test_domain_acceptance'. The
    factory owns that module and that import contract, so it must repair a
    stub that cannot satisfy the test the factory itself stamps."""
    from app.factory.build.domain_acceptance import (
        backfill_domain_substrate,
        render_product_tests,
    )

    specs = {"booking": {"entity": "booking",
                         "fields": [{"name": "reference", "type": "string"}]}}
    assert "from app.domain_ops import OUTCOMES, perform_all" in render_product_tests(specs)

    ws = _Workspace(tmp_path)
    ws.write_text("app/domain_ops.py", '"""a stub the agent left"""\n')

    result = backfill_domain_substrate(ws, specs)

    repaired = result.get("repaired", [])
    assert any("domain_ops" in r for r in repaired), (
        f"the stub was not repaired: written={result['written']} "
        f"skipped={result['skipped']} repaired={repaired}"
    )
    body = (tmp_path / "app" / "domain_ops.py").read_text(encoding="utf-8")
    assert "OUTCOMES" in body and "def perform_all" in body, (
        "domain_ops still cannot satisfy the stamped test's import"
    )


def test_a_module_level_agent_file_is_still_left_alone(tmp_path):
    """The repair keys on NAME-level import contracts only. A module the
    stamped suite imports whole (from app import backup) has no name contract,
    so an agent-authored backup.py must still be preserved, not clobbered."""
    ws = _Workspace(tmp_path)
    mine = "# authored by the coding agent, not the factory\n"
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "backup.py").write_text(mine, encoding="utf-8")

    backfill_platform_substrate(ws)

    assert (tmp_path / "app" / "backup.py").read_text(encoding="utf-8") == mine


def test_a_partial_real_module_is_a_conflict_not_a_clobber(tmp_path):
    """Safety valve: if a file provides SOME of the required names but not all,
    it may be real authored work the factory must not overwrite. Repair is
    unsafe -> report a conflict (the caller halts FACTORY, no rework)."""
    from app.factory.build.domain_acceptance import backfill_domain_substrate

    specs = {"booking": {"entity": "booking",
                         "fields": [{"name": "reference", "type": "string"}]}}
    ws = _Workspace(tmp_path)
    # Provides OUTCOMES but not perform_all -- a partial, possibly-real module.
    ws.write_text("app/domain_ops.py", 'OUTCOMES = ("create_persists",)\n')

    result = backfill_domain_substrate(ws, specs)

    assert any("domain_ops" in c for c in result.get("conflicts", [])), result
    # Not silently overwritten.
    assert "OUTCOMES" in (tmp_path / "app" / "domain_ops.py").read_text(encoding="utf-8")


def test_every_substrate_file_lands_when_the_agent_wrote_none(tmp_path):
    ws = _Workspace(tmp_path)
    result = backfill_platform_substrate(ws)

    expected = [rel for rel, _ in platform_substrate()]
    assert sorted(result["written"]) == sorted(expected)
    assert result["skipped"] == []
    for rel in expected:
        assert (tmp_path / rel).is_file(), rel


def test_the_agents_own_files_are_never_overwritten(tmp_path):
    """Fills gaps; does not converge. The agent's bytes must survive."""
    ws = _Workspace(tmp_path)
    mine = "# authored by the coding agent, not the factory\n"
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "backup.py").write_text(mine, encoding="utf-8")

    result = backfill_platform_substrate(ws)

    assert "app/backup.py" in result["skipped"]
    assert "app/backup.py" not in result["written"]
    assert (tmp_path / "app" / "backup.py").read_text(encoding="utf-8") == mine


def test_the_schema_bearing_files_stay_the_agents(tmp_path):
    """store.py and 0001_baseline carry the entity schema -- never backfilled."""
    rels = [rel for rel, _ in platform_substrate()]
    assert "app/store.py" not in rels
    assert "alembic/versions/0001_baseline.py" not in rels
    # The lifecycle-audit revision has no specs in it, so it is substrate.
    assert f"alembic/versions/{REVISION_0002}.py" in rels


def test_a_module_never_shadows_a_package_the_agent_wrote(tmp_path):
    """The writer prompt asks for app/migrations/; the suite needs
    app/migrations.py. With both present the package wins and the import
    fails in a way that reads as the agent's bug."""
    ws = _Workspace(tmp_path)
    pkg = tmp_path / "app" / "migrations"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("# agent's package\n", encoding="utf-8")

    result = backfill_platform_substrate(ws)

    assert not (tmp_path / "app" / "migrations.py").exists()
    assert any(s.startswith("app/migrations.py") for s in result["skipped"]), result
    # The rest of the substrate still lands.
    assert "app/backup.py" in result["written"]


def test_backfill_is_idempotent(tmp_path):
    ws = _Workspace(tmp_path)
    first = backfill_platform_substrate(ws)
    second = backfill_platform_substrate(ws)

    assert first["written"]
    assert second["written"] == []
    assert sorted(second["skipped"]) == sorted(first["written"])


def test_the_codewhale_path_calls_the_backfill():
    """The wiring, not just the helper: the production path must use it."""
    import inspect

    from app.factory.build import roles_handlers

    src = inspect.getsource(roles_handlers._run_writer_via_codewhale_worker)
    assert "backfill_platform_substrate" in src, (
        "the CodeWhale writer path does not backfill the substrate -- the "
        "helper exists but production never calls it"
    )


#: app/ modules the coding agent authors (schema-bearing or capability code).
#: Everything else the emitted suite imports must be factory substrate.
AGENT_OWNED = {"app/store.py"}


def test_every_app_module_the_emitted_suite_imports_has_an_owner():
    """The divergence guard.

    The writer prompt and the emitted suite drifted apart: the prompt names
    ``app/migrations/`` and ``scripts/release_gate.py`` and never mentions
    backup, alembic or an entrypoint, while the suite imports
    ``app.backup`` and ``app.migrations``. emit_writer_artifacts used to
    bridge that, and the CodeWhale early return took it out of production.
    Any future import added to the suite must land in the substrate or be
    explicitly declared agent-owned -- never silently unowned.
    """
    import re

    suite = render_product_tests(SPECS)
    modules: set[str] = set()
    for line in suite.splitlines():
        m = re.match(r"\s*from app import ([\w, ]+)", line)
        if m:
            modules.update(f"app/{n.strip()}.py" for n in m.group(1).split(","))
            continue
        m = re.match(r"\s*from app\.(\w+) import ", line)
        if m:
            modules.add(f"app/{m.group(1)}.py")

    assert modules, "no app imports parsed -- the parser or the suite changed"
    owned = {rel for rel, _ in platform_substrate()} | AGENT_OWNED
    unowned = sorted(modules - owned)
    assert not unowned, (
        "the emitted suite imports app modules nothing is required to write: "
        + ", ".join(unowned)
    )


STAMP = '"""Written by the factory WRITER role (codewhale exec)"""\nAUTHORED_BY = "codewhale exec"\n'


def _handler(root: Path, cap: str) -> None:
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / f"{cap}.py").write_text(
        STAMP + f'CAPABILITY_ID = "{cap}"\n\n\ndef handle(payload):\n    return payload\n',
        encoding="utf-8",
    )


class TestReworkRoundIsNotJudgedEmpty:
    """A rework round that writes no NEW handler is not a silent writer.

    ``persist_workspace_root`` returns the staging root, and staging is
    rmtree'd fresh each phase, so the role's authored count saw only the
    current round. A round that fixed app/dispatch.py and a route scored
    authored=0 and killed a build carrying stamped handlers -- live
    sess_b6d51f9089e14176: status='completed' tools=123 authored=0.
    """

    def test_handlers_already_committed_are_counted(self, tmp_path):
        from app.factory.build.authorship import (
            agent_written_handler_ids_in_workspace,
        )

        destination = tmp_path / "build"
        staging = tmp_path / ".build.staging-writer"
        staging.mkdir(parents=True, exist_ok=True)
        _handler(destination, "record_checkin")
        _handler(destination, "list_todays_checkins")
        # This round fixed a non-handler file only.
        (staging / "app").mkdir(parents=True, exist_ok=True)
        (staging / "app" / "dispatch.py").write_text("# fixed\n", encoding="utf-8")

        assert agent_written_handler_ids_in_workspace(staging) == []
        carried = agent_written_handler_ids_in_workspace(destination)
        assert sorted(carried) == ["list_todays_checkins", "record_checkin"]

    def test_the_role_falls_back_to_the_committed_tree(self):
        """The wiring: the refusal must consult the destination."""
        import inspect

        from app.factory.build import roles_handlers

        src = inspect.getsource(roles_handlers._run_writer_via_codewhale_worker)
        assert "carried_over" in src
        assert "not (authored or carried_over)" in src, (
            "writer_no_output still judges the staging round alone -- a rework "
            "round that writes no new handler will kill the build"
        )

    def test_a_writer_that_produced_nothing_at_all_is_still_refused(self, tmp_path):
        """The check must not become vacuous: zero everywhere is still zero."""
        from app.factory.build.authorship import (
            agent_written_handler_ids_in_workspace,
        )

        destination = tmp_path / "build"
        destination.mkdir(parents=True, exist_ok=True)
        staging = tmp_path / ".build.staging-writer"
        staging.mkdir(parents=True, exist_ok=True)

        assert agent_written_handler_ids_in_workspace(staging) == []
        assert agent_written_handler_ids_in_workspace(destination) == []


class TestWriterPromptNamesTheRealContract:
    """The prompt, the substrate and the emitted suite are one contract.

    v2 of the prompt asked for an ``app/migrations/`` package and never
    mentioned store/backup/alembic, so the agent learned the persistence
    contract from red tests. These bind the three together.
    """

    def _prompt(self) -> str:
        from app.factory.build.writer_prompt import render_writer_prompt

        class _Bp:
            product_id = "probe"
            product_name = "Probe"
            vertical = "probe"
            summary = "probe"

        return render_writer_prompt(_Bp(), brief="x")

    def test_the_prompt_no_longer_asks_for_the_shadowing_package(self):
        prompt = self._prompt()
        assert "- app/block_inputs.py, app/migrations/" not in prompt
        assert "Do not\ncreate an app/migrations/ package" in prompt

    def test_every_python_substrate_module_is_named_as_factory_written(self):
        prompt = " ".join(self._prompt().split())
        for rel, _ in platform_substrate():
            if rel.endswith(".py") or rel.endswith(".sh") or rel == "alembic.ini":
                assert rel in prompt, f"{rel} is backfilled but the prompt never says so"

    def test_the_agent_owned_files_are_named_with_their_surface(self):
        prompt = " ".join(self._prompt().split())
        for rel in AGENT_OWNED | {"alembic/versions/0001_baseline.py"}:
            assert rel in prompt, rel
        # Every store attribute the emitted suite touches is in the prompt.
        import re

        suite = render_product_tests(SPECS)
        used = set(re.findall(r"\bstore\.([A-Za-z_]+)", suite))
        missing = sorted(a for a in used if a not in prompt)
        assert not missing, f"the suite calls store.{missing} but the prompt never asks for it"


def test_the_prompt_forbids_killing_processes_the_agent_did_not_start():
    """The agent shares a container with the factory's server. A name-based
    kill (pkill uvicorn) after its own probe run is the prime suspect for the
    factory receiving a clean SIGTERM mid-build, twice."""
    from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt

    class _Bp:
        product_id = product_name = vertical = summary = "probe"

    prompt = " ".join(render_writer_prompt(_Bp(), brief="x").split())
    for phrase in ("pkill", "killall", "uvicorn", "exact PID", "$PORT"):
        assert phrase in prompt, phrase
    assert PROMPT_VERSION.endswith(".v4") or int(PROMPT_VERSION.rsplit(".v", 1)[1]) >= 4


class TestProvenanceOnTheProductionPath:
    """The product's Dockerfile runs scripts/release_gate.py, which fails the
    image build without docs/build_provenance.json. run_writer() writes it far
    below its CodeWhale early return, so production only had one when the agent
    happened to write it. Live: a dental platform passed four phases and died
    in Docker, 0/13, on 'docs/build_provenance.json: MISSING'."""

    def _run(self, tmp_path, monkeypatch, *, agent_wrote=None):
        import json as _json

        from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
        from tests.factory.test_codewhale_writer_dispatch import (
            _ctx,
            _plant_authored_handler,
            _receipt,
        )

        ctx = _ctx(tmp_path)
        _plant_authored_handler(tmp_path / "build")
        if agent_wrote is not None:
            (tmp_path / "build" / "docs").mkdir(parents=True, exist_ok=True)
            (tmp_path / "build" / "docs" / "build_provenance.json").write_text(
                _json.dumps(agent_wrote), encoding="utf-8"
            )
        monkeypatch.setattr(
            "app.factory.build.codewhale_worker.run_worker_job",
            lambda *a, **k: _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}]),
        )
        assert _run_writer_via_codewhale_worker(ctx).ok is True
        return _json.loads(
            (tmp_path / "build" / "docs" / "build_provenance.json").read_text(encoding="utf-8")
        )

    def test_the_manifest_exists_after_a_codewhale_pass(self, tmp_path, monkeypatch):
        prov = self._run(tmp_path, monkeypatch)

        assert prov["engine"] == "codewhale_worker"
        sources = prov["artifact_sources"]
        assert sources, "release_gate counts agent artifacts from this map"
        # release_gate.py credits the agent by this exact prefix.
        assert all(str(v).startswith("coder CLI") for v in sources.values())

    def test_the_manifest_lets_the_products_own_floor_check_pass(self, tmp_path, monkeypatch):
        """acceptance.py's authorship_floor (check 13) merges this manifest and
        calls full_pilot_authorship_from. Building the image is not enough."""
        from app.factory.build.authorship import full_pilot_authorship_from

        prov = self._run(tmp_path, monkeypatch)

        floor = full_pilot_authorship_from(prov, tmp_path / "build")
        assert floor.meets_floor, (floor.need, floor.action_py, floor.cli_authored_ids)

    def test_an_agent_written_manifest_is_left_alone(self, tmp_path, monkeypatch):
        mine = {"schema_version": "build_provenance.v1", "artifact_sources": {"x": "coder CLI"}, "by": "agent"}

        assert self._run(tmp_path, monkeypatch, agent_wrote=mine) == mine


class TestEveryStampedSuiteImportsSomethingProductionWrites:
    """The same hole, generalised: backup was the first, domain_ops the second.

    ``run_tester`` stamps its suite unconditionally. Every module those files
    import from ``app.`` must be written on the path production actually
    takes, or the suite dies at COLLECTION -- before a single assertion runs --
    and the agent burns a rework round per missing file trying to
    reverse-engineer a contract the factory owns.

    ``backfill_platform_substrate`` closed this for ``app/backup.py``. It is
    explicitly "the spec-independent half", so ``app/domain_ops.py`` -- written
    only by ``domain_acceptance.emit_writer_artifacts``, which the CodeWhale
    branch returns before reaching -- was left uncovered. Live build
    sess_42d244d317f042b2 (Cerebrum VenueOps, run 2) died on exactly that:

        suite_red: missing module -- FAILED tests.test_domain_acceptance
        - collection failure

    This test is the general invariant rather than a third named file.
    """

    #: (emitted suite file, its renderer) that ``run_tester`` always stamps.
    def _stamped_suites(self):
        from app.factory.build import data_lifecycle, deploy, domain_acceptance, ui_e2e

        specs = {
            "book_slot": {
                "entity": "booking",
                "fields": {"name": "str", "qty": "int"},
            }
        }
        return {
            "tests/test_data_lifecycle.py": data_lifecycle.render_product_tests(specs),
            "tests/test_deploy.py": deploy.render_product_tests(specs),
            "tests/test_domain_acceptance.py": domain_acceptance.render_product_tests(specs),
            "tests/test_ui_operator_flow.py": ui_e2e.render_ui_tests(specs),
        }

    def _modules_production_writes(self):
        """Every app/ module a production build actually puts on disk."""
        from app.factory.build.data_lifecycle import platform_substrate
        from app.factory.build.deploy import deploy_substrate
        from app.factory.build.domain_acceptance import domain_substrate

        written = {rel for rel, _ in platform_substrate()}
        written |= {rel for rel, _ in deploy_substrate()}
        written |= {rel for rel, _ in domain_substrate({})}
        # The agent authors these; the backfill deliberately leaves them alone.
        written |= {"app/store.py", "app/main.py", "app/models.py"}
        return {
            rel[len("app/"):-len(".py")]
            for rel in written
            if rel.startswith("app/") and rel.endswith(".py")
        }

    def test_no_stamped_suite_imports_a_module_production_never_writes(self):
        import ast

        available = self._modules_production_writes()
        missing = {}
        for name, source in self._stamped_suites().items():
            needed = set()
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app."):
                    needed.add(node.module[len("app."):].split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.module == "app":
                    needed |= {a.name for a in node.names}
            gap = sorted(needed - available)
            if gap:
                missing[name] = gap
        assert not missing, (
            "a stamped suite imports a module no production path writes, so it "
            f"dies at collection: {missing}"
        )
