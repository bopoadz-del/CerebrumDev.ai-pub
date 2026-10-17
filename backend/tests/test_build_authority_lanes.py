"""Build roles must be confined to their lane by code, not by prompt.

New-shape tests for the manufacturing kernel. The failure these guard against
is not a crash — it is a *quietly valid-looking* build: a WRITER that edits
the tests judging it, a TESTER that patches the code under test, or any role
that walks out of the workspace via ``..`` or a symlink and edits the factory
itself. Each of those produces an artifact that passes its gate and means
nothing, so every one of them has to raise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.factory.build.authority import (
    BUILD_PHASES,
    FACTORY_RESIDUE,
    KERNEL_ROUTE_NAMES,
    AgentSeat,
    AuthorityError,
    BuildRole,
    LaneRoot,
    assert_phase_order,
    assert_write_allowed,
    authority_manifest,
    jobs_manifest,
    role_contract,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "product"
    ws.mkdir()
    return ws


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    st = tmp_path / "store"
    (st / "block_registry").mkdir(parents=True)
    return st


def test_collector_is_read_only(workspace):
    """The collector reports; materialising is the cloner's job."""
    with pytest.raises(AuthorityError, match="read-only"):
        assert_write_allowed(
            BuildRole.COLLECTOR, workspace / "vendor" / "x.py", workspace=workspace
        )


@pytest.mark.parametrize(
    ("role", "allowed", "denied"),
    (
        (BuildRole.CLONER, "vendor/blocks/web/block.py", "app/actions/orders.py"),
        (BuildRole.CLONER, "kits/platform/manifest.json", "app/actions/orders.py"),
        (BuildRole.WRITER, "app/actions/orders.py", "vendor/blocks/web/block.py"),
        (BuildRole.TESTER, "tests/test_orders.py", "app/actions/orders.py"),
    ),
)
def test_each_role_is_confined_to_its_lane(workspace, role, allowed, denied):
    assert assert_write_allowed(role, workspace / allowed, workspace=workspace)
    with pytest.raises(AuthorityError, match="its lanes are"):
        assert_write_allowed(role, workspace / denied, workspace=workspace)


def test_writer_may_write_named_openapi_files_not_vendor(workspace):
    """Store ships root + docs OpenAPI; named files only, vendor stays sealed."""
    from app.factory.build.authority import SEALED_AFTER_CLONER

    assert assert_write_allowed(
        BuildRole.WRITER, workspace / "openapi.json", workspace=workspace
    )
    (workspace / "docs").mkdir()
    assert assert_write_allowed(
        BuildRole.WRITER, workspace / "docs" / "openapi.json", workspace=workspace
    )
    with pytest.raises(AuthorityError, match="its lanes are"):
        assert_write_allowed(
            BuildRole.WRITER, workspace / "openapi.yaml", workspace=workspace
        )
    with pytest.raises(AuthorityError, match="sealed after CLONER"):
        assert_write_allowed(
            BuildRole.WRITER,
            workspace / "vendor" / "openapi.json",
            workspace=workspace,
            sealed=SEALED_AFTER_CLONER,
        )


def test_writer_cannot_touch_the_tests_that_judge_it(workspace):
    """The load-bearing separation: no self-grading."""
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.WRITER, workspace / "tests" / "test_smoke.py", workspace=workspace
        )


def test_mutation_no_role_may_write_the_build_ledger(workspace):
    """Kills: RoleWorkspace.write_text('build_ledger.jsonl') as a raw NOTE sink."""
    assert "build_ledger.jsonl" in FACTORY_RESIDUE
    for role in (BuildRole.CLONER, BuildRole.WRITER, BuildRole.TESTER):
        with pytest.raises(AuthorityError, match="factory residue"):
            assert_write_allowed(
                role, workspace / "build_ledger.jsonl", workspace=workspace
            )


def test_tester_cannot_patch_the_code_under_test(workspace):
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.TESTER, workspace / "app" / "actions" / "orders.py", workspace=workspace
        )


def test_exact_file_lane_does_not_widen_to_siblings(workspace):
    """`blocks.lock.json` is a file lane, not a prefix."""
    assert assert_write_allowed(
        BuildRole.CLONER, workspace / "blocks.lock.json", workspace=workspace
    )
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.CLONER, workspace / "blocks.lock.json.bak", workspace=workspace
        )


def test_parent_traversal_out_of_the_workspace_is_refused(workspace):
    escape = workspace / "app" / ".." / ".." / "factory" / "generator.py"
    with pytest.raises(AuthorityError):
        assert_write_allowed(BuildRole.WRITER, escape, workspace=workspace)


def test_absolute_path_outside_the_workspace_is_refused(workspace, tmp_path):
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.WRITER, tmp_path / "elsewhere" / "app" / "x.py", workspace=workspace
        )


def test_symlink_out_of_the_workspace_is_refused(workspace, tmp_path):
    """A lane check on the unresolved path would pass this.

    Windows needs SeCreateSymbolicLinkPrivilege, which a normal dev shell
    does not hold, so this skips there. It must NOT skip on POSIX -- a skip
    everywhere would make this a dead test that only looks like coverage, so
    on POSIX a failure to create the link is a failure, not a skip.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "app"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            raise
        pytest.skip(reason="Windows without SeCreateSymbolicLinkPrivilege")

    with pytest.raises(AuthorityError):
        assert_write_allowed(BuildRole.WRITER, link / "escaped.py", workspace=workspace)


def test_vcs_directories_are_forbidden_even_inside_a_lane(workspace):
    """Rewriting .git would let a role forge the build's own provenance."""
    with pytest.raises(AuthorityError, match="version-control"):
        assert_write_allowed(
            BuildRole.CLONER, workspace / "vendor" / ".git" / "config", workspace=workspace
        )


def test_store_manager_writes_the_store_not_the_product(workspace, store):
    assert assert_write_allowed(
        BuildRole.STORE_MANAGER,
        store / "block_registry" / "invoice_parser" / "block.json",
        workspace=workspace,
        store_root=store,
    )
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.STORE_MANAGER,
            workspace / "app" / "actions" / "orders.py",
            workspace=workspace,
            store_root=store,
        )


def test_store_lane_is_inert_when_no_store_root_is_supplied(workspace, store):
    """A build that is not promoting upstream must not reach the store."""
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.STORE_MANAGER,
            store / "block_registry" / "x" / "block.json",
            workspace=workspace,
        )


def test_phases_run_in_order(workspace):
    assert_phase_order(BuildRole.COLLECTOR, [])
    assert_phase_order(BuildRole.WRITER, [BuildRole.COLLECTOR, BuildRole.CLONER])

    with pytest.raises(AuthorityError, match="CLONER"):
        assert_phase_order(BuildRole.WRITER, [BuildRole.COLLECTOR])
    with pytest.raises(AuthorityError, match="COLLECTOR"):
        assert_phase_order(BuildRole.TESTER, [BuildRole.CLONER, BuildRole.WRITER])


def test_manifest_covers_every_phase_and_marks_the_read_only_role():
    manifest = authority_manifest()
    assert manifest["phases"] == [p.value for p in BUILD_PHASES]
    assert set(manifest["roles"]) == {p.value for p in BUILD_PHASES}
    assert manifest["roles"]["COLLECTOR"]["read_only"] is True
    assert manifest["roles"]["WRITER"]["read_only"] is False
    # Every role states a gate — a phase with no gate is a phase that cannot fail.
    assert all(r["gate"] for r in manifest["roles"].values())
    assert manifest["roles"]["COLLECTOR"]["title"] == "Binding surveyor"
    assert manifest["sealed_after_cloner"] == ["vendor/**"]
    assert manifest["factory_residue"] == ["build_ledger.jsonl"]
    assert manifest["roles"]["COLLECTOR"]["agent"] == AgentSeat.CONSULT.value
    assert manifest["roles"]["WRITER"]["http_routes"]


def test_every_phase_has_a_contract():
    for phase in BUILD_PHASES:
        contract = role_contract(phase)
        assert contract.mandate
        assert contract.gate
        assert contract.title
        assert contract.agent in AgentSeat
        assert contract.http_routes
    assert role_contract("STORE_MANAGER").write_lanes[0][0] is LaneRoot.STORE


def test_each_kernel_has_a_distinct_job_title_and_http_surface():
    titles = [role_contract(p).title for p in BUILD_PHASES]
    assert len(titles) == len(set(titles))
    assert role_contract(BuildRole.COLLECTOR).agent is AgentSeat.CONSULT
    assert role_contract(BuildRole.CLONER).agent is AgentSeat.NONE
    assert role_contract(BuildRole.WRITER).agent is AgentSeat.MANUFACTURE
    assert role_contract(BuildRole.TESTER).agent is AgentSeat.NONE
    assert role_contract(BuildRole.STORE_MANAGER).agent is AgentSeat.NONE
    roster = jobs_manifest()
    assert [j["kernel"] for j in roster] == [p.value for p in BUILD_PHASES]
    assert {j["agent"] for j in roster} == {"consult", "manufacture", "none"}
    # Distinctive routes do not steal /v1/jobs — that is the shared roster.
    for job in roster:
        assert all(
            r.startswith(("GET ", "POST ", "PUT ", "DELETE "))
            for r in job["http_routes"]
        )
        assert "GET /v1/jobs" not in job["http_routes"]
    assert "jobs" in KERNEL_ROUTE_NAMES
    assert role_contract(BuildRole.COLLECTOR).http_routes == ("GET /v1/catalog",)
    assert role_contract(BuildRole.CLONER).http_routes == ("GET /v1/inventory",)
    assert "GET /v1/gates" in role_contract(BuildRole.TESTER).http_routes
    assert "GET /v1/provenance" in role_contract(BuildRole.STORE_MANAGER).http_routes
