"""The platform runs, and it runs without the store."""

import os

import pytest


def test_capabilities_import():
    from app.actions import analytics_surface
    assert analytics_surface.CAPABILITY_ID
    from app.actions import dashboard_surface
    assert dashboard_surface.CAPABILITY_ID


def test_dispatch_runs_offline():
    """No store env, no network: every block must LOAD and EXECUTE from
    vendor/. A block-level refusal of this bare probe is still local
    execution; an import failure is the store dependency this test
    exists to catch."""
    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
        os.environ.pop(var, None)
    from app.dispatch import execute, load_block
    for block_id in ['analytics', 'dashboard']:
        load_block(block_id)
    actions = {}
    for block_id in ['analytics', 'dashboard']:
        try:
            result = execute(block_id, {}, action=actions.get(block_id))
        except RuntimeError as exc:
            # The block ran and refused the empty probe -- fine here;
            # the pilot test below demands Store-backed success.
            # An import error is never fine.
            assert "No module named" not in str(exc), (block_id, exc)
            assert "cannot import" not in str(exc), (block_id, exc)
        else:
            assert isinstance(result, dict), block_id


def test_kit_packs_present():
    """The download is a product tree: kits/ next to vendor/blocks."""
    from pathlib import Path as _Path
    kits = _Path(__file__).resolve().parents[1] / "kits"
    assert kits.is_dir(), "kits/ missing from the delivered platform"
    assert list(kits.glob("*/manifest.json")), "no kit pack manifests"


def test_every_capability_handle_returns_mapping():
    """Code-phase: the coder wired handle() and it returns a dict.
    Store ok: False or a Store exception is not this gate."""
    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY"):
        os.environ.pop(var, None)
    failures = []
    from app.actions import analytics_surface
    try:
        out = analytics_surface.handle({'reference': 'sample', 'status': 'open', 'quantity': 0})
    except Exception as exc:
        out = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}
    if not isinstance(out, dict):
        failures.append('analytics_surface handle() must return a dict, got ' + type(out).__name__)
    from app.actions import dashboard_surface
    try:
        out = dashboard_surface.handle({'reference': 'sample', 'status': 'open', 'quantity': 0})
    except Exception as exc:
        out = {'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)}
    if not isinstance(out, dict):
        failures.append('dashboard_surface handle() must return a dict, got ' + type(out).__name__)
    assert not failures, "; ".join(failures)


@pytest.mark.pilot
def test_every_capability_executes_end_to_end():
    """Pilot: each handler runs its blocks on a spec payload and
    Store must accept it. Not the factory code-phase gate."""
    for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY"):
        os.environ.pop(var, None)
    import json as _json
    failures = []
    from app.actions import analytics_surface
    out = analytics_surface.handle({'reference': 'sample', 'status': 'open', 'quantity': 0})
    if not isinstance(out, dict):
        failures.append('analytics_surface returned a non-dict: ' + repr(out)[:120])
    elif out.get("ok") is False:
        failures.append('analytics_surface rejected a payload built from its own schema: ' + str(out.get('error')))
    elif '\"status\": \"error\"' in _json.dumps(out) or '\"status\": \"failed\"' in _json.dumps(out):
        failures.append('analytics_surface reported ok around a failed block call: ' + _json.dumps(out)[:300])
    from app.actions import dashboard_surface
    out = dashboard_surface.handle({'reference': 'sample', 'status': 'open', 'quantity': 0})
    if not isinstance(out, dict):
        failures.append('dashboard_surface returned a non-dict: ' + repr(out)[:120])
    elif out.get("ok") is False:
        failures.append('dashboard_surface rejected a payload built from its own schema: ' + str(out.get('error')))
    elif '\"status\": \"error\"' in _json.dumps(out) or '\"status\": \"failed\"' in _json.dumps(out):
        failures.append('dashboard_surface reported ok around a failed block call: ' + _json.dumps(out)[:300])
    assert not failures, "; ".join(failures)
