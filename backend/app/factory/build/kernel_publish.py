"""The kernel roster a platform publishes (``app/jobs.py``) -- one source.

``GET /v1/jobs`` / ``/v1/catalog`` / ``/v1/gates`` publish the Factory's own
role contracts. That data is the Factory's, never the coder's, and TESTER's
emitted ``test_kernel_jobs_roster`` asserts it. Both read it from here.

``run_writer`` stamped ``app/jobs.py`` only on its template path, below the
CodeWhale branch it returns at -- so in production the agent wrote its own
copy and the stamped test asserted keys that copy did not carry (live
2026-10-06, vineyard repro on dd1b353d: ``CATALOG = {}`` / ``GATES = {}`` ->
``KeyError: 'kernel'``, a FACTORY_FAULT the writer could never fix). The
CodeWhale path now stamps the roster too, from these same functions.

``CAPABILITIES`` is different: it is the product's capability manifest, and
it is one of the declared-entity sources the round-trip probes read
(``entity_contract``). The stamp KEEPS whatever manifest the product already
declares and never invents an entity (no capability-id fallback).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from app.factory.build.authority import BuildRole, role_contract

JOBS_REL = Path("app") / "jobs.py"

#: The kernels the roster publishes, in pipeline order.
ROSTER_ROLES = (
    BuildRole.COLLECTOR,
    BuildRole.CLONER,
    BuildRole.WRITER,
    BuildRole.TESTER,
    BuildRole.STORE_MANAGER,
)

#: The suites TESTER stamps, as the roster describes them.
TESTER_SUITE: tuple = (
    {"file": "tests/test_smoke.py", "covers": "import, offline dispatch load, handle() returns a mapping", "gated": True},
    {"file": "tests/test_smoke.py", "covers": "Store-backed handle() ok and nested error scan", "marker": "pilot", "gated": False},
    {"file": "tests/test_models.py", "covers": "sqlite round-trip via store.save / store.get", "gated": True},
    {"file": "tests/test_data_lifecycle.py", "covers": "Alembic up/down on populated v1, restore drill, parallel writes", "gated": True},
    {"file": "tests/test_deploy.py", "covers": "Fail-closed /health, correlation logs, revision rollback identity", "gated": True},
    {"file": "tests/test_domain_acceptance.py", "covers": "Ten business outcomes through execute_action", "marker": "pilot", "gated": False},
    {"file": "tests/test_routes.py", "covers": "HTTP 200 JSON for /health, kernel jobs, and each capability POST", "gated": True},
    {"file": "tests/test_routes.py", "covers": "Store-backed POST accepted (ok is not False) and persisted", "marker": "pilot", "gated": False},
    {"file": "tests/agent_domain_cases.py", "covers": "optional coding-agent domain mutations of spec payloads", "optional": True, "gated": False},
)


def roster_titles() -> Dict[str, str]:
    """kernel -> published title, from the role contracts."""
    return {role.value: role_contract(role).title for role in ROSTER_ROLES}


def collector_catalog(state: Mapping[str, Any], plan: Any) -> Dict[str, Any]:
    collector = role_contract(BuildRole.COLLECTOR)
    gaps = {str(g) for g in (state.get("gaps") or ())}
    return {
        "kernel": collector.role.value,
        "title": collector.title,
        "mandate": collector.mandate,
        "agent": collector.agent.value,
        "resolved_blocks": list(state.get("resolved_blocks") or state.get("vendored_blocks") or []),
        "gaps": list(state.get("gaps") or []),
        "bindings": [
            {
                "capability_id": cap.capability_id,
                "block_ids": list(cap.block_ids or []),
                "gap": cap.capability_id in gaps or not cap.block_ids,
            }
            for cap in getattr(plan, "capabilities", ()) or ()
        ],
        "agent_reviews": list(state.get("agent_binding_reviews") or []),
        "agent_model": state.get("agent_binding_model") or "",
    }


def tester_gates() -> Dict[str, Any]:
    tester = role_contract(BuildRole.TESTER)
    return {
        "kernel": tester.role.value,
        "title": tester.title,
        "mandate": tester.mandate,
        "agent": tester.agent.value,
        "runs_over_http": False,
        "suite": [dict(item) for item in TESTER_SUITE],
    }


def capabilities_from_entries(entries: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The manifest the template path declares, from its rendered routes."""
    return [
        {
            "id": e["capability_id"],
            "entity": e["entity"],
            "source": e["source"],
            "http": {
                "create": f"POST /v1/{e['name']}",
                "list": f"GET /v1/{e['name']}",
                "get": f"GET /v1/{e['name']}/{{id}}",
                "update": f"PUT /v1/{e['name']}/{{id}}",
                "delete": f"DELETE /v1/{e['name']}/{{id}}",
            },
        }
        for e in entries
    ]


def declared_capabilities(jobs_src: Optional[str]) -> List[Dict[str, Any]]:
    """The ``CAPABILITIES`` literal an existing ``app/jobs.py`` declares.

    Read from the syntax tree (never executed); anything that is not a plain
    list of dicts declares nothing -- the stamp writes ``[]`` and the probes
    fall through to the product's other declarations.
    """
    if not jobs_src:
        return []
    try:
        tree = ast.parse(jobs_src)
    except SyntaxError:
        return []
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "CAPABILITIES"
        ):
            try:
                value = ast.literal_eval(node.value)
            except ValueError:
                return []
            if isinstance(value, list) and all(isinstance(i, dict) for i in value):
                return [dict(i) for i in value]
            return []
    return []


def render_roster(state: Mapping[str, Any], plan: Any, capabilities: List[Mapping[str, Any]]) -> str:
    from app.factory.build.roles_handlers import _render_jobs_module

    return _render_jobs_module(
        catalog=collector_catalog(state, plan),
        capabilities=[dict(c) for c in capabilities],
        gates=tester_gates(),
    )


def _defined_names(node: ast.stmt) -> List[str]:
    """The top-level names one statement binds."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def _product_declares_capabilities(product_text: str) -> bool:
    """True when the product's own bytes (outside the Factory block) bind
    ``CAPABILITIES`` -- a literal or a roster computed from its models."""
    try:
        tree = ast.parse(product_text)
    except SyntaxError:
        return False
    return any("CAPABILITIES" in _defined_names(n) for n in tree.body)


def roster_block(rendered: str, *, include_capabilities: bool) -> str:
    """The Factory's roster as a block body: every top-level statement of the
    rendered roster except the docstring and ``from __future__`` (which only
    a file's head may carry), and ``CAPABILITIES`` only when the product
    declares none of its own."""
    tree = ast.parse(rendered)
    lines = rendered.splitlines()
    parts: List[str] = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if not include_capabilities and "CAPABILITIES" in _defined_names(node):
            continue
        parts.append("\n".join(lines[node.lineno - 1:node.end_lineno]))
    return "\n\n\n".join(parts) + "\n"


def _current_text(workspace: Any, rel: Path) -> str:
    """The file as the product currently has it, read THROUGH the workspace.

    A staged WRITER pass writes into an empty staging tree while the product's
    bytes stay in the destination (the CodeWhale agent edits the destination
    directly), so a raw staging path reads "no file" -- and a stamp that edits
    "no file" commits its block over the product's own (live 5dd46d47: the
    roster block bound ``CAPABILITIES = []`` over the product's manifest and
    every declared route 404'd). The workspace's own read resolves staging
    first, then the destination.
    """
    resolve = getattr(workspace, "read_path", None)
    if callable(resolve):
        path = Path(resolve(rel))
    else:
        path = Path(getattr(workspace, "workspace", workspace)) / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def stamp_roster(ctx: Any) -> bool:
    """Stamp the kernel roster into ``app/jobs.py``; True when it changed.

    ``app/jobs.py`` is SHARED: the product owns its capability manifest
    (``CAPABILITIES``, which its routes may be built from, in any form), the
    Factory owns the roster (JOBS/CATALOG/GATES and their readers). So the
    stamp edits ONLY its marked block (factory_block) -- appended last, so
    its names are the ones the module ends up binding -- and every byte
    outside it stays exactly as the product wrote it. Re-rendering the whole
    file wrote ``CAPABILITIES = []`` over a computed manifest and every
    capability route vanished (live 9de69276 vineyard repro, 8 POSTs 404).
    """
    from app.factory.build.factory_block import apply_block, outside, split_block

    existing = _current_text(ctx.workspace, JOBS_REL)
    product = outside(existing)
    product_declares = _product_declares_capabilities(product)
    # Keep a manifest the Factory itself carried in its block (template path).
    _b, block_now, _a = split_block(existing)
    carried = declared_capabilities(block_now) if block_now else declared_capabilities(product)
    rendered = render_roster(ctx.state, ctx.plan, [] if product_declares else carried)
    text = apply_block(
        existing, roster_block(rendered, include_capabilities=not product_declares)
    )
    if existing == text:
        return False
    ctx.workspace.write_text(JOBS_REL, text)
    return True


def render_roster_test() -> List[str]:
    """TESTER's ``test_kernel_jobs_roster`` lines, from the same contracts.

    Every value it asserts -- the kernel set, each title, which kernel each
    distinctive route belongs to -- is read from the role contracts that
    render the roster it tests, so the two cannot drift.
    """
    titles = roster_titles()
    collector = BuildRole.COLLECTOR.value
    cloner = BuildRole.CLONER.value
    tester = BuildRole.TESTER.value
    store = BuildRole.STORE_MANAGER.value
    return [
        "def test_kernel_jobs_roster():",
        '    """GET /v1/jobs publishes every kernel JD; distinctive routes answer."""',
        '    resp = client.get("/v1/jobs", headers=AUTH)',
        "    assert resp.status_code == 200",
        '    jobs = resp.json()["jobs"]',
        '    by_kernel = {j["kernel"]: j for j in jobs}',
        f"    assert set(by_kernel) == set({sorted(titles)!r})",
        *(
            f'    assert by_kernel[{kernel!r}]["title"] == {title!r}'
            for kernel, title in titles.items()
        ),
        "    for job in jobs:",
        '        assert job["mandate"] and job["http_routes"] and job["agent"]',
        '    catalog = client.get("/v1/catalog", headers=AUTH)',
        "    assert catalog.status_code == 200",
        f'    assert catalog.json()["kernel"] == {collector!r}',
        '    inventory = client.get("/v1/inventory", headers=AUTH)',
        "    assert inventory.status_code == 200",
        f'    assert inventory.json()["kernel"] == {cloner!r}',
        '    assert "lock" in inventory.json()',
        '    caps_resp = client.get("/v1/capabilities", headers=AUTH)',
        "    assert caps_resp.status_code == 200",
        '    assert isinstance(caps_resp.json()["items"], list)',
        '    gates = client.get("/v1/gates", headers=AUTH)',
        "    assert gates.status_code == 200",
        f'    assert gates.json()["kernel"] == {tester!r}',
        '    assert gates.json()["runs_over_http"] is False',
        '    prov = client.get("/v1/provenance", headers=AUTH)',
        "    assert prov.status_code == 200",
        f'    assert prov.json()["kernel"] == {store!r}',
    ]
