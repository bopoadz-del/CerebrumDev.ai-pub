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


def test_registry_lists_exactly_the_four_chromadb_ids():
    rows = json.loads(REGISTRY.read_text(encoding="utf-8"))["adjudications"]
    ids = sorted(r["id"] for r in rows)
    assert ids == [
        "PYSEC-2026-311",
        "PYSEC-2026-3813",
        "PYSEC-2026-3814",
        "PYSEC-2026-3815",
    ]
    assert all(r["package"] == "chromadb" for r in rows)
    assert all(r["date"] == "2026-09-11" for r in rows)


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
