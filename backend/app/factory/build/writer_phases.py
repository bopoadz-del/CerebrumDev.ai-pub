"""One FACTORY_CODE_CLI WRITER, three gated C-BRIEF phases.

Owner 2026-09-12: the coding agent is still one seat. The compiler cuts
the same deterministic brief into three DO / ACCEPTANCE gates with STOP
between them. Resume reuses the #403 ledger checkpoint spine
(``stage=checkpoint`` + ``blueprint_hash``) so a landed phase is not
redone from zero. Phase N must accept before phase N+1 dispatch.

An LLM never writes these cuts. #403 ramp and #405 reuse_accept stay
independent — this module does not skip, weaken, or timeout them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.factory.build.brief_lines import emitted_line_set
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.factory.build.persist_accept import persist_handler_rel
from app.factory.build.ui_surface import MIN_MODULE_CHARS, declared_ui_modules

#: Same ledger NOTE stage as per-capability resume (#403).
CHECKPOINT_STAGE = "checkpoint"

WRITER_PHASE_BACKEND = "backend"
WRITER_PHASE_FRONTEND_RAG = "frontend_rag"
WRITER_PHASE_INTEGRATION = "integration"

WRITER_PHASES: Tuple[str, ...] = (
    WRITER_PHASE_BACKEND,
    WRITER_PHASE_FRONTEND_RAG,
    WRITER_PHASE_INTEGRATION,
)

#: A capability owes the RAG ingest + query routes when it binds a block that
#: retrieves (inventory_needs_rag). Its persist POST/GET is not that surface.

#: The platform's RAG contract, as the brief states it.
RAG_INGEST_PATHS = ("/v1/rag/ingest",)
RAG_QUERY_PATHS = ("/v1/rag/query",)
#: The ingest body fields that carry the document text. Stated to the writer
#: in the PHASE 2 brief below, rendered into the Factory's keep-path RAG module
#: and read by the Store gate's plant -- one declaration, so the plant never
#: posts a body shape the brief never asked for.
RAG_INGEST_TEXT_FIELDS = ("text", "content", "paragraph")

#: What the checker accepts: the contract's shape, under any route prefix a
#: product uses (``/v1/rag/ingest`` or ``/v1/<prefix>/rag/ingest``). The shape
#: is the rule; no product's routes are listed.
_RAG_ROUTE_RE = re.compile(r"""["'](/v1/(?:[a-z0-9_]+/)*rag/(ingest|query))["']""")

PHASE_TITLES = {
    WRITER_PHASE_BACKEND: "BACKEND",
    WRITER_PHASE_FRONTEND_RAG: "FRONTEND + RAG",
    WRITER_PHASE_INTEGRATION: "INTEGRATION + RENDER-READY",
}

RENDER_READY_RELS = (
    Path("Dockerfile"),
    # Was render.yaml. The phase keeps its name, but what it gates on is the
    # platform-neutral deploy contract: Render is gone, and a blueprint for a
    # suspended account is not evidence that a tree would deploy.
    Path("deploy") / "contract.json",
    Path("app") / "main.py",
)


class PhaseAcceptHalt(RuntimeError):
    """Phase N acceptance failed. Do not dispatch phase N+1."""


@dataclass(frozen=True)
class WriterPhase:
    phase_id: str
    index: int  # 1-based
    title: str

    @property
    def check(self) -> str:
        return f"writer_phase_{self.phase_id}"


def writer_phase(phase_id: str) -> WriterPhase:
    cid = str(phase_id or "").strip()
    if cid not in WRITER_PHASES:
        raise ValueError(f"unknown writer phase: {phase_id!r}")
    return WriterPhase(
        phase_id=cid,
        index=WRITER_PHASES.index(cid) + 1,
        title=PHASE_TITLES[cid],
    )


def prior_writer_phase(phase_id: str) -> Optional[str]:
    cid = str(phase_id or "").strip()
    if cid not in WRITER_PHASES:
        return None
    idx = WRITER_PHASES.index(cid)
    if idx == 0:
        return None
    return WRITER_PHASES[idx - 1]


def phase_step0_line(phase_id: Optional[str] = None) -> str:
    """STEP 0 cut. PHASE 2 must not hide RAG HTTP behind 'named GAPS only'.

    sess_5782f226 run6: ``Author only named GAPS`` plus ``work=6`` capability
    hole-fill let kimi complete PHASE 2 without quoting ingest/query.
    """
    shared = (
        "STEP 0 INVENTORY + registry REUSE (this phase): use CUT 1 "
        "verified-present ids. Bind them. Author only named GAPS. "
        "Do not invent a block id."
    )
    if str(phase_id or "").strip() != WRITER_PHASE_FRONTEND_RAG:
        return shared
    return (
        "STEP 0 INVENTORY + registry REUSE (this phase): use CUT 1 "
        "verified-present ids. Bind them. Author named GAPS. "
        "When inventory names rag / dual_rag, HARD WRITE "
        "app/rag_routes.py ingest/query HTTP — those routes are not named "
        "GAPS and not persist POST/GET. Do not invent a block id."
    )


def phase_do_text(phase_id: str) -> str:
    """Deterministic DO cut. No LLM."""
    spec = writer_phase(phase_id)
    shared = (
        f"# PHASE {spec.index} of 3 — {spec.title} (DO)\n"
        f"{phase_step0_line(spec.phase_id)}\n"
        "One FACTORY_CODE_CLI writer. No extra coder roles."
    )
    if spec.phase_id == WRITER_PHASE_BACKEND:
        body = (
            "Write routes, handlers, and schema only.\n"
            "Every required capability gets a one-record POST/GET round-trip "
            "(POST creates, GET returns it).\n"
            "Honour reuse_accept / schema-accept / persist-accept. "
            "Do not skip BLOCK_DEFAULT_ACTIONS.\n"
            "STOP / checkpoint after PHASE 1 acceptance. "
            "Do not start PHASE 2."
        )
    elif spec.phase_id == WRITER_PHASE_FRONTEND_RAG:
        body = (
            "UI on the working backend (frontend modules call the live POST/GET "
            "routes). Do not invent a second API.\n"
            "RAG ingest/query where the inventory names a rag / dual_rag "
            "surface (capability id contains rag, or block id rag / dual_rag / "
            "rag_*) — otherwise do not invent a RAG surface.\n"
            "When RAG is owed, ship these HTTP routes as quoted paths in "
            "app/**/*.py (not docs/rag JSON, not phase-1 persist POST/GET): "
            "ingest POST /v1/rag/ingest, taking the document text in any of "
            "the JSON body fields " + "/".join(RAG_INGEST_TEXT_FIELDS) + "; "
            "query GET or POST /v1/rag/query "
            "(a route prefix before /rag/ is allowed).\n"
            "HARD WRITE app/rag_routes.py (Factory keep-path plants this "
            "file when the CLI miss-scopes work items to capability "
            "gaps_only): quoted POST /v1/rag/ingest and GET|POST "
            "/v1/rag/query. Acceptance cannot pass without those quoted "
            "paths in app/**/*.py.\n"
            "A vector_search bind is reuse_accept — do not invent a second "
            "vector store. It is not a substitute for those ingest/query "
            "routes. A capability's one-record POST/GET is persist, not "
            "ingest/query.\n"
            "STOP / checkpoint after PHASE 2 acceptance. "
            "Do not start PHASE 3."
        )
    else:
        body = (
            "Package, boot, and integration: shippable tree that *would* "
            "deploy (Dockerfile + deploy/contract.json + app/main.py).\n"
            "render-ready is not live Render deploy and not Store Docker "
            "acceptance — those stay owner-gated.\n"
            "Then existing TESTER / STORE_MANAGER. STOP / checkpoint after "
            "PHASE 3 acceptance."
        )
    return f"{shared}\n{body}"


def phase_acceptance_lines() -> str:
    """ACCEPTANCE bullets the harness runs between dispatches."""
    return "\n".join(
        [
            "- PHASE 1 of 3 BACKEND accepted: routes/handlers/schema and "
            "one-record POST/GET per required capability  "
            "[check:writer_phase_backend]",
            "- PHASE 2 of 3 FRONTEND + RAG accepted: UI on working backend; "
            "RAG ingest/query where needed — POST /v1/rag/ingest plus "
            "GET|POST /v1/rag/query (any route prefix) in app/**/*.py  "
            "[check:writer_phase_frontend_rag]",
            "- PHASE 3 of 3 INTEGRATION accepted: package/boot/render-ready "
            "(not live Render; not Store Docker)  "
            "[check:writer_phase_integration]",
            "- fail-closed: phase N acceptance before phase N+1 dispatch  "
            "[check:writer_phase_gate]",
            "- resume skips landed writer phases (do not redo completed "
            "earlier phases from zero)  [check:writer_phase_resume]",
        ]
    )


def phase_forbidden_lines() -> str:
    return "\n".join(
        [
            "- starting PHASE N+1 before PHASE N acceptance "
            "[check:writer_phase_gate]",
            "- redoing a landed writer phase from zero "
            "[check:writer_phase_resume]",
            "- extra coder roles — one FACTORY_CODE_CLI writer",
            "- treating PHASE 3 render-ready as live Render deploy or "
            "Store Docker acceptance",
        ]
    )


def compile_phase_brief(compiled: Any, phase_id: str) -> Any:
    """Overlay the active-phase banner on the compiled whole-job brief."""
    spec = writer_phase(phase_id)
    banner = "\n".join(
        [
            f"# PHASE {spec.index} of 3 ACTIVE — {spec.title}",
            "One FACTORY_CODE_CLI writer. Do only this phase.",
            "STOP after this phase's acceptance. Do not start the next phase.",
            "Resume must not redo a landed earlier phase from zero.",
            phase_step0_line(spec.phase_id),
            "",
            phase_do_text(phase_id),
        ]
    )
    sources = dict(getattr(compiled, "line_sources", None) or {})
    for line in banner.splitlines():
        stripped = line.strip()
        if stripped:
            sources[stripped[:80]] = f"writer_phases.{spec.phase_id}"
    text = banner + "\n\n" + str(getattr(compiled, "text", "") or "")
    if hasattr(compiled, "text"):
        emitted = frozenset(getattr(compiled, "emitted_lines", None) or ())
        return replace(
            compiled,
            text=text,
            line_sources=sources,
            emitted_lines=emitted | emitted_line_set(banner),
        )
    return compiled


def should_dispatch_writer_phase(
    phase_id: str,
    dispatch: Any,
    remaining_work: Optional[Sequence[str]] = None,
) -> bool:
    """Later phases open another FACTORY_CODE_CLI session only.

    HTTP oneshot / skipped / unavailable stay one dispatch (PHASE 1).
    That preserves keyed-path CI (one compiled-brief shot) and does not
    invent extra coder roles.

    When ``remaining_work`` is passed, a later phase with no open
    C-BRIEF ids (handlers already keepable, or a prior empty CLI
    already recorded those gaps) must not re-dispatch the same list.
    """
    cid = str(phase_id or "").strip()
    if cid == WRITER_PHASE_BACKEND:
        return True
    via = str(getattr(dispatch, "via", "") or "")
    if via != "cli":
        return False
    if remaining_work is not None:
        return bool(remaining_work)
    return True


def landed_phase_ids(ledger: Any, inputs_hash: str) -> List[str]:
    """Writer phases already checkpointed for this ``blueprint_hash``."""
    digest = str(inputs_hash or "")
    out: List[str] = []
    seen: set = set()
    events = ledger.events() if hasattr(ledger, "events") else ()
    for event in events:
        payload = getattr(event, "payload", None) or {}
        if str(payload.get("stage") or "") != CHECKPOINT_STAGE:
            continue
        if str(payload.get("inputs_hash") or "") != digest:
            continue
        phase = str(payload.get("phase") or "").strip()
        if phase in WRITER_PHASES and phase not in seen:
            seen.add(phase)
            out.append(phase)
    return out


def later_writer_phases() -> Tuple[str, ...]:
    """PHASE 2–3 this WRITER invocation, even if already on the resume spine.

    sess_5782f226 run8: TESTER rework reopened BACKEND, last_event stuck
    on ``landed writer phase backend``, and frontend_rag / integration
    never re-stamped before outer WRITER returned. Resume still skips
    re-dispatch of landed PHASE 1; land stamps for 2–3 must happen
    before this pass completes.
    """
    return (WRITER_PHASE_FRONTEND_RAG, WRITER_PHASE_INTEGRATION)


def pending_writer_phases(ctx: Any) -> List[str]:
    """Phase order minus phases already on the resume spine."""
    landed = {
        str(item)
        for item in (getattr(ctx, "state", {}) or {}).get("landed_writer_phases") or ()
        if item
    }
    return [phase for phase in WRITER_PHASES if phase not in landed]


def should_reopen_writer_phase(ctx: Any, phase_id: str) -> bool:
    """Resume skips landed phases; TESTER rework reopens BACKEND.

    A process-kill resume (empty ``work_list``) must not redo PHASE 1
    from zero. A TESTER rework carries findings in ``work_list`` and
    must compile + dispatch the backend brief again so the coder sees
    them (``test_rework_findings_are_handed_to_the_coder``).
    """
    cid = str(phase_id or "").strip()
    if getattr(ctx, "work_list", None):
        return cid == WRITER_PHASE_BACKEND
    return cid in pending_writer_phases(ctx)


def checkpoint_landed_phase(ctx: Any, phase_id: str) -> None:
    """Persist one landed writer phase onto the #403 resume spine."""
    spec = writer_phase(phase_id)
    state = getattr(ctx, "state", None)
    if not isinstance(state, dict):
        return
    digest = str(state.get("inputs_hash") or "")
    landed = [str(item) for item in (state.get("landed_writer_phases") or []) if item]
    if spec.phase_id not in landed:
        landed.append(spec.phase_id)
        state["landed_writer_phases"] = landed
    note = getattr(ctx, "note", None)
    if callable(note):
        note(
            f"landed writer phase {spec.phase_id}",
            stage=CHECKPOINT_STAGE,
            phase=spec.phase_id,
            inputs_hash=digest,
        )


def inventory_needs_rag(compiled: Any, store_root: Any = None) -> bool:
    """True when STEP 0 binds a block that retrieves.

    A capability owes the RAG ingest/query contract when it binds a Store
    block whose signed block.json declares the retrieval read (the same
    signal that makes the floor's rag_roundtrip_hit a veto) -- never because
    of what the capability or the block is called.
    """
    from app.factory.build.acceptance_floor import binds_retrieving_block

    bids = [
        bid
        for item in getattr(compiled, "inventory", ()) or ()
        for bid in list(getattr(item, "block_ids", ()) or ())
        + list(getattr(item, "verified_present", ()) or ())
    ]
    return binds_retrieving_block(bids, store_root=store_root)


def required_capability_ids(compiled: Any) -> List[str]:
    caps = list(getattr(compiled, "capabilities", ()) or [])
    return [str(c).strip() for c in caps if str(c).strip()]


def _workspace_root(ctx: Any) -> Path:
    ws = getattr(ctx, "workspace", None)
    if ws is None:
        return Path(".")
    return Path(getattr(ws, "workspace", ws))


def _read_if(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _app_python_sources(root: Path) -> str:
    """Concatenated ``app/**/*.py``. Docs JSON stamps are not routes."""
    app = root / "app"
    if not app.is_dir():
        return ""
    parts: List[str] = []
    for path in sorted(app.rglob("*.py")):
        parts.append(_read_if(path))
    return "\n".join(parts)


def _rag_routes(root: Path) -> set:
    return {m.group(2) for m in _RAG_ROUTE_RE.finditer(_app_python_sources(root))}


def rag_ingest_route_present(root: Path) -> bool:
    return "ingest" in _rag_routes(root)


def rag_query_route_present(root: Path) -> bool:
    return "query" in _rag_routes(root)


def phase_acceptance_errors(
    ctx: Any,
    phase_id: str,
    compiled: Any,
) -> List[str]:
    """File-level phase gate. No live HTTP, no Docker, no Render deploy."""
    spec = writer_phase(phase_id)
    root = _workspace_root(ctx)
    errors: List[str] = []
    if spec.phase_id == WRITER_PHASE_BACKEND:
        routes = _read_if(root / "app" / "routes.py")
        models = root / "app" / "models.py"
        if not models.is_file():
            errors.append("app/models.py missing (schema)")
        for cid in required_capability_ids(compiled):
            rel = persist_handler_rel(cid)
            if not (root / rel).is_file():
                errors.append(f"{cid}: handler missing ({rel})")
            name = cid.replace("-", "_")
            if routes and f"/v1/{name}" not in routes and name not in routes:
                errors.append(f"{cid}: no POST/GET route")
            elif not routes:
                errors.append(f"{cid}: app/routes.py missing (POST/GET)")
    elif spec.phase_id == WRITER_PHASE_FRONTEND_RAG:
        modules = declared_ui_modules(root)
        ui_root = root / "frontend" / "src" / "modules"
        if modules:
            if not ui_root.is_dir():
                errors.append(
                    f"{len(modules)} UI module(s) declared but frontend/ was not emitted"
                )
            else:
                for module in modules:
                    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in module)
                    path = ui_root / f"{safe}.tsx"
                    if not path.is_file():
                        errors.append(f"declared UI module {module!r} was not emitted")
                    else:
                        body = _read_if(path)
                        if len(body.strip()) < MIN_MODULE_CHARS:
                            errors.append(
                                f"{module}: emitted as a {len(body.strip())}-char placeholder"
                            )
        if inventory_needs_rag(compiled):
            if not rag_ingest_route_present(root):
                errors.append(
                    "RAG ingest/query where needed — ingest route missing "
                    "(need POST /v1/rag/ingest or POST /v1/steward/rag/ingest "
                    "quoted in app/**/*.py)"
                )
            if not rag_query_route_present(root):
                errors.append(
                    "RAG ingest/query where needed — query route missing "
                    "(need GET|POST /v1/rag/query or GET|POST "
                    "/v1/steward/rag/query quoted in app/**/*.py)"
                )
    else:
        for rel in RENDER_READY_RELS:
            if not (root / rel).is_file():
                errors.append(f"render-ready missing {rel.as_posix()}")
    return errors


def accept_writer_phase(
    ctx: Any,
    phase_id: str,
    compiled: Any,
) -> None:
    """Fail-closed: prior phase must already be landed; this phase must accept."""
    spec = writer_phase(phase_id)
    state = getattr(ctx, "state", None) or {}
    landed = {str(item) for item in (state.get("landed_writer_phases") or ()) if item}
    prior = prior_writer_phase(spec.phase_id)
    if prior and prior not in landed:
        raise PhaseAcceptHalt(
            f"WRITER [check:writer_phase_gate] failed — {spec.phase_id} "
            f"before {prior} accepted (fail-closed: phase N acceptance "
            "before phase N+1 dispatch)"
        )
    errors = phase_acceptance_errors(ctx, spec.phase_id, compiled)
    if errors:
        raise PhaseAcceptHalt(
            f"WRITER [check:{spec.check}] failed — " + "; ".join(errors[:8])
        )


def writer_phase_slot_bodies() -> Dict[str, str]:
    """Deterministic fragments injected into BUILD / ACCEPTANCE / FORBIDDEN."""
    return {
        "BUILD": "\n\n".join(phase_do_text(phase) for phase in WRITER_PHASES),
        "ACCEPTANCE": phase_acceptance_lines(),
        "FORBIDDEN": phase_forbidden_lines(),
    }
