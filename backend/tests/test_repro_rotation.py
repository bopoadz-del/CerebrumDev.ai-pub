"""The release bar's rotating repro set (owner ruling, 2026-10-08).

Every release cycle builds the two ANCHORS (the regression pair in
scripts/release_cycle.json) plus two blueprints picked by ROTATION from a pool
of at least eight across verticals (backend/tests/repro_pool), so each pool
blueprint comes round every four cycles. Release = every build exports
certified + smoke A and smoke B pass, one commit.

The pool is DATA: nothing in the Factory may branch on which blueprint runs
(the hardwiring gate's ``blueprint_name`` form, tested in
tests/factory/test_hardwiring_gate.py). Selection is deterministic -- pool
order is the index's declared order; slot 1 repeats the previous cycle's
failing pick, slot 2 is the next blueprint never built, a certified blueprint
is never re-run (derived from the cycle reports plus a committed seed), and
nothing is random.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CYCLE_PATH = REPO_ROOT / "scripts" / "release_cycle.py"
POOL_MOD_PATH = REPO_ROOT / "scripts" / "repro_pool.py"
CONFIG_PATH = REPO_ROOT / "scripts" / "release_cycle.json"
POOL_INDEX = REPO_ROOT / "backend" / "tests" / "repro_pool" / "pool.json"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "post-deploy-smoke.yml"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def pool_mod():
    return _load("repro_pool", POOL_MOD_PATH)


@pytest.fixture
def cycle():
    return _load("release_cycle", CYCLE_PATH)


@pytest.fixture
def pool(pool_mod):
    return pool_mod.load_pool(POOL_INDEX)


# -- the pool ----------------------------------------------------------------


def test_the_pool_holds_eight_floor_complete_blueprints_with_unique_ids(pool):
    assert len(pool) >= 8
    ids = [bp["id"] for bp in pool]
    assert len(ids) == len(set(ids))
    for bp in pool:
        for field in ("id", "name", "vertical", "country", "currency", "build_level", "brief"):
            assert str(bp.get(field) or "").strip(), (bp.get("id"), field)
        assert len(bp["country"]) == 2 and bp["country"].isupper()
        assert len(bp["currency"]) == 3 and bp["currency"].isupper()
        assert bp["build_level"] in {"prototype", "light", "pilot", "production"}
    # Across verticals: no two blueprints share one.
    verticals = [bp["vertical"] for bp in pool]
    assert len(verticals) == len(set(verticals))


def test_a_blueprints_id_is_its_file_name_so_it_never_drifts(pool):
    for bp in pool:
        assert bp["file"] == bp["id"] + ".json"


def test_pool_order_is_the_indexs_declared_order_not_the_filesystems(pool):
    declared = json.loads(POOL_INDEX.read_text(encoding="utf-8"))["blueprints"]
    assert [bp["file"] for bp in pool] == declared
    # The declared order is not the alphabetical one, so a loader that globbed
    # the folder would visibly disagree with it.
    assert declared != sorted(declared)


def test_the_first_rotation_pair_is_fintech_then_hotel_operations(pool):
    """Owner: fintech is rotation pick #1 on the first cycle -- the money
    contract and the country/currency intake get exercised for real."""
    assert "ledger" in pool[0]["brief"] and "KYC" in pool[0]["brief"]
    assert pool[0]["currency"] != "USD"
    assert "hotel" in pool[1]["brief"]


def _write_pool(root: Path, docs, order=None):
    root.mkdir(parents=True, exist_ok=True)
    files = []
    for doc in docs:
        name = f"{doc['id']}.json"
        (root / name).write_text(json.dumps(doc), encoding="utf-8")
        files.append(name)
    index = {"schema": "repro_pool.v1", "picks_per_cycle": 2, "blueprints": order or files}
    (root / "pool.json").write_text(json.dumps(index), encoding="utf-8")
    return root / "pool.json"


def _doc(bid, **over):
    doc = {
        "schema": "repro_blueprint.v1", "id": bid, "name": f"{bid} name",
        "vertical": f"{bid}_vertical", "country": "FR", "currency": "EUR",
        "build_level": "production", "brief": f"Build me {bid}.",
    }
    doc.update(over)
    return doc


@pytest.mark.parametrize(
    "bad",
    [
        {"country": "France"},
        {"currency": "euro"},
        {"build_level": "deluxe"},
        {"brief": ""},
        {"vertical": ""},
        {"name": ""},
    ],
)
def test_a_blueprint_that_is_not_floor_complete_is_refused(pool_mod, tmp_path, bad):
    index = _write_pool(tmp_path / "p", [_doc("repro_a"), _doc("repro_b", **bad)])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)


def test_a_file_in_the_folder_but_not_in_the_index_is_refused(pool_mod, tmp_path):
    """Every pool file is in the rotation: an unlisted one would never be built."""
    index = _write_pool(tmp_path / "p", [_doc("repro_a"), _doc("repro_b")], order=["repro_a.json"])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)


def test_an_index_entry_with_no_file_or_a_duplicate_is_refused(pool_mod, tmp_path):
    index = _write_pool(tmp_path / "p", [_doc("repro_a")], order=["repro_a.json", "repro_gone.json"])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)
    index = _write_pool(tmp_path / "q", [_doc("repro_a")], order=["repro_a.json", "repro_a.json"])
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(index)


def test_an_id_that_differs_from_its_file_name_is_refused(pool_mod, tmp_path):
    root = tmp_path / "p"
    _write_pool(root, [_doc("repro_a")])
    (root / "repro_a.json").write_text(json.dumps(_doc("repro_other")), encoding="utf-8")
    with pytest.raises(pool_mod.PoolError):
        pool_mod.load_pool(root / "pool.json")


# -- rotation v2 (owner rule, 2026-10-08) -------------------------------------
#
# Slot 1 repeats the previous cycle's failing pick; slot 2 is the next pool
# blueprint never built before; an already-certified blueprint is never
# re-run. The state is derived from every readable cycle report (newest
# first) plus a committed seed (scripts/release_cycle.json rotation.seed) --
# nothing is stored anywhere else, and nothing is random.


def _run(certified):
    run = {"role": "rotation", "status": "exported" if certified else "failed"}
    if certified:
        run["export"] = {"manifest": {"certified": True}}
    return run


def _history_report(picks, verdict="fail"):
    """picks: {blueprint id: certified?}, in the order the cycle picked them."""
    return {
        "verdict": verdict,
        "rotation": {"picks": list(picks)},
        "runs": {name: _run(ok) for name, ok in picks.items()},
    }


NO_SEED = {"certified": [], "tried": []}


def _ids(bps):
    return [bp["id"] for bp in bps]


def test_with_no_history_the_first_two_unused_blueprints_are_picked(cycle, pool):
    state = cycle.rotation_state([], NO_SEED)
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [pool[0]["id"], pool[1]["id"]]


def test_slot_one_repeats_the_failing_pick_and_slot_two_is_the_next_unused(cycle, pool):
    a, b, c = pool[0]["id"], pool[1]["id"], pool[2]["id"]
    state = cycle.rotation_state([_history_report({a: False, b: True})], NO_SEED)
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [a, c]


def test_a_certified_blueprint_is_never_re_run(cycle, pool):
    a, b = pool[0]["id"], pool[1]["id"]
    history = [_history_report({a: False, b: True}), _history_report({a: False, b: True})]
    picked = _ids(cycle.select_rotation(pool, cycle.rotation_state(history, NO_SEED), picks=2))
    assert b not in picked


def test_a_pass_moves_both_slots_to_unused_blueprints(cycle, pool):
    a, b, c, d = (bp["id"] for bp in pool[:4])
    state = cycle.rotation_state([_history_report({a: True, b: True}, verdict="pass")], NO_SEED)
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [c, d]


def test_a_failure_older_than_the_last_cycle_is_still_repeated_eventually(cycle, pool):
    a, b, c, d = (bp["id"] for bp in pool[:4])
    # Cycle 1: a and b both failed. Cycle 2 repeated a (certified) beside c.
    history = [_history_report({a: True, c: True}), _history_report({a: False, b: False})]
    picked = _ids(cycle.select_rotation(pool, cycle.rotation_state(history, NO_SEED), picks=2))
    assert picked == [b, d]


def test_the_seed_counts_as_history(cycle, pool):
    a, b, c = pool[0]["id"], pool[1]["id"], pool[2]["id"]
    seed = {"certified": [b], "tried": [a, b]}
    state = cycle.rotation_state([], seed)
    # a was tried and not certified: it repeats; b is certified: never again.
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [a, c]


def test_cycle_three_is_fintech_repeat_and_motor_insurance_fresh(cycle, pool):
    """The handover's cycle 3, from the committed seed and the cycle 1-2
    reports' shape: fintech failed both cycles, hotel ops certified both."""
    config = cycle.load_config(CONFIG_PATH)
    fintech, hotel, motor = pool[0]["id"], pool[1]["id"], pool[2]["id"]
    history = [_history_report({fintech: False, hotel: True}), _history_report({fintech: False, hotel: True})]
    state = cycle.rotation_state(history, cycle.rotation_seed(config))
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [fintech, motor]
    # And from the seed alone (cycle reports expired): the same pair.
    state = cycle.rotation_state([], cycle.rotation_seed(config))
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [fintech, motor]


def _report_on(commit, picks, verdict="fail"):
    report = _history_report(picks, verdict)
    report["commit"] = commit
    return report


VOID = "0" * 40


def test_a_voided_cycles_picks_are_neither_tried_nor_failing(cycle, pool):
    a, b = pool[0]["id"], pool[1]["id"]
    seed = {"certified": [], "tried": [], "void": [VOID]}
    history = [_report_on(VOID, {a: False, b: False})]
    state = cycle.rotation_state(history, seed)
    assert state["tried"] == [] and state["failing"] == []
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [a, b]


def test_a_certification_in_a_voided_cycle_still_stands(cycle, pool):
    a, b = pool[0]["id"], pool[1]["id"]
    seed = {"certified": [], "tried": [], "void": [VOID]}
    state = cycle.rotation_state([_report_on(VOID, {a: True, b: False})], seed)
    assert a in state["certified"]
    assert a not in _ids(cycle.select_rotation(pool, state, picks=2))


def test_a_void_record_needs_a_full_commit_and_a_reason(cycle):
    for bad in ([{"commit": "5410237b", "reason": "x"}], [{"commit": VOID}], ["5410237b"]):
        with pytest.raises(cycle.CycleError):
            cycle.rotation_seed({"rotation": {"seed": {"void": bad}}})


def test_cycle_five_is_fintech_repeat_and_motor_insurance_fresh(cycle, pool):
    """Cycle 3 (5410237b) is voided by the committed seed: motor insurance
    was never judged, so it re-enters fresh. Cycle 4 failed fintech (repeat)
    and fleet delivery (fresh): fintech leads slot 1."""
    config = cycle.load_config(CONFIG_PATH)
    ids = {bp["id"]: bp["id"] for bp in pool}
    fintech, motor, fleet = (
        ids["repro_fintech_payments_ledger"],
        ids["repro_motor_insurance_claims"],
        ids["repro_fleet_delivery_logistics"],
    )
    cycle4 = _report_on("55d91d62cdf424440e88343fa66dd10f8932830a", {fintech: False, fleet: False})
    cycle3 = _report_on("5410237b7cc04ef994b13723a4626ecb97366ff7", {fintech: False, motor: False})
    state = cycle.rotation_state([cycle4, cycle3], cycle.rotation_seed(config))
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [fintech, motor]



def test_cycle_seven_reruns_the_voided_cycle_six_picks(cycle, pool):
    """Cycle 6 (75a5a1ef) is voided by the committed seed: the server stopped
    mid-cycle and orphaned every writer. Fintech is still failing from cycle
    5 (repeat); clinic was never judged, so it re-enters fresh. Motor
    insurance, certified in cycle 5, never comes back."""
    config = cycle.load_config(CONFIG_PATH)
    ids = {bp["id"]: bp["id"] for bp in pool}
    fintech, motor, clinic, fleet = (
        ids["repro_fintech_payments_ledger"],
        ids["repro_motor_insurance_claims"],
        ids["repro_clinic_appointment_scheduling"],
        ids["repro_fleet_delivery_logistics"],
    )
    cycle6 = _report_on("75a5a1ef82d459be81283679723d9e6f8cd2e0e4", {fintech: False, clinic: False})
    cycle5 = _report_on("d024b231b23fc29e942c3c11408c7c5f2eca14b5", {fintech: False, motor: True})
    cycle4 = _report_on("55d91d62cdf424440e88343fa66dd10f8932830a", {fintech: False, fleet: False})
    cycle3 = _report_on("5410237b7cc04ef994b13723a4626ecb97366ff7", {fintech: False, motor: False})
    history = [cycle6, cycle5, cycle4, cycle3]
    # What cycle 6 itself ran (the live record: fintech repeat, clinic fresh).
    before = cycle.rotation_state(history[1:], cycle.rotation_seed(config))
    assert _ids(cycle.select_rotation(pool, before, picks=2)) == [fintech, clinic]
    state = cycle.rotation_state(history, cycle.rotation_seed(config))
    assert motor in state["certified"]
    assert clinic not in state["failing"]
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [fintech, clinic]

def test_a_pick_listed_without_a_run_counts_as_not_certified(cycle, pool):
    a, b, c = pool[0]["id"], pool[1]["id"], pool[2]["id"]
    report = {"verdict": "fail", "rotation": {"picks": [a, b]}, "runs": {b: _run(True)}}
    state = cycle.rotation_state([report], NO_SEED)
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [a, c]


def test_an_exhausted_pool_picks_nothing_rather_than_re_running_a_certified_one(cycle, pool):
    seed = {"certified": [bp["id"] for bp in pool], "tried": [bp["id"] for bp in pool]}
    assert cycle.select_rotation(pool, cycle.rotation_state([], seed), picks=2) == []


def test_one_cycle_never_builds_a_blueprint_twice(cycle, pool):
    a = pool[0]["id"]
    state = cycle.rotation_state([_history_report({a: False})], NO_SEED)
    picked = _ids(cycle.select_rotation(pool, state, picks=2))
    assert len(picked) == len(set(picked)) == 2


def test_a_seed_naming_a_blueprint_outside_the_pool_is_refused(cycle, pool):
    with pytest.raises(cycle.CycleError):
        cycle.select_rotation(pool, cycle.rotation_state([], {"certified": ["nope"], "tried": []}), picks=2)


def test_the_selector_never_reads_a_clock_or_a_random_source():
    source = CYCLE_PATH.read_text(encoding="utf-8") + POOL_MOD_PATH.read_text(encoding="utf-8")
    assert "import random" not in source and "from random" not in source
    assert "secrets.choice" not in source and "shuffle(" not in source


def test_a_run_that_never_wrote_a_report_is_skipped(cycle, pool):
    a, b, c = pool[0]["id"], pool[1]["id"], pool[2]["id"]
    state = cycle.rotation_state([None, _history_report({a: False, b: True})], NO_SEED)
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [a, c]


def test_a_cycle_from_before_the_rotation_says_nothing(cycle, pool):
    state = cycle.rotation_state([{"verdict": "pass"}], NO_SEED)
    assert _ids(cycle.select_rotation(pool, state, picks=2)) == [pool[0]["id"], pool[1]["id"]]


@pytest.mark.parametrize("bad", [
    {"verdict": "maybe", "rotation": {"picks": []}},
    {"rotation": {"picks": []}},
    {"verdict": "fail", "rotation": {"picks": "a"}},
    {"verdict": "fail", "rotation": "x"},
    "not a report",
])
def test_an_unreadable_report_fails_closed(cycle, bad):
    with pytest.raises(cycle.CycleError):
        cycle.rotation_state([bad], NO_SEED)


def test_a_history_that_cannot_be_read_fails_closed(cycle):
    def broken():
        raise cycle.CycleError("the previous report could not be downloaded")
        yield  # pragma: no cover

    with pytest.raises(cycle.CycleError):
        cycle.rotation_state(broken(), NO_SEED)


def test_the_state_reads_no_clock_no_random_source_and_no_local_state(cycle):
    import inspect

    for fn in (cycle.rotation_state, cycle.select_rotation):
        src = inspect.getsource(fn)
        for forbidden in ("random", "time.", "datetime", "open(", "Path("):
            assert forbidden not in src, (fn.__name__, forbidden)


def test_planning_without_a_readable_history_fails_closed(cycle, monkeypatch):
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(var, raising=False)
    config = cycle.load_config(CONFIG_PATH)
    with pytest.raises(cycle.CycleError):
        cycle.plan_cycle(config, "0" * 40)


def test_a_plan_records_its_picks_and_why(cycle, pool):
    config = cycle.load_config(CONFIG_PATH)
    fintech, hotel, motor = pool[0]["id"], pool[1]["id"], pool[2]["id"]
    plan = cycle.plan_cycle(config, "0" * 40, reports=[_history_report({fintech: False, hotel: True})])
    rot = plan["rotation"]
    assert rot["picks"] == [fintech, motor]
    assert rot["repeats"] == [fintech] and rot["fresh"] == [motor]
    assert hotel in rot["certified"]
    assert rot["pool_size"] == len(pool)


# -- planning one cycle -------------------------------------------------------


def test_a_cycle_runs_the_anchors_then_the_rotation_picks(cycle, pool):
    config = cycle.load_config(CONFIG_PATH)
    runs = cycle.plan_runs(config, pool[:2])
    anchors = [r for r in runs if r["role"] == "anchor"]
    picks = [r for r in runs if r["role"] == "rotation"]
    assert [r["name"] for r in anchors] == [a["name"] for a in config["anchors"]]
    assert [r["name"] for r in picks] == [pool[0]["id"], pool[1]["id"]]
    assert [r["role"] for r in runs] == ["anchor"] * len(anchors) + ["rotation"] * 2
    names = [r["name"] for r in runs]
    assert len(names) == len(set(names))
    # A pick carries its blueprint's typed intake; an anchor declares none.
    assert picks[0]["intake"] == {
        "vertical": pool[0]["vertical"],
        "country": pool[0]["country"],
        "currency": pool[0]["currency"],
    }
    assert picks[0]["level"] == pool[0]["build_level"]
    assert all(not r["intake"] for r in anchors)


def test_the_anchors_are_still_the_regression_pair(cycle):
    config = cycle.load_config(CONFIG_PATH)
    assert len(config["anchors"]) == 2
    assert config["rotation"]["pool"] == "backend/tests/repro_pool/pool.json"
    rotation = cycle.load_rotation(config)
    # How many picks a cycle takes has ONE source: the pool index.
    assert rotation["picks"] == 2
    assert "picks" not in config["rotation"]
    assert [bp["id"] for bp in rotation["pool"]][:2] == [
        "repro_fintech_payments_ledger", "repro_hotel_operations",
    ]


class _IntakeSmoke:
    """Records every Floor chat turn the cycle sends."""

    TRANSIENT = {502, 503, 504}
    BuildDeadline = _load(
        "post_deploy_smoke_for_intake", REPO_ROOT / "scripts" / "post_deploy_smoke.py"
    ).BuildDeadline

    def __init__(self):
        self.turns = []

    def req(self, method, path, body=None, token=None, raw=False, **kw):
        if path == "/v1/sessions/":
            return 200, {"session_id": "sess_x"}
        if path.endswith("/product/package"):
            return 409, b"{}"
        return 200, {"build": {"state": "failed"}}

    def chat_until_drafted(self, sid, tok, brief):
        self.turns.append(("draft", None, {}))
        return "blueprint", 1

    def chat(self, sid, tok, msg, action=None, value=None, fields=None, **kw):
        self.turns.append((action, value, dict(fields or {})))
        return "ok"


def test_a_pick_declares_its_country_currency_and_vertical_on_the_floor(cycle):
    smoke = _IntakeSmoke()
    intake = {"vertical": "v_x", "country": "AE", "currency": "AED"}
    cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=0, poll_s=0, intake=intake)
    by_action = {a: f for a, _v, f in smoke.turns}
    # Typed on the intake line with the level, and stated again on Approve so a
    # model proposal confirmed in between can never replace what was typed.
    assert by_action["set_build_level"] == intake
    assert by_action["approve"] == intake
    assert [a for a, _v, _f in smoke.turns] == ["draft", "set_build_level", "confirm_intake", "approve"]


def test_an_anchor_declares_no_intake_exactly_as_before(cycle):
    smoke = _IntakeSmoke()
    cycle.drive_build(smoke, "tok", "brief", level="production", wait_s=0, poll_s=0)
    assert all(f == {} for _a, _v, f in smoke.turns)


def test_the_smoke_client_sends_typed_intake_fields_beside_the_action(monkeypatch):
    smoke = _load("post_deploy_smoke", REPO_ROOT / "scripts" / "post_deploy_smoke.py")
    sent = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def urlopen(rq, timeout=0):
        sent["body"] = json.loads(rq.data)
        return _Resp()

    monkeypatch.setattr(smoke.urllib.request, "urlopen", urlopen)
    smoke.chat("sess_x", "tok", "", action="approve", fields={"country": "AE", "currency": "AED"})
    assert sent["body"] == {"message": "", "action": "approve", "country": "AE", "currency": "AED"}


# -- accounts and slots ------------------------------------------------------


def test_the_roster_covers_every_build_plus_the_smoke(cycle):
    from app.core.trial_limits import SMOKE_PRINCIPALS, SMOKE_RESERVED_PRINCIPAL

    config = cycle.load_config(CONFIG_PATH)
    builds = len(config["anchors"]) + cycle.load_rotation(config)["picks"]
    assert len(SMOKE_PRINCIPALS) >= builds + 1
    assert SMOKE_PRINCIPALS[0] == SMOKE_RESERVED_PRINCIPAL
    plan = cycle.assign_accounts([f"r{i}" for i in range(builds)], available=len(SMOKE_PRINCIPALS))
    assert plan["smoke"] == 0 and 0 not in plan["repros"].values()


def test_a_repro_waits_out_the_queue_ahead_of_it(cycle):
    """The live box runs two user builds at once (3 slots, 1 reserved for the
    smoke); four repros run in two waves, so a second-wave repro must not be
    timed out while it is still queued."""
    assert cycle.repro_wait_s(builds=4, user_slots=2, build_wait_s=5400) == 2 * 5400
    assert cycle.repro_wait_s(builds=2, user_slots=2, build_wait_s=5400) == 5400
    assert cycle.repro_wait_s(builds=5, user_slots=2, build_wait_s=100) == 300


def test_the_config_declares_the_live_boxs_user_slots(cycle):
    config = cycle.load_config(CONFIG_PATH)
    assert int(config["user_build_slots"]) >= 1


# -- the report --------------------------------------------------------------


def _row(role, certified=True):
    return {
        "status": "exported", "role": role, "account_index": 1,
        "export": {"bytes": 10, "files": 6, "manifest": {
            "certified": certified, "build_level": {"build_level": "production"},
            "advisory_checks": [],
        }},
        "wall_s": 120.0,
    }


def _report(cycle, pick_certified=True):
    return cycle.build_report(
        smoke_a={"status": "pass"},
        repros={
            "coop": _row("anchor"), "vineyard": _row("anchor"),
            "repro_a": _row("rotation"), "repro_b": _row("rotation", pick_certified),
        },
        smoke_b={"status": "pass"},
        started_at=0.0, finished_at=4000.0, commit="abc", live_sha="abc",
        rotation={"picks": ["repro_a", "repro_b"], "repeats": ["repro_a"], "fresh": ["repro_b"], "pool_size": 8},
    )


def test_the_report_has_a_row_per_pick_with_the_same_columns(cycle):
    report = _report(cycle)
    assert report["verdict"] == "pass"
    assert report["rotation"]["picks"] == ["repro_a", "repro_b"]
    for name in ("repro_a", "repro_b"):
        row = report["runs"][name]
        assert row["role"] == "rotation"
        assert set(row["export"]["manifest"]) == {"certified", "build_level", "advisory_checks"}
        assert row["wall_s"] == 120.0
    md = cycle.render_markdown(report)
    assert "| Run | Role |" in md
    assert "repro_a" in md and "repro_b" in md and "rotation" in md
    assert "repeats repro_a" in md and "fresh repro_b" in md


def test_an_uncertified_rotation_pick_fails_the_release(cycle):
    report = _report(cycle, pick_certified=False)
    assert report["verdict"] == "fail"
    assert "repro_b" in report["reason"]


# -- the workflow ------------------------------------------------------------


def _jobs():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]


def test_the_repro_job_can_read_the_previous_cycle_report():
    job = _jobs()["release-repros"]
    assert job.get("permissions", {}).get("actions") == "read"
    assert job.get("permissions", {}).get("contents") == "read"
    step = next(s for s in job["steps"] if "release_cycle.py repros" in str(s.get("run", "")))
    assert "GITHUB_TOKEN" in step.get("env", {})


def test_the_report_artifact_the_counter_reads_is_the_one_the_cycle_uploads(cycle):
    uploads = [
        s["with"]["name"]
        for s in _jobs()["smoke-b"]["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
    ]
    assert cycle.REPORT_ARTIFACT in uploads
    assert cycle.CYCLE_WORKFLOW == WORKFLOW_PATH.name


def test_the_repro_job_outlasts_every_wave_of_builds(cycle):
    config = cycle.load_config(CONFIG_PATH)
    builds = len(config["anchors"]) + cycle.load_rotation(config)["picks"]
    wait_s = cycle.repro_wait_s(builds=builds, user_slots=int(config["user_build_slots"]), build_wait_s=5400)
    need_min = (wait_s + 1200) // 60
    assert int(_jobs()["release-repros"]["timeout-minutes"]) >= need_min
    assert int(_jobs()["release-repros"]["timeout-minutes"]) <= 360, "GitHub's job ceiling"
