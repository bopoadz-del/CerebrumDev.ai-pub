"""S7: exactly one network posture — P1 offline strict."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.network_posture import (
    NETWORK_POSTURE,
    P1_CAPTURE_ADAPTER,
    P1_ENV_EXAMPLE,
    P1_SOCKET_BLOCKER_MARKERS,
    POSTURE_ID,
    REJECTED_ALTERNATIVES,
    apply_p1_capture_manifest,
    assert_workspace_posture,
)
from app.factory.build.roles import RoleContext, _CONFTEST, run_cloner
from app.factory.build.runner import RoleRunner
from app.factory.build.workspace import RoleWorkspace
from tests.factory.store_paths import store_block  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
MIRROR_CAPTURE_JSON = (
    store_block("capture") / "block.json"
)
MIRROR_CAPTURE_PY = store_block("capture") / "block.py"


def test_chosen_posture_is_p1():
    assert NETWORK_POSTURE == "P1"
    assert POSTURE_ID == "P1"
    assert "P2" in REJECTED_ALTERNATIVES
    assert "P3" in REJECTED_ALTERNATIVES
    assert "blocker" in REJECTED_ALTERNATIVES["P3"].lower()


def test_socket_blocker_markers_unchanged():
    for marker in P1_SOCKET_BLOCKER_MARKERS:
        assert marker in _CONFTEST
    assert "socket.socket.connect = _offline_connect" in _CONFTEST
    assert "P1" in _CONFTEST


def test_vendor_mirror_capture_json_defaults_are_p1():
    """Lock pins Store capture bytes; P1 is a CLONER rewrite, not the pin."""
    data = json.loads(MIRROR_CAPTURE_JSON.read_text(encoding="utf-8"))
    assert data["permissions"]["network"] is False
    rewritten = apply_p1_capture_manifest(data)
    providers = {
        item["name"]: item.get("default")
        for item in rewritten["inputs"]
        if isinstance(item, dict)
    }
    assert providers["llm_provider"] == "none"
    assert providers["ocr_engine"] == "tesseract"
    assert providers["ollama_base_url"] == ""
    assert providers["deepseek_model"] == ""
    assert providers["openrouter_model"] == ""
    assert providers["anthropic_model"] == ""
    assert providers["store_captures"] is False
    assert "get_block" in MIRROR_CAPTURE_PY.read_text(encoding="utf-8")


def test_apply_p1_manifest_does_not_enable_network():
    rewritten = apply_p1_capture_manifest(
        {
            "permissions": {"network": False},
            "inputs": [{"name": "llm_provider", "default": "deepseek"}],
        }
    )
    assert rewritten["permissions"]["network"] is False
    assert rewritten["inputs"][0]["default"] == "none"


def test_p1_capture_run_is_scripted_not_echo(tmp_path):
    path = tmp_path / "p1_capture.py"
    path.write_text(P1_CAPTURE_ADAPTER, encoding="utf-8")
    ns: dict = {}
    exec(path.read_text(encoding="utf-8"), ns)
    out = ns["run"](text="Reach me at ops@example.com https://local.test 42")
    assert out["posture"] == "P1"
    assert out["llm_provider"] == "none"
    assert out["raw_text"]
    assert "ops@example.com" in out["entities"]["emails"]
    assert out["capture_id"] != "Reach me at ops@example.com https://local.test 42"
    assert "import httpx" not in P1_CAPTURE_ADAPTER
    assert 'llm_provider": "none"' in P1_CAPTURE_ADAPTER or 'llm_provider": "none"' in str(out)


def test_cloner_emits_p1_capture_without_blocks_root(tmp_path):
    src = tmp_path / "src" / "capture"
    src.mkdir(parents=True)
    (src / "block.json").write_text(
        json.dumps(
            {
                "id": "capture",
                # The block DECLARES what it is; the P1 adapter follows
                # the declaration, never the id.
                "capability_class": "vision_capture",
                "permissions": {"network": False},
                "inputs": [
                    {"name": "llm_provider", "default": "deepseek"},
                    {"name": "ocr_engine", "default": "tesseract"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (src / "block.py").write_text(
        "from app.blocks import get_block\n\ndef run(**kwargs):\n    return get_block('capture')()\n",
        encoding="utf-8",
    )
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=None,
        state={"resolved_blocks": ("capture",)},
    )
    import app.factory.build.roles as roles_mod

    original = roles_mod._block_source_dir
    roles_mod._block_source_dir = lambda bid, root: src
    try:
        result = run_cloner(ctx)
    finally:
        roles_mod._block_source_dir = original
    assert result.ok, result.detail
    shipped = ws.read_text(Path("vendor") / "blocks" / "capture" / "block.py")
    assert "P1" in shipped
    assert "get_block" not in shipped
    meta = json.loads(ws.read_text(Path("vendor") / "blocks" / "capture" / "block.json"))
    defaults = {i["name"]: i.get("default") for i in meta["inputs"]}
    assert defaults["llm_provider"] == "none"
    assert meta["permissions"]["network"] is False


def test_role_runner_tree_is_p1(tmp_path, monkeypatch, stub_coder):
    out = tmp_path / "build"
    result = RoleRunner(load_blueprint(SMOKE), out).run()
    assert result.ok, result.to_dict()
    text = (out / ".env.example").read_text(encoding="utf-8")
    assert text == P1_ENV_EXAMPLE
    # F1: the deploy token line is the deterministic placeholder, never
    # the world-known dev literal; runtime refuses the placeholder.
    assert "PLATFORM_TOKEN=set-at-deploy" in text
    assert "dev-local-token" not in text
    assert_workspace_posture(out)


def test_staged_writer_reads_destination_readme(tmp_path):
    """Rework does not rewrite README; posture must see the previous stamp."""
    stage = tmp_path / "stage"
    dest = tmp_path / "dest"
    stage.mkdir()
    dest.mkdir()
    (dest / "README.md").write_text("# LotDesk\nNETWORK_POSTURE: P1\n", encoding="utf-8")
    for name in ("Dockerfile", ".env.example", "requirements.txt"):
        (stage / name).write_text("NETWORK_POSTURE=P1\n", encoding="utf-8")
    # Was render.yaml. The deploy contract carries the posture now; Render is
    # gone and the platform no longer ships a blueprint for it.
    (stage / "deploy").mkdir()
    (stage / "deploy" / "contract.json").write_text(
        '{"schema": "cerebrum.deploy.v1", "network_posture": "P1"}\n', encoding="utf-8"
    )
    (stage / "app").mkdir()
    (stage / "app" / "main.py").write_text("NETWORK_POSTURE = 'P1'\n", encoding="utf-8")
    (stage / "docs").mkdir()
    (stage / "docs" / "network_posture.json").write_text(
        '{"posture":"P1","reason":"Delivered platforms run in-process against vendored blocks; local/scripted OCR only; no Store URL, no cloud LLM, no Ollama, no outbound HTTP at runtime."}\n',
        encoding="utf-8",
    )
    (stage / "docs" / "build_provenance.json").write_text(
        '{"network_posture":"P1"}\n', encoding="utf-8"
    )
    assert_workspace_posture(stage, fallback=dest)


def test_missing_artifact_is_fail_closed(tmp_path):
    (tmp_path / "Dockerfile").write_text("NETWORK_POSTURE=P1\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("NETWORK_POSTURE=P1\n", encoding="utf-8")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.py").write_text("NETWORK_POSTURE = 'P1'\n", encoding="utf-8")
    from app.factory.build.network_posture import PostureError

    with pytest.raises(PostureError):
        assert_workspace_posture(tmp_path)


def test_p1_is_judged_by_structure_not_by_service_names(tmp_path):
    """An outbound target is caught by what it IS (a datastore, a non-loopback
    URL in a setting or in code), whatever service it names; prose and
    loopback are not targets."""
    from app.factory.build.network_posture import PostureError, outbound_url

    assert outbound_url("https://zorblat.example/v9/run") == "https://zorblat.example/v9/run"
    assert outbound_url("http://127.0.0.1:8000/health") == ""
    assert outbound_url("http://localhost:11434") == ""
    assert outbound_url("/app/data") == ""

    root = tmp_path / "ws"
    (root / "deploy").mkdir(parents=True)
    (root / "app").mkdir()
    (root / "docs").mkdir()
    doc = {"posture": NETWORK_POSTURE, "reason": __import__(
        "app.factory.build.network_posture", fromlist=["x"]).NETWORK_POSTURE_REASON}
    (root / "docs" / "network_posture.json").write_text(json.dumps(doc), encoding="utf-8")
    for rel in ("README.md", "requirements.txt", "docs/build_provenance.json"):
        (root / rel).write_text("P1 -- mentions https://zorblat.example in prose\n", encoding="utf-8")
    (root / "Dockerfile").write_text("# P1\nENV QUUX_URL=https://zorblat.example\n", encoding="utf-8")
    (root / ".env.example").write_text("# P1\n# FROB_URL=https://zorblat.example\nSTORAGE_PATH=./data\n", encoding="utf-8")
    (root / "app" / "main.py").write_text(
        '"""P1 docs may name https://zorblat.example."""\nNETWORK_POSTURE = "P1"\nBASE = "https://frob.example/run"\n',
        encoding="utf-8",
    )
    (root / "deploy" / "contract.json").write_text(
        json.dumps({"network_posture": "P1", "datastores": [{"kind": "zorbdb"}], "environment": {}}),
        encoding="utf-8",
    )
    with pytest.raises(PostureError) as caught:
        assert_workspace_posture(root)
    message = str(caught.value)
    assert "Dockerfile: P1 forbids outbound setting QUUX_URL" in message
    assert "app/main.py: P1 forbids outbound URL https://frob.example/run" in message
    assert "datastores" in message
    assert ".env.example" not in message  # a commented example is documentation
    assert "README.md" not in message  # prose is never searched
