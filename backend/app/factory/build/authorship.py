"""Fail-closed authorship accounting — CLI keep-path is agent-written.

sess_4e1ec7afa3894dc8 on tip 160af0e: harvest kept four FACTORY_CODE_CLI
handlers tagged ``coder CLI (/usr/local/bin/kimi)``, but writer_contract
and Floor authorship only counted ``coder LLM`` prefixes. The operator
saw ``0 by the coding agent, 27 templated`` after a ~16-minute CLI keep.

A capability kept from CLI is coding-agent work. It must not also sit in
the templated inventory for the same run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = (
    "DualListedAuthorshipError",
    "FULL_PILOT_AUTHORSHIP_CHECK",
    "FULL_PILOT_MIN_AUTHORED_ACTIONS",
    "FullPilotAuthorship",
    "coding_agent_artifact_ids",
    "dual_listed_capability_ids",
    "exclusive_authorship_caps",
    "cli_authored_ids_from",
    "full_pilot_authorship_acceptance_line",
    "full_pilot_authorship_forbidden_lines",
    "full_pilot_authorship_from",
    "full_pilot_authorship_need",
    "full_pilot_authorship_rules_text",
    "is_action_artifact_id",
    "action_artifact_id",
    "action_artifact_ids",
    "n_required_capabilities_from",
    "persist_required_capability_inputs",
    "thin_store_green_export_blocker",
    "is_coding_agent_source",
    "kept_handler_ids_from",
    "promote_cli_keep_ids",
    "refuse_dual_listed_caps",
    "writer_authorship_counts",
    "writer_contract_role_detail",
)

#: Absolute launching-ready bar when n_required is unknown or ≥5.
#: Products with fewer required capabilities use that smaller count
#: (``need = min(5, max(1, n_required))``). STORE_GREEN / package zip is
#: not honest below ``need`` agent-written action handlers (or
#: ``cli_authored_ids``).
FULL_PILOT_MIN_AUTHORED_ACTIONS = 5
FULL_PILOT_AUTHORSHIP_CHECK = "full_pilot_authorship"

#: Workspace files that may list required / planned capability ids.
_N_REQUIRED_WORKSPACE_FILES = (
    Path("docs") / "blueprint" / "product_blueprint.json",
    Path("docs") / "product_blueprint.json",
    Path("factory_plan.json"),
    Path("docs") / "provenance" / "provenance.json",
)


def full_pilot_authorship_need(n_required: Optional[int] = None) -> int:
    """Authorship floor for package / Store-green SUCCESS.

    Owner rule after #387/#388 vs 4-cap goldens: refuse thin scaffolds,
    but do not demand 5 handlers when the brief only has 4 required
    capabilities. ``need = min(5, max(1, n_required))`` when the required
    count is known; unknown stays 5.
    """
    if n_required is None:
        return FULL_PILOT_MIN_AUTHORED_ACTIONS
    if isinstance(n_required, bool):
        return FULL_PILOT_MIN_AUTHORED_ACTIONS
    try:
        n = int(n_required)
    except (TypeError, ValueError):
        return FULL_PILOT_MIN_AUTHORED_ACTIONS
    if n <= 0:
        return FULL_PILOT_MIN_AUTHORED_ACTIONS
    return min(FULL_PILOT_MIN_AUTHORED_ACTIONS, max(1, n))


def _capability_id(item: Any) -> str:
    if isinstance(item, str):
        text = item.strip()
        if not text or " " in text or ":" in text or "/" in text:
            return ""
        return text
    if isinstance(item, Mapping):
        return str(item.get("id") or item.get("capability_id") or "").strip()
    return str(
        getattr(item, "id", None) or getattr(item, "capability_id", None) or ""
    ).strip()


def _capability_is_required(item: Any) -> bool:
    if isinstance(item, Mapping):
        if "required" not in item:
            return True
        return item.get("required") is not False
    if hasattr(item, "required"):
        return getattr(item, "required") is not False
    return True


def _count_required_capabilities(items: Any) -> Optional[int]:
    if not items:
        return None
    if isinstance(items, int) and not isinstance(items, bool) and items > 0:
        return items
    if not isinstance(items, (list, tuple)):
        return None
    ids: List[str] = []
    for item in items:
        if not _capability_is_required(item):
            continue
        cid = _capability_id(item)
        if cid and cid not in ids:
            ids.append(cid)
    return len(ids) if ids else None


def _n_required_from_blueprintish(source: Any) -> Optional[int]:
    """Count required caps on a blueprint, plan, compiled brief, or dict."""
    if source is None:
        return None
    caps = getattr(source, "capabilities", None)
    counted = _count_required_capabilities(caps)
    if counted:
        return counted
    if not isinstance(source, Mapping):
        return None
    counted = _count_required_capabilities(source.get("capabilities"))
    if counted:
        return counted
    for nested_key in ("blueprint", "plan"):
        nested = source.get(nested_key)
        if nested is None or nested is source:
            continue
        counted = _n_required_from_blueprintish(nested)
        if counted:
            return counted
    return None


def persist_required_capability_inputs(
    workspace: Path | str,
    *,
    blueprint: Any = None,
    plan: Any = None,
    n_required: Optional[int] = None,
) -> None:
    """Write ``docs/blueprint/product_blueprint.json`` so package can resolve need.

    RoleRunner does not call ``ProductGenerator.generate()``, so Approve→
    GENERATE workspaces often lack that 14-class file. Without it
    ``n_required`` stays unknown (need=5) on the next process that only
    reads the tree. Does not write ``factory_plan.json`` — that stays a
    ProductGenerator extra (emitter parity).
    """
    if n_required is None:
        n_required = _n_required_from_blueprintish(plan)
    root = Path(workspace)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    bp_payload: Optional[Mapping[str, Any]] = None
    if blueprint is not None:
        if isinstance(blueprint, Mapping):
            bp_payload = blueprint
        else:
            try:
                from app.factory.blueprint import blueprint_to_dict

                bp_payload = blueprint_to_dict(blueprint)
            except Exception:  # noqa: BLE001 — persist must not fail a run
                dump = getattr(blueprint, "model_dump", None)
                if callable(dump):
                    try:
                        bp_payload = dump(mode="json")
                    except Exception:  # noqa: BLE001
                        bp_payload = None
    if isinstance(bp_payload, Mapping) and bp_payload.get("capabilities"):
        dest = root / "docs" / "blueprint"
        try:
            dest.mkdir(parents=True, exist_ok=True)
            payload = dict(bp_payload)
            if n_required is not None:
                payload.setdefault("n_required", n_required)
            (dest / "product_blueprint.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass


def n_required_capabilities_from(
    status: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Path | str] = None,
    *,
    plan: Any = None,
    blueprint: Any = None,
    compiled: Any = None,
    state: Optional[Mapping[str, Any]] = None,
) -> Optional[int]:
    """Required-capability count for the authorship floor.

    Prefer an explicit ``n_required`` on status/authorship, then the
    compiled brief / plan / blueprint required caps (including a session
    ``product_design.blueprint``), then workspace
    ``product_blueprint.json`` / ``factory_plan.json``, then the session
    snapshot next to a ``sessions/{id}/{product}`` tree. ``None`` means
    unknown — callers keep need=5.
    """
    blobs: List[Any] = []
    status_map = dict(status or {})
    authorship = status_map.get("authorship")
    if isinstance(authorship, Mapping):
        blobs.append(authorship)
    blobs.append(status_map)
    if isinstance(state, Mapping):
        blobs.append(state)
        nested = state.get("brief_dispatch")
        if isinstance(nested, Mapping):
            blobs.append(nested)

    for blob in blobs:
        if not isinstance(blob, Mapping):
            continue
        for key in ("n_required", "n_required_capabilities"):
            counted = _count_required_capabilities(blob.get(key))
            if counted:
                return counted
        caps = blob.get("required_capabilities")
        counted = _count_required_capabilities(caps)
        if counted:
            return counted

    for blob in blobs:
        if not isinstance(blob, Mapping):
            continue
        for nested_key in ("blueprint", "plan", "product_design"):
            nested = blob.get(nested_key)
            counted = _n_required_from_blueprintish(nested)
            if counted:
                return counted

    for source in (compiled, plan, blueprint):
        counted = _n_required_from_blueprintish(source)
        if counted:
            return counted

    if workspace is None:
        return None
    if hasattr(workspace, "workspace"):
        root = Path(workspace.workspace)
    else:
        try:
            root = Path(workspace)
        except TypeError:
            return None
    if not root.exists():
        return None
    for rel in _N_REQUIRED_WORKSPACE_FILES:
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, Mapping):
            continue
        for key in ("n_required", "n_required_capabilities"):
            counted = _count_required_capabilities(data.get(key))
            if counted:
                return counted
        counted = _count_required_capabilities(data.get("capabilities"))
        if counted:
            return counted
        plan_blob = data.get("plan")
        if isinstance(plan_blob, Mapping):
            counted = _count_required_capabilities(plan_blob.get("capabilities"))
            if counted:
                return counted
    try:
        from app.factory.build.orphan_recovery import load_blueprint_for_workspace

        parked = load_blueprint_for_workspace(root)
    except Exception:  # noqa: BLE001 — session lookup must not fail the floor
        parked = None
    return _n_required_from_blueprintish(parked)


def full_pilot_authorship_rules_text(n_required: Optional[int] = None) -> str:
    """BUILD cut: coder must emit the launching-ready floor before done."""
    n = full_pilot_authorship_need(n_required)
    if n_required is not None and 0 < int(n_required) < FULL_PILOT_MIN_AUTHORED_ACTIONS:
        scale = (
            f" This brief has {int(n_required)} required capabilities, so the "
            f"floor is {n} (dynamic floor — not a fixed "
            f"≥{FULL_PILOT_MIN_AUTHORED_ACTIONS})."
        )
    else:
        scale = ""
    return (
        f"Launching-ready full-pilot authorship floor: emit ≥{n} keepable "
        "agent-written app/actions/*.py handlers (or equivalent "
        f"cli_authored_ids).{scale} Fewer than {n} is "
        "FACTORY_CODE_CLI_THIN_AUTHORSHIP — CODE_GREEN / pilot_ready=false, "
        "package 409. Do not treat the job as done below this floor."
    )


def full_pilot_authorship_acceptance_line(
    n_required: Optional[int] = None,
) -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    n = full_pilot_authorship_need(n_required)
    extra = ""
    if n_required is not None and 0 < int(n_required) < FULL_PILOT_MIN_AUTHORED_ACTIONS:
        extra = (
            f" (dynamic floor: {int(n_required)} required capabilities → "
            f"need ≥{n}, not a fixed ≥{FULL_PILOT_MIN_AUTHORED_ACTIONS})"
        )
    return (
        f"- launching-ready full-pilot authorship: ≥{n} keepable "
        "agent-written app/actions/*.py handlers (or equivalent "
        f"cli_authored_ids){extra}  [check:{FULL_PILOT_AUTHORSHIP_CHECK}]"
    )


def full_pilot_authorship_forbidden_lines(
    n_required: Optional[int] = None,
) -> str:
    """FORBIDDEN cut: the #387 thin-authorship refuse the coder must see."""
    n = full_pilot_authorship_need(n_required)
    extra = ""
    if n_required is not None and 0 < int(n_required) < FULL_PILOT_MIN_AUTHORED_ACTIONS:
        extra = " (dynamic floor)"
    return (
        f"- authorship below the launching-ready full-pilot floor "
        f"(<{n} agent-written app/actions/*.py / cli_authored_ids){extra} — "
        "FACTORY_CODE_CLI_THIN_AUTHORSHIP"
    )


#: Writer extras that are not ``app/actions/*.py`` handlers.
_NON_ACTION_ARTIFACT_IDS = frozenset(
    {
        "jobs",
        "readme",
        "entrypoint",
        "requirements",
        "release_gate",
        "deploy_scaffold",
        "network_posture",
        "sbom",
        "permissions",
        "domain_pack",
        "persistence",
        "migrations",
        "deploy_observe",
        "domain_acceptance",
        "emitter_parity",
    }
)


class DualListedAuthorshipError(ValueError):
    """A capability cannot be both agent-written and templated."""


#: The authorship marker: a module-level ``AUTHORED_BY = "<source>"``
#: assignment in each action handler. It is read from the SYNTAX TREE --
#: a docstring sentence decides nothing (a prose stamp is text, and the
#: Factory never decides from text). One name, used by every writer and
#: reader: the Factory's emitters, the writer brief, this module, the
#: stamped acceptance harness and the release gate.
AUTHORSHIP_MARKER = "AUTHORED_BY"


def marker_source(text: str) -> str:
    """The source a handler module's ``AUTHORED_BY`` assignment names, or ''.

    Only a module-level ``AUTHORED_BY = "<str>"`` (or annotated) counts; a
    file that does not parse, or a marker inside a function or a string,
    names nothing.
    """
    import ast

    try:
        tree = ast.parse(text or "")
    except (SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == AUTHORSHIP_MARKER for t in targets):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value.strip()
    return ""


def authorship_marker_line(source: str) -> str:
    """The line every writer puts at a handler's module level."""
    return f"{AUTHORSHIP_MARKER} = {str(source)!r}"


#: The reader above as source, rendered into scripts a delivered product
#: runs without the Factory (the acceptance harness, the release gate).
RENDERED_MARKER_READER = f'''
def _authored_by(text):
    import ast as _ast
    try:
        tree = _ast.parse(text or "")
    except (SyntaxError, ValueError):
        return ""
    for node in tree.body:
        if isinstance(node, _ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, _ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, _ast.Name) and t.id == {AUTHORSHIP_MARKER!r} for t in targets):
            if isinstance(value, _ast.Constant) and isinstance(value.value, str):
                return value.value.strip()
    return ""
'''


def agent_written_handler_ids_in_workspace(workspace: Path | str) -> List[str]:
    """Handler files whose stamped WRITER source is the coding agent.

    Ground truth for the writer-contract gate: a gate must not trust the
    writer's own status claim, so the agent-authored set is re-derived from
    the files the writer physically produced (the module-level
    ``AUTHORED_BY`` assignment in ``app/actions/*.py``). Zero is
    ``writer_no_output`` -- templated and factory-grounded writes do not
    count.
    """
    root = Path(workspace)
    actions = root / "app" / "actions"
    if not actions.is_dir():
        return []
    ids: List[str] = []
    for path in sorted(actions.glob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if is_coding_agent_source(marker_source(text)):
            ids.append(path.stem)
    return ids


#: Stamp sources that mean "a coding agent wrote this". ONE vocabulary:
#: scripts the factory renders into a product (the acceptance floor's
#: authorship_floor, the release gate) derive their test from these rather
#: than keeping a private copy -- a private copy is what drifted behind the
#: CodeWhale writer and reported authored=0 for a fully agent-written tree
#: (live 2026-10-01, automotive_aiops: acceptance 20/21 on that line alone).
#: ``codewhale`` is Phase 5's headless worker; its stamped handlers count
#: toward the artifact gate like any other.
AGENT_SOURCE_PREFIXES = ("coder LLM", "coder CLI", "FACTORY_CODE_CLI", "codewhale")
AGENT_SOURCE_EXACT = frozenset({"harvested workspace handler", "compiled-brief oneshot"})


def is_coding_agent_source(source: Any) -> bool:
    """True for coder LLM, coder CLI, codewhale, or harvested keep-path labels.

    Factory-grounded persist/event_bus emit is not the coding agent.
    Deterministic templates are not the coding agent.
    """
    text = str(source or "").strip()
    if not text:
        return False
    if text.startswith(AGENT_SOURCE_PREFIXES):
        return True
    return text.lower() in AGENT_SOURCE_EXACT


#: Exact stamps the Factory writes for its OWN grounded emit and templates.
#: A source is classified by membership in this closed vocabulary -- the
#: strings the emitters write -- never by searching a stamp for a word.
SOURCE_CODER_CLI = "coder CLI"
TEMPLATED_SOURCE = "deterministic contract template"
FACTORY_GROUNDED_KEEP_PATH_SOURCE = "factory-grounded reuse keep-path"


def factory_grounded_sources() -> frozenset:
    """Every stamp the Factory's grounded emitters write (exact strings)."""
    from app.factory.build.persist_accept import FACTORY_GROUNDED_PERSIST_SOURCE
    from app.factory.build.rag_surface import FACTORY_GROUNDED_RAG_SOURCE
    from app.factory.build.workflow_accept import FACTORY_GROUNDED_EVENT_BUS_SOURCE

    return frozenset(
        {
            FACTORY_GROUNDED_PERSIST_SOURCE,
            FACTORY_GROUNDED_RAG_SOURCE,
            FACTORY_GROUNDED_EVENT_BUS_SOURCE,
            FACTORY_GROUNDED_KEEP_PATH_SOURCE,
        }
    )


def is_factory_grounded_source(source: Any) -> bool:
    return str(source or "").strip() in factory_grounded_sources()


def is_templated_source(source: Any) -> bool:
    return str(source or "").strip() == TEMPLATED_SOURCE


def stamped_writer_source(text: str) -> str:
    """The source a handler file's ``AUTHORED_BY`` marker names, or ''."""
    return marker_source(text)


def coding_agent_artifact_ids(sources: Optional[Mapping[str, Any]]) -> List[str]:
    return sorted(
        str(k) for k, v in (sources or {}).items() if is_coding_agent_source(v)
    )


def writer_authorship_counts(
    sources: Optional[Mapping[str, Any]],
) -> Dict[str, int]:
    src = dict(sources or {})
    written = len(coding_agent_artifact_ids(src))
    return {
        "artifacts": len(src),
        "agent_written": written,
        "templated": len(src) - written,
    }


def writer_contract_role_detail(
    capabilities: Sequence[str],
    sources: Optional[Mapping[str, Any]],
) -> str:
    counts = writer_authorship_counts(sources)
    return (
        f"{len(capabilities)} capability(ies); {counts['artifacts']} artifact(s) — "
        f"{counts['agent_written']} by the coding agent, "
        f"{counts['templated']} templated"
    )


def _dispatch_map(state_or_dispatch: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    raw = dict(state_or_dispatch or {})
    nested = raw.get("brief_dispatch")
    if isinstance(nested, Mapping):
        return nested
    return raw


def kept_handler_ids_from(state_or_dispatch: Optional[Mapping[str, Any]]) -> List[str]:
    """``brief_dispatch.kept_handler_ids`` from state or the dispatch map itself."""
    dispatch = _dispatch_map(state_or_dispatch)
    ids: List[str] = []
    for item in dispatch.get("kept_handler_ids") or ():
        cid = str(item or "").strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def cli_authored_ids_from(state_or_dispatch: Optional[Mapping[str, Any]]) -> Optional[List[str]]:
    """Explicit CLI harvest ids, or None when the field was never recorded.

    An empty list after kimi/DeepSeek exit 0 is not a keep-path credit
    (``FACTORY_CODE_CLI_NO_AUTHORSHIP``). Missing key keeps #375
    ``kept_handler_ids`` promotion for real CLI keep-path receipts.
    """
    dispatch = _dispatch_map(state_or_dispatch)
    if "cli_authored_ids" not in dispatch:
        return None
    ids: List[str] = []
    for item in dispatch.get("cli_authored_ids") or ():
        cid = str(item or "").strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def promote_cli_keep_ids(state_or_dispatch: Optional[Mapping[str, Any]]) -> List[str]:
    """Ids inspect may credit as CLI keep-path writes.

    Prefer ``cli_authored_ids`` when present (including empty). Otherwise
    fall back to ``kept_handler_ids`` for receipts that predate that field.
    """
    authored = cli_authored_ids_from(state_or_dispatch)
    if authored is not None:
        return authored
    return kept_handler_ids_from(state_or_dispatch)


def exclusive_authorship_caps(
    caps_written: Sequence[str],
    caps_templated: Sequence[str],
) -> Tuple[List[str], List[str]]:
    """Written wins. The same capability id must not appear in both lists."""
    written = [
        c
        for c in dict.fromkeys(str(x).strip() for x in caps_written)
        if c
    ]
    written_set = set(written)
    templated = [
        c
        for c in dict.fromkeys(str(x).strip() for x in caps_templated)
        if c and c not in written_set
    ]
    return written, templated


def dual_listed_capability_ids(
    caps_written: Iterable[str],
    caps_templated: Iterable[str],
) -> List[str]:
    return sorted({str(c) for c in caps_written} & {str(c) for c in caps_templated})


def refuse_dual_listed_caps(
    caps_written: Sequence[str],
    caps_templated: Sequence[str],
) -> None:
    """Fail-closed: overlapping written+templated ids are a factory bug."""
    overlap = dual_listed_capability_ids(caps_written, caps_templated)
    if overlap:
        raise DualListedAuthorshipError(
            "capability id(s) listed as both agent-written and templated: "
            + ", ".join(overlap)
        )


def is_action_artifact_id(artifact_id: str) -> bool:
    """True for a capability action-handler id, not models/routes/extras."""
    text = str(artifact_id or "").strip()
    if not text or ":" in text or "/" in text:
        return False
    if text.endswith(".py") or text.endswith(".tsx") or text.endswith(".md"):
        return False
    if text in _NON_ACTION_ARTIFACT_IDS:
        return False
    return True


#: ``app/actions/<capability>.py`` -- the file spelling of an action-handler
#: artifact key. Both the factory's own CodeWhale manifest and coding agents
#: key ``artifact_sources`` this way, but the grader accepted only the bare
#: capability id, so every path-keyed handler was rejected: live build
#: sess_617f60024df24a4e went 13/13 with 8 agent-written handlers on disk and
#: graded action_py=0, which demoted it to a code-cycle prototype.


def action_artifact_id(key: Any) -> Optional[str]:
    """The capability id an artifact key names, or None if it is no handler.

    Accepts both spellings in use -- the bare capability id
    (``record_checkin``) and the handler's path
    (``app/actions/record_checkin.py``). Package plumbing such as
    ``app/actions/__init__.py`` is not a capability.
    """
    text = str(key or "").strip().replace("\\", "/")
    # The handler's path, read as a path: exactly app/actions/<identifier>.py.
    path = PurePosixPath(text)
    if (
        len(path.parts) == 3
        and path.parts[:2] == ("app", "actions")
        and path.suffix == ".py"
        and path.stem.isidentifier()
    ):
        text = path.stem
        if text.startswith("__"):
            return None
    return text if is_action_artifact_id(text) else None


def action_artifact_ids(keys: Iterable[Any]) -> List[str]:
    """Capability ids of the handler artifacts in *keys*, deduplicated, in order."""
    ids: List[str] = []
    for key in keys or ():
        cid = action_artifact_id(key)
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def _as_nonneg_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _unique_ids(values: Iterable[Any]) -> List[str]:
    ids: List[str] = []
    for item in values:
        cid = str(item or "").strip()
        if cid and cid not in ids:
            ids.append(cid)
    return ids


def _cli_ids_from_mapping(blob: Any) -> Optional[List[str]]:
    if not isinstance(blob, Mapping):
        return None
    if "cli_authored_ids" in blob:
        return _unique_ids(blob.get("cli_authored_ids") or ())
    nested = cli_authored_ids_from(blob)
    return nested


@dataclass(frozen=True)
class FullPilotAuthorship:
    """Measured action-handler authorship against the launching-ready floor."""

    action_ids: List[str] = field(default_factory=list)
    cli_authored_ids: List[str] = field(default_factory=list)
    action_py: int = 0
    measured: bool = False
    meets_floor: bool = False
    n_required: Optional[int] = None
    need: int = FULL_PILOT_MIN_AUTHORED_ACTIONS

    @property
    def below_floor(self) -> bool:
        """Unmeasured or zero authorship is below floor (0.5).

        The old path returned ``measured and not meets_floor``, so a build
        with no authorship record at all slipped past the floor silently.
        Zero artifacts can never meet the floor; unmeasured is unmeasured
        because nothing was recorded to measure.
        """
        return not (self.measured and self.meets_floor)


def full_pilot_authorship_from(
    status: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Path | str] = None,
    *,
    n_required: Optional[int] = None,
    plan: Any = None,
    blueprint: Any = None,
) -> FullPilotAuthorship:
    """Count agent-written action handlers / ``cli_authored_ids``.

    ``full_pilot`` needs ≥ ``full_pilot_authorship_need(n_required)`` of
    either. Missing counts are not a pass. A present count below the
    floor is a measured refuse (VetCare action_py=3). A 4-cap golden
    with 4 authored ids meets the floor when ``n_required`` is 4.
    """
    status = dict(status or {})
    authorship = status.get("authorship")
    if not isinstance(authorship, Mapping):
        authorship = {}
    receipt = status.get("coder_receipt")
    if not isinstance(receipt, Mapping):
        receipt = {}

    resolved = n_required
    if resolved is None:
        resolved = n_required_capabilities_from(
            status, workspace, plan=plan, blueprint=blueprint
        )
    need = full_pilot_authorship_need(resolved)

    cli_ids: Optional[List[str]] = None
    for blob in (authorship, receipt, status.get("brief_dispatch")):
        found = _cli_ids_from_mapping(blob)
        if found is not None:
            cli_ids = found
            break

    action_ids: List[str] = []
    measured_actions = False
    artifacts = authorship.get("agent_artifacts")
    if isinstance(artifacts, list):
        action_ids = action_artifact_ids(artifacts)
        measured_actions = True
    explicit_action_py = _as_nonneg_int(authorship.get("action_py"))
    if explicit_action_py is not None and not measured_actions:
        measured_actions = True
        action_ids = action_ids or [f"action_{i}" for i in range(explicit_action_py)]

    action_py = len(action_ids)
    if measured_actions and explicit_action_py is not None and not artifacts:
        action_py = explicit_action_py

    if not measured_actions:
        written = _as_nonneg_int(authorship.get("agent_written"))
        if written is not None:
            measured_actions = True
            action_py = written

    cli_list = cli_ids if cli_ids is not None else []
    measured = measured_actions or cli_ids is not None
    meets = action_py >= need or len(cli_list) >= need
    return FullPilotAuthorship(
        action_ids=list(action_ids),
        cli_authored_ids=list(cli_list),
        action_py=action_py,
        measured=measured,
        meets_floor=bool(measured and meets),
        n_required=resolved,
        need=need,
    )


def thin_store_green_export_blocker(
    status: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Path | str] = None,
    *,
    n_required: Optional[int] = None,
    plan: Any = None,
    blueprint: Any = None,
) -> Optional[str]:
    """Refuse a Store-green / full-pilot zip when authorship is below floor.

    Code-cycle prototypes (PRODUCT/STORE not run) are not this lie.
    """
    status = dict(status or {})
    floor = full_pilot_authorship_from(
        status,
        workspace,
        n_required=n_required,
        plan=plan,
        blueprint=blueprint,
    )
    if not floor.below_floor:
        return None
    grade = status.get("level_grade")
    gates = grade.get("three_gate") if isinstance(grade, Mapping) else None
    if not isinstance(gates, Mapping):
        from app.factory.build.level_grade import three_gate_verdict

        gates = three_gate_verdict(status)
    cycle = str(status.get("cycle") or "").strip().lower()
    claiming_store_green = (
        cycle == "pilot"
        or (
            str(gates.get("PRODUCT") or "") == "PASS"
            and str(gates.get("STORE") or "") == "PASS"
        )
    )
    if not claiming_store_green:
        return None
    n_req = (
        f", n_required={floor.n_required}" if floor.n_required is not None else ""
    )
    return (
        "FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below the "
        f"full-pilot floor (action_py={floor.action_py}, "
        f"cli_authored_ids={len(floor.cli_authored_ids)}, "
        f"need ≥{floor.need}{n_req}) — will not ship a "
        "Store-green / full-pilot zip"
    )
