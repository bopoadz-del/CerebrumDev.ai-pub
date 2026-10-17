"""Each kernel publishes a job description and a distinctive HTTP surface."""

from __future__ import annotations

from app.factory.build.authority import (
    BUILD_PHASES,
    KERNEL_ROUTE_NAMES,
    jobs_manifest,
    kernel_seat_brief,
    role_contract,
)
from app.factory.build.roles import _render_jobs_module, _render_routes
from app.factory.coder import (
    _COLLECTOR_REVIEW_SYSTEM,
    _PLATFORM_SYSTEM,
    _ROUTE_SYSTEM,
    _TESTER_CASES_SYSTEM,
)


def test_jobs_manifest_is_the_source_of_generated_jobs_module(tmp_path):
    src = _render_jobs_module(
        catalog={"kernel": "COLLECTOR", "title": "Binding surveyor"},
        capabilities=[{"id": "orders", "http": {"create": "POST /v1/orders"}}],
        gates={"kernel": "TESTER", "runs_over_http": False},
    )
    jobs_path = tmp_path / "app" / "jobs.py"
    jobs_path.parent.mkdir()
    jobs_path.write_text(src, encoding="utf-8")
    ns: dict = {"__file__": str(jobs_path)}
    exec(compile(src, str(jobs_path), "exec"), ns)
    assert [j["kernel"] for j in ns["JOBS"]] == [p.value for p in BUILD_PHASES]
    assert ns["CATALOG"]["title"] == "Binding surveyor"
    assert ns["GATES"]["runs_over_http"] is False
    assert ns["inventory"]()["kernel"] == "CLONER"
    assert ns["provenance"]()["kernel"] == "STORE_MANAGER"
    assert ns["provenance"]()["store_ops"] == []


def test_rendered_router_registers_kernel_routes_before_capabilities():
    src = _render_routes(
        [
            {
                "capability_id": "orders",
                "name": "orders",
                "entity": "order",
                "body": '    return {"ok": True}',
                "source": "template",
            }
        ]
    )
    jobs_at = src.index('@router.get("/jobs")')
    catalog_at = src.index('@router.get("/catalog")')
    cap_at = src.index('@router.post("/orders")')
    get_id_at = src.index('@router.get("/orders/{item_id}")')
    assert jobs_at < catalog_at < cap_at < get_id_at
    for name in KERNEL_ROUTE_NAMES:
        assert f'@router.get("/{name}")' in src
    assert "HTTPException" in src
    assert "from app import jobs, store" in src
    assert "except Exception as exc" in src


def test_coder_prompts_carry_each_kernel_jd():
    collector = role_contract("COLLECTOR")
    writer = role_contract("WRITER")
    tester = role_contract("TESTER")
    assert collector.title in _COLLECTOR_REVIEW_SYSTEM
    assert "collector kernel" in _COLLECTOR_REVIEW_SYSTEM
    assert writer.title in _PLATFORM_SYSTEM
    assert "Unknown channel: in_process" in _PLATFORM_SYSTEM
    assert 'channel to "mcp"' in _PLATFORM_SYSTEM
    assert writer.title in _ROUTE_SYSTEM
    assert "GET /v1/jobs" in _ROUTE_SYSTEM
    assert tester.title in _TESTER_CASES_SYSTEM
    assert "tester kernel" in _TESTER_CASES_SYSTEM
    assert "Binding surveyor" in kernel_seat_brief("COLLECTOR")
    roster = {j["kernel"]: j for j in jobs_manifest()}
    assert roster["CLONER"]["agent"] == "none"
    assert roster["STORE_MANAGER"]["agent"] == "none"

def test_every_capability_crud_route_requires_the_platform_token():
    """No capability route is unauthenticated: create/list/get/update/delete
    and the work_queue trio all guard with require_platform_token. The live
    audit (vetclinic) found GET/PUT/DELETE open — creation-only auth."""
    src = _render_routes(
        [
            {
                "capability_id": "orders",
                "name": "orders",
                "entity": "order",
                "body": '    return {"ok": True}',
                "source": "template",
            }
        ]
    )
    # create + list + get + update + delete (capability) and
    # enqueue + process + list (work_queue).
    assert src.count("require_platform_token(request)") == 8
    for route in (
        '@router.post("/orders")',
        '@router.get("/orders")',
        '@router.get("/orders/{item_id}")',
        '@router.put("/orders/{item_id}")',
        '@router.delete("/orders/{item_id}")',
    ):
        block = src[src.index(route):]
        assert "require_platform_token" in block[:400], route
