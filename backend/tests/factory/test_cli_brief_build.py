"""The CLI's build path carries the customer brief, and every command routes.

The build subcommand declares ``--brief`` (customer_brief.v1 JSON) and hands
it to the role runner. A run without a brief stops before any phase, so the
CLI is one of the four doors that must pass one through. The store
subcommands route through their own handler -- ``main`` never asks a store
command for a blueprint.
"""

from __future__ import annotations

import argparse
import json

import pytest

import app.factory.cli as cli

BRIEF_A = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'
BRIEF_B = '{"ask": "Upload the pdf.", "documents": {"file_types": ["pdf"], "uploads": true}}'


class _Outcome:
    ok = True

    def to_dict(self):
        return {"ok": True, "detail": "stub"}


@pytest.mark.parametrize("args", [["store", "manifest"], ["store", "health-scan"]])
def test_store_commands_route_without_a_blueprint(args, capsys):
    """A dropped ``if args.cmd == 'store'`` route walks a store command into
    load_blueprint, which raises on the absent --blueprint. Both commands
    print their JSON and exit 0 through the store handler."""
    assert cli.main(args) == 0
    out = capsys.readouterr().out.strip()
    assert json.loads(out)


@pytest.mark.parametrize("brief", [BRIEF_A, BRIEF_B])
def test_the_build_command_reads_the_brief_flag(brief, monkeypatch):
    """``--brief`` is a declared build option and reaches _build_cmd's args."""
    seen = {}

    def _fake_build_cmd(args, _bp, _blocks_root):
        seen["brief"] = args.brief
        return 0

    monkeypatch.setattr(cli, "_build_cmd", _fake_build_cmd)
    monkeypatch.setattr(cli, "load_blueprint", lambda _path: {"product_id": "p"})
    assert cli.main(["build", "--blueprint", "bp.yaml", "--out", "out", "--brief", brief]) == 0
    assert seen["brief"] == brief


@pytest.mark.parametrize("brief", [BRIEF_A, BRIEF_B])
def test_build_cmd_passes_the_brief_to_the_runner_and_exits_zero(brief, monkeypatch, tmp_path, capsys):
    """_build_cmd threads ``args.brief`` into RoleRunner and exits 0 only on a
    RUN_SUCCEEDED -- a constant return would read as a pass with no run."""
    captured = {}

    def _fake_runner(blueprint, out, **kw):
        captured["blueprint"] = blueprint
        captured["out"] = out
        captured.update(kw)
        return _FakeRunner()

    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _fake_runner)
    args = argparse.Namespace(
        out=str(tmp_path / "out"),
        max_rework=3,
        wall_clock=30.0,
        phase_wall_clock=15.0,
        cycle="code",
        brief=brief,
    )
    assert cli._build_cmd(args, {"product_id": "p"}, None) == 0
    assert captured["brief"] == brief
    assert captured["cycle"] == "code"
    assert json.loads(capsys.readouterr().out)["ok"] is True


class _FakeRunner:
    def __init__(self, *args, **kwargs):
        self.state = {"artifact_sources": {}}

    def run(self):
        return _Outcome()
