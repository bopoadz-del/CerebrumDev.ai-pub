"""A release cycle starts once per deploy that is DECLARED ready (order 1736Z).

Every push to master deploys (deploy-aws), and every completed deploy used to
start a release cycle (post-deploy-smoke on workflow_run). Landing a batch of
N PRs started N cycles -- each one a full smoke A + repros + smoke B on the
live box, each later one queued behind (or cancelling) the one before.

The rule, general and data-free:
  * a deploy from a PUSH starts a cycle only when its commit declares it
    ready: the marker ``[ready-for-cycle]`` in the commit message (for a PR
    merge, put it in the PR title -- GitHub's merge commit carries the title);
  * a DISPATCHED deploy starts a cycle only when dispatched with
    ``cycle: true`` (its run name then carries ``[cycle]``);
  * a dispatched cycle (Actions -> Post-deploy smoke -> Run workflow) always
    runs -- the owner asked for it;
  * a deploy not declared ready deploys and starts nothing; its skipped run
    never takes the release-cycle concurrency slot, so it cannot displace a
    cycle that is waiting.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
GATE_PATH = REPO_ROOT / "scripts" / "cycle_gate.py"
SMOKE_WF = REPO_ROOT / ".github" / "workflows" / "post-deploy-smoke.yml"
DEPLOY_WF = REPO_ROOT / ".github" / "workflows" / "deploy-aws.yml"


def _gate():
    spec = importlib.util.spec_from_file_location("cycle_gate", GATE_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _wf(path: Path):
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    # PyYAML reads the bare key ``on`` as True.
    if True in raw and "on" not in raw:
        raw["on"] = raw.pop(True)
    return raw


def test_a_batch_merge_deploys_without_starting_a_cycle():
    g = _gate()
    run, why = g.starts_cycle(
        event_name="workflow_run",
        deploy_event="push",
        deploy_title="Merge pull request #1 from x/y",
        commit_message="Merge pull request #1 from x/y\n\nSome change",
    )
    assert run is False and "not declared ready" in why


def test_a_merge_declared_ready_starts_one_cycle():
    g = _gate()
    run, _ = g.starts_cycle(
        event_name="workflow_run",
        deploy_event="push",
        deploy_title="Merge pull request #2 from x/z",
        commit_message=f"Merge pull request #2 from x/z\n\nLast of the batch {g.READY_MARKER}",
    )
    assert run is True


def test_a_dispatched_deploy_starts_a_cycle_only_with_cycle_true():
    g = _gate()
    yes, _ = g.starts_cycle(
        event_name="workflow_run",
        deploy_event="workflow_dispatch",
        deploy_title=f"deploy-aws (dispatched) {g.DISPATCH_MARKER}",
        commit_message=f"tip of master {g.READY_MARKER}",
    )
    no, _ = g.starts_cycle(
        event_name="workflow_run",
        deploy_event="workflow_dispatch",
        deploy_title="deploy-aws (dispatched)",
        commit_message=f"tip of master {g.READY_MARKER}",
    )
    assert yes is True
    assert no is False, "a dispatch without cycle=true starts nothing, whatever the tip's message says"


def test_a_dispatched_cycle_always_runs():
    run, _ = _gate().starts_cycle(event_name="workflow_dispatch", deploy_event="", deploy_title="", commit_message="")
    assert run is True


def test_the_cycle_workflow_runs_its_jobs_only_after_the_gate_says_so():
    wf = _wf(SMOKE_WF)
    jobs = wf["jobs"]
    gate = jobs["gate"]
    text = yaml.safe_dump(gate)
    assert "scripts/cycle_gate.py" in text
    assert "run" in (gate.get("outputs") or {})
    resolve = jobs["resolve"]
    needs = resolve.get("needs")
    assert needs == "gate" or "gate" in (needs or [])
    assert "needs.gate.outputs.run == 'true'" in str(resolve.get("if"))
    # every other job hangs off resolve, so a gated-off cycle runs nothing
    for name, job in jobs.items():
        if name in ("gate", "resolve"):
            continue
        n = job.get("needs")
        assert "resolve" in ([n] if isinstance(n, str) else n), name


def test_a_skipped_cycle_never_takes_the_release_cycle_slot():
    g = _gate()
    group = str(_wf(SMOKE_WF)["concurrency"]["group"])
    assert g.READY_MARKER in group and g.DISPATCH_MARKER in group
    assert "github.run_id" in group, "a run that is not a cycle gets a slot of its own"
    assert "head_commit.message" in group and "display_title" in group


def test_the_deploy_can_be_dispatched_with_cycle_true():
    g = _gate()
    wf = _wf(DEPLOY_WF)
    inputs = wf["on"]["workflow_dispatch"]["inputs"]
    assert inputs["cycle"]["type"] == "boolean" and inputs["cycle"]["default"] is False
    assert g.DISPATCH_MARKER in str(wf.get("run-name"))
    # every push to master still deploys
    assert wf["on"]["push"]["branches"] == ["master"]
