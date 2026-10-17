"""F1: no world-known bearer token survives into a product's RUNTIME or deploy env.

Live (owner's pilot test, 2026-09-29): the literal ``dev-local-token-b`` opened
a second tenant on a CERTIFIED product in production — read AND wrote a work
order — because:

* the factory's own runtime emitters baked ``dev-local-token`` as a fallback
  (``render_tenancy_module``/``render_auth_module``), and ``generator.py`` wrote
  the same literal into every product's deploy env, so production accepted a
  world-known token unless the operator overrode it; and
* the emitted negative suite demanded a SECOND authenticated tenant while the
  factory provisioned none — so the writer hardwired the harness's fallback
  into ``app/tenancy.py`` to make ``cross_tenant_404`` pass. The harness forced
  the backdoor it then certified.

The contract now: RUNTIME reads tokens from the environment only and refuses
the shipped placeholder (fail closed); the DEPLOY env carries the
deterministic ``set-at-deploy`` placeholder so scaffolds stay byte-
reproducible; the TEST bootstrap
(conftest) is the one place well-known values may exist, and it provisions the
second tenant through the legitimate ``TENANT_TOKENS`` mechanism; and a new
floor check refuses any runtime token literal so none of this can regress.
"""

from __future__ import annotations

import re
from pathlib import Path

LITERALS = ("dev-local-token-b", "dev-local-token")

#: environ.get("PLATFORM_TOKEN...", "<literal>") or  ... or "<literal>"
FALLBACK_SHAPE = re.compile(
    r"environ\s*\.\s*get\(\s*[\"']PLATFORM_TOKEN[^\"']*[\"']\s*,\s*[\"'][^\"']+[\"']"
    r"|environ\s*\.\s*get\(\s*[\"']PLATFORM_TOKEN[^\"']*[\"']\s*\)\s*or\s*[\"'][^\"']+[\"']"
)


# -- the factory's own runtime emitters carry no literal ------------------------


def test_rendered_tenancy_has_no_token_literal():
    from app.factory.build.store_acceptance import render_tenancy_module

    body = render_tenancy_module()
    for lit in LITERALS:
        assert lit not in body, f"runtime tenancy still bakes {lit!r}"
    assert not FALLBACK_SHAPE.search(body), "tenancy still has a token fallback shape"


def test_rendered_auth_has_no_token_literal():
    from app.factory.build.store_acceptance import render_auth_module

    body = render_auth_module()
    for lit in LITERALS:
        assert lit not in body, f"runtime auth still bakes {lit!r}"
    assert not FALLBACK_SHAPE.search(body)


# -- the emitted negative suite reads env, and the tests it teaches from --------


def test_negative_floor_header_reads_tokens_from_env_only():
    from app.factory.build.negative_floor import HEADER

    for lit in LITERALS:
        assert lit not in HEADER, (
            f"the emitted negative suite still carries {lit!r} -- the exact "
            "pattern the writer copied into app/tenancy.py"
        )
    assert 'os.environ["PLATFORM_TOKEN"]' in HEADER
    assert 'os.environ["PLATFORM_TOKEN_B"]' in HEADER


def test_conftest_provisions_test_tokens_and_the_second_tenant():
    """The TEST bootstrap is the literals' one legitimate home, and it must
    provision tenant B through TENANT_TOKENS -- the mechanism whose absence
    forced the writer to invent the runtime backdoor."""
    from app.factory.build.roles_constants import _CONFTEST

    assert 'setdefault("PLATFORM_TOKEN", "dev-local-token")' in _CONFTEST
    assert 'setdefault("PLATFORM_TOKEN_B", "dev-local-token-b")' in _CONFTEST
    assert 'setdefault("TENANT_TOKENS"' in _CONFTEST
    assert "dev-local-token-b:" in _CONFTEST, "token B must map to a real tenant"


# -- the deploy env carries the deterministic placeholder, never the literal --
#
# A render-time RANDOM token broke the factory's byte-reproducibility
# invariant (7 determinism tests: "non-determinism escaped into
# .env.example"). The scaffold therefore ships the existing deploy-time
# placeholder -- deterministic, world-known-value-free -- and RUNTIME
# refuses the placeholder exactly like an empty token, so production must
# supply a real value at deploy.


def test_generator_env_example_ships_the_placeholder_not_the_literal():
    source = Path("app/factory/generator.py").read_text(encoding="utf-8")
    assert "dev-local-token" not in source, (
        "generator.py still ships the world-known token as the deploy default"
    )
    assert "PLATFORM_TOKEN=set-at-deploy" in source


def test_network_posture_env_ships_the_placeholder_not_the_literal():
    from app.factory.build.network_posture import P1_ENV_EXAMPLE

    source = Path("app/factory/build/network_posture.py").read_text(encoding="utf-8")
    assert "dev-local-token" not in source
    assert "PLATFORM_TOKEN=set-at-deploy" in P1_ENV_EXAMPLE


def test_runtime_refuses_the_placeholder_as_a_credential():
    """An operator who deploys .env.example verbatim must get FAIL-CLOSED
    (no platform token), not a world-known bearer value."""
    from app.factory.build.store_acceptance import (
        render_auth_module,
        render_tenancy_module,
    )

    assert 'set-at-deploy' in render_tenancy_module()
    assert 'set-at-deploy' in render_auth_module()


# -- the floor refuses a runtime token literal, forever -------------------------


def test_the_floor_gained_no_token_literal_before_authorship():
    from app.factory.build.acceptance_floor import check_ids

    ids = check_ids()
    assert "no_token_literal" in ids
    assert ids[-1] == "authorship_floor", "authorship stays the LAST line"


def _load_named_check(tmp_path, name):
    from app.factory.build.store_acceptance import render_acceptance_script

    script = render_acceptance_script()
    ns: dict = {
        "__file__": str(tmp_path / "scripts" / "acceptance.py"),
        "__name__": "acceptance_under_test",
    }
    (tmp_path / "scripts").mkdir(parents=True, exist_ok=True)
    exec(compile(script, "acceptance.py", "exec"), ns)
    return ns[name]


def test_check_fails_a_runtime_fallback_shape(tmp_path):
    app = tmp_path / "app"
    app.mkdir(parents=True)
    (app / "tenancy.py").write_text(
        'import os\n'
        'second = (os.environ.get("PLATFORM_TOKEN_B") or "dev-local-token-b").strip()\n',
        encoding="utf-8",
    )
    check = _load_named_check(tmp_path, "check_no_token_literal")
    status, detail = check()
    assert status == "FAIL", detail
    assert "tenancy.py" in detail


def test_check_fails_a_bare_literal_even_without_the_shape(tmp_path):
    app = tmp_path / "app"
    app.mkdir(parents=True)
    (app / "auth.py").write_text('TOKENS = ("dev-local-token",)\n', encoding="utf-8")
    check = _load_named_check(tmp_path, "check_no_token_literal")
    status, detail = check()
    assert status == "FAIL", detail


def test_check_passes_a_clean_runtime_and_ignores_tests(tmp_path):
    app = tmp_path / "app"
    app.mkdir(parents=True)
    (app / "tenancy.py").write_text(
        'import os\nplatform = (os.environ.get("PLATFORM_TOKEN") or "").strip()\n',
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    # Test files may carry the well-known TEST values -- that is their home.
    (tests / "test_x.py").write_text('AUTH = "dev-local-token"\n', encoding="utf-8")
    check = _load_named_check(tmp_path, "check_no_token_literal")
    status, detail = check()
    assert status == "PASS", detail
