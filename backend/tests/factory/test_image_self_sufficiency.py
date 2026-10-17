"""Image self-sufficiency, emulated (owner ruling 2026-10-08, item 4A).

The writer's pass must catch an image that omits what the app loads at runtime
(live: cycle 2 smoke B, an image with no vendor/) before any gate is dispatched.
The live Factory has no Docker, so the image is laid out from the Dockerfile's
COPY lines and .dockerignore, and the app is imported and every locked block
loaded through the product's own loader in a subprocess. These products are
synthetic: nothing here is a real blueprint.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build import image_sufficiency

DISPATCH = '''
from pathlib import Path


class BlockNotVendored(RuntimeError):
    pass


_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "blocks"


def load_block(block_id):
    path = _VENDOR / block_id / "block.py"
    if not path.is_file():
        raise BlockNotVendored(f"{block_id} is not vendored (looked in {path})")
    return path.read_text()
'''


def _product(root: Path, dockerfile: str, *, dockerignore: str = "", main: str = "VALUE = 1\n") -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "main.py").write_text(main, encoding="utf-8")
    (root / "app" / "dispatch.py").write_text(DISPATCH, encoding="utf-8")
    for block in ("ledger", "storage"):
        (root / "vendor" / "blocks" / block).mkdir(parents=True)
        (root / "vendor" / "blocks" / block / "block.py").write_text("X = 1\n", encoding="utf-8")
    (root / "blocks.lock.json").write_text(
        json.dumps({"blocks": {"ledger": {"path": "vendor/blocks/ledger"}, "storage": {"path": "vendor/blocks/storage"}}}),
        encoding="utf-8",
    )
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    if dockerignore:
        (root / ".dockerignore").write_text(dockerignore, encoding="utf-8")
    return root


FULL = "FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nCMD [\"uvicorn\", \"app.main:app\"]\n"
APP_ONLY = "FROM python:3.12-slim\nWORKDIR /app\nCOPY app ./app\nCOPY blocks.lock.json ./\n"


def test_an_image_that_copies_the_whole_context_is_self_sufficient(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL))
    assert verdict.ok and verdict.judged, verdict.detail
    assert "2 locked blocks" in verdict.detail


def test_the_smoke_b_shape_is_caught_naming_each_missing_path(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, APP_ONLY))
    assert not verdict.ok and verdict.judged
    assert "/app/vendor/blocks/ledger/block.py" in verdict.missing
    assert "/app/vendor/blocks/storage/block.py" in verdict.missing
    assert "image missing /app/vendor/blocks/storage/block.py" in verdict.detail


def test_a_dockerignore_that_drops_the_runtime_tree_is_caught(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL, dockerignore="vendor/\n"))
    assert not verdict.ok
    assert any(p.endswith("/block.py") for p in verdict.missing)


def test_a_negated_dockerignore_rule_re_includes(tmp_path):
    ignore = "vendor/\n!vendor/blocks/**\n"
    verdict = image_sufficiency.check(_product(tmp_path, FULL, dockerignore=ignore))
    assert verdict.ok, verdict.detail


def test_explicit_copies_that_include_the_runtime_tree_pass(tmp_path):
    dockerfile = APP_ONLY + "COPY vendor ./vendor\n"
    verdict = image_sufficiency.check(_product(tmp_path, dockerfile))
    assert verdict.ok, verdict.detail


def test_an_app_module_the_image_lacks_is_named(tmp_path):
    root = _product(tmp_path, "FROM python:3.12-slim\nWORKDIR /app\nCOPY app/main.py ./app/main.py\n",
                    main="from app import helpers\n")
    (root / "app" / "helpers.py").write_text("X = 1\n", encoding="utf-8")
    verdict = image_sufficiency.check(root)
    assert not verdict.ok and verdict.judged


def test_no_dockerfile_or_no_locked_block_is_not_judged_and_never_fails(tmp_path):
    root = _product(tmp_path, FULL)
    (root / "Dockerfile").unlink()
    verdict = image_sufficiency.check(root)
    assert verdict.ok and not verdict.judged
    (root / "Dockerfile").write_text(FULL, encoding="utf-8")
    (root / "blocks.lock.json").write_text(json.dumps({"blocks": {}}), encoding="utf-8")
    verdict = image_sufficiency.check(root)
    assert verdict.ok and not verdict.judged


def test_a_copy_from_another_stage_is_not_judged(tmp_path):
    dockerfile = "FROM python:3.12 AS build\nRUN true\nFROM python:3.12-slim\nWORKDIR /app\nCOPY --from=build /x /app\n"
    verdict = image_sufficiency.check(_product(tmp_path, dockerfile))
    assert verdict.ok and not verdict.judged


def test_a_third_party_package_the_factory_lacks_is_not_judged(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL, main="import zz_not_installed_pkg\n"))
    assert verdict.ok and not verdict.judged


def test_the_writer_gate_runs_it_and_names_the_path(tmp_path, monkeypatch):
    """The WRITER gate refuses with image_missing_runtime_path and one finding
    per missing path -- rework before any gate dispatch."""
    from app.factory.build import gates
    from app.factory.build.authority import BuildRole

    root = _product(tmp_path, APP_ONLY)
    ok = gates.GateResult(ok=True, gate="x", detail="ok")
    monkeypatch.setattr(
        "app.factory.build.authorship.agent_written_handler_ids_in_workspace", lambda _ws: {"cap"}
    )
    monkeypatch.setattr(gates, "gate_workspace_compiles", lambda _ctx: ok)
    monkeypatch.setattr(gates, "gate_writer_behaviour", lambda _ctx: ok)
    monkeypatch.setattr(gates, "gate_ui_surface", lambda _ctx: ok)
    monkeypatch.setattr("app.factory.build.ui_e2e.gate_ui_end_to_end", lambda _ctx: ok)

    class _Money:
        status, reason, findings = "PASS", "", []

    monkeypatch.setattr("app.factory.build.money_contract.money_verdict", lambda _ws: _Money())
    result = gates.gate_writer_contract(gates.GateContext(workspace=root, role=BuildRole.WRITER))
    assert result.ok is False, result.detail
    assert result.reason == "image_missing_runtime_path"
    assert any("image missing /app/vendor/blocks/storage/block.py" in f for f in result.findings)


def test_nothing_here_names_a_product_block_or_directory():
    """No hardwiring: the module reads which blocks, which paths and which
    files to copy from the product's own data."""
    source = Path(image_sufficiency.__file__).read_text(encoding="utf-8")
    for literal in ('"vendor"', "'vendor'", '"storage"', '"analytics"', '"/app"'):
        assert literal not in source, literal


def test_a_file_copied_to_the_workdir_or_an_existing_directory_lands_inside_it(tmp_path):
    """Docker semantics: ``COPY f .`` and ``COPY f <existing dir>`` copy INTO
    the directory (live CI 2026-10-08: the emulator wrote over the directory
    and reported "Is a directory")."""
    root = _product(tmp_path, "FROM python:3.12-slim\nWORKDIR /app\nCOPY blocks.lock.json .\n"
                    "COPY app app\nCOPY app/main.py app\nCOPY vendor ./vendor\n")
    verdict = image_sufficiency.check(root)
    assert verdict.judged and verdict.ok, verdict.detail


def test_the_verdict_never_names_the_scratch_directory(tmp_path, monkeypatch):
    """The detail is written to the build ledger, which must be identical run
    to run: no temp path ever appears in it."""
    def boom(_root, image):
        raise OSError(f"[Errno 21] Is a directory: '{image}/app'")

    monkeypatch.setattr(image_sufficiency, "emulate_image", boom)
    verdict = image_sufficiency.check(_product(tmp_path, FULL))
    assert not verdict.judged and verdict.ok
    assert "image-emulation" not in verdict.detail and "/tmp" not in verdict.detail
    assert "Is a directory: '/app'" in verdict.detail  # the image path, not the scratch one


def test_the_probe_env_carries_os_startup_plumbing_and_nothing_else(monkeypatch, tmp_path):
    """Live CI (Windows) 2026-10-08: a probe without SYSTEMROOT cannot start
    the socket layer (WinError 10106) and every app read as broken. The probe
    gets what an interpreter needs to start, plus the environment the Store
    gate runs the image with -- never the Factory's own config."""
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("FACTORY_SECRET_THING", "do-not-pass")
    for name in image_sufficiency.GATE_RUN_ENV:
        monkeypatch.setenv(name, "the-factorys-own-value")
    env = image_sufficiency._probe_env(tmp_path)
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["PYTHONPATH"] == str(tmp_path)
    assert "FACTORY_SECRET_THING" not in env
    for name, value in image_sufficiency.GATE_RUN_ENV.items():
        assert env[name] == value  # the gate's value, never the Factory's
    assert set(env) <= (
        set(image_sufficiency._OS_STARTUP_VARS)
        | set(image_sufficiency.GATE_RUN_ENV)
        | {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
    )


FALLBACK_DISPATCH = DISPATCH + '''

import importlib as _importlib


def load_block(block_id):  # noqa: F811 -- the writer's silent fallback shape
    path = _VENDOR / block_id / "block.py"
    if path.is_file():
        return path.read_text()
    return _importlib.import_module(f"app.fallback_blocks.{block_id}")
'''


def test_a_locked_block_loaded_from_anywhere_but_its_locked_path_is_refused(tmp_path):
    """Live (revoked smoke B): the loader fell back to a local copy when the
    vendored block was absent, so one block "loaded" and the image looked
    sound. Every locked block must come from its locked path, whoever wrote
    the loader."""
    root = _product(tmp_path, APP_ONLY)
    (root / "app" / "dispatch.py").write_text(FALLBACK_DISPATCH, encoding="utf-8")
    (root / "app" / "fallback_blocks").mkdir()
    (root / "app" / "fallback_blocks" / "__init__.py").write_text("", encoding="utf-8")
    for block in ("ledger", "storage"):
        (root / "app" / "fallback_blocks" / f"{block}.py").write_text("X = 2\n", encoding="utf-8")
    verdict = image_sufficiency.check(root)
    assert not verdict.ok and verdict.judged, verdict.detail
    assert any("ledger" in m and "vendor/blocks/ledger" in m for m in verdict.misloaded), verdict.misloaded
    assert "/tmp" not in verdict.detail


def test_blocks_loaded_from_their_locked_paths_pass_the_origin_check(tmp_path):
    root = _product(tmp_path, FULL)
    (root / "app" / "dispatch.py").write_text(FALLBACK_DISPATCH, encoding="utf-8")
    verdict = image_sufficiency.check(root)
    assert verdict.ok and verdict.judged and not verdict.misloaded, verdict.detail


def test_the_writer_gate_names_a_block_loaded_outside_its_lock(tmp_path, monkeypatch):
    from app.factory.build import gates
    from app.factory.build.authority import BuildRole

    root = _product(tmp_path, APP_ONLY)
    (root / "app" / "dispatch.py").write_text(FALLBACK_DISPATCH, encoding="utf-8")
    (root / "app" / "fallback_blocks").mkdir()
    (root / "app" / "fallback_blocks" / "__init__.py").write_text("", encoding="utf-8")
    for block in ("ledger", "storage"):
        (root / "app" / "fallback_blocks" / f"{block}.py").write_text("X = 2\n", encoding="utf-8")
    ok = gates.GateResult(ok=True, gate="x", detail="ok")
    monkeypatch.setattr(
        "app.factory.build.authorship.agent_written_handler_ids_in_workspace", lambda _ws: {"cap"}
    )
    monkeypatch.setattr(gates, "gate_workspace_compiles", lambda _ctx: ok)
    monkeypatch.setattr(gates, "gate_writer_behaviour", lambda _ctx: ok)
    monkeypatch.setattr(gates, "gate_ui_surface", lambda _ctx: ok)
    monkeypatch.setattr("app.factory.build.ui_e2e.gate_ui_end_to_end", lambda _ctx: ok)

    class _Money:
        status, reason, findings = "PASS", "", []

    monkeypatch.setattr("app.factory.build.money_contract.money_verdict", lambda _ws: _Money())
    result = gates.gate_writer_contract(gates.GateContext(workspace=root, role=BuildRole.WRITER))
    assert result.ok is False
    assert result.reason == "locked_block_loaded_elsewhere"
    assert any("vendor/blocks/storage" in f for f in result.findings), result.findings


# -- the probe boots the image the way the Store gate runs it -------------------
#
# Live (cycle 4 fintech, sess_9d348d067ca84b17, 55d91d62): the writer's app
# refuses to start without its deploy token -- correct, fail-closed -- and the
# probe, which ran the image with an empty environment, reported "image cannot
# load the app: ConfigError: PLATFORM_TOKEN is required". The Store gate runs
# the same image WITH that token (store-gate.yml docker run), so the probe was
# judging the build box, not the image.

FAIL_CLOSED_MAIN = '''
import os


class ConfigError(RuntimeError):
    pass


if not os.getenv("PLATFORM_TOKEN"):
    raise ConfigError("PLATFORM_TOKEN is required: set it in the deploy environment")
'''

REQUIRED_SETTING_MAIN = '''
import os

ZORBLAT_CLIENT_ID = os.environ["ZORBLAT_CLIENT_ID"]
'''


def test_the_probe_runs_the_image_with_the_gates_run_environment(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL, main=FAIL_CLOSED_MAIN))
    assert verdict.ok and verdict.judged, verdict.detail


def test_a_setting_the_operator_supplies_at_deploy_time_is_stood_in_for(tmp_path):
    verdict = image_sufficiency.check(_product(tmp_path, FULL, main=REQUIRED_SETTING_MAIN))
    assert verdict.ok and verdict.judged, verdict.detail


def test_the_stand_in_reading_app_sources_never_counts_as_loading_a_block(tmp_path):
    # The stand-in parses app/*.py before the audit hook is installed, so a
    # block is judged only by what the loader itself opened.
    verdict = image_sufficiency.check(_product(tmp_path, FULL, main=REQUIRED_SETTING_MAIN))
    assert verdict.misloaded == []


# -- the rework names the line to change (cycle 8 co-op anchor) ----------------------
#
# sess_ece9a0e5c44e4650: the writer's Dockerfile left vendor/ out of the image;
# round 1 named only the image paths, the writer fixed it, then a later pass
# rewrote the Dockerfile and dropped vendor/ again. The finding now says which
# context file is missing from the image and why.

SRV_APP_ONLY = "FROM python:3.12-slim\nWORKDIR /srv/app\nCOPY app ./app\nCOPY blocks.lock.json ./\n"


def test_a_missing_path_names_the_context_file_and_the_missing_copy(tmp_path):
    root = _product(tmp_path, SRV_APP_ONLY)
    cause = image_sufficiency.context_cause(
        "/srv/app/vendor/blocks/ledger/block.py", "/srv/app", root, image_sufficiency.read_dockerignore(root)
    )
    assert "vendor/blocks/ledger/block.py" in cause
    assert "no COPY/ADD" in cause and "vendor/" in cause and "/srv/app" in cause


def test_a_missing_path_names_the_dockerignore_rule_that_drops_it(tmp_path):
    root = _product(tmp_path, FULL, dockerignore="vendor/\n")
    cause = image_sufficiency.context_cause(
        "/app/vendor/blocks/ledger/block.py", "/app", root, image_sufficiency.read_dockerignore(root)
    )
    assert ".dockerignore rule 'vendor'" in cause


def test_the_writer_gate_finding_carries_the_cause():
    import inspect

    from app.factory.build import gates

    assert "image.causes" in inspect.getsource(gates.gate_writer_contract)
