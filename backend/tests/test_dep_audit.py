"""Dependency-audit twins: dated note + live constraint, never a bare ignore.

S03. The previous miss was a workplan that invented a ``click`` ceiling.
``gTTS 2.5.4`` really declares ``click<8.2`` — and ``gTTS`` is not a
factory runtime pin. These tests read declared Requires-Dist, not a
scanner blurb.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

import pytest
from packaging.version import Version

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "dep_audit.py"
REGISTRY = (
    REPO_ROOT / "docs" / "security" / "dependency-adjudications" / "registry.json"
)
NOTE = (
    REPO_ROOT
    / "docs"
    / "security"
    / "dependency-adjudications"
    / "2026-09-11-chromadb.md"
)
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
REQ = REPO_ROOT / "backend" / "requirements.txt"
LOCK = REPO_ROOT / "backend" / "requirements.lock"
FRONTEND_LOCK = REPO_ROOT / "frontend" / "package-lock.json"


def _load_script():
    spec = importlib.util.spec_from_file_location("dep_audit", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    return _load_script()


def _run(*args: str, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd or REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ci_jobs():
    return (yaml.safe_load(CI_YML.read_text(encoding="utf-8")).get("jobs") or {})


def _job_runs(job: dict) -> str:
    return "\n".join(str(s.get("run") or "") for s in (job.get("steps") or []))


def _pin(name: str, text: str) -> str:
    key = name.lower().replace("-", "-")
    for line in text.splitlines():
        raw = line.split("#", 1)[0].strip()
        if not raw or "==" not in raw:
            continue
        pkg, ver = raw.split("==", 1)
        if pkg.lower().replace("_", "-") == key:
            return ver
    raise AssertionError(f"{name} pin missing")


# -- registry + notes ------------------------------------------------------


def test_every_registry_row_has_a_dated_note_naming_the_id():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    rows = registry.get("adjudications") or []
    assert rows, "registry has no adjudications"
    for row in rows:
        date.fromisoformat(row["date"])
        note = REPO_ROOT / row["note"]
        assert note.is_file(), row["note"]
        text = note.read_text(encoding="utf-8")
        assert row["id"] in text
        assert row["date"] in text
        assert row["package"] in text
        assert "accept_until_fix" in row["decision"] or "Decision:" in text


def test_chromadb_note_covers_the_four_linux_ids():
    text = NOTE.read_text(encoding="utf-8")
    for vid in (
        "PYSEC-2026-311",
        "PYSEC-2026-3813",
        "PYSEC-2026-3814",
        "PYSEC-2026-3815",
    ):
        assert f"## {vid}" in text, vid
    assert "2026-09-11" in text
    assert "PersistentClient" in text
    assert "fix_versions" in text


def test_registry_lists_exactly_chromadb_adjudications():
    # Exactly one adjudicated package remains; any other row is a rogue
    # suppression and must fail here. chromadb = the four Linux-resolve ids
    # (no fixed version). The pillow accept_risk from ORDERS_0400Z is GONE:
    # fastembed 0.9.1 relaxes the transitive constraint to pillow<14, so
    # pillow==13.0.0 ships and clears the 37 image-codec CVEs on merit. Its
    # fix-on-merit is guarded by the probe-determinism test, not an adjudication.
    # picomatch (2026-11-17) is the one dated, expiring npm row; its twin is
    # the picomatch section below.
    rows = json.loads(REGISTRY.read_text(encoding="utf-8"))["adjudications"]
    by_pkg: dict[str, list] = {}
    for r in rows:
        by_pkg.setdefault(r["package"], []).append(r)
    assert set(by_pkg) == {"chromadb", "picomatch"}, sorted(by_pkg)
    assert "pillow" not in by_pkg, "pillow must be fixed on merit, not adjudicated"

    assert sorted(r["id"] for r in by_pkg["chromadb"]) == [
        "PYSEC-2026-311",
        "PYSEC-2026-3813",
        "PYSEC-2026-3814",
        "PYSEC-2026-3815",
    ]
    assert all(r["date"] == "2026-09-11" for r in by_pkg["chromadb"])


# -- adjudicate against declared metadata, not a scanner summary -----------


def test_fastapi_and_starlette_do_not_ceiling_python_multipart():
    """The six PYSEC rows were upgradeable. A ceiling would have been a lie."""
    for dist in ("fastapi", "starlette"):
        reqs = metadata.requires(dist) or []
        multipart = [r for r in reqs if r.lower().startswith("python-multipart")]
        assert multipart, f"{dist} no longer declares python-multipart"
        for req in multipart:
            assert "<" not in req.split(";")[0], (
                f"{dist} invented a python-multipart ceiling: {req}"
            )
            assert ">=0.0.18" in req


def test_python_multipart_pin_clears_the_highest_published_fix():
    """Highest fix on the Linux resolve was 0.0.31. 0.0.32 is latest."""
    assert Version(_pin("python-multipart", REQ.read_text(encoding="utf-8"))) >= Version(
        "0.0.32"
    )
    assert Version(_pin("python-multipart", LOCK.read_text(encoding="utf-8"))) >= Version(
        "0.0.32"
    )


def test_huggingface_hub_declares_click_below_nine_not_below_eight_two():
    """gTTS 2.5.4 caps click<8.2. That package is not in this graph."""
    reqs = metadata.requires("huggingface_hub") or []
    click_reqs = [r for r in reqs if r.lower().startswith("click")]
    assert click_reqs, "huggingface_hub dropped its click pin"
    joined = " ".join(click_reqs)
    assert "<9.0.0" in joined
    assert ">=8.4.2" in joined
    assert "<8.2" not in joined


def test_factory_runtime_does_not_install_gtts():
    """Store mapping is not a factory pin. Do not inherit gTTS's click cap."""
    with pytest.raises(metadata.PackageNotFoundError):
        metadata.version("gTTS")
    req_text = REQ.read_text(encoding="utf-8").lower()
    assert "gtts" not in req_text
    from app.factory.build.block_obligations import DISTRIBUTIONS

    assert DISTRIBUTIONS["gtts"] == "gTTS"


def test_click_pin_is_already_past_the_real_fix():
    """The real prior fix was 8.3.3, not a ceiling."""
    assert Version(_pin("click", REQ.read_text(encoding="utf-8"))) >= Version("8.3.3")


def test_chromadb_pin_is_the_published_latest_and_embedded_only():
    assert _pin("chromadb", REQ.read_text(encoding="utf-8")) == "1.5.9"
    sources = [
        REPO_ROOT / "backend" / "app" / "core" / "chroma_store.py",
        REPO_ROOT / "backend" / "app" / "core" / "packager.py",
        REPO_ROOT / "backend" / "app" / "core" / "platform_packager.py",
    ]
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sources)
    assert "PersistentClient" in joined
    assert "HttpClient" not in joined
    assert "trust_remote_code" not in joined


# -- script mutations (synthetic scanner JSON, no live feed) ---------------


def test_undocumented_finding_exits_1(audit, tmp_path):
    pip = _write_json(
        tmp_path / "pip.json",
        {
            "dependencies": [
                {
                    "name": "demo",
                    "version": "1.0.0",
                    "vulns": [{"id": "PYSEC-2099-1", "fix_versions": ["1.0.1"], "aliases": []}],
                }
            ]
        },
    )
    npm = _write_json(tmp_path / "npm.json", {"vulnerabilities": {}})
    proc = _run("--pip-json", str(pip), "--npm-json", str(npm))
    assert proc.returncode == 1, proc.stderr
    assert "undocumented" in proc.stderr
    assert "PYSEC-2099-1" in proc.stderr
    assert "--ignore-vuln" in proc.stderr


def test_adjudicated_finding_with_empty_fix_exits_0(audit, tmp_path):
    pip = _write_json(
        tmp_path / "pip.json",
        {
            "dependencies": [
                {
                    "name": "chromadb",
                    "version": "1.5.9",
                    "vulns": [
                        {
                            "id": "PYSEC-2026-311",
                            "fix_versions": [],
                            "aliases": ["CVE-2026-45829"],
                        }
                    ],
                }
            ]
        },
    )
    npm = _write_json(tmp_path / "npm.json", {"vulnerabilities": {}})
    proc = _run("--pip-json", str(pip), "--npm-json", str(npm))
    assert proc.returncode == 0, proc.stderr
    assert "ok:" in proc.stdout


def test_stale_adjudication_when_a_fix_appears_exits_1(audit, tmp_path):
    """A dated note is not a forever ignore. A published fix must ship."""
    pip = _write_json(
        tmp_path / "pip.json",
        {
            "dependencies": [
                {
                    "name": "chromadb",
                    "version": "1.5.9",
                    "vulns": [
                        {
                            "id": "PYSEC-2026-311",
                            "fix_versions": ["1.5.10"],
                            "aliases": ["CVE-2026-45829"],
                        }
                    ],
                }
            ]
        },
    )
    npm = _write_json(tmp_path / "npm.json", {"vulnerabilities": {}})
    proc = _run("--pip-json", str(pip), "--npm-json", str(npm))
    assert proc.returncode == 1, proc.stdout
    assert "stale adjudication" in proc.stderr
    assert "1.5.10" in proc.stderr


def test_missing_note_file_exits_1(audit, tmp_path):
    registry = {
        "adjudications": [
            {
                "id": "PYSEC-2099-2",
                "aliases": [],
                "package": "demo",
                "date": "2026-09-11",
                "note": "docs/security/dependency-adjudications/no-such-note.md",
                "decision": "accept_until_fix",
            }
        ]
    }
    reg = _write_json(tmp_path / "registry.json", registry)
    pip = _write_json(tmp_path / "pip.json", {"dependencies": []})
    npm = _write_json(tmp_path / "npm.json", {"vulnerabilities": {}})
    proc = _run(
        "--pip-json",
        str(pip),
        "--npm-json",
        str(npm),
        "--registry",
        str(reg),
        "--repo",
        str(REPO_ROOT),
    )
    assert proc.returncode == 1, proc.stdout
    assert "note missing" in proc.stderr


def test_evaluate_rejects_a_suppress_decision(audit):
    registry = {
        "adjudications": [
            {
                "id": "PYSEC-2099-3",
                "aliases": [],
                "package": "demo",
                "date": "2026-09-11",
                "note": "docs/security/dependency-adjudications/2026-09-11-chromadb.md",
                "decision": "suppress",
            }
        ]
    }
    errors = audit.evaluate([], registry, repo=REPO_ROOT)
    assert any("bare suppression" in e for e in errors)


def test_duplicate_pip_audit_rows_collapse_to_one(audit):
    """PYSEC-2026-311 is emitted twice. That is one advisory, not two."""
    findings = audit.findings_from_pip_audit(
        {
            "dependencies": [
                {
                    "name": "chromadb",
                    "version": "1.5.9",
                    "vulns": [
                        {"id": "PYSEC-2026-311", "fix_versions": [], "aliases": []},
                        {"id": "PYSEC-2026-311", "fix_versions": [], "aliases": []},
                    ],
                }
            ]
        }
    )
    assert len(findings) == 1
    assert findings[0].vuln_id == "PYSEC-2026-311"


# -- CI wiring -------------------------------------------------------------


def test_ci_has_fail_closed_python_and_npm_audit_jobs():
    jobs = _ci_jobs()
    for name in ("backend-dep-audit", "frontend-dep-audit"):
        assert name in jobs, f"ci.yml lost {name}"
        job = jobs[name]
        assert job.get("continue-on-error") in (None, False)
        assert job.get("runs-on") == "ubuntu-latest"
        runs = _job_runs(job)
        assert "dep_audit.py" in runs
        for step in job.get("steps") or []:
            assert step.get("continue-on-error") in (None, False)


def test_wrapper_audits_requirements_txt_not_the_runner_env(audit):
    """GHA's setuptools (PYSEC-2026-3447) is not a factory pin.

    Mutation killed: dropping ``-r requirements.txt`` and scanning the
    local env, then papering over the extra finding with --ignore-vuln.
    """
    argv = audit.pip_audit_argv(REPO_ROOT / "backend" / "requirements.txt")
    joined = " ".join(argv)
    assert "-r" in argv
    assert str(REPO_ROOT / "backend" / "requirements.txt") in argv
    assert "--ignore-vuln" not in joined
    req = REQ.read_text(encoding="utf-8").lower()
    assert "setuptools" not in req


def test_ci_python_job_runs_pip_audit_via_the_wrapper():
    runs = _job_runs(_ci_jobs()["backend-dep-audit"])
    assert "--python" in runs
    assert "dep_audit.py" in runs
    assert "--ignore-vuln" not in runs


def test_ci_npm_job_runs_npm_audit_via_the_wrapper():
    job = _ci_jobs()["frontend-dep-audit"]
    runs = _job_runs(job)
    assert "--npm" in runs
    assert any("npm ci" in str(s.get("run") or "") for s in (job.get("steps") or []))


def test_workflows_have_no_bare_ignore(audit):
    for path in (
        CI_YML,
        REPO_ROOT / ".github" / "workflows" / "dependabot-automerge.yml",
    ):
        hits = audit.workflow_has_bare_ignore(path.read_text(encoding="utf-8"))
        assert hits == [], hits


def test_npm_lockfile_clears_the_three_dev_advisories():
    """The Linux npm resolve had three fixable transitives. Upgrade, don't note."""
    lock = FRONTEND_LOCK.read_text(encoding="utf-8")
    data = json.loads(lock)
    packages = data.get("packages") or {}
    baseline = packages["node_modules/baseline-browser-mapping"]["version"]
    braces = packages["node_modules/brace-expansion"]["version"]
    browsers = packages["node_modules/browserslist"]["version"]
    assert Version(baseline) >= Version("2.11.0")
    assert Version(braces) >= Version("5.0.9")
    assert Version(browsers) >= Version("4.28.7")


# -- picomatch GHSA-fc5q-39xc-vjpf (2026-11-17): dev tooling, 14-day expiry --

PICOMATCH_ID = "GHSA-fc5q-39xc-vjpf"
PICOMATCH_NOTE = (
    REPO_ROOT
    / "docs"
    / "security"
    / "dependency-adjudications"
    / "2026-11-17-picomatch.md"
)
FRONTEND_PROD_DOCKERFILE = REPO_ROOT / "frontend" / "Dockerfile.prod"


def _picomatch_row() -> dict:
    rows = json.loads(REGISTRY.read_text(encoding="utf-8"))["adjudications"]
    hits = [r for r in rows if r["package"] == "picomatch"]
    assert len(hits) == 1, hits
    return hits[0]


def _lock_packages() -> dict:
    return json.loads(FRONTEND_LOCK.read_text(encoding="utf-8"))["packages"]


def _resolve(packages: dict, parent: str, name: str) -> str | None:
    """Node's lookup: nested node_modules first, then each ancestor."""
    base = parent
    while True:
        key = f"{base}/node_modules/{name}" if base else f"node_modules/{name}"
        if key in packages:
            return key
        if not base:
            return None
        cut = base.rfind("/node_modules/")
        base = base[:cut] if cut >= 0 else ""


def test_picomatch_row_expires_within_fourteen_days_and_is_not_open_ended():
    row = _picomatch_row()
    assert row["id"] == PICOMATCH_ID
    assert row["date"] == "2026-11-17"
    granted = date.fromisoformat(row["date"])
    expires = date.fromisoformat(row["expires"])
    assert 0 < (expires - granted).days <= 14
    assert row["decision"] not in ("suppress", "accept_until_fix")
    text = PICOMATCH_NOTE.read_text(encoding="utf-8")
    assert f"## {PICOMATCH_ID}" in text
    assert row["expires"] in text
    assert "first_patched_version: null" in text


def test_every_locked_picomatch_is_dev_only_and_inside_the_advisory_range():
    """A row for a copy that is no longer vulnerable is a stale row: delete it."""
    packages = _lock_packages()
    copies = {k: v for k, v in packages.items() if k.endswith("node_modules/picomatch")}
    assert copies, "picomatch left the lockfile: delete the adjudication row"
    for key, info in copies.items():
        assert info.get("dev") is True, f"{key} is not dev-only"
        assert Version(info["version"]) <= Version("4.0.7"), (
            f"{key} is {info['version']}: outside the advisory range, delete the row"
        )


def test_runtime_dependencies_never_reach_picomatch():
    packages = _lock_packages()
    root = packages[""]
    seen: set[str] = set()
    stack: list[str] = []
    for name in root.get("dependencies") or {}:
        key = _resolve(packages, "", name)
        assert key, name
        stack.append(key)
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        info = packages[key]
        for name in {**(info.get("dependencies") or {}), **(info.get("peerDependencies") or {})}:
            child = _resolve(packages, key, name)
            if child is not None:
                stack.append(child)
    assert seen, "no runtime dependency resolved"
    assert not [k for k in seen if k.endswith("node_modules/picomatch")]


def test_prod_image_runtime_stage_installs_no_node_modules():
    text = FRONTEND_PROD_DOCKERFILE.read_text(encoding="utf-8")
    runtime = text.split("AS runtime", 1)[1]
    assert "npm " not in runtime
    assert "node_modules" not in runtime
    copies = [ln.split()[2] for ln in runtime.splitlines() if ln.startswith("COPY ")]
    assert copies == [
        "/app/dist",
        "/app/serve.mjs",
        "/app/security-headers.json",
        "/app/package.json",
    ]


def _picomatch_npm_json() -> dict:
    return {
        "vulnerabilities": {
            "picomatch": {
                "range": "*",
                "via": [
                    {
                        "name": "picomatch",
                        "url": f"https://github.com/advisories/{PICOMATCH_ID}",
                        "range": "<=4.0.7",
                    }
                ],
                "fixAvailable": {"name": "vite", "version": "6.2.7", "isSemVerMajor": True},
            }
        }
    }


def test_adjudicated_picomatch_finding_exits_0(audit, tmp_path):
    pip = _write_json(tmp_path / "pip.json", {"dependencies": []})
    npm = _write_json(tmp_path / "npm.json", _picomatch_npm_json())
    proc = _run("--pip-json", str(pip), "--npm-json", str(npm))
    assert proc.returncode == 0, proc.stderr


def test_picomatch_row_goes_red_when_its_expiry_passes(audit, tmp_path):
    row = dict(_picomatch_row())
    row["expires"] = "2026-01-01"
    reg = _write_json(tmp_path / "registry.json", {"adjudications": [row]})
    pip = _write_json(tmp_path / "pip.json", {"dependencies": []})
    npm = _write_json(tmp_path / "npm.json", _picomatch_npm_json())
    proc = _run(
        "--pip-json",
        str(pip),
        "--npm-json",
        str(npm),
        "--registry",
        str(reg),
        "--repo",
        str(REPO_ROOT),
    )
    assert proc.returncode == 1, proc.stdout
    assert "adjudication expired 2026-01-01" in proc.stderr
