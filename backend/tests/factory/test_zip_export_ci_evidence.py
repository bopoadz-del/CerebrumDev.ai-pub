"""A platform that passed the Store gate in CI can be downloaded.

Live: sess_b4fcca22b6204b68 passed 13/13 in Docker (commit status
``store-gate: success`` on cerebrum-builds) and the Floor's Export button
returned HTTP 500:

    ExportManifestError: zip_requires_green_ci: no CI run id recorded

``ci_run_id`` reads GITHUB_RUN_ID from the process environment, but the
process serving the download is the API server, which never runs inside the
CI job -- so in production every runner-built platform was refused.
"""

from __future__ import annotations

import pytest

from app.factory.build.builds_push import (
    FACTORY_INTERNAL_NAMES,
    _sync_workspace_onto_tree,
)
from app.factory.build.export_manifest import (
    ExportManifestError,
    assert_zip_eligibility,
    ci_evidence,
)

SHA = "d6220d656a916b19d496c64e4687f71ac4394b05"


def _green_status(**over):
    status = {
        "state": "succeeded",
        "authorship": {"agent_written": 44},
        "builds_sha": SHA,
        "acceptance": {
            "ok": True, "passed": 13, "total": 13,
            "via": "github-commit-status:store-gate",
        },
    }
    status.update(over)
    return status


def test_the_live_green_build_is_eligible_with_no_ci_env_at_all():
    status = _green_status()
    evidence = ci_evidence(status, env={})

    assert evidence == f"github-commit-status:store-gate@{SHA}"
    assert_zip_eligibility(status, evidence)  # must not raise


def test_an_explicit_ci_run_id_still_wins():
    assert ci_evidence(_green_status(), env={"GITHUB_RUN_ID": "987"}) == "987"


@pytest.mark.parametrize(
    "acceptance",
    [
        {"ok": False, "passed": 12, "total": 13, "via": "github-commit-status:store-gate"},
        {"ok": True, "passed": 12, "total": 13, "via": "github-commit-status:store-gate"},
        {"ok": True, "passed": 0, "total": 0, "via": "github-commit-status:store-gate"},
        # A host-side measurement is not CI.
        {"ok": True, "passed": 13, "total": 13, "via": "store_gate.json"},
        {"ok": True, "passed": "x", "total": 13, "via": "github-commit-status:store-gate"},
        {},
    ],
)
def test_anything_short_of_a_complete_ci_verdict_is_no_evidence(acceptance):
    status = _green_status(acceptance=acceptance)

    assert ci_evidence(status, env={}) == ""
    with pytest.raises(ExportManifestError, match="not greened by CI"):
        assert_zip_eligibility(status, ci_evidence(status, env={}))


def test_a_verdict_not_pinned_to_a_sha_is_no_evidence():
    assert ci_evidence(_green_status(builds_sha=""), env={}) == ""


def test_the_download_route_refuses_with_a_readable_409_not_a_500():
    import inspect

    from app.routers import session_product

    src = inspect.getsource(session_product.download_product_package)
    assert "ci_evidence(status)" in src
    assert "except ExportManifestError" in src and "status_code=409" in src


def test_factory_bookkeeping_does_not_ship_to_cerebrum_builds(tmp_path):
    src, dest = tmp_path / "ws", tmp_path / "clone"
    (src / "app").mkdir(parents=True)
    (src / "app" / "main.py").write_text("x", encoding="utf-8")
    (src / ".codewhale" / "state").mkdir(parents=True)
    (src / ".codewhale" / "state" / "subagents.v1.lock").write_text("", encoding="utf-8")
    (src / ".generation_quota_account").write_text("acct_secret", encoding="utf-8")
    (src / "build_ledger.jsonl.lock").write_text("", encoding="utf-8")
    (src / "build_ledger.jsonl").write_text("{}", encoding="utf-8")

    _sync_workspace_onto_tree(src, dest)

    shipped = {p.name for p in dest.iterdir()}
    # build_ledger.jsonl used to ship. It is the Factory's record of the
    # build -- role transitions, gate verdicts, the agent's narration -- and
    # the customer gets the platform, not the transcript of making it.
    assert shipped == {"app"}, shipped
    assert not (FACTORY_INTERNAL_NAMES & shipped)


def test_factory_bookkeeping_does_not_ship_in_the_customers_zip(tmp_path):
    import zipfile

    from app.routers.session_product import zip_generated_product

    out = tmp_path / "product"
    (out / "app").mkdir(parents=True)
    (out / "app" / "main.py").write_text("x", encoding="utf-8")
    (out / ".codewhale" / "state").mkdir(parents=True)
    (out / ".codewhale" / "state" / "subagents.v1.lock").write_text("", encoding="utf-8")
    (out / ".generation_quota_account").write_text("acct_secret", encoding="utf-8")
    (out / "build_ledger.jsonl.lock").write_text("", encoding="utf-8")

    archive = zip_generated_product(out, tmp_path / "export")

    names = set(zipfile.ZipFile(archive).namelist())
    assert "app/main.py" in names
    leaked = {n for n in names if any(part in FACTORY_INTERNAL_NAMES for part in n.split("/"))}
    assert not leaked, leaked


def test_a_build_branch_inherits_only_the_gate_from_main(tmp_path):
    """cerebrum-builds main carried a whole platform (a build branch was merged
    into it on 2026-09-14). The overlay only replaces what the workspace has,
    so another customer's kits/, receipt.json and account file rode along in
    every later platform's branch."""
    from app.factory.build.builds_push import _drop_inherited_product, _sync_workspace_onto_tree

    main = tmp_path / "clone"
    (main / ".git").mkdir(parents=True)
    (main / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (main / ".github" / "workflows").mkdir(parents=True)
    (main / ".github" / "workflows" / "store-gate.yml").write_text("gate", encoding="utf-8")
    # someone else's platform, sitting on main
    (main / "kits" / "hotel").mkdir(parents=True)
    (main / "kits" / "hotel" / "k.json").write_text("{}", encoding="utf-8")
    (main / "receipt.json").write_text('{"session_id": "sess_other"}', encoding="utf-8")
    (main / ".generation_quota_account").write_text("acct_other", encoding="utf-8")
    (main / "app").mkdir()
    (main / "app" / "old.py").write_text("old", encoding="utf-8")

    ws = tmp_path / "ws"
    (ws / "app").mkdir(parents=True)
    (ws / "app" / "main.py").write_text("new", encoding="utf-8")

    _drop_inherited_product(main)
    _sync_workspace_onto_tree(ws, main)

    names = {p.relative_to(main).as_posix() for p in main.rglob("*") if p.is_file()}
    assert names == {".git/HEAD", ".github/workflows/store-gate.yml", "app/main.py"}, names
