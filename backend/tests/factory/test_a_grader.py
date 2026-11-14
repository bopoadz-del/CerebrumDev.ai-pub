"""The in-build GRADER: the writer's tree is graded BEFORE the TESTER judges it.

The grader is never the thing it grades (The_Level #6): deterministic
kernel layers grade freely; the model layer runs only under an INDEPENDENT
grader identity, and self-grading is REFUSED with the refusal quoted. The
grader verdicts -- it never reworks.

Nothing in this file names a real product, client, vertical or capability:
the fixtures are invented.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# Imported by full module path so the CI-M mutation gate (which selects
# tests by the module's dotted token) associates this file with every
# function in grader.py.
import app.factory.build.grader as grader
from app.factory.build.product_grader import grade_record
from tests.factory.grader_contract import COMPUTES, write_models

# A handle that computes from the contract grader_contract declares.
READS_INPUT = COMPUTES
CONSTANT = "def handle(payload):\n    return {'ok': True}\n"

IDS = ["intake_form", "invoice_total", "visit_log", "tenant_notice"]


def _workspace(tmp_path: Path, bodies: dict, extra: dict = None) -> Path:
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (actions / "__init__.py").write_text("")
    for cid, body in bodies.items():
        (actions / f"{cid}.py").write_text(body)
    write_models(tmp_path, list(bodies))
    for rel, text in (extra or {}).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def _bp(ids, ui_modules=()):
    return {
        "capabilities": [{"id": i, "description": f"record the {i} entry with owner and amount"} for i in ids],
        "ui_modules": list(ui_modules),
    }


def _passing_l2(blueprint):
    """A Layer-2 completion that PASSes every question it is sent, by id,
    quoting evidence -- read from the call, never hand-answered."""

    def complete(messages):
        questions = json.loads(messages[-1]["content"])["questions"]
        return json.dumps(
            [
                {"id": q["id"], "verdict": "PASS", "evidence": "the product delivers this (quoted)"}
                for q in questions
            ]
        )

    return complete


@pytest.fixture()
def independent_grader(monkeypatch):
    """An independent grader model config (provider/model differ from the
    writer's) plus a stubbed completion -- so Layer 2 runs without a network."""
    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "grader-key")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://api.graderco.example/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, "grader-model-1")
    monkeypatch.setattr(grader, "_grader_complete", _passing_l2(_bp(IDS)))


@pytest.fixture()
def green_ui(monkeypatch):
    """The UI layer runs and is green, without a real product or browser."""

    def fake_grade_ui(blueprint, root, gradroot, base_url):
        return grader._UiNotRun("unused"), False, ""

    class _GreenUi:
        @property
        def ran(self):
            return True

        @property
        def failing(self):
            return []

        @property
        def errors(self):
            return []

        @property
        def ok(self):
            return True

        def to_dict(self):
            return {"ok": True, "ran": True, "failing": [], "steps": []}

    monkeypatch.setattr(grader, "_grade_ui", lambda blueprint, root, gradroot, base_url: (_GreenUi(), True, ""))
    return fake_grade_ui


# --- derivation (no hardwiring) ---------------------------------------------


def test_derive_forbidden_takes_brief_shapes_not_prose():
    brief = (
        'The client "Acme Trading Co" needs 240 invoices and a "Nimbus" portal. '
        "Plain lowercase prose words are not fixture-shaped."
    )
    forbidden = grader.derive_forbidden({}, brief, "")
    assert "Acme Trading Co" in forbidden
    assert "Nimbus" in forbidden
    # FG-4: a bare number is the two-dataset check's, and a capitalised
    # word is not an identity -- neither is a brief literal.
    assert "240" not in forbidden
    assert "The" not in forbidden and "Plain" not in forbidden
    assert "prose" not in forbidden
    assert "lowercase" not in forbidden


def test_derive_forbidden_reads_structured_blueprint_values():
    bp = {"client": "Blue Harbor Estates", "vertical": "property"}
    forbidden = grader.derive_forbidden(bp, "some brief for blue harbor estates", "")
    assert "Blue Harbor Estates" in forbidden
    # The vertical is the product's own (Factory-assigned) identity.
    assert "property" not in forbidden
    # A client value the user never said is not brief data.
    assert "Blue Harbor Estates" not in grader.derive_forbidden(bp, "some brief", "")


@pytest.mark.parametrize("identity", ["north_wing_b", "acct_7731"])
def test_derive_forbidden_takes_identity_tokens_but_not_contract_or_factory_text(identity):
    brief = f"Bill everything to {identity} via the intake_form flow."
    bp = {"capabilities": [{"id": "intake_form", "input_schema": {"unit_ref": {}}}]}
    assert identity in grader.derive_forbidden(bp, brief, "")
    # A contract-required id or schema key is never a brief literal ...
    assert "intake_form" not in grader.derive_forbidden(bp, brief, "")
    assert "unit_ref" not in grader.derive_forbidden(bp, brief + " Key unit_ref.", "")
    # ... and one the Factory's own prompt text carries is vocabulary.
    assert identity not in grader.derive_forbidden(bp, brief, f"the Factory says {identity}")


def test_derive_forbidden_takes_document_references_and_file_shapes():
    brief = "See document:handover_notes.docx, document:Lease and the rates_sheet.xlsx or plan.pdf values."
    forbidden = grader.derive_forbidden({}, brief, "")
    assert "handover_notes.docx" in forbidden
    assert "handover_notes" in forbidden
    assert "rates_sheet.xlsx" in forbidden
    assert "Lease" in forbidden and "plan.pdf" in forbidden
    # A one-word stem is vocabulary: only the full document name counts.
    assert "plan" not in forbidden


def test_render_product_includes_app_and_frontend_only_bounded():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _workspace(root, {i: READS_INPUT for i in IDS[:2]}, extra={
            "frontend/src/app.tsx": "export const App = () => null;",
            ".venv/x.py": "SECRET = 'should never appear'",
        })
        rendered = grader.render_product(root)
        assert "app/actions/intake_form.py" in rendered
        assert "frontend/src/app.tsx" in rendered
        assert "should never appear" not in rendered


# --- independence (The_Level #6) ---------------------------------------------


@pytest.mark.parametrize("content", ['[{"id": "q1", "verdict": "PASS", "evidence": "e"}]', "[]"])
def test_grader_complete_returns_the_models_raw_content(monkeypatch, content):
    """Layer 2's completion posts to the dedicated grader endpoint and
    returns the model's raw content text."""
    import httpx

    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "grader-key")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://api.graderco.example/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, "grader-model-1")
    calls: list = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class _Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json=None, headers=None):
            calls.append((url, json, headers))
            return _Resp()

    monkeypatch.setattr(httpx, "Client", _Client)
    assert grader._grader_complete([{"role": "system", "content": "x"}]) == content
    assert calls


@pytest.mark.parametrize("missing_env", [grader.GRADER_LLM_KEY_ENV, grader.GRADER_LLM_BASE_URL_ENV])
def test_grader_complete_refuses_without_a_complete_config(monkeypatch, missing_env):
    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "grader-key")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://api.graderco.example/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, "grader-model-1")
    monkeypatch.delenv(missing_env)
    with pytest.raises(RuntimeError):
        grader._grader_complete([{"role": "system", "content": "x"}])


@pytest.mark.parametrize(
    "provider,model,version",
    [("DeepSeek", "deepseek-v3", ""), ("graderco", "g-1", "1.0")],
)
def test_identity_is_normalised_case_and_space_insensitive(provider, model, version):
    assert grader._norm_identity(provider.upper(), " " + model + " ", version) == grader._norm_identity(
        provider.lower(), model, version
    )


@pytest.mark.parametrize("host,expected", [("api.deepseek.com", "deepseek"), ("openrouter.ai", "openrouter")])
def test_provider_token_is_the_registrable_root(host, expected):
    assert grader._provider_token(host) == expected


def test_self_grading_is_refused_with_the_identities_quoted(monkeypatch):
    from app.factory import code_cli

    # The writer's own identity: the DeepSeek coder path, as production
    # configures it (DEEPSEEK_API_KEY selects the deepseek provider).
    monkeypatch.setenv(code_cli.DEEPSEEK_API_KEY_ENV, "writer-key")
    writer = grader.writer_identity()
    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "k")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://api.deepseek.com/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, code_cli.DEFAULT_DEEPSEEK_MODEL)
    refusal = grader.independence_refusal(writer, grader.grader_llm_config())
    assert refusal is not None
    assert grader._norm_identity("deepseek", code_cli.DEFAULT_DEEPSEEK_MODEL) in refusal


def test_an_unconfigured_grader_is_refused_not_fallbacken():
    refusal = grader.independence_refusal(grader.writer_identity(), None)
    assert refusal is not None
    assert "CEREBRUM_GRADER_LLM_*" in refusal


def test_an_independent_grader_is_not_refused(monkeypatch):
    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "k")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://api.othermodel.example/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, "other-model")
    assert grader.independence_refusal(grader.writer_identity(), grader.grader_llm_config()) is None


@pytest.mark.parametrize(
    "grader_model",
    ["deepseek-v3", "deepseek/deepseek-v3"],
)
def test_family_independence_refuses_a_different_host_same_vendor(monkeypatch, grader_model):
    """F5: OpenRouter serving a deepseek model refuses against a deepseek
    writer -- the vendor family matches even though the host/identity differ."""
    from app.factory import code_cli

    monkeypatch.setenv(code_cli.DEEPSEEK_API_KEY_ENV, "writer-key")
    writer = grader.writer_identity()
    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "k")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://openrouter.ai/api/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, grader_model)
    refusal = grader.independence_refusal(writer, grader.grader_llm_config())
    assert refusal is not None
    assert "deepseek" in refusal


@pytest.mark.parametrize(
    "grader_model",
    ["anthropic/claude-sonnet-4.5", "openai/gpt-4o"],
)
def test_family_independence_allows_a_different_vendor(monkeypatch, grader_model):
    """F5: OpenRouter serving a different vendor is independent of a deepseek
    writer."""
    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "k")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://openrouter.ai/api/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, grader_model)
    assert grader.independence_refusal(grader.writer_identity(), grader.grader_llm_config()) is None


@pytest.mark.parametrize(
    "model_id, expected",
    [
        ("deepseek-v3", "deepseek"),
        ("deepseek/deepseek-v3", "deepseek"),
        ("anthropic/claude-sonnet-4.5", "anthropic"),
        ("meta-llama/llama-3", "meta-llama"),
        ("  DeepSeek-Reasoner ", "deepseek"),
        ("", ""),
    ],
)
def test_model_family_is_structural(model_id, expected):
    """F5: the family token is derived from the model id, not a model list."""
    assert grader._model_family(model_id) == expected


# --- the smallest helpers (mutation-gate coverage) ---------------------------


@pytest.mark.parametrize("env_value,expected", [("0", False), ("1", True)])
def test_grader_enabled_reads_the_switch(monkeypatch, env_value, expected):
    monkeypatch.setenv(grader.DISABLE_ENV, env_value)
    assert grader.grader_enabled() is expected


class _ModelBlueprint:
    def __init__(self, payload):
        self.__dict__.update(payload)

    def model_dump(self):
        return dict(self.__dict__)


@pytest.mark.parametrize(
    "blueprint", [{"vertical": "alpha"}, _ModelBlueprint({"vertical": "beta"})]
)
def test_as_mapping_accepts_a_mapping_and_a_model_object(blueprint):
    expected = {"vertical": "alpha" if isinstance(blueprint, dict) else "beta"}
    assert grader._as_mapping(blueprint) == expected


@pytest.mark.parametrize(
    "blueprint",
    [
        {"client": "Alpha Co"},
        {"project": "Beta Wing", "vertical": "beta", "product_id": "p-1"},
        _ModelBlueprint({"client": "Gamma Co"}),
    ],
)
def test_structured_literals_reads_the_schema_fields(blueprint):
    if isinstance(blueprint, _ModelBlueprint):
        expected = {"Gamma Co"}
    else:
        # The product's own identity (vertical, product id) is not client data.
        expected = {v for k, v in blueprint.items() if k in ("client", "project")}
    assert grader._structured_literals(blueprint) == expected


@pytest.mark.parametrize("text", ["one, two. three;", 'a "quoted" b'])
def test_tokens_split_on_whitespace_and_strip_punctuation(text):
    tokens = grader._tokens(text)
    assert len(tokens) == 3
    assert all(t.strip(".,;:()[]{}\"'`") == t for t in tokens)


@pytest.mark.parametrize(
    "functions,ui",
    [
        ({"ok": True}, {"ok": True}),
        ({"ok": False}, {"ok": True}),
    ],
)
def test_layer2_layer1_view_reports_the_combined_ok(functions, ui):
    view = grader._layer2_layer1_view(functions, ui)
    assert view["ok"] is (functions["ok"] is True and ui["ok"] is True)


@pytest.mark.parametrize("root_name", ["build-one", "build-two"])
def test_boot_env_imports_only_the_product_workspace(tmp_path, monkeypatch, root_name):
    root = tmp_path / root_name
    root.mkdir()
    monkeypatch.setenv("PYTHONPATH", "elsewhere")
    env = grader.boot_env(root, tmp_path / "scratch", 8123)
    assert env["PYTHONPATH"] == str(root)
    assert str(tmp_path / "scratch") == env["STORAGE_PATH"]


@pytest.mark.parametrize("attempts", [2, 3])
def test_free_port_returns_distinct_in_range_ports(attempts):
    ports = [grader._free_port() for _ in range(attempts)]
    assert all(0 < p < 65536 for p in ports)
    assert len(set(ports)) == attempts


@pytest.mark.parametrize("reason", ["", "the UI grader is unavailable"])
def test_ui_not_run_shape_is_never_a_pass(reason):
    ui = grader._UiNotRun(reason)
    assert ui.ran is False
    assert ui.ok is False
    assert ui.errors == ([reason] if reason else [])
    # like UiGrade, the reason flows into failing -- and never into a pass
    assert ui.failing == ([reason] if reason else [])
    assert ui.to_dict() == {"ok": False, "ran": False, "failing": ([reason] if reason else []), "steps": []}


@pytest.mark.parametrize("workspace_kind", ["no_app_main", "missing_dir"])
def test_boot_product_failure_paths_are_named_not_raised(tmp_path, workspace_kind):
    if workspace_kind == "no_app_main":
        root = tmp_path / "w"
        (root / "app").mkdir(parents=True)
        (root / "app" / "__init__.py").write_text("")
    else:
        root = tmp_path / "does-not-exist"
    boot, reason = grader.boot_product(root, ready_timeout_s=30)
    assert boot is None
    if workspace_kind == "no_app_main":
        # The product's own import error ends the boot: its exit code and
        # its output tail are the finding, not a timeout.
        assert reason.startswith("product boot exited with code ")
        assert "app.main" in reason.split("output tail:", 1)[1]
    else:
        assert reason.startswith("product boot did not start: ")


# --- the grader itself --------------------------------------------------------


@pytest.mark.parametrize("ui_modules", [["portal"], ["map", "board"]])
def test_grade_ui_without_a_booted_product_is_recorded_not_run(tmp_path, ui_modules):
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    bp = _bp(IDS, ui_modules=ui_modules)
    ui, ran, not_run = grader._grade_ui(bp, ws, ws.parent / "g", None)
    assert ran is False
    assert not_run
    assert ui.ok is False


@pytest.mark.parametrize("boot_url", ["http://127.0.0.1:9", "http://127.0.0.1:10"])
def test_grade_ui_uses_the_booted_products_url(monkeypatch, tmp_path, boot_url):
    """When the product boots, the UI layer grades the BOOTED url -- the boot
    block's effect is observable, so dropping it fails this test."""
    import app.factory.build.product_grader as pg

    recorded: dict = {}

    class _Boot:
        def stop(self):
            recorded["stopped"] = True

    monkeypatch.setattr(grader, "boot_product", lambda root: (_Boot(), boot_url))

    class _Ui:
        @property
        def ran(self):
            return True

        @property
        def failing(self):
            return []

        @property
        def errors(self):
            return []

        @property
        def ok(self):
            return True

        def to_dict(self):
            return {"ok": True, "ran": True, "failing": [], "steps": []}

    class _UiGrader:
        @staticmethod
        def grade_ui(blueprint, workdir, *, base_url):
            recorded["base_url"] = base_url
            return _Ui()

        @staticmethod
        def UiGrade(steps, errors):
            return grader._UiNotRun(errors[0] if errors else "")

    monkeypatch.setattr(pg, "ui_grader", _UiGrader)
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    bp = _bp(IDS)
    ui, ran, not_run = grader._grade_ui(bp, ws, ws.parent / "g", None)
    assert ran is True
    assert recorded["base_url"] == boot_url
    assert recorded["stopped"] is True
    assert ui.ok is True


@pytest.mark.parametrize("brief_summary", ["a generic brief about entries and amounts", "another brief about owners"])
def test_certified_when_every_layer_runs_and_is_green(tmp_path, independent_grader, green_ui, brief_summary):
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    bp = _bp(IDS)
    verdict = grader.run_grader(
        plan_capabilities=IDS,
        blueprint=bp,
        workspace=ws,
        product_id="invented-product",
        compiled_brief={"summary": brief_summary},
    )
    assert verdict.tier == grader.CERTIFIED
    assert verdict.ok
    assert verdict.ungraded == ()
    assert (ws / "grade.json").is_file()
    assert (ws / "GRADE.md").is_file()
    assert grade_record.certification_refusal(ws) is None


@pytest.mark.parametrize("product_id", ["invented-product", "invented-product-two"])
def test_self_grading_verdict_is_not_certified_with_the_refusal_quoted(tmp_path, green_ui, product_id):
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    bp = _bp(IDS)
    verdict = grader.run_grader(plan_capabilities=IDS, blueprint=bp, workspace=ws, product_id=product_id)
    assert verdict.tier == grader.NOT_CERTIFIED
    assert not verdict.ok
    assert any("may not grade the writer's tree" in item for item in verdict.items)
    assert any(item in verdict.ungraded for item in verdict.items if "not graded" in item)
    # the record still lands; N3 refuses an uncertified build
    assert (ws / "grade.json").is_file()
    assert grade_record.certification_refusal(ws) is not None


#: A handle for a capability with no declared field: it answers TESTER's
#: one input ({}) ok, or refuses it.
ONE_INPUT_OK = "def handle(payload):\n    return {'ok': True, 'count': len(payload) + 1}\n"
ONE_INPUT_REFUSES = "def handle(payload):\n    return {'ok': False, 'error': 'nothing declared'}\n"


@pytest.mark.parametrize("gap,body,certified", [
    ("invoice_total", ONE_INPUT_OK, True),
    ("visit_log", ONE_INPUT_OK, True),
    ("invoice_total", ONE_INPUT_REFUSES, False),
    ("visit_log", ONE_INPUT_REFUSES, False),
])
def test_capability_without_a_declared_contract_is_judged_by_testers_one_input_rule(
    tmp_path, independent_grader, green_ui, gap, body, certified
):
    ws = _workspace(tmp_path, {i: (body if i == gap else READS_INPUT) for i in IDS})
    write_models(ws, IDS, declared=[i for i in IDS if i != gap])
    verdict = grader.run_grader(plan_capabilities=IDS, blueprint=_bp(IDS), workspace=ws, product_id="invented-product")
    assert verdict.tier == (grader.CERTIFIED if certified else grader.NOT_CERTIFIED)
    assert verdict.ungraded == ()
    assert (f"function grade FAIL: {gap}" in verdict.items) is (not certified)
    assert not any(r["class"] in ("ungraded", "stub_handler") for r in verdict.reasons)
    record = json.loads((ws / "grade.json").read_text("utf-8"))
    (fn,) = [f for f in record["layer1"]["functions"]["functions"] if f["capability_id"] == gap]
    assert fn["detail"] == ("one_input" if certified else f"{gap} rejected a payload built from its own schema: nothing declared")


@pytest.mark.parametrize("bad_cid", ["invoice_total", "visit_log"])
def test_red_function_grade_verdict_quotes_the_failing_capability(tmp_path, independent_grader, green_ui, bad_cid):
    ws = _workspace(tmp_path, {i: (CONSTANT if i == bad_cid else READS_INPUT) for i in IDS})
    bp = _bp(IDS)
    verdict = grader.run_grader(plan_capabilities=IDS, blueprint=bp, workspace=ws, product_id="invented-product")
    assert verdict.tier == grader.NOT_CERTIFIED
    assert not verdict.ok
    assert f"function grade FAIL: {bad_cid}" in verdict.items
    # option (b): the verdict carries the failing capability's reason class
    assert {"capability": bad_cid, "class": "stub_handler", "layer": "function"} in verdict.reasons


@pytest.mark.parametrize("crash_message", ["grader exploded", "grader blew up"])
def test_grader_crash_is_an_error_verdict_and_still_writes_a_red_record(tmp_path, green_ui, monkeypatch, crash_message):
    from app.factory.build.product_grader import function_grader as fg

    def boom(*args, **kwargs):
        raise RuntimeError(crash_message)

    monkeypatch.setattr(fg, "grade_functions", boom)
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    verdict = grader.run_grader(plan_capabilities=IDS, blueprint=_bp(IDS), workspace=ws, product_id="invented-product")
    assert verdict.tier == grader.ERROR
    assert not verdict.ok
    assert any(crash_message in item for item in verdict.items)
    assert (ws / "grade.json").is_file()
    assert grade_record.certification_refusal(ws) is not None


@pytest.mark.parametrize("bad_answer", ["not json", "{}"])
def test_layer2_that_never_answers_is_an_error_verdict(tmp_path, independent_grader, green_ui, monkeypatch, bad_answer):
    monkeypatch.setattr(grader, "_grader_complete", lambda messages: bad_answer)
    ws = _workspace(tmp_path, {i: READS_INPUT for i in IDS})
    verdict = grader.run_grader(plan_capabilities=IDS, blueprint=_bp(IDS), workspace=ws, product_id="invented-product")
    assert verdict.tier == grader.ERROR
    assert not verdict.ok
    assert any("Layer 2 did not run" in item for item in verdict.items)


@pytest.mark.parametrize("literal", ["Acme Trading Co", "Nimbus"])
def test_hardwired_brief_literal_is_found_and_quoted_in_the_verdict(tmp_path, independent_grader, green_ui, literal):
    brief = f'The client "{literal}" runs 240 invoices through the portal.'
    ws = _workspace(
        tmp_path,
        {i: READS_INPUT for i in IDS},
        extra={"app/constants.py": f'CLIENT = "{literal}"\n'},
    )
    bp = _bp(IDS)
    verdict = grader.run_grader(
        plan_capabilities=IDS,
        blueprint=bp,
        workspace=ws,
        product_id="invented-product",
        compiled_brief={"summary": brief},
        prompt_text="",
    )
    assert verdict.tier == grader.NOT_CERTIFIED
    assert any(literal in item for item in verdict.items)


@pytest.mark.parametrize("token", ["Nimbus", "Zephyr"])
def test_prompt_leak_is_found_when_the_literal_sits_in_both(tmp_path, independent_grader, green_ui, token):
    brief = f"The project is code-named {token} for its portal."
    # The writer was given the brief (it is in the dispatched prompt) and
    # copied one of its clauses into a value.
    prompt = f"Build the portal.\n{brief}"
    ws = _workspace(
        tmp_path,
        {i: READS_INPUT for i in IDS},
        extra={"app/settings.py": f'PROJECT = "The project is code-named {token} for its portal"\n'},
    )
    bp = _bp(IDS)
    verdict = grader.run_grader(
        plan_capabilities=IDS,
        blueprint=bp,
        workspace=ws,
        product_id="invented-product",
        compiled_brief={"summary": brief},
        prompt_text=prompt,
    )
    assert verdict.tier == grader.NOT_CERTIFIED
    assert any("remove copied brief from app/settings.py" in item for item in verdict.items)


# --- the runner wiring -------------------------------------------------------


@pytest.fixture()
def smoke_blueprint():
    from app.factory.blueprint import load_blueprint

    return load_blueprint(
        Path(__file__).resolve().parents[3] / "blueprints" / "examples" / "runner_smoke.yaml"
    )


def _red_product(workspace: Path, constant: tuple = ("analytics_surface", "dashboard_surface")) -> None:
    actions = workspace / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (workspace / "app" / "__init__.py").write_text("")
    (actions / "__init__.py").write_text("")
    for cid in ("analytics_surface", "dashboard_surface"):
        body = CONSTANT if cid in constant else READS_INPUT
        (actions / f"{cid}.py").write_text(body)


def _fake_pass(ctx):
    """A role that passes instantly; as a writer it records what it was told
    (FG-4), as every real writer path does."""
    from app.factory.build.roles import RoleResult
    from app.factory.build.writer_prompt import record_writer_dispatch

    record_writer_dispatch(ctx.state, supplied="", brief="invented brief", prompt="invented prompt")
    return RoleResult(ok=True, detail="fake pass")


def _fake_pass_harness(monkeypatch):
    """Every phase role passes instantly and every phase gate is green, so
    the run reaches the grader step and nothing else decides."""
    from app.factory.build import runner as runner_mod
    from app.factory.build.gates import GateResult
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    roles = {role: _fake_pass for role in ROLE_IMPLEMENTATIONS}
    monkeypatch.setattr(runner_mod, "gate_for", lambda role: (lambda ctx: GateResult(ok=True, gate="fake_gate")))
    return roles


@pytest.mark.parametrize("capability", ["zorblat_check", "plinket_check"])
def test_runner_hands_back_to_the_writer_for_two_rounds_then_stops(smoke_blueprint, tmp_path, monkeypatch, capability):
    """A NOT_CERTIFIED verdict hands the quoted items back to the WRITER in
    the build -- the grader's own two rounds; a third failure stops the run.
    Never the TESTER's machinery."""
    from app.factory.build import grader as grader_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import EventKind
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)

    def not_certified(**kwargs):
        return grader_mod.GraderVerdict(
            tier=grader_mod.NOT_CERTIFIED,
            items=(f"function grade FAIL: {capability}",),
            reasons=({"capability": capability, "class": "stub_handler", "layer": "function"},),
            writer_items=(f"function grade FAIL: {capability}",),
        )

    monkeypatch.setattr(grader_mod, "run_grader", not_certified)
    captured: list = []
    roles = _fake_pass_harness(monkeypatch)

    def recording_writer(ctx):
        captured.append(tuple(ctx.work_list))
        return _fake_pass(ctx)

    roles[BuildRole.WRITER] = recording_writer
    runner = RoleRunner(smoke_blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()
    assert not outcome.ok
    assert outcome.detail.startswith("GRADER: NOT_CERTIFIED after 1 round(s)")
    assert f"function grade FAIL: {capability}" in outcome.detail
    # the run started its ledger: the first event is RUN_STARTED (pins
    # run()'s first if for CI-M -- start_run writes exactly that event)
    events = list(runner.ledger.events())
    assert events and events[0].kind is EventKind.RUN_STARTED
    reworks = [e for e in runner.ledger.events() if e.kind is EventKind.REWORK]
    assert len(reworks) == 1
    assert all((r.payload or {}).get("gate") == grader_mod.GRADER_GATE for r in reworks)
    assert all((r.payload or {}).get("source") == "GRADER" for r in reworks)
    # the writer received the quoted items as its work list
    assert any(f"function grade FAIL: {capability}" in item for wl in captured for item in wl)
    # option (b): every grader NOTE carries the verdict's reason classes as they are
    notes = [
        e for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("grader_tier") == grader_mod.NOT_CERTIFIED
    ]
    assert notes
    assert all(
        (n.payload or {}).get("grader_reasons")
        == [{"capability": capability, "class": "stub_handler", "layer": "function"}]
        for n in notes
    )


@pytest.mark.parametrize("owed_file", ["zorblat/stamped_mod.py", "plinket/vendored_mod.py"])
def test_runner_spends_no_writer_round_on_rows_the_factory_owes(smoke_blueprint, tmp_path, monkeypatch, owed_file):
    """FG-1: a NOT_CERTIFIED verdict whose rows are all Factory-owed (or
    run-level) ends the run naming the Factory: no REWORK, the writer is
    not dispatched again, and the tier is never CERTIFIED."""
    from app.factory.build import grader as grader_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.ledger import EventKind
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    owed = f"FACTORY owes baked literals in {owed_file} (in a file the Factory rendered): ['Qux']"

    def factory_owed(**kwargs):
        return grader_mod.GraderVerdict(
            tier=grader_mod.NOT_CERTIFIED, items=(owed,), factory_owed=(owed,)
        )

    monkeypatch.setattr(grader_mod, "run_grader", factory_owed)
    captured: list = []
    roles = _fake_pass_harness(monkeypatch)

    def recording_writer(ctx):
        captured.append(tuple(ctx.work_list))
        return _fake_pass(ctx)

    roles[BuildRole.WRITER] = recording_writer
    runner = RoleRunner(smoke_blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()
    assert not outcome.ok
    assert outcome.detail.startswith("GRADER: NOT_CERTIFIED -- FACTORY_FAULT (no writer round)")
    assert owed_file in outcome.detail
    assert [e for e in runner.ledger.events() if e.kind is EventKind.REWORK] == []
    assert len(captured) == 1  # the writer's first pass only; nothing handed back
    assert not any(owed_file in item for wl in captured for item in wl)
    notes = [e for e in runner.ledger.events() if (e.payload or {}).get("writer_dispatched") is False]
    assert notes and all((n.payload or {}).get("owner") == "FACTORY" for n in notes)
    assert all((n.payload or {}).get("factory_owed") == [owed] for n in notes)


def test_runner_proceeds_when_the_verdict_is_certified(smoke_blueprint, tmp_path, monkeypatch):
    """A CERTIFIED verdict lets the run continue past the writer -- the
    tester runs, and no grader verdict ends the run."""
    from app.factory.build import grader as grader_mod
    from app.factory.build.ledger import EventKind
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    monkeypatch.setattr(
        grader_mod, "run_grader", lambda **kwargs: grader_mod.GraderVerdict(tier=grader_mod.CERTIFIED)
    )
    roles = _fake_pass_harness(monkeypatch)
    runner = RoleRunner(smoke_blueprint, tmp_path / "build", roles=roles)
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    # the run started its ledger: the first event is RUN_STARTED (pins
    # run()'s first if for CI-M -- start_run writes exactly that event)
    events = list(runner.ledger.events())
    assert events and events[0].kind is EventKind.RUN_STARTED
    graders = [
        e for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("grader_tier") == grader_mod.CERTIFIED
    ]
    assert graders


def test_runner_grader_is_none_when_the_switch_is_off(smoke_blueprint, tmp_path, monkeypatch):
    from app.factory.build.runner import RoleRunner

    workspace = tmp_path / "w"
    workspace.mkdir()
    _red_product(workspace)
    runner = RoleRunner(smoke_blueprint, workspace)
    monkeypatch.setenv(grader.DISABLE_ENV, "0")
    assert runner._writer_grader() is None
