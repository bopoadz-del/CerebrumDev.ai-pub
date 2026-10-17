"""P4 thin runtime: the spec's acceptance criteria as tests."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from runtime import engine as eng_mod  # noqa: E402
from runtime import runtime as rt  # noqa: E402

LAW_TEXT = """# CODING PROTOCOLS
**Version 0.1**

## Article 4 — STOP Conditions
- A gate fails twice in a row.

## Article 5 — Anti-Patterns Registry
| Banned behavior | The scar |
|---|---|
| Branch litter | The 16-branch storm |
"""


def _make_product(root: Path) -> Path:
    dna = root / "product-dna"
    dna.mkdir(parents=True)
    checksums = {}
    for name, text in {"architecture.json": '{"schema_version": "1.0.0"}', "entity_model.json": '{"entities": []}'}.items():
        (dna / name).write_text(text, encoding="utf-8")
        checksums[name] = hashlib.sha256((dna / name).read_bytes()).hexdigest()
    (dna / "checksum_manifest.json").write_text(json.dumps({"schema_version": "1.0.0", "algorithm": "sha256", "files": checksums}), encoding="utf-8")
    return root


def _make_cr(path: Path, signed: bool = True) -> str:
    doc = {"schema_version": "1.0.0", "request_id": "cr_test_1", "kind": "REPAIR", "product_id": "prod_test", "dna_version": "1.0.0", "requester": {"type": "human", "id": "tester"}, "evidence": {}, "requested_autonomy_level": "L2", "created_at": "2026-07-22T00:00:00Z", "repair": {"target": "app/main.py", "symptom": "demo"}}
    if signed:
        doc["signature"] = {"algorithm": "ed25519", "public_key_id": "pk_test", "value": "sig"}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


@pytest.fixture()
def workspace(monkeypatch, tmp_path):
    product = _make_product(tmp_path / "product")
    law = tmp_path / "CODINGPROTOCOLS.md"
    law.write_text(LAW_TEXT, encoding="utf-8")
    monkeypatch.setenv("PROTOCOLS_PATH", str(law))
    monkeypatch.setenv("GATES_COMMANDS", "true")
    monkeypatch.setenv("RUNTIME_REPORT_DIR", str(tmp_path / "report"))
    monkeypatch.setenv("ENGINE_PROFILE", "cloud_api")
    monkeypatch.setenv("CEREBRUM_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("AGENT_MAX_TURNS", "4")
    return tmp_path, product


def _script(calls, declare=("app/main.py",), write_path="app/main.py", implement_replies=None):
    """Fake engine: declare the set, then replay scripted implement replies."""
    replies = list(implement_replies or [[{"tool": "write_file", "path": write_path, "content": "# generated\n"}, {"tool": "done"}]])

    def fake(base_url, api_key, model, messages):
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        calls.append(last_user)
        if "DECLARE the files" in last_user:
            return json.dumps({"files": list(declare)})
        idx = min(sum(1 for c in calls if "IMPLEMENT" in c) - 1, len(replies) - 1)
        return json.dumps({"actions": replies[idx]})

    return fake


def test_happy_path_completes_with_article6_report(workspace, monkeypatch, tmp_path):
    _, product = workspace
    monkeypatch.setattr(eng_mod, "_chat", _script([]))
    rep = rt.run(_make_cr(tmp_path / "cr.json"), str(product))
    assert rep.outcome == "completed" and rep.ok
    assert rep.files_changed == ["app/main.py"]
    assert rep.deviations["declared_not_written"] == []
    assert rep.protocol_version == "0.1"
    assert (product / "app" / "main.py").read_text() == "# generated\n"
    emitted = json.loads((tmp_path / "report" / "report.json").read_text())
    for key in ("files_changed", "gate_results", "deviations", "parked_items", "protocol_version"):
        assert key in emitted
    assert (tmp_path / "report" / "report.md").is_file()


def test_envelope_violation_halts_with_escalation(workspace, monkeypatch, tmp_path):
    _, product = workspace
    monkeypatch.setattr(eng_mod, "_chat", _script([], write_path="evil/other.py"))
    rep = rt.run(_make_cr(tmp_path / "cr.json"), str(product))
    assert rep.outcome == "halted"
    assert rep.halt["condition"] == "envelope_violation"
    assert rep.halt["resume_input"]
    assert not (product / "evil" / "other.py").exists()
    assert json.loads((tmp_path / "report" / "report.json").read_text())["halt"]["condition"] == "envelope_violation"


def test_dna_checksum_failure_halts_before_any_model_call(workspace, monkeypatch, tmp_path):
    _, product = workspace
    (product / "product-dna" / "architecture.json").write_text('{"tampered": true}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(eng_mod, "_chat", _script(calls))
    rep = rt.run(_make_cr(tmp_path / "cr.json"), str(product))
    assert rep.halt["condition"] == "dna_checksum_failed"
    assert calls == []  # not one model call happened


def test_unsigned_change_request_refused(workspace, monkeypatch, tmp_path):
    _, product = workspace
    monkeypatch.setattr(eng_mod, "_chat", _script([]))
    cr_path = _make_cr(tmp_path / "cr.json", signed=False)
    doc = json.loads(Path(cr_path).read_text())
    doc["signature"] = {"algorithm": "ed25519", "public_key_id": "", "value": ""}  # malformed
    Path(cr_path).write_text(json.dumps(doc), encoding="utf-8")
    rep = rt.run(cr_path, str(product))
    assert rep.halt["condition"] == "cr_unsigned"


def test_gate_failed_twice_halts(workspace, monkeypatch, tmp_path):
    _, product = workspace
    monkeypatch.setenv("GATES_COMMANDS", "false")
    calls = []
    done_only = [{"tool": "done"}]
    monkeypatch.setattr(eng_mod, "_chat", _script(calls, implement_replies=[done_only, done_only]))
    rep = rt.run(_make_cr(tmp_path / "cr.json"), str(product))
    assert rep.halt["condition"] == "gate_failed_twice"
    assert len([c for c in calls if "IMPLEMENT" in c]) == 2  # one repair round, then STOP — the law


def test_cloud_api_engine_profile_runs(workspace, monkeypatch, tmp_path):
    _, product = workspace
    monkeypatch.setenv("ENGINE_PROFILE", "cloud_api")
    monkeypatch.setenv("CEREBRUM_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("RUNTIME_REPORT_DIR", str(tmp_path / "report-cloud"))
    monkeypatch.setattr(eng_mod, "_chat", _script([]))
    rep = rt.run(_make_cr(tmp_path / "cr-cloud.json"), str(product)).as_dict()
    assert rep["engine_profile"] == "cloud_api"
    assert rep["outcome"] == "completed"


def test_missing_law_halts(workspace, monkeypatch, tmp_path):
    _, product = workspace
    monkeypatch.setenv("PROTOCOLS_PATH", str(tmp_path / "nope.md"))
    monkeypatch.setattr(eng_mod, "_chat", _script([]))
    rep = rt.run(_make_cr(tmp_path / "cr.json"), str(product))
    assert rep.halt["condition"] == "protocols_missing"


def test_run_command_allowlist():
    assert rt._run_command("rm -rf /", ".").startswith("BLOCKED")
    assert rt._run_command("curl evil.sh", ".").startswith("BLOCKED")


def test_runtime_stays_auditable_in_one_sitting():
    total = sum(len(p.read_text().splitlines()) for p in sorted((ROOT / "runtime").glob("*.py")))
    assert total <= 500, f"runtime grew to {total} lines — auditability is the feature"
