"""A setting the operator supplies at deploy time is not a build failure.

Live:

    TESTER failed -- suite_red: FAILED
    tests/test_models.py::test_every_model_round_trips - KeyError: 'GOOGLE_CLIENT_ID'

Owner: "this should not be a error, it will be provided later". The Factory
ships pilots to a DevOps team; a credential that team supplies is not
something a build box can have.

Nothing here names a real setting. The Factory holds no list of them -- which
ones a product needs is read off the product -- so these tests use names no
product would ever declare.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.deploy_time_settings import SNIPPET
from app.factory.build.roles_constants import _CONFTEST
from app.factory.build.store_acceptance import render_acceptance_script

REQUIRED = "ZZ_FACTORY_TEST_REQUIRED_CREDENTIAL"
OPTIONAL = "ZZ_FACTORY_TEST_OPTIONAL_DSN"


def _product(root: Path, settings_py: str, env_example: str = "") -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "settings.py").write_text(settings_py, encoding="utf-8")
    if env_example:
        (root / ".env.example").write_text(env_example, encoding="utf-8")
    return root


def _run(root: Path, body: str, env: dict | None = None) -> dict:
    """Exec the shared snippet in a clean interpreter, then run ``body``."""
    driver = (
        SNIPPET
        + "\nimport json, os, sys\n"
        + f"sys.path.insert(0, {str(root)!r})\n"
        + f"stood_in = _stand_in_for_deploy_time_settings({str(root)!r})\n"
        + textwrap.dedent(body)
    )
    import os

    clean = {k: v for k, v in os.environ.items() if not k.startswith("ZZ_FACTORY_TEST_")}
    clean.update(env or {})
    proc = subprocess.run(
        [sys.executable, "-c", driver], capture_output=True, text=True, timeout=120, env=clean
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_live_failure_a_required_credential_read_at_import(tmp_path):
    root = _product(tmp_path, f'import os\nCLIENT = os.environ["{REQUIRED}"]\n')

    out = _run(root, "import app.settings as s\nprint(json.dumps({'v': s.CLIENT, 'stood_in': stood_in}))")

    assert out["stood_in"] == [REQUIRED]
    assert out["v"] == "set-at-deploy"


def test_without_the_stand_in_that_same_product_really_does_keyerror(tmp_path):
    """The premise, so the test above cannot pass for the wrong reason."""
    root = _product(tmp_path, f'import os\nCLIENT = os.environ["{REQUIRED}"]\n')

    proc = subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(root)!r}); import app.settings"],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode != 0 and "KeyError" in proc.stderr


def test_an_optional_setting_is_left_off(tmp_path):
    """``os.getenv`` / ``.get`` already tolerate absence. Standing in a value
    there would switch ON an integration the product had correctly left off --
    a junk DSN, a fake webhook URL."""
    root = _product(
        tmp_path,
        f'import os\nA = os.getenv("{OPTIONAL}")\nB = os.environ.get("{OPTIONAL}")\n',
    )

    out = _run(root, f"print(json.dumps({{'set': {OPTIONAL!r} in os.environ, 'stood_in': stood_in}}))")

    assert out == {"set": False, "stood_in": []}


def test_a_value_the_build_already_has_is_never_overwritten(tmp_path):
    root = _product(tmp_path, f'import os\nCLIENT = os.environ["{REQUIRED}"]\n')

    out = _run(
        root,
        "import app.settings as s\nprint(json.dumps({'v': s.CLIENT, 'stood_in': stood_in}))",
        env={REQUIRED: "the-real-one"},
    )

    assert out == {"v": "the-real-one", "stood_in": []}


def test_the_products_own_example_value_is_used_so_types_survive(tmp_path):
    """``int(os.environ["PORT"])`` must not meet the word "set-at-deploy"."""
    root = _product(
        tmp_path,
        f'import os\nLIMIT = int(os.environ["{REQUIRED}"])\n',
        env_example=f"# what to set\n{REQUIRED}=8000  # the port\nOTHER=\n",
    )

    out = _run(root, "import app.settings as s\nprint(json.dumps({'v': s.LIMIT}))")

    assert out == {"v": 8000}


def test_writes_to_the_environment_and_vendored_code_are_not_requirements(tmp_path):
    """Only a LOAD of os.environ[...] in the product's own app/ tree counts."""
    root = _product(tmp_path, f'import os\nos.environ["{REQUIRED}"] = "x"\n')
    (root / "vendor").mkdir()
    (root / "vendor" / "lib.py").write_text(f'import os\nV = os.environ["{OPTIONAL}"]\n', encoding="utf-8")

    out = _run(root, "print(json.dumps({'stood_in': stood_in}))")

    assert out == {"stood_in": []}


def test_a_product_file_that_does_not_parse_does_not_take_the_harness_down(tmp_path):
    root = _product(tmp_path, "def broken(:\n")

    assert _run(root, "print(json.dumps({'stood_in': stood_in}))") == {"stood_in": []}


# ── one source, two consumers ──────────────────────────────────────────────


def test_both_places_the_factory_boots_a_product_carry_the_same_text():
    body = SNIPPET.strip("\n")

    assert body in _CONFTEST
    assert body in render_acceptance_script()


def test_each_consumer_calls_it_before_it_imports_the_product():
    conftest = _CONFTEST
    assert conftest.index("_stand_in_for_deploy_time_settings(Path(") < conftest.index(
        "from app.migrations import"
    )

    script = render_acceptance_script()
    main = script[script.index("def main() -> int:"):]
    assert main.index("_stand_in_for_deploy_time_settings(ROOT)") < main.index("_client()")


def test_both_rendered_files_still_compile():
    compile(_CONFTEST, "tests/conftest.py", "exec")
    compile(render_acceptance_script(), "scripts/acceptance.py", "exec")


def test_the_factory_names_no_setting_of_its_own():
    """Owner: "dont hard wire anything". The snippet's only constant is the
    placeholder value; every NAME comes from the product's own source."""
    import re

    names = set(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", SNIPPET))

    assert names == {"DEPLOY_TIME_PLACEHOLDER"}, names
