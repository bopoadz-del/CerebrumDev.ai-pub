"""Gate replay inside the Factory (Route A, owner order 2026-10-13).

The replay runs HERE, on the Factory at the PR head (a local checkout), with
the gate cerebrum-builds handed over: its gate files, the selection its own
record made and each selected build's tree, vendored under
``.github/gate_replay/vendor/<builds commit>/`` with a manifest of sha256s and
tree ids (cerebrum-builds ``.github/gate_handover/``).

What these tests hold:

* the workflows reach no other repository and use no secret -- only the
  default GITHUB_TOKEN of this repository, implicitly;
* the vendored copy is exactly the manifest (drift fails);
* the pass rule (replay_flips.py), the fixtures (known_bad_fixtures.json) and
  the selection rule (replay_select.py) are RUN from the vendored files,
  never copied or rewritten;
* the gate job is store-gate.yml itself: every step but the two that fetch
  from cerebrum-builds is the vendored step, unchanged.
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
GATE_REPLAY_DIR = REPO / ".github" / "gate_replay"
WORKFLOWS = REPO / ".github" / "workflows"
REPLAY_WORKFLOW = WORKFLOWS / "gate-replay.yml"
STORE_GATE_WORKFLOW = WORKFLOWS / "gate-replay-store-gate.yml"
BUILDS = "bopoadz-del/cerebrum-builds"


def _load_module():
    path = GATE_REPLAY_DIR / "replay_in_factory.py"
    spec = importlib.util.spec_from_file_location("replay_in_factory", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["replay_in_factory"] = module
    spec.loader.exec_module(module)
    return module


rif = _load_module()


def _yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["on"] = data.get("on", data.get(True))
    return data


def _steps(workflow: dict):
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps") or []:
            yield job_id, step


def _manifest() -> dict:
    return json.loads((rif.vendor_dir() / "manifest.json").read_text(encoding="utf-8"))


# -- no cross-repo access, no secret ---------------------------------------------


@pytest.mark.parametrize("path", [REPLAY_WORKFLOW, STORE_GATE_WORKFLOW], ids=lambda p: p.name)
def test_the_replay_workflows_use_no_secret(path):
    text = path.read_text(encoding="utf-8")
    assert "secrets." not in text and "secrets[" not in text
    for _job, step in _steps(_yaml(path)):
        assert "token" not in {str(k).lower() for k in (step.get("with") or {})}, step
        assert "ssh-key" not in (step.get("with") or {}), step


@pytest.mark.parametrize("path", [REPLAY_WORKFLOW, STORE_GATE_WORKFLOW], ids=lambda p: p.name)
def test_the_replay_workflows_check_out_no_other_repository(path):
    workflow = _yaml(path)
    for _job, step in _steps(workflow):
        uses = str(step.get("uses") or "")
        if uses.startswith("actions/checkout"):
            assert "repository" not in (step.get("with") or {}), step
    for job in workflow["jobs"].values():
        assert not str(job.get("uses") or "").startswith(BUILDS.split("/")[0] + "/"), job
    # Nothing that runs or is passed names cerebrum-builds (comments may).
    live = json.dumps({"jobs": workflow["jobs"], "env": workflow.get("env") or {}})
    assert BUILDS not in live and "cerebrum-builds" not in live


def test_the_replay_never_dispatches_to_cerebrum_builds():
    runs = " ".join(str(step.get("run") or "") for _job, step in _steps(_yaml(REPLAY_WORKFLOW)))
    assert "gate_replay dispatch" not in runs
    assert "dispatches" not in runs


def test_the_factory_under_replay_is_the_pr_head():
    workflow = _yaml(REPLAY_WORKFLOW)
    ref = str((workflow.get("env") or {}).get("FACTORY_REF"))
    assert "github.event.pull_request.head.sha" in ref and "inputs.sha" in ref
    for job_id, step in _steps(workflow):
        if str(step.get("uses") or "").startswith("actions/checkout"):
            assert "env.FACTORY_REF" in str((step.get("with") or {}).get("ref")), job_id
    replay = workflow["jobs"]["replay"]
    assert replay["uses"] == "./.github/workflows/gate-replay-store-gate.yml"
    # A reusable call's inputs cannot read the workflow env: the same expression.
    passed = str(replay["with"]["factory_ref"])
    assert "github.event.pull_request.head.sha" in passed and "inputs.sha" in passed
    for _job, step in _steps(_yaml(STORE_GATE_WORKFLOW)):
        if str(step.get("uses") or "").startswith("actions/checkout"):
            assert "inputs.factory_ref" in str((step.get("with") or {}).get("ref"))


# -- the vendored copy is the manifest ---------------------------------------------


def test_exactly_one_handover_is_vendored():
    dirs = [p for p in (GATE_REPLAY_DIR / "vendor").iterdir() if p.is_dir()]
    assert len(dirs) == 1
    assert dirs[0].name == _manifest()["source_commit"]
    assert _manifest()["source_repo"] == BUILDS


def test_the_vendored_copy_matches_its_manifest():
    assert rif.verify() == []


def _copy_vendor(tmp_path: Path) -> Path:
    dest = tmp_path / rif.vendor_dir().name
    shutil.copytree(rif.vendor_dir(), dest)
    return dest


def test_a_changed_vendored_gate_file_is_drift(tmp_path):
    vendor = _copy_vendor(tmp_path)
    flips = vendor / "gate" / ".github" / "store_gate" / "replay_flips.py"
    flips.write_bytes(flips.read_bytes() + b"\n# loosened\n")
    problems = rif.verify(vendor)
    assert any("replay_flips.py" in p and "sha256" in p for p in problems), problems


def test_an_extra_or_missing_vendored_file_is_drift(tmp_path):
    vendor = _copy_vendor(tmp_path)
    (vendor / "gate" / ".github" / "store_gate" / "extra.py").write_text("x = 1\n", encoding="utf-8")
    (vendor / "gate" / ".github" / "store_gate" / "known_bad_fixtures.json").unlink()
    problems = rif.verify(vendor)
    assert any("extra.py" in p and "not in the manifest" in p for p in problems), problems
    assert any("known_bad_fixtures.json" in p and "missing" in p for p in problems), problems


def _synthetic_vendor(tmp_path: Path) -> Path:
    """A copy of the vendored handover (its real gate files) plus a SYNTHETIC
    selection: three certified build trees and one tree per known-bad fixture,
    generated here at test time. The vendored handover itself holds no product
    tree (dropped -- see its manifest), so the code that verifies, extracts and
    selects handed-over trees is exercised on these."""
    vendor = _copy_vendor(tmp_path)
    data = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    store_gate = vendor / "gate" / ".github" / "store_gate"
    spec = importlib.util.spec_from_file_location("_synthetic_replay_select", store_gate / "replay_select.py")
    rs = importlib.util.module_from_spec(spec)
    written, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in the copy
    try:
        spec.loader.exec_module(rs)
    finally:
        sys.dont_write_bytecode = written
    fixtures = rs.read_known_bad(store_gate / "known_bad_fixtures.json")["fixtures"]
    rows, products = [], {}

    def bundle(sha: str, label: str) -> None:
        files = {
            "README.md": f"synthetic product {label}\n".encode("utf-8"),
            "app/main.py": f"LABEL = {label!r}\n".encode("utf-8"),
            "scripts/acceptance.py": b"print('synthetic acceptance')\n",
        }
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w") as tar:
            for name, body in files.items():
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(body), 0o644
                tar.addfile(info, io.BytesIO(body))
        blob = gzip.compress(out.getvalue())
        rel = f"products/{sha}.tar.gz"
        (vendor / "products").mkdir(exist_ok=True)
        (vendor / rel).write_bytes(blob)
        products[sha] = {
            "bundle": rel,
            "bundle_sha256": hashlib.sha256(blob).hexdigest(),
            "tree": rif.tree_id(rif.bundle_members(vendor / rel)),
        }

    for i in range(3):
        branch = f"build/plt_synthetic{i}"
        sha = hashlib.sha1(branch.encode("utf-8")).hexdigest()
        rows.append({"branch": branch, "certified_at": f"2026-10-1{5 - i}T00:00:00Z", "label": rs.label(branch),
                     "run_id": 1000 + i, "sha": sha})
        bundle(sha, rs.label(branch))
    for f in fixtures:
        rows.append({"branch": f["branch"], "expect": "fail", "expected_check": f["expected_failing_check"],
                     "label": rs.KNOWN_BAD_LABEL_PREFIX + rs.label(f["branch"]), "sha": f["sha"]})
        bundle(f["sha"], rs.label(f["branch"]))
    data["products"], data["selection"] = products, rows
    (vendor / "manifest.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert rif.verify(vendor) == []
    return vendor


def test_the_vendored_handover_holds_no_product_tree_and_says_why():
    data = _manifest()
    assert data["products"] == {} and data["selection"] == []
    assert "private kit" in data["dropped"]["products"]


def test_with_no_certified_build_the_replay_is_not_applicable():
    # Reversed with evidence (ORDERS 2026-10-28 0200Z #3). The handover currently
    # holds 0 certified builds. The OLD behaviour raised "hand over a new
    # selection" and so failed EVERY PR until a handover no code change can
    # produce exists -- a gate wired to nothing (placeholder rule). The gate is
    # NOT weakened: it stays strict the moment a certified build is handed over
    # (see test_a_handover_below_the_minimum_still_fails and the per-build
    # replay), and the known-bad fixtures still run. With 0 certified builds it
    # is NOT APPLICABLE -- select() returns no certified build and does not
    # raise; the verdict job exits 0 with an annotation.
    rows = rif.select(n=3)
    assert [r for r in rows if r.get("expect") != "fail"] == []


def test_a_changed_product_bundle_is_drift(tmp_path):
    vendor = _synthetic_vendor(tmp_path)
    sha, item = next(iter(rif.manifest(vendor)["products"].items()))
    (vendor / item["bundle"]).write_bytes(b"not the tree")
    assert any(sha in p for p in rif.verify(vendor))


def test_a_bundle_that_is_not_the_handed_over_tree_is_refused(tmp_path):
    """Re-packed with one file changed (and the manifest's sha256 updated to
    match), the bundle's recomputed git tree id no longer matches."""
    vendor = _synthetic_vendor(tmp_path)
    manifest_path = vendor / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    sha, item = next(iter(data["products"].items()))
    raw = gzip.decompress((vendor / item["bundle"]).read_bytes())
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as src, tarfile.open(fileobj=out, mode="w") as dst:
        for i, member in enumerate(src.getmembers()):
            data_bytes = src.extractfile(member).read() if member.isfile() else None
            if i == 0 and member.isfile():
                data_bytes += b"\n"
                member.size = len(data_bytes)
            dst.addfile(member, io.BytesIO(data_bytes) if data_bytes is not None else None)
    blob = gzip.compress(out.getvalue())
    (vendor / item["bundle"]).write_bytes(blob)
    data["products"][sha]["bundle_sha256"] = hashlib.sha256(blob).hexdigest()
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    assert any("tree" in p for p in rif.verify(vendor))


def test_extract_restores_exactly_the_handed_over_tree(tmp_path):
    vendor = _synthetic_vendor(tmp_path)
    sha, item = next(iter(rif.manifest(vendor)["products"].items()))
    rif.extract(sha, tmp_path / "product", vendor=vendor)
    names = sorted(p.relative_to(tmp_path / "product").as_posix() for p in (tmp_path / "product").rglob("*") if p.is_file())
    assert names == sorted(rif.bundle_files(vendor / item["bundle"]))
    assert (tmp_path / "product" / "scripts" / "acceptance.py").is_file()


def test_extract_refuses_a_sha_that_was_not_handed_over(tmp_path):
    with pytest.raises(ValueError):
        rif.extract("f" * 40, tmp_path / "product")


# -- the pass rule, the fixtures and the selection are the vendored files ---------


def test_the_pass_rule_is_the_vendored_replay_flips_run_as_is():
    verdict = _yaml(REPLAY_WORKFLOW)["jobs"]["verdict"]
    assert verdict["name"] == "gate replay (certified builds)"
    runs = "\n".join(str(step.get("run") or "") for step in verdict["steps"])
    assert "gate/.github/store_gate/replay_flips.py selection.json results" in runs
    assert "--routing routing" in runs
    # The verdict job verifies the vendored copy before it runs it.
    assert "replay_in_factory.py verify" in runs
    # No other copy of the pass rule or the fixtures exists in this repository.
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    vendored = rif.vendor_dir().relative_to(REPO).as_posix() + "/"
    for name in ("replay_flips.py", "replay_select.py", "known_bad_fixtures.json", "store-gate.yml"):
        copies = [p for p in tracked if p.endswith("/" + name) and not p.startswith(vendored)]
        assert copies == [], copies


@pytest.mark.parametrize(
    "rel",
    [
        ".github/store_gate/replay_flips.py",
        ".github/store_gate/replay_select.py",
        ".github/store_gate/known_bad_fixtures.json",
        ".github/store_gate/revoked_certifications.json",
        ".github/workflows/store-gate.yml",
    ],
)
def test_the_pass_rule_fixtures_and_gate_are_byte_identical_to_the_handover(rel):
    data = (rif.vendor_dir() / "gate" / rel).read_bytes()
    assert hashlib.sha256(data).hexdigest() == _manifest()["gate_files"][rel]


def test_the_selection_is_made_by_the_vendored_rule(tmp_path):
    vendor = _synthetic_vendor(tmp_path)
    rows = rif.select(n=3, vendor=vendor)
    fixtures = json.loads((vendor / "gate/.github/store_gate/known_bad_fixtures.json").read_text(encoding="utf-8"))
    bad = [r for r in rows if r.get("expect") == "fail"]
    good = [r for r in rows if r.get("expect") != "fail"]
    assert good and len(good) <= 3
    assert {(r["branch"], r["sha"], r["expected_check"]) for r in bad} == {
        (f["branch"], f["sha"], f["expected_failing_check"]) for f in fixtures["fixtures"]
    }
    # The rows are the handed-over selection, made the same way.
    assert [(r["branch"], r["sha"], r["label"]) for r in rows] == [
        (r["branch"], r["sha"], r["label"]) for r in rif.manifest(vendor)["selection"]
    ]
    assert all(r["sha"] in rif.manifest(vendor)["products"] for r in rows)


def test_a_revoked_certification_is_not_selected(tmp_path):
    vendor = _synthetic_vendor(tmp_path)
    first = rif.manifest(vendor)["selection"][0]
    revoked = vendor / "gate/.github/store_gate/revoked_certifications.json"
    data = json.loads(revoked.read_text(encoding="utf-8"))
    data["revoked"].append({"branch": first["branch"], "sha": first["sha"], "run_id": first["run_id"], "reason": "r", "evidence": "e"})
    revoked.write_text(json.dumps(data), encoding="utf-8")
    rows = rif.select(n=3, vendor=vendor, verify_first=False)
    assert first["branch"] not in [r["branch"] for r in rows]


def test_the_vendored_pass_rule_fails_a_flip_and_a_missed_known_bad(tmp_path):
    vendor = _synthetic_vendor(tmp_path / "handover")
    rows = rif.select(n=3, vendor=vendor)
    (tmp_path / "selection.json").write_text(json.dumps(rows), encoding="utf-8")

    def result(label, ok, failing=()):
        d = tmp_path / "results" / f"gate-replay-{label}"
        d.mkdir(parents=True, exist_ok=True)
        lines = [{"name": n, "status": "FAIL"} for n in failing] + [{"name": "health", "status": "PASS"}]
        (d / "store_gate.json").write_text(json.dumps({"ok": ok, "score": "x", "lines": lines}), encoding="utf-8")

    flips = rif.vendor_dir() / "gate/.github/store_gate/replay_flips.py"

    def run():
        return subprocess.run([sys.executable, str(flips), "selection.json", "results"], cwd=tmp_path, capture_output=True, text=True)

    for r in rows:
        if r.get("expect") == "fail":
            result(r["label"], False, [r["expected_check"]])
        else:
            result(r["label"], True)
    assert run().returncode == 0
    bad = next(r for r in rows if r.get("expect") == "fail")
    result(bad["label"], True)
    assert run().returncode == 1  # a known-bad that passes is a MISS
    result(bad["label"], False, [bad["expected_check"]])
    good = next(r for r in rows if r.get("expect") != "fail")
    result(good["label"], False, ["health"])
    assert run().returncode == 1  # certified -> red is a FLIP


# -- the gate job is store-gate.yml ------------------------------------------------


def test_the_gate_workflow_is_generated_from_the_vendored_store_gate():
    assert STORE_GATE_WORKFLOW.read_text(encoding="utf-8") == rif.generate_store_gate()


def test_every_gate_step_is_the_vendored_step_but_the_two_fetches():
    vendored = _yaml(rif.vendor_dir() / "gate/.github/workflows/store-gate.yml")["jobs"]["store-gate"]
    ours = _yaml(STORE_GATE_WORKFLOW)["jobs"]["store-gate"]
    for key in ("name", "runs-on", "timeout-minutes", "env", "services"):
        assert ours.get(key) == vendored.get(key), key
    mapped = {0, 2}  # the product checkout; the gate-scripts checkout
    theirs = [s for i, s in enumerate(vendored["steps"]) if i not in mapped]
    kept = [s for s in ours["steps"] if s in theirs]
    assert kept == theirs
    # What replaced the two fetches reads only the handover.
    added = [s for s in ours["steps"] if s not in theirs]
    for step in added:
        text = json.dumps(step)
        assert "replay_in_factory.py" in text or "actions/checkout" in text or "_factory_vendor" in text, step


def test_the_gate_workflow_is_called_only_as_a_replay():
    on = _yaml(STORE_GATE_WORKFLOW)["on"]
    assert set(on) == {"workflow_call"}
    inputs = on["workflow_call"]["inputs"]
    assert inputs["replay_label"]["required"] is True
    assert inputs["factory_ref"]["required"] is True
    # The vendored gate's permissions, less the commit-status write a replay
    # never makes.
    vendored = dict(_yaml(rif.vendor_dir() / "gate/.github/workflows/store-gate.yml")["permissions"])
    assert vendored.pop("statuses") == "write"
    assert _yaml(STORE_GATE_WORKFLOW)["permissions"] == vendored
    # The caller grants what the gate reads, and no more.
    granted = _yaml(REPLAY_WORKFLOW)["jobs"]["replay"]["permissions"]
    assert granted == vendored


def test_a_handover_vendored_under_another_builds_commit_fails_by_name(tmp_path):
    """The vendored directory names the cerebrum-builds commit whose gate is
    replayed; a copy whose manifest records another commit is STALE, by name."""
    vendor = _copy_vendor(tmp_path)
    renamed = vendor.with_name("c74f853d87e2809b2ad39de5ecb53a3aae370a35")
    vendor.rename(renamed)
    problems = rif.verify(renamed)
    assert any(p.startswith("STALE HANDOVER") and _manifest()["source_commit"] in p for p in problems), problems


# -- the empty-handover ruling (ORDERS 2026-10-28 0200Z #3) ------------------------
# (the 0-certified case is covered by test_with_no_certified_build_the_replay_is_not_applicable)


def test_a_handover_below_the_minimum_still_fails(tmp_path):
    """The gate is NOT weakened: the moment a certified build exists but the
    handover is below the configured minimum, select() still raises -- hand over
    more, never a silent pass. (The minimum lives in .github/gate_replay.json.)"""
    vendor = _copy_vendor(tmp_path)
    m = json.loads((vendor / "manifest.json").read_text(encoding="utf-8"))
    m["selection"] = [
        {
            "branch": "plt_one",
            "sha": "a" * 40,
            "expect": "pass",
            "run_id": "1",
            "certified_at": "2026-01-01T00:00:00Z",
        }
    ]
    (vendor / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    with pytest.raises(ValueError):
        rif.select(vendor=vendor, verify_first=False)
