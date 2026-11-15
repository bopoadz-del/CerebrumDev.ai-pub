"""FG-7(b): one probe environment for every grader process.

The function probes, the two-dataset probes and the grading boot run the
product in ONE allowlisted environment (``function_grader.probe_env``): the
OS start-up variables an interpreter needs, the test context, the gate's run
token and a scratch store -- never the Factory's own keys or settings.

Two inputs each. Nothing here names a product, a probe or a capability the
Factory ships: the ids and variables are invented by the test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.factory.build import grader
from app.factory.build.authority import capability_name
from app.factory.build.image_sufficiency import _OS_STARTUP_VARS, GATE_RUN_ENV
from app.factory.build.product_grader import function_grader, two_dataset
from app.factory.build.product_grader.function_grader import harness_process_env, probe_env

IDS = ["quell-tally", "brisk_ratio"]

#: Variables the product reports back; FACTORY_ONLY_* are set in the
#: Factory's process by the test and must never reach the product.
_SEEN = ["STORAGE_PATH", "ENV", "PYTHONPATH", "TEMP", "FACTORY_ONLY_SETTING_A", "FACTORY_ONLY_SETTING_B",
         *GATE_RUN_ENV]


def _workspace(root: Path, cid: str) -> Path:
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", "utf-8")
    (actions / "__init__.py").write_text("", "utf-8")
    (actions / f"{capability_name(cid)}.py").write_text(
        "import os\n"
        f"_SEEN = {_SEEN!r}\n"
        "def handle(payload):\n"
        "    return {'result': {k: os.environ.get(k) for k in _SEEN}}\n",
        "utf-8",
    )
    return root


@pytest.mark.parametrize(
    "factory_var,os_var",
    [("FACTORY_ONLY_SETTING_A", "TEMP"), ("DATABASE_URL", "SYSTEMROOT")],
)
def test_probe_env_is_an_allowlist_carrying_the_os_startup_variables(tmp_path, factory_var, os_var):
    source = {factory_var: "must-not-reach-the-product", os_var: "os-value-" + os_var}
    storage = tmp_path / "store"
    env = probe_env(tmp_path / "p", storage, source)
    assert os_var in _OS_STARTUP_VARS
    assert factory_var not in env
    assert env[os_var] == "os-value-" + os_var
    for key, value in {**harness_process_env(tmp_path / "p"), **GATE_RUN_ENV}.items():
        assert env[key] == value
    assert env["STORAGE_PATH"] == str(storage)
    assert set(env) == {os_var, *harness_process_env(tmp_path / "p"), *GATE_RUN_ENV, "STORAGE_PATH"}


@pytest.mark.parametrize("port", [5555, 6001])
def test_the_boot_is_the_probe_environment_plus_its_port(tmp_path, port):
    source = {"TEMP": "os-temp", "FACTORY_ONLY_SETTING_A": "secret"}
    storage = tmp_path / "store"
    env = grader.boot_env(tmp_path / "p", storage, port, source)
    assert env == {**probe_env(tmp_path / "p", storage, source), "PORT": str(port)}


@pytest.mark.parametrize("cid", IDS)
def test_the_two_dataset_probe_runs_in_the_probe_environment(tmp_path, monkeypatch, cid):
    monkeypatch.setenv("FACTORY_ONLY_SETTING_A", "must-not-reach-the-product")
    monkeypatch.setenv("FACTORY_ONLY_SETTING_B", "must-not-reach-the-product")
    monkeypatch.setenv("TEMP", str(tmp_path))
    root = _workspace(tmp_path / "p", cid)
    pair = two_dataset.DatasetPair(cid, {"x": 1.0}, None, {"x": 2.0}, None)
    [check] = two_dataset.run_two_dataset([pair], root)
    seen = check.out_a
    assert isinstance(seen, dict), check
    assert seen["FACTORY_ONLY_SETTING_A"] is None
    assert seen["FACTORY_ONLY_SETTING_B"] is None
    assert seen["TEMP"] == str(tmp_path)
    assert seen["ENV"] == harness_process_env(root)["ENV"]
    assert seen["PYTHONPATH"] == str(root)
    for key, value in GATE_RUN_ENV.items():
        assert seen[key] == value
    # a scratch store of the probe's own, outside the product workspace
    assert seen["STORAGE_PATH"]
    assert not Path(seen["STORAGE_PATH"]).is_relative_to(root)


@pytest.mark.parametrize("cid", IDS)
def test_the_function_probe_runs_in_the_probe_environment(tmp_path, monkeypatch, cid):
    monkeypatch.setenv("FACTORY_ONLY_SETTING_A", "must-not-reach-the-product")
    monkeypatch.setenv("TEMP", str(tmp_path))
    root = _workspace(tmp_path / "p", cid)
    [graded] = function_grader.derive_functions([cid], root)
    seen: dict = {}

    def capture(cmd, **kwargs):
        seen.update(kwargs["env"])
        seen["__store_is_dir__"] = Path(kwargs["env"]["STORAGE_PATH"]).is_dir()
        raise subprocess.TimeoutExpired(cmd, 0)

    monkeypatch.setattr(function_grader.subprocess, "run", capture)
    result = function_grader.run_function(graded, root)
    assert result.detail == "timeout"
    assert seen.pop("__store_is_dir__") is True
    assert seen == probe_env(root, seen["STORAGE_PATH"])
    assert "FACTORY_ONLY_SETTING_A" not in seen
    assert seen["TEMP"] == str(tmp_path)
