"""THE VERDICT LINE MUST NOT OVERCLAIM (owner's ruling 1, 2026-09-01).

FINDING 3: the factory reported "all phase gates passed" while the code-phase
gate runs ``pytest -m "not pilot"`` -- and ``@pytest.mark.pilot`` is the
marker on the only tests that exercise a business action. The sentence was
literally "everything except the tests that check the product works passed".
residential-lettings (sess_6400b6c273414352) built, shipped a 216-file zip,
booted, served seventeen routes, and could not persist one record, with every
gate green.

The fix is a third gate and an honest sentence. Both are tested here:

* PRODUCT boots the product, runs the pilot-marked suite against it, and
  round-trips one record per capability (R1e: POST creates, GET returns it).
* The verdict names THREE gates, each with its scope, and a gate that did not
  run says NOT RUN rather than being folded into a pass.

Each test names the mutation it kills.
"""

import ast
from dataclasses import replace

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext
from app.factory.build.product_gate import (
    GATE_SCOPES,
    ROUND_TRIP_PROBE,
    gate_product,
    gate_round_trip,
)


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _ctx(tmp_path, proc, *, models=True, marker="pilot"):
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    if models:
        (tmp_path / "app" / "models.py").write_text("MODELS = {}\n", encoding="utf-8")
    (tmp_path / "tests").mkdir(exist_ok=True)
    (tmp_path / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    calls = []

    def runner(argv, *, cwd=None, timeout=None):
        calls.append(argv)
        return proc(argv) if callable(proc) else proc

    ctx = GateContext(
        workspace=tmp_path,
        role=BuildRole.TESTER,
        runner=runner,
        suite_marker=marker,
        cycle="pilot",
    )
    return ctx, calls


# -- the round-trip half ---------------------------------------------------


def test_a_product_that_does_not_boot_is_named_as_that(tmp_path):
    """Mutation killed: reporting an import failure as a round-trip failure.
    A build-environment fault sent back as "your handlers are wrong" cost
    three rework rounds once already."""
    proc = _Proc(1, stderr="GATE-FINDING: workspace does not import: ImportError: no app\n")
    ctx, _ = _ctx(tmp_path, proc)
    res = gate_round_trip(ctx)
    assert not res.ok
    assert "did not boot" in res.detail
    assert "ImportError" in res.findings[0]


def test_a_capability_that_forgets_its_record_fails_the_gate(tmp_path):
    """The residential-lettings shape, at the gate that should have caught
    it. Mutation killed: treating GATE-MISS lines as advisory."""
    proc = _Proc(1, stdout=(
        "GATE-MISS: unit_registry: POST reported success and unit holds 0 row(s) "
        "-- the product did not remember what it was told\n"
        "PRODUCT-SUMMARY: 3 round-tripped, 1 failed, 0 unjudged, 4 capabilities\n"
    ))
    ctx, _ = _ctx(tmp_path, proc)
    res = gate_round_trip(ctx)
    assert not res.ok
    assert "1 capability(ies) did not remember" in res.detail
    assert "unit_registry" in res.findings[0]
    assert res.payload["summary"].startswith("3 round-tripped")


def test_a_gate_that_judged_nothing_is_not_a_pass(tmp_path):
    """The same defect class as the excluded pilot tests, one level up: a
    check that ran and decided nothing must not report green.

    Mutation killed: returning ok on a clean exit without looking at how
    many capabilities were actually judged.
    """
    proc = _Proc(0, stdout=(
        "GATE-UNJUDGED: report_builder (no readable entity 'report_builder')\n"
        "PRODUCT-SUMMARY: 0 round-tripped, 0 failed, 1 unjudged, 1 capabilities\n"
    ))
    ctx, _ = _ctx(tmp_path, proc)
    res = gate_round_trip(ctx)
    assert not res.ok
    assert "decided nothing" in res.detail
    assert "report_builder" in res.findings[0]


def test_a_nonzero_exit_with_no_finding_still_fails(tmp_path):
    """Mutation killed: keying only on marked lines, so a probe that dies
    before printing anything reads as a pass."""
    proc = _Proc(2, stderr="Segmentation fault\n")
    ctx, _ = _ctx(tmp_path, proc)
    res = gate_round_trip(ctx)
    assert not res.ok
    assert "exited 2" in res.detail
    assert res.findings


def test_a_clean_round_trip_passes_and_reports_the_count(tmp_path):
    proc = _Proc(0, stdout="PRODUCT-SUMMARY: 4 round-tripped, 0 failed, 0 unjudged, 4 capabilities\n")
    ctx, _ = _ctx(tmp_path, proc)
    res = gate_round_trip(ctx)
    assert res.ok
    assert "4 round-tripped" in res.detail


def test_a_workspace_with_no_models_has_no_product_to_boot(tmp_path):
    ctx, _ = _ctx(tmp_path, _Proc(0), models=False)
    res = gate_round_trip(ctx)
    assert not res.ok
    assert "no product to boot" in res.detail


# -- the two halves, composed ----------------------------------------------


def _suite_then_trip(suite_rc, trip_stdout, trip_rc=0):
    def runner(argv):
        if "pytest" in argv:
            return _Proc(suite_rc, stdout="FAILED tests/test_a.py::test_a\n1 failed\n"
                         if suite_rc else "4 passed\n")
        return _Proc(trip_rc, stdout=trip_stdout)
    return runner


def test_a_red_pilot_suite_names_that_half_and_stops(tmp_path):
    """Mutation killed: running the round-trip anyway and reporting only its
    verdict, which hides which half failed."""
    ctx, calls = _ctx(tmp_path, _suite_then_trip(1, ""))
    res = gate_product(ctx)
    assert not res.ok
    assert res.detail.startswith("PRODUCT (pilot-marked suite):")
    assert res.payload["half"] == "pilot_suite"
    assert len(calls) == 1, "the round-trip must not run after a red suite"


def test_a_red_pilot_suite_names_the_assertion_class_in_detail(tmp_path):
    """sess_5dfb4a3: Floor showed only 'suite is red'. Findings had the class."""

    def runner(argv):
        return _Proc(
            1,
            stdout=(
                "FAILED tests/test_domain_acceptance.py::"
                "test_ten_business_outcomes_are_performed_through_the_kernel\n"
                "E   AssertionError: {'ok': False, 'failed': ['create_persists']}\n"
                "1 failed\n"
            ),
        )

    ctx, _ = _ctx(tmp_path, runner)
    res = gate_product(ctx)
    assert not res.ok
    assert "domain acceptance: create_persists" in res.detail
    assert res.payload.get("assertion_classes") == [
        "domain acceptance: create_persists"
    ]


def test_a_red_round_trip_names_that_half(tmp_path):
    ctx, _ = _ctx(tmp_path, _suite_then_trip(
        0, "GATE-MISS: unit_registry: POST reported success and unit holds 0 row(s)\n"
           "PRODUCT-SUMMARY: 0 round-tripped, 1 failed, 0 unjudged, 1 capabilities\n", 1))
    res = gate_product(ctx)
    assert not res.ok
    assert res.detail.startswith("PRODUCT (one-record round-trip):")
    assert res.payload["half"] == "round_trip"


def test_both_halves_green_passes_and_says_so(tmp_path):
    ctx, calls = _ctx(tmp_path, _suite_then_trip(
        0, "PRODUCT-SUMMARY: 4 round-tripped, 0 failed, 0 unjudged, 4 capabilities\n"))
    res = gate_product(ctx)
    assert res.ok
    assert "4 round-tripped" in res.detail
    assert len(calls) == 2


def test_the_pilot_suite_half_runs_the_pilot_marker(tmp_path):
    """Mutation killed: running the code-phase marker here, which would make
    PRODUCT a second copy of CODE and leave the business-action tests
    excluded exactly as before."""
    ctx, calls = _ctx(tmp_path, _suite_then_trip(
        0, "PRODUCT-SUMMARY: 1 round-tripped, 0 failed, 0 unjudged, 1 capabilities\n"),
        marker="not pilot")
    gate_product(ctx)
    argv = calls[0]
    # ``python -m pytest`` puts a -m first; the marker flag is the LAST one.
    last = len(argv) - 1 - argv[::-1].index("-m")
    assert argv[last + 1] == "pilot", argv


# -- the probe itself ------------------------------------------------------


def _lift(names):
    """Exec named defs out of the shipped probe source, so what is tested is
    what ships rather than a copy of it."""
    tree = ast.parse(ROUND_TRIP_PROBE)
    picked = [
        n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in names)
        or (isinstance(n, ast.Assign)
            and any(getattr(t, "id", None) in names for t in n.targets))
    ]
    assert len(picked) == len(names), sorted(names)
    ns: dict = {}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<probe>", "exec"), ns)
    return ns


def test_the_probe_isolates_storage_path_like_writer_behaviour():
    """Live Veterinary Care Platform: PRODUCT used leftover ./data stamped
    at 0001_baseline, upgrade_head was a no-op, POST raised no such table.
    WRITER already isolates STORAGE_PATH; PRODUCT must too.
    """
    assert 'tempfile.mkdtemp(prefix="product-gate-")' in ROUND_TRIP_PROBE
    assert 'os.environ["STORAGE_PATH"]' in ROUND_TRIP_PROBE
    tree = ast.parse(ROUND_TRIP_PROBE)
    assigns_storage = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Attribute)
                and target.value.attr == "environ"
            ):
                assigns_storage = True
    assert assigns_storage


def test_the_probe_enters_the_client_context_so_the_lifespan_runs():
    """A bare ``TestClient(app)`` skips lifespan: no migrations, and since
    R1c no platform preconditions either. Every capability would then fail
    on a schema-less database for a reason that has nothing to do with the
    product.

    Mutation killed: ``client = TestClient(app)`` without ``__enter__``.
    """
    tree = ast.parse(ROUND_TRIP_PROBE)
    entered = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "__enter__"
        for n in ast.walk(tree)
    )
    assert entered, "the probe must enter the TestClient context manager"


_ONE_CONVENTION_MODELS = '''
from dataclasses import dataclass


@dataclass
class Zorblat:
    name: str = ""
    count: int = 3
    active: bool = False
    contact_email: str = ""
    status: str = "draft"
    scheduled_time: str = ""
    duration_minutes: int = 1
    channel: str = "email"
    FIELDS = ["name", "count", "active", "contact_email", "status",
              "scheduled_time", "duration_minutes", "channel"]
    # Every non-generic sample comes from a DECLARATION (vocabulary, bound,
    # format) -- never from the field's name.
    CONSTRAINTS = {
        "status": {"allowed_values": ["draft", "live"]},
        "count": {"min": 3},
        "scheduled_time": {"format": "time"},
        "contact_email": {"format": "email"},
        "channel": {"allowed_values": ["email", "sms"]},
    }


MODELS = {"zorblat": Zorblat}
'''


def test_the_probe_payload_matches_the_writer_probe_s(tmp_path):
    """Two probes, one convention -- by construction now: the WRITER probe and
    the PRODUCT round-trip probe both render the ONE payload source
    (payload_helpers.render_probe_payload) over TESTER's base sample, so
    WRITER, PRODUCT and TESTER judge the same payload for the same product.

    Mutations killed: a probe carrying a sampler of its own (the defect that
    stopped the writer gate on payloads TESTER accepted, live 9de69276); a
    probe rendering a different sample base than TESTER's.
    """
    import importlib.util

    import app.factory.build.writer_behaviour as wb
    from app.factory.build.payload_helpers import (
        PROBE_PAYLOAD_SLOT,
        base_samples,
        render_probe_payload,
    )
    from app.factory.build.product_gate import render_round_trip_probe

    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text(_ONE_CONVENTION_MODELS, encoding="utf-8")
    tester = base_samples(tmp_path)  # TESTER's base: its sampler over the live models

    payload_src = render_probe_payload(PROBE_PAYLOAD_SLOT)
    for probe in (wb._render_probe(), render_round_trip_probe()):
        assert payload_src in probe
        names = {n.name for n in ast.parse(probe).body if isinstance(n, ast.FunctionDef)}
        assert not {"_ann", "_value", "_payload"} & names, names
        assert "_payload_for" in names

    # What the probes compute when they RUN, over the same live models, is
    # TESTER's base exactly.
    ns: dict = {}
    exec(compile(payload_src, "<probe-payload>", "exec"), ns)
    spec = importlib.util.spec_from_file_location("zorblat_models", tmp_path / "app" / "models.py")
    models = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(models)
    probe_base = {
        cap: ns["_sample_payload"](s) for cap, s in ns["_declared_specs"](models.MODELS).items()
    }
    assert probe_base == tester and tester

    payload = probe_base["zorblat"]
    assert payload["scheduled_time"] == "10:00:00"
    assert payload["scheduled_time"] != "sample"
    assert payload["duration_minutes"] == 1
    assert payload["count"] == 3  # declared min
    assert payload["channel"] == "email"  # allowed_values[0]
    assert payload["contact_email"] == "guest@example.com"  # format email
    assert payload["status"] == "draft"  # allowed_values[0]
    # An undeclared field gets its type's neutral value, whatever it is called.
    assert payload["name"] == "sample"


def test_a_returned_record_counts_only_when_it_carries_a_supplied_value():
    ns = _lift({"_record_matches"})
    match = ns["_record_matches"]
    body = {"name": "sample", "count": 3}
    assert match({"id": 1, "name": "sample"}, body)
    assert match({"id": 1, "count": "3"}, body), "string/int forms are the same value"
    assert not match({"id": 1, "name": "something else"}, body)
    assert not match({"id": 1}, body), "an id the product invented is not the record"
    assert not match("not a dict", body)


@pytest.mark.parametrize("payload,want", [
    ([{"a": 1}], [{"a": 1}]),
    ({"items": [{"a": 1}]}, [{"a": 1}]),
    ({"records": [{"a": 1}]}, [{"a": 1}]),
    ({"results": [{"a": 1}]}, [{"a": 1}]),
    ({"data": [{"a": 1}]}, [{"a": 1}]),
    ({"rows": [{"a": 1}]}, [{"a": 1}]),
    ({"count": 1}, []),
    ("nonsense", []),
])
def test_the_list_route_is_read_in_whatever_shape_it_answers(payload, want):
    """Generated list routes answer in several shapes. Reading only one makes
    the GET half silently inert for the others."""
    assert _lift({"_listed"})["_listed"](payload) == want


# -- the verdict line ------------------------------------------------------


def test_every_gate_name_carries_a_scope_sentence():
    """A gate name with no scope is what let "all phase gates passed" mean
    nothing. Mutation killed: emptying a scope string."""
    assert set(GATE_SCOPES) == {"CODE", "PRODUCT", "STORE"}
    for name, scope in GATE_SCOPES.items():
        assert len(scope) > 30, name
    assert 'pytest -m "not pilot"' in GATE_SCOPES["CODE"]
    assert "round-trip" in GATE_SCOPES["PRODUCT"]
    assert "pilot-marked" in GATE_SCOPES["PRODUCT"]
    assert "restart" in GATE_SCOPES["STORE"]
    assert "acceptance" in GATE_SCOPES["STORE"]


def test_the_runner_never_claims_all_phase_gates_passed_again():
    """Mutation killed: restoring the old sentence. It is asserted against
    the shipped source because the string is the claim."""
    import app.factory.build.runner as runner_mod

    source = open(runner_mod.__file__, encoding="utf-8").read()
    body = "\n".join(
        ln for ln in source.splitlines() if not ln.lstrip().startswith("#")
    )
    assert '"all phase gates passed"' not in body


def test_a_code_cycle_success_says_the_product_gate_did_not_run():
    """THE FINDING, as a test. A code-phase pass must be unable to read as a
    product pass.

    Mutation killed: reporting the code cycle as a plain SUCCESS with no
    mention of the gates that never ran.
    """
    import app.factory.build.runner as runner_mod

    source = open(runner_mod.__file__, encoding="utf-8").read()
    assert 'PRODUCT NOT RUN — %s' in source
    assert 'STORE NOT RUN — %s' in source
    assert 'if self.cycle != "pilot":' in source


# -- the phase gate --------------------------------------------------------


def test_the_code_cycle_tester_gate_is_unchanged_and_still_refuses(tmp_path):
    """The ruling: "Code-phase gate stays as is." A red code suite is still
    a refusal, and the round-trip must NOT run -- booting the product is not
    what a 20-30 minute coder pass is judged on.

    Mutation killed: routing the code cycle through gate_product, which
    would make every code-phase build boot the product and fail on work the
    coder was never asked to do.
    """
    from app.factory.build.gates import gate_tester_contract

    ctx, calls = _ctx(tmp_path, _Proc(1, stdout="FAILED tests/test_a.py::test_a\n1 failed\n"),
                      marker="not pilot")
    ctx = replace(ctx, cycle="code")
    res = gate_tester_contract(ctx)
    assert not res.ok
    assert res.gate == "suite_green"
    assert len(calls) == 1, "the round-trip probe must not run on the code cycle"
    argv = calls[0]
    last = len(argv) - 1 - argv[::-1].index("-m")
    assert argv[last + 1] == "not pilot", argv


def test_a_green_code_cycle_does_not_boot_the_product_either(tmp_path):
    from app.factory.build.gates import gate_tester_contract

    ctx, calls = _ctx(tmp_path, _Proc(0, stdout="12 passed\n"), marker="not pilot")
    ctx = replace(ctx, cycle="code")
    res = gate_tester_contract(ctx)
    assert res.ok
    assert res.gate == "suite_green"
    assert len(calls) == 1


def test_the_pilot_cycle_tester_gate_is_the_product_gate_and_refuses(tmp_path):
    """FINDING 3 closed at the phase where it was open: on the pilot cycle
    the TESTER gate boots the product and refuses one that cannot remember a
    record, however green its suite.

    Mutation killed: leaving BuildRole.TESTER wired to gate_suite_green, so
    the round-trip exists but nothing ever calls it.
    """
    from app.factory.build.gates import gate_tester_contract

    ctx, calls = _ctx(tmp_path, _suite_then_trip(
        0,
        "GATE-MISS: unit_registry: POST reported success and unit holds 0 row(s)\n"
        "PRODUCT-SUMMARY: 0 round-tripped, 1 failed, 0 unjudged, 1 capabilities\n",
        1,
    ))
    ctx = replace(ctx, cycle="pilot")
    res = gate_tester_contract(ctx)
    assert not res.ok
    assert res.gate == "product_green"
    assert "one-record round-trip" in res.detail
    assert len(calls) == 2, "the pilot cycle runs the suite AND the round-trip"


def test_the_pilot_cycle_passes_only_when_both_halves_do(tmp_path):
    from app.factory.build.gates import gate_tester_contract

    ctx, _ = _ctx(tmp_path, _suite_then_trip(
        0, "PRODUCT-SUMMARY: 4 round-tripped, 0 failed, 0 unjudged, 4 capabilities\n"))
    ctx = replace(ctx, cycle="pilot")
    res = gate_tester_contract(ctx)
    assert res.ok
    assert res.gate == "product_green"
    assert "4 round-tripped" in res.detail
