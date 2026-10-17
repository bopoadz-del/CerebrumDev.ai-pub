"""Gate replay (owner rule, 2026-10-08): a gate change is replayed before
merge against the builds the gate already certified.

The Factory half: re-render every Factory-written file onto a certified tree
with the PR's code and route the replayed result through the live ingest
rule. Since Route A (2026-10-13) the replay itself runs in THIS repository on
the PR head, with the gate cerebrum-builds hands over (vendored under
.github/gate_replay/, held by test_gate_replay_in_factory.py). Branch
selection and the flip rule are cerebrum-builds' (replay_select.py,
replay_flips.py), run here from the vendored copy and tested there.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import yaml

from app.factory.build import gate_replay
from app.factory.build.n3_store_gate import N3_STORE_GATE_GREEN
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
    ACCEPTANCE_SCRIPT_REL,
    render_acceptance_script,
)

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "gate-replay.yml"
BUILD = REPO / "backend" / "app" / "factory" / "build"
#: The modules the gate's Factory half is made of (their own imports from
#: app.factory.build are derived below, never listed).
GATE_ROOTS = (
    "gate_replay",
    "stamp_registry",
    "n3_store_gate",
    "factory_refresh",
    "store_acceptance",
    "acceptance_floor",
)


def _gate_modules() -> set:
    found = set(GATE_ROOTS)
    for name in GATE_ROOTS:
        tree = ast.parse((BUILD / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.factory.build."):
                found.add(node.module.rsplit(".", 1)[1])
    return found


def _workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # PyYAML reads the bare key `on` as True.
    data["on"] = data.get("on", data.get(True))
    return data


# -- config ---------------------------------------------------------------------


def test_n_comes_from_the_config(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / gate_replay.CONFIG_REL).write_text(json.dumps({"certified_branches": 5}), encoding="utf-8")
    assert gate_replay.replay_config(tmp_path)["certified_branches"] == 5


def test_n_defaults_to_three_without_a_valid_config(tmp_path):
    assert gate_replay.replay_config(tmp_path)["certified_branches"] == 3
    (tmp_path / ".github").mkdir()
    (tmp_path / gate_replay.CONFIG_REL).write_text('{"certified_branches": "many"}', encoding="utf-8")
    assert gate_replay.replay_config(tmp_path)["certified_branches"] == 3


def test_the_repo_config_is_the_one_read():
    assert gate_replay.replay_config()["certified_branches"] == json.loads(
        (REPO / gate_replay.CONFIG_REL).read_text(encoding="utf-8")
    )["certified_branches"]


# -- the replay's install -------------------------------------------------------

REPLAY_REQS = REPO / "backend" / "requirements-replay.txt"


def _req_lines(path: Path) -> list:
    return [
        line.split("#", 1)[0].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip()
    ]


def test_the_replay_install_names_no_constraints_file_with_extras():
    # Live #696: `-c requirements.txt` -> "Constraints cannot have extras"
    # (psycopg[binary]); pip refuses any constraint carrying [extras].
    for line in _req_lines(REPLAY_REQS):
        if line.startswith(("-c", "--constraint")):
            target = REPLAY_REQS.parent / line.split(None, 1)[1]
            assert not [x for x in _req_lines(target) if "[" in x], f"{target.name} has extras"


def test_the_replay_pins_what_requirements_pins_at_the_same_version():
    from app.factory.build.dependency_pins import dist_name

    main = {dist_name(x): x.split("==", 1)[1] for x in _req_lines(REPO / "backend" / "requirements.txt") if "==" in x}
    for line in _req_lines(REPLAY_REQS):
        name = dist_name(line)
        if name in main:
            assert line == f"{name}=={main[name]}", f"{line!r} drifts from requirements.txt ({main[name]})"


def test_a_rendered_product_constraints_file_never_carries_extras():
    # The same pip rule binds every product image (`pip install -c
    # constraints.txt`): a constraint is a bare name and a version.
    from app.factory.build.dependency_pins import render_constraints

    text = render_constraints(["psycopg[binary]>=3", "sentry-sdk[fastapi]", "uvicorn[standard]"])
    pins = [x for x in text.splitlines() if x and not x.startswith("#")]
    assert pins
    assert not [x for x in pins if "[" in x]


# -- the workflow ---------------------------------------------------------------


def test_every_gate_module_triggers_the_replay():
    paths = set(_workflow()["on"]["pull_request"]["paths"])
    missing = sorted(
        f"backend/app/factory/build/{m}.py"
        for m in _gate_modules()
        if f"backend/app/factory/build/{m}.py" not in paths
    )
    assert not missing, f"gate modules that would change the gate without a replay: {missing}"
    from app.factory.build.acceptance_floor import floor_path

    floor = floor_path().relative_to(REPO).as_posix()
    assert floor in paths
    assert ".github/workflows/gate-replay.yml" in paths
    assert gate_replay.CONFIG_REL.as_posix() in paths


def test_the_replay_is_one_named_check_run_here_on_the_pr_head():
    """Route A (2026-10-13): the replay runs in this repository on the PR head
    -- no dispatch to cerebrum-builds, no cross-repo token. The one check the
    merge rule reads is the verdict job (tests/factory/test_gate_replay_in_factory.py
    holds the rest)."""
    jobs = _workflow()["jobs"]
    checks = [job for job in jobs.values() if job.get("name") == "gate replay (certified builds)"]
    assert len(checks) == 1
    runs = " ".join(str(step.get("run") or "") for job in jobs.values() for step in job.get("steps") or [])
    assert "gate_replay dispatch" not in runs
    assert "github.event.pull_request.head.sha" in str(_workflow()["env"]["FACTORY_REF"])
    from app.factory.build.builds_push import BUILDS_TOKEN_ENV

    assert BUILDS_TOKEN_ENV not in WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")


def test_the_replay_also_runs_on_demand_for_a_named_commit():
    """A PR that touches no gate module gets no automatic replay; the merge
    rule still asks for one. workflow_dispatch takes the commit as data."""
    on = _workflow()["on"]
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["sha"]["required"] is True
    assert "inputs.sha" in str(_workflow()["env"]["FACTORY_REF"])

# -- render ---------------------------------------------------------------------


def _certified_tree(root: Path) -> Path:
    for rel, text in {
        "app/__init__.py": "",
        "app/main.py": "from fastapi import FastAPI\napp = FastAPI()\n",
        "requirements.txt": "fastapi\nuvicorn\n",
        # A harness an older Factory rendered.
        str(ACCEPTANCE_SCRIPT_REL): "# an older Factory's harness\n",
    }.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    dna = root / "product-dna"
    dna.mkdir()
    shutil.copy(REPO / "blueprints" / "examples" / "basic_product.yaml", dna / "product_blueprint.yaml")
    return root


def test_render_puts_this_factorys_brief_aware_harness_on_the_tree(tmp_path):
    root = _certified_tree(tmp_path)
    blueprint = gate_replay.load_tree_blueprint(root)
    assert blueprint is not None

    changed = gate_replay.render_onto(root)

    assert ACCEPTANCE_SCRIPT_REL.as_posix() in changed
    assert (root / ACCEPTANCE_SCRIPT_REL).read_text(encoding="utf-8") == render_acceptance_script(blueprint)
    # The product's own code is never a Factory file.
    assert "app/main.py" not in changed


def test_render_twice_changes_nothing_the_second_time(tmp_path):
    root = _certified_tree(tmp_path)
    gate_replay.render_onto(root)
    assert gate_replay.render_onto(root) == []


# -- verdict --------------------------------------------------------------------


def _payload(failing=(), score=None) -> dict:
    lines = [
        {"name": n, "status": "FAIL" if n in failing else "PASS", "detail": "x"}
        for n in ACCEPTANCE_CHECK_NAMES
    ]
    passed = ACCEPTANCE_REQUIRED - len(failing)
    return {
        "ok": not failing and score is None,
        "passed": passed,
        "total": ACCEPTANCE_REQUIRED,
        "score": score or f"{passed}/{ACCEPTANCE_REQUIRED}",
        "lines": lines,
        "sha": "a" * 40,
        "via": "scripts/acceptance.py-in-docker",
    }


def test_a_certified_result_routes_green(tmp_path):
    out = gate_replay.verdict(_certified_tree(tmp_path), _payload(), branch="build/x")
    assert out["ok"] is True
    assert out["honesty"] == N3_STORE_GATE_GREEN


def test_a_red_result_never_routes_green(tmp_path):
    out = gate_replay.verdict(_certified_tree(tmp_path), _payload(failing=("no_token_401",)), branch="build/x")
    assert out["ok"] is False


def test_an_unscored_result_never_routes_green(tmp_path):
    payload = {"ok": False, "passed": 0, "total": 0, "score": "no score", "lines": [], "sha": "b" * 40}
    out = gate_replay.verdict(_certified_tree(tmp_path), payload, branch="build/x")
    assert out["ok"] is False


# -- dispatch -------------------------------------------------------------------


class _GitHub:
    def __init__(self, conclusion="success", dispatch_status=204):
        self.conclusion = conclusion
        self.dispatch_status = dispatch_status
        self.request = ""
        self.calls = []

    def __call__(self, method, path, *, token, body=None, **_kw):
        self.calls.append((method, path, body))
        if method == "POST":
            self.request = body["inputs"]["request"]
            return self.dispatch_status, {}
        if path.startswith("/repos/o/r/actions/workflows/"):
            return 200, {
                "workflow_runs": [
                    {"id": 7, "display_title": "gate replay someone-else", "status": "completed", "conclusion": "failure"},
                    {
                        "id": 9,
                        "display_title": f"gate replay {self.request}",
                        "status": "completed",
                        "conclusion": self.conclusion,
                        "html_url": "https://example.invalid/run/9",
                    },
                ]
            }
        return 404, {}


ENV = {"CEREBRUM_BUILDS_GITHUB_TOKEN": "t", "CEREBRUM_BUILDS_REPO": "o/r"}
CFG = {"certified_branches": 4, "builds_ref": "main"}


def test_dispatch_reports_the_replay_runs_conclusion():
    gh = _GitHub("success")
    out = gate_replay.dispatch_and_wait("f" * 40, env=ENV, request_fn=gh, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is True and "run/9" in out["detail"]
    method, path, body = gh.calls[0]
    assert path.endswith(f"/actions/workflows/{gate_replay.REPLAY_WORKFLOW}/dispatches")
    assert body["inputs"]["factory_ref"] == "f" * 40
    assert body["inputs"]["certified_branches"] == "4"


def test_a_failed_replay_fails_the_check():
    gh = _GitHub("failure")
    out = gate_replay.dispatch_and_wait("f" * 40, env=ENV, request_fn=gh, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is False


def test_a_refused_dispatch_fails_the_check_with_the_answer():
    gh = _GitHub(dispatch_status=403)
    out = gate_replay.dispatch_and_wait("f" * 40, env=ENV, request_fn=gh, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is False and "HTTP 403" in out["detail"]


def test_no_token_fails_the_check():
    out = gate_replay.dispatch_and_wait("f" * 40, env={}, sleep=lambda _s: None, config=CFG)
    assert out["ok"] is False
