"""Regression coverage for the delivery-path fixes.

Two live-factory failures, one root each:

1. The headless CodeWhale worker was dispatched as
   ``codewhale exec --auto --json --provider deepseek --api-key ...`` â€”
   codewhale 0.9.13 parses ``--provider``/``--api-key`` as GLOBAL flags
   and refuses them after ``exec`` ("--provider must be placed before
   `exec`"), so the WRITER died in under a second and authored nothing.

2. The TESTER harness generated ``tests/test_models.py`` with a bare
   ``def test_every_model_round_trips():`` when a plan declared
   capabilities but no on-disk handler produced model specs â€” an
   IndentationError that burned the rework budget and masked the real
   gap ("No module named 'app'").
"""

from __future__ import annotations

import ast
import json
import subprocess
from types import SimpleNamespace
from unittest import mock

from app.factory.build import codewhale_worker
from app.factory.build.authority import BuildRole
from app.factory.build.roles_handlers import run_tester
from app.factory.build.roles_models import RoleContext
from app.factory.build.tenant_bind import bind_tenant_store
from app.factory.build.workspace import RoleWorkspace


def test_worker_argv_puts_provider_and_api_key_before_exec(tmp_path):
    """Global flags precede the `exec` subcommand (codewhale 0.9.13 usage)."""
    prompt = "write the platform"
    captured: dict = {}

    def fake_popen(argv, *, cwd, stdout, stderr, text, bufsize, env, stdin, **agent_kwargs):
        captured["argv"] = argv
        captured["cwd"] = cwd
        captured["env"] = env
        captured["stdin"] = stdin
        captured["agent_kwargs"] = agent_kwargs
        return _FakeProc(
            argv,
            json.dumps({"status": "completed", "termination_reason": "resolved"}),
        )

    class _FakeProc:
        def __init__(self, argv, summary_text):
            import io

            self.stdout = io.StringIO(summary_text + "\n")
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = 9

    # The slot manager is NOT mocked out: patching it made this test blind to
    # every slot regression, and it passed `object()` as the tenant handle â€”
    # a handle with no tenant_key, which the accounting must refuse rather
    # than drop into a shared bucket. Use the handle production binds.
    tenant_store = bind_tenant_store("acct-writer-delivery")
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker.subprocess, "Popen", side_effect=fake_popen):
        receipt = codewhale_worker.run_worker_job(
            prompt, tmp_path / "checkout", tenant_store=tenant_store
        )

    assert receipt.status == "completed"
    # The child's stdin is DEVNULL: an interactive CLI prompt after the
    # banner must never be able to block the writer on unanswerable stdin
    # (live-factory silent hang, sess_e4bcf26ec19c4d04).
    assert captured["stdin"] is subprocess.DEVNULL
    # The writer runs in its own session: its shell can never signal the
    # Factory server's process group (exactly the agent_process keywords,
    # nothing more).
    from app.factory.build.agent_process import agent_popen_kwargs

    assert captured["agent_kwargs"] == agent_popen_kwargs()
    # The slot was really taken and really given back.
    assert codewhale_worker.worker_slots_snapshot() == {"total": 0, "by_tenant": {}}
    # The child runs under an explicit per-job environment, never the live
    # process env inherited by reference at fork.
    assert isinstance(captured["env"], dict)
    # The brief travels by file, never argv (the 128 KiB exec cliff --
    # "[Errno 7] Argument list too long" killed a live dispatch); argv ends
    # with the fixed-size pointer and the file carries the brief verbatim.
    assert captured["argv"][:-1] == [
        "codewhale",
        "--provider",
        "deepseek",
        "--api-key",
        "sk-test",
        "exec",
        "--auto",
        "--json",
    ]
    assert "docs/writer_prompt.txt" in captured["argv"][-1]
    assert (
        tmp_path / "checkout" / "docs" / "writer_prompt.txt"
    ).read_text(encoding="utf-8") == prompt
    # Global flags must never be appended after the subcommand again.
    assert captured["argv"].index("--provider") < captured["argv"].index("exec")


def test_writer_uses_codewhale_falls_back_to_process_env(monkeypatch):
    """The runner invokes role handlers with ctx only â€” env is always None
    in production â€” so the worker switch must read os.environ (the same
    fallback writer_uses_cli_pivot applies). Without it the seam silently
    never arms (live-factory failure sess_b9db05967cb94e6f: writer_no_output,
    28 templated files, zero agent-authored)."""
    from app.factory.build.roles_handlers import writer_uses_codewhale

    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")
    assert writer_uses_codewhale(None) is True
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "0")
    assert writer_uses_codewhale(None) is False


def test_tenant_store_binding_is_identity_derived(tmp_path, monkeypatch):
    """Phase 1: the store handle is server-derived from the authenticated
    identity â€” same account, same digest; never a client-supplied name in
    the path; no identity, no binding."""
    from app.factory.build.tenant_bind import (
        bind_tenant_store,
        tenant_stores_root,
    )

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    a = bind_tenant_store("acct_alpha")
    b = bind_tenant_store("acct_alpha")
    c = bind_tenant_store("acct_beta")
    assert a is not None and b is not None and c is not None
    assert a.tenant_key == b.tenant_key
    assert a.store_dir == b.store_dir
    assert a.tenant_key != c.tenant_key
    assert a.store_dir.parent == tenant_stores_root()
    assert "acct_alpha" not in str(a.store_dir)
    assert bind_tenant_store(None) is None
    assert bind_tenant_store("") is None
    assert bind_tenant_store("  ") is None


def test_runner_seeds_bound_tenant_store_into_writer_state(tmp_path):
    """The runner must hand the WRITER the bound handle â€” the worker's
    isolation gate reads ctx.state['tenant_store'] and refuses an unbound
    job (live-factory failure sess_b9db05967cb94e6f)."""
    from pathlib import Path as _Path

    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    smoke = _Path(__file__).resolve().parents[2] / "blueprints" / "examples" / "runner_smoke.yaml"
    workspace = tmp_path / "out"
    workspace.mkdir()
    binding = object()
    runner = RoleRunner(
        load_blueprint(smoke),
        workspace,
        tenant_store=binding,
    )
    assert runner.state.get("tenant_store") is binding


def test_start_runner_build_binds_tenant_from_session_identity_when_account_absent(
    tmp_path, monkeypatch
):
    """Master-key (admin) callers carry no account id: the tenant binding
    falls back to the server-owned session identity so the worker's
    isolation gate still gets a bound handle (live-factory failure
    sess_665604dce87046d7)."""
    from pathlib import Path as _Path

    from app.factory.blueprint import load_blueprint
    from app.factory.build import tenant_bind
    import app.factory.build_jobs as bj

    smoke = _Path(__file__).resolve().parents[2] / "blueprints" / "examples" / "runner_smoke.yaml"
    captured = {}

    def fake_run(
        blueprint, output_dir, blocks_root, cycle="code", tenant_store=None, brief="", inputs_hash=""
    ):
        captured["tenant_store"] = tenant_store
        captured["brief"] = brief
        captured["inputs_hash"] = inputs_hash

    class _InlineThread:
        def __init__(self, *args, **kwargs):
            self._args = args
            self._kwargs = kwargs
            self.name = kwargs.get("name", "inline")
            self.daemon = True

        def start(self):
            self._kwargs.get("target", lambda *_: None)(*self._kwargs.get("args", ()))

        def is_alive(self):
            # Runs inline: once start() returns, the build is not running.
            return False

    monkeypatch.setattr(bj, "_run", fake_run)
    monkeypatch.setattr("threading.Thread", _InlineThread)
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")

    bj.start_runner_build(
        load_blueprint(smoke),
        tmp_path / "out",
        tenant_identity="sess_admin_test",
        brief="I need a platform for my car dealership",
    )
    ts = captured["tenant_store"]
    assert ts is not None
    assert ts.tenant_key
    assert "sess_admin_test" not in str(ts.store_dir)
    # The user's Floor-chat brief threads alongside the tenant binding.
    assert captured["brief"] == "I need a platform for my car dealership"

    # A legacy caller that passes only quota_account_id still binds from
    # the authenticated account identity.
    bj.start_runner_build(
        load_blueprint(smoke),
        tmp_path / "out2",
        quota_account_id="acct_1",
    )
    assert captured["tenant_store"].tenant_key == tenant_bind.bind_tenant_store("acct_1").tenant_key


def test_writer_prompt_carries_the_compiled_brief_not_an_empty_brief(tmp_path):
    """The worker must follow the C-BRIEF CLONER compiled â€” live builds
    shipped an EMPTY BRIEF section because ctx.state['brief'] was never
    set and the seam never read the compiled brief
    (sess_9f67681a79324fcc class)."""
    from app.factory.build.roles_handlers import _compiled_writer_brief
    from app.factory.build.writer_prompt import render_writer_prompt

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "coder_brief.md").write_text(
        "# C-BRIEF\n\ninventory: REUSE audit {json:true}\n",
        encoding="utf-8",
    )
    bp = SimpleNamespace(
        product_id="automotive",
        product_name="AutoDealer",
        vertical="automotive",
        summary="car dealership",
        capabilities=[],
    )
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace("WRITER", tmp_path),
        blueprint=bp,
        plan=SimpleNamespace(capabilities=[]),
        state={},
    )
    brief = _compiled_writer_brief(ctx)
    assert "inventory: REUSE audit" in brief
    # Literal braces in the brief must survive rendering (values are
    # inserted verbatim by str.format).
    prompt = render_writer_prompt(bp, brief=brief)
    assert "inventory: REUSE audit {json:true}" in prompt


def test_user_floor_brief_wins_over_the_compiled_brief(tmp_path):
    """ALL brief sources reach the worker together: the user's Floor-chat
    words, the compiled C-BRIEF, and the capability specs."""
    from app.factory.build.roles_handlers import _compiled_writer_brief

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "coder_brief.md").write_text(
        "# C-BRIEF\n\ncompiled inventory",
        encoding="utf-8",
    )
    bp = SimpleNamespace(
        product_id="automotive",
        product_name="AutoDealer",
        vertical="automotive",
        summary="car dealership",
        capabilities=[
            SimpleNamespace(id="vehicles", description="inventory make/model/year"),
        ],
    )
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace("WRITER", tmp_path),
        blueprint=bp,
        plan=SimpleNamespace(capabilities=[]),
        state={"brief": "I need a platform for my car dealership"},
    )
    brief = _compiled_writer_brief(ctx)
    assert "USER BRIEF" in brief
    assert "I need a platform for my car dealership" in brief
    assert "COMPILED C-BRIEF" in brief
    assert "compiled inventory" in brief
    assert "CAPABILITIES" in brief
    assert "vehicles: inventory make/model/year" in brief


def test_writer_brief_falls_back_to_capability_specs_when_no_coder_brief(tmp_path):
    """Without a compiled brief the worker still gets the blueprint's
    capability specs â€” never an empty BRIEF section."""
    from app.factory.build.roles_handlers import _compiled_writer_brief

    bp = SimpleNamespace(
        product_id="automotive",
        product_name="AutoDealer",
        vertical="automotive",
        summary="car dealership platform",
        capabilities=[
            SimpleNamespace(id="vehicles", description="inventory make/model/year"),
            SimpleNamespace(id="leads", description="customer inquiries"),
        ],
    )
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace("WRITER", tmp_path),
        blueprint=bp,
        plan=SimpleNamespace(capabilities=[]),
        state={},
    )
    brief = _compiled_writer_brief(ctx)
    assert brief
    assert "vehicles: inventory make/model/year" in brief
    assert "leads: customer inquiries" in brief


def test_tester_harness_models_file_compiles_with_no_specs(tmp_path):
    """Capabilities declared but zero handler specs must still yield a
    syntactically valid tests/test_models.py (bare-def regression)."""
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=RoleWorkspace("TESTER", tmp_path),
        blueprint=SimpleNamespace(
            product_id="automotive",
            product_name="AutoDealer Hub",
            vertical="automotive",
        ),
        plan=SimpleNamespace(
            capabilities=[SimpleNamespace(capability_id="automotive_core")]
        ),
        state={"model_specs": {}, "route_bodies": {}, "vendored_blocks": ()},
    )
    result = run_tester(ctx)
    assert result.ok, result.detail

    models_src = (tmp_path / "tests" / "test_models.py").read_text(encoding="utf-8")
    ast.parse(models_src)  # must not raise
    assert "def test_every_model_round_trips():" in models_src
    assert "    pass" in models_src, "round-trip test must have a body"

    # Every other generated harness file must parse too â€” a red suite that
    # cannot even collect hides the real gap from the rework round.
    for test_file in sorted((tmp_path / "tests").glob("test_*.py")):
        ast.parse(test_file.read_text(encoding="utf-8"))


def test_tester_harness_models_file_compiles_with_specs(tmp_path):
    """The populated path stays valid: one capability with sampleable fields."""
    spec = {
        "entity": "vehicle",
        "fields": [
            {"name": "make", "type": "str"},
            {"name": "year", "type": "int"},
        ],
    }
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=RoleWorkspace("TESTER", tmp_path),
        blueprint=SimpleNamespace(
            product_id="automotive",
            product_name="AutoDealer Hub",
            vertical="automotive",
        ),
        plan=SimpleNamespace(
            capabilities=[SimpleNamespace(capability_id="automotive_core")]
        ),
        state={
            "model_specs": {"automotive_core": spec},
            "route_bodies": {},
            "vendored_blocks": (),
        },
    )
    result = run_tester(ctx)
    assert result.ok, result.detail

    models_src = (tmp_path / "tests" / "test_models.py").read_text(encoding="utf-8")
    ast.parse(models_src)
    assert "record = {'make':" in models_src
    assert "def test_models_expose_their_fields():" in models_src
