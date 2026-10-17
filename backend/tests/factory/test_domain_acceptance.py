"""S12: ten business outcomes are performed through execute_action.

CRUD persist/read-back, list-only-persisted, queue process (not a no-op),
refused/failed action (not ok:true), idempotent duplicate-safe create,
unauthorized and missing-field rejection. LotDesk-class fixtures fail
this gate. LLM-authored route bodies stay forbidden.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.domain_acceptance import (
    OUTCOMES,
    reject_lotdesk_domain,
    render_work_queue,
)
from app.factory.build.roles import _coder_route_body
from app.factory.build.runner import RoleRunner
from tests.factory.conftest import stub_coder_patches

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    # Module-scoped: autouse monkeypatch has not run yet.
    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path_factory.mktemp("s12") / "build"
    # 0.5: without a coding agent the WRITER refuses (writer_no_output);
    # stub a deterministic agent so the S12 build still goes green.
    with stub_coder_patches():
        outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    return out


def _probe(built: Path, storage: Path, body: str) -> dict:
    script = (
        "import json, os, sys\n"
        f"os.environ['STORAGE_PATH'] = {str(storage)!r}\n"
        f"sys.path.insert(0, {str(built)!r})\n"
        + body
        + "\nprint('S12_PROBE=' + json.dumps(result))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=built,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("S12_PROBE=")]
    assert line, proc.stdout + proc.stderr
    return json.loads(line[-1].split("=", 1)[1])


def test_role_runner_emits_kernel_domain_ops_and_queue(built):
    ops = (built / "app" / "domain_ops.py").read_text(encoding="utf-8")
    assert "execute_action" in ops
    assert "async def perform_all" in ops
    for name in OUTCOMES:
        assert name in ops
    store = (built / "app" / "store.py").read_text(encoding="utf-8")
    assert "def update(" in store
    assert "def delete(" in store
    assert "CREATE TABLE" not in store
    queue = (built / "app" / "work_queue.py").read_text(encoding="utf-8")
    assert "def enqueue(" in queue
    assert "def mark(" in queue
    assert "PROCESSED" in queue
    routes = (built / "app" / "routes.py").read_text(encoding="utf-8")
    assert "from app.domain_ops import perform as perform_domain" in routes
    assert '@router.put("/analytics_surface/{item_id}")' in routes
    assert '@router.delete("/analytics_surface/{item_id}")' in routes
    assert '@router.post("/work_queue")' in routes
    assert "await perform_domain" in routes
    assert (built / "tests" / "test_domain_acceptance.py").is_file()
    harness = (built / "tests" / "test_domain_acceptance.py").read_text(encoding="utf-8")
    assert "@pytest.mark.pilot" in harness
    assert "perform_all" in harness
    doc = json.loads((built / "docs" / "domain_acceptance.json").read_text(encoding="utf-8"))
    assert doc["stage"] == "S12"
    assert len(doc["outcomes"]) == 10
    assert "PILOT_READY" not in (built / "docs" / "domain_acceptance.json").read_text(
        encoding="utf-8"
    )
    baseline = (built / "alembic" / "versions" / "0001_baseline.py").read_text(
        encoding="utf-8"
    )
    assert "work_queue" in baseline
    assert "idempotency" in baseline


def test_work_queue_source_coerces_item_ids():
    """Live PRODUCT: FastAPI / JSON handed str ids into get/claim/mark."""
    src = render_work_queue()
    assert "def _as_item_id" in src
    assert "return int(item_id)" in src
    assert "item_id = _as_item_id(item_id)" in src
    assert 'item["id"] = int(item["id"])' in src


def test_coder_route_body_stays_none_and_pilot_patch_is_absent():
    assert _coder_route_body(None, None, None) is None
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")
    assert "prepare_pilot_workspace" not in Path(runner_mod.__file__).read_text(
        encoding="utf-8"
    )


def test_ten_outcomes_are_performed_through_execute_action(built, tmp_path):
    result = _probe(
        built,
        tmp_path,
        "\n".join(
            [
                "import asyncio",
                "from app.migrations import upgrade_head",
                "from app.domain_ops import OUTCOMES, perform_all",
                "upgrade_head()",
                "result = asyncio.run(perform_all())",
                "result['named'] = list(OUTCOMES)",
            ]
        ),
    )
    assert result["kernel"] == "execute_action"
    assert result["named"] == list(OUTCOMES)
    assert result["ok"] is True, result
    assert result["failed"] == []
    assert result["performed"] == list(OUTCOMES)
    for name in OUTCOMES:
        assert result["outcomes"][name]["status"] == "performed", (name, result)


def test_lotdesk_fails_all_ten_outcomes_and_is_not_patched():
    result = reject_lotdesk_domain()
    assert result["ok"] is False
    assert result["lotdesk"] == "fixture only; not patched"
    assert result["f1_present"] is True
    assert result["f5_present"] is True
    assert result["f6_present"] is True
    assert set(result["failed"]) == set(OUTCOMES)
    assert result["performed"] == []
    for name in OUTCOMES:
        assert result["outcomes"][name]["status"] == "failed", name


def test_unknown_capability_is_rejected(built, tmp_path):
    result = _probe(
        built,
        tmp_path,
        "\n".join(
            [
                "import asyncio",
                "from app.migrations import upgrade_head",
                "from app.domain_ops import perform",
                "upgrade_head()",
                "result = asyncio.run(perform('create', 'typo', {'reference': 'x'}))",
            ]
        ),
    )
    assert result["status"] == "validation_error"
    assert result["error_code"] == "unknown_capability"
