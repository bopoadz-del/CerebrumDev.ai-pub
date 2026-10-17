"""Shared pytest fixtures for CerebrumDev.ai backend tests."""

from __future__ import annotations

import contextlib
import os
from unittest import mock

from tests.factory.coder_stub_bodies import invoking_handler_body

# The suite runs without credentials, so it needs the anonymous dev principal.
# That is no longer the default -- it must be asked for -- and app.main runs
# verify_production_auth() at import, so this has to be set BEFORE the import
# below or collection fails.
os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

# TestClient fires startup events; without this every test app would arm the
# nightly backup scheduler and take a bootstrap snapshot into ./storage.
# Scheduler tests opt back in explicitly with monkeypatch.
os.environ.setdefault("BACKUP_SCHEDULE_ENABLED", "0")
os.environ.pop("SMOKE_GATE_TOKEN", None)


def _pinned_store_beside_the_repo():
    """A Store checkout next to this repo at exactly the ``store.pin`` commit.

    CI checks the pinned Store out and sets CEREBRUM_BLOCKS_ROOT; a developer
    runner usually has it cloned alongside instead. Only a checkout whose HEAD
    is the pin counts -- a Store at another commit would test something else.
    """
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    try:
        pin = (repo / "store.pin").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    for sibling in sorted(repo.parent.iterdir()):
        if sibling == repo or not (sibling / "block_registry").is_dir():
            continue
        try:
            head = subprocess.run(
                ["git", "-C", str(sibling), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10, check=False,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            continue
        if pin and head == pin:
            return str(sibling)
    return None


if not os.environ.get("CEREBRUM_BLOCKS_ROOT"):
    _store = _pinned_store_beside_the_repo()
    if _store:
        os.environ["CEREBRUM_BLOCKS_ROOT"] = _store

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


#: Env vars that arm the coder's cross-provider fallback leg. OPENROUTER_API_KEY
#: is a normal thing to have exported in a dev shell, and it adds a REAL second
#: fallback leg to every coder failure path. Without this fixture, assertions
#: like "exactly two model legs were tried" pass or fail depending on whose
#: machine runs them. Tests that want the leg set these themselves.
_FALLBACK_LEG_ENV = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "FACTORY_LLM_FALLBACK_API_KEY",
    "FACTORY_LLM_FALLBACK_MODEL",
    "FACTORY_LLM_FALLBACK_PROVIDER",
    "FACTORY_LLM_FALLBACK_ALLOW_PAID",
    # DeepSeek is the default FACTORY_CODE_CLI when DEEPSEEK_API_KEY is set.
    # Isolate so a laptop / Cloud .env cannot flip code_cli_command() to claude.
    "DEEPSEEK_API_KEY",
    "FACTORY_CODE_PROVIDER",
    "ANTHROPIC_AUTH_TOKEN",
)


@pytest.fixture(autouse=True)
def _no_ambient_fallback_leg(monkeypatch):
    """Keep the cross-provider fallback leg out of tests unless asked for."""
    for var in _FALLBACK_LEG_ENV:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client() -> TestClient:
    """Shared FastAPI test client."""
    return TestClient(app)


# -- Phase 0.5 stub coder ----------------------------------------------------
#
# A WRITER pass with zero agent-authored artifacts refuses with
# writer_no_output, so any test that needs a green WRITER phase must give the
# writer a real (stubbed) coding agent. The stubs are deterministic, so
# byte-equality tests stay meaningful, and they never reach a live API.
# ``stub_coder_patches`` is the same stub for module-scoped build fixtures,
# which cannot take a function-scoped monkeypatch fixture.


def _stable_oneshot(**kwargs):
    caps = list(kwargs.get("capabilities") or [])
    body = invoking_handler_body({"run": "stable"})
    return {
        "specs": {
            cid: {
                "entity": cid.replace("-", "_"),
                "fields": [{"name": "reference", "type": "str", "required": True}],
            }
            for cid in caps
        },
        "handlers": {cid: body for cid in caps},
        "model": "stub-coder",
    }


def _stable_handler(**kwargs):
    return {"body": invoking_handler_body({"run": "stable"}), "model": "stub-coder"}


def _stable_spec(**kw):
    return {
        "entity": kw["capability_id"].replace("-", "_"),
        "fields": [{"name": "reference", "type": "str", "required": True}],
        "model": "stub-spec",
    }


def _stable_route(**kw):
    return {
        "body": '    return {"ok": True, "capability": CAPABILITY_ID}',
        "model": "stub-route",
    }


def _stable_readme(messages):
    return (
        "# Generated platform\n\n## Run it\n\n"
        "pip install -r requirements.txt\n"
        "pip install -r requirements-dev.txt\n"
        "pytest\n",
        "stub-model",
    )


_PATCH_TARGETS = {
    "app.factory.build.coder_session.cli_available": lambda command=None: False,
    "app.factory.coder.generate_from_compiled_brief": _stable_oneshot,
    "app.factory.coder.generate_platform_handler": _stable_handler,
    "app.factory.coder.generate_model_spec": _stable_spec,
    "app.factory.coder.generate_route_body": _stable_route,
    "app.factory.coder._llm_code_call": _stable_readme,
}

_ENV_OVERRIDES = {
    "FACTORY_CODER_ENABLED": "1",
    "FACTORY_BRIEF_HTTP_ONESHOT": "1",
}


@contextlib.contextmanager
def stub_coder_patches():
    """Apply the deterministic coder stubs for the duration of the block.

    For module-scoped fixtures that build a platform once per module.
    """
    patchers = [mock.patch(name, new=fn) for name, fn in _PATCH_TARGETS.items()]
    old_env = {key: os.environ.get(key) for key in _ENV_OVERRIDES}
    os.environ.update(_ENV_OVERRIDES)
    for patcher in patchers:
        patcher.start()
    try:
        yield
    finally:
        for patcher in patchers:
            patcher.stop()
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture()
def stub_coder(monkeypatch):
    """Enable a fully stubbed, deterministic coding agent.

    Same entry-point set as test_coder_nondeterminism's fixture, but stable
    output. ``FACTORY_BRIEF_HTTP_ONESHOT=1`` pins the compiled-brief path;
    the per-capability entry points are stubbed anyway so no code path can
    reach a live model.
    """
    for key, value in _ENV_OVERRIDES.items():
        monkeypatch.setenv(key, value)
    for name, fn in _PATCH_TARGETS.items():
        monkeypatch.setattr(name, fn)
