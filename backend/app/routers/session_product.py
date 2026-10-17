"""Session-scoped product architecture API (Design Product mode).

POST /v1/sessions/{id}/product/draft
POST /v1/sessions/{id}/product/plan
POST /v1/sessions/{id}/product/approve
POST /v1/sessions/{id}/product/generate
POST /v1/sessions/{id}/product/n3-reseed
POST /v1/sessions/{id}/product/coder-control
POST /v1/sessions/{id}/product/pilot
GET  /v1/sessions/{id}/product
GET  /v1/sessions/{id}/product/package   (export the generated platform zip)
POST /v1/sessions/{id}/product/mode
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from app.core.auth import Principal, require_api_key
from app.core.billing import require_entitled
from app.core.llm_throttle import require_llm_rate
from app.core.session_guard import owned_session_or_404
from app.core.session_store import update_session
from app.core.trial_limits import require_remaining, require_within_limit

logger = logging.getLogger(__name__)
from app.factory.build.builds_push import (
    EXPORT_SKIP_DIR_NAMES,
    EXPORT_SKIP_SUFFIXES,
    is_exported,
)
from app.factory.blocks_source import resolve_blocks_root
from app.factory.blueprint import BlueprintError, ProductBlueprint
from app.factory.dual_registry import DualRegistryError
from app.factory.floor_actions import require_build_level
from app.factory.locale_choice import intake_state
from app.factory.paths import UnsafeOutputDir, factory_outputs_root, safe_output_dir
from app.factory.product_architect import (
    blueprint_to_yaml,
    draft_blueprint_from_brief,
    generate_product,
    plan_blueprint,
    session_domain_from_blueprint,
)

router = APIRouter()


def _session_product_inputs(state: Any) -> tuple[Any, Any]:
    """Blueprint / plan parked on the session — used to resolve n_required."""
    pd = getattr(state, "product_design", None)
    raw_bp = getattr(pd, "blueprint", None) if pd is not None else None
    plan = getattr(pd, "plan", None) if pd is not None else None
    blueprint: Any = None
    if isinstance(raw_bp, dict) and raw_bp:
        try:
            blueprint = ProductBlueprint.model_validate(raw_bp)
        except Exception:  # noqa: BLE001 — dict still has capabilities
            blueprint = raw_bp
    return blueprint, plan


def generation_with_live_build(
    generation: Optional[Dict[str, Any]],
    *,
    blueprint: Any = None,
    plan: Any = None,
) -> Optional[Dict[str, Any]]:
    """Re-read the workspace ledger onto ``generation.build``.

    ``_record_generation`` snapshots ``build`` at session start
    (state=building, current_phase=COLLECTOR, last_event_age_s≈0.7).
    Floor / Platforms poll ``/product/build-status`` (live ledger) and
    correctly show CODING AGENT STOPPED after RUN_FAILED. GET /product
    used to return that frozen snapshot — live sess_14e690829d1f4282.
    """
    if not generation:
        return generation
    out = generation.get("output_dir")
    if not out:
        return generation
    from app.factory.build_jobs import build_status

    live = build_status(Path(out), blueprint=blueprint, plan=plan)
    attached = dict(generation)
    attached["build"] = live
    if isinstance(live, dict) and "phases_done" in live:
        attached["phases_done"] = live.get("phases_done")
    return attached


def _clear_sticky_thin_authorship_error(
    session_id: str,
    state: Any,
    live_generation: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Drop a pre-#392 THIN_AUTHORSHIP last_error once live status is SUCCESS.

    Platforms must not keep painting the baked ``need ≥5`` string after
    package / build-status already re-evaluated n_required.
    """
    pd = getattr(state, "product_design", None)
    if pd is None:
        return None
    last_error = getattr(pd, "last_error", None)
    live_build = (live_generation or {}).get("build") if isinstance(live_generation, dict) else None
    live_ok = isinstance(live_build, dict) and live_build.get("state") == "succeeded"
    # The refusal's typed blocker field -- the ``<BLOCKER>:`` token every named
    # refusal leads with, read by position -- not a search of its sentence.
    from app.factory.build_jobs import _thin_authorship_detail

    sticky = _thin_authorship_detail(last_error)
    persisted = (getattr(pd, "generation", None) or {}).get("build")
    persisted_failed = (
        isinstance(persisted, dict)
        and persisted.get("state") == "failed"
        and _thin_authorship_detail(persisted.get("detail"))
    )
    if not live_ok or not (sticky or persisted_failed):
        return last_error
    if sticky:
        pd.last_error = None
    if persisted_failed and isinstance(live_generation, dict):
        gen = dict(pd.generation or {})
        gen["build"] = live_generation.get("build")
        if "phases_done" in live_generation:
            gen["phases_done"] = live_generation.get("phases_done")
        pd.generation = gen
    try:
        update_session(session_id, state)
    except Exception:  # noqa: BLE001 — serving honesty must not 500
        logger.exception("could not persist live SUCCESS over sticky thin-authorship")
    return pd.last_error


class DraftBody(BaseModel):
    brief: str = Field(..., min_length=1)
    vertical_hint: Optional[str] = None
    #: Country (ISO 3166 alpha-2) and currency (ISO 4217) the user typed --
    #: typed fields, shape-validated, persisted on the session.
    country: Optional[str] = None
    currency: Optional[str] = None
    #: Client's delivery choice at request time: zip | github_repo.
    delivery_format: Optional[str] = None


class ApproveBody(BaseModel):
    approve: bool = True
    blueprint: Optional[Dict[str, Any]] = None


class GenerateBody(BaseModel):
    output_dir: Optional[str] = None
    #: ``pilot`` reopens TESTER/STORE on the existing workspace (same hash).
    cycle: Optional[Literal["code", "pilot"]] = None
    #: One-time HANDOFF_TO_N3 reseed + store-gate ingest. Never launches WRITER/BA.
    n3_reseed: bool = False
    builds_sha: Optional[str] = None
    builds_branch: Optional[str] = None
    builds_owner: Optional[str] = None
    builds_repo: Optional[str] = None
    cli_authored_ids: Optional[list[str]] = None


class N3ReseedBody(BaseModel):
    """Dedicated body for ``POST .../product/n3-reseed`` (same as generate flag)."""

    builds_sha: str = Field(..., min_length=7)
    builds_branch: str = Field(..., min_length=1)
    cli_authored_ids: list[str] = Field(..., min_length=1)
    builds_owner: Optional[str] = None
    builds_repo: Optional[str] = None
    output_dir: Optional[str] = None


class ModeBody(BaseModel):
    mode: Literal["kit", "product"]


class CoderControlBody(BaseModel):
    action: Literal["pause", "stop", "resume"]


def _refuse_without_build_level(state: Any) -> None:
    """A build starts only once the user has chosen its level (409, typed
    reason). There is no default level and none is inferred."""
    refused = require_build_level(state.product_design)
    if refused is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": refused["refused"], "message": refused["summary"]},
        )


def _require_session(session_id: str, principal: Principal):
    """Same ownership rule as every other session-scoped router."""
    return owned_session_or_404(session_id, principal)


def _session_output(session_id: str, product_id: str) -> Path:
    return factory_outputs_root() / "sessions" / session_id / product_id


def _enforce_export_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: exports are metered per account."""
    require_within_limit(account_id, "export")


def _enforce_generation_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: generations are metered per account."""
    require_within_limit(account_id, "generation")


def _raise_product_error(session_id: str, state, exc: BaseException) -> None:
    """400 for blueprint/registry mistakes; 500 (and Sentry) for the rest."""
    state.product_design.last_error = str(exc)
    update_session(session_id, state)
    if isinstance(exc, HTTPException):
        raise exc
    if isinstance(
        exc, (BlueprintError, DualRegistryError, UnsafeOutputDir, ValidationError)
    ):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.exception("session product handler failed")
    raise HTTPException(status_code=500, detail="internal_error") from exc


def _consume_generation_on_start(account_id: Optional[str]) -> None:
    """Charge one generation after a build actually started."""
    require_within_limit(account_id, "generation")


# One rule set for the zip and the cerebrum-builds push (builds_push.is_exported).
_EXPORT_SKIP_DIR_NAMES = EXPORT_SKIP_DIR_NAMES
_EXPORT_SKIP_SUFFIXES = EXPORT_SKIP_SUFFIXES
# ZIP spec rejects DOS timestamps before 1980. Copied kit/kernel files in
# this environment (and some git checkouts) have mtime 0.
_ZIP_MIN_EPOCH = 315532800  # 1980-01-01 UTC


_PROTOTYPE_MARKER = "CODE_CYCLE_PROTOTYPE.txt"
_PROTOTYPE_MARKER_BODY = (
    "This export is a CODE-CYCLE PROTOTYPE, not a pilot-ready product.\n"
    "\n"
    "The factory recorded RUN_SUCCEEDED with cycle=code and pilot_ready=false.\n"
    "PRODUCT (pytest -m pilot) and STORE ops have not passed. Dual certification\n"
    "is pending. Frontend modules and some handlers may be Factory templates.\n"
    "\n"
    "Say continue on the Factory Floor (or POST /product/pilot) to open a\n"
    "Store-green cycle on the same workspace. Do not treat this zip as a\n"
    "finished production platform.\n"
)


def _maybe_write_prototype_marker(zf: zipfile.ZipFile, out: Path) -> None:
    """Stamp a code-cycle zip so the export cannot be mistaken for finished."""
    try:
        from app.factory.build.ledger import BuildLedger
        from app.factory.build_jobs import _ledger_path

        ledger = BuildLedger(_ledger_path(out))
        if not ledger.exists() or ledger.pilot_ready():
            return
        if not ledger.succeeded():
            return
    except Exception:  # noqa: BLE001
        return
    if _PROTOTYPE_MARKER in zf.namelist():
        return
    import time as _time

    info = zipfile.ZipInfo(_PROTOTYPE_MARKER, date_time=_time.localtime()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, _PROTOTYPE_MARKER_BODY)


def zip_generated_product(out: Path, archive_base: Path) -> Path:
    """Zip ``out`` to ``{archive_base}.zip``, omitting TESTER caches.

    ``shutil.make_archive`` copies the whole tree, including ``__pycache__``
    and ``.pytest_cache`` left by the factory's own pytest run. Those are
    not the product; the live winery-hospitality export shipped 146 files
    of which a large slice was bytecode.
    """
    import time as _time

    zip_path = Path(str(archive_base) + ".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        _maybe_write_prototype_marker(zf, out)
        for path in sorted(out.rglob("*")):
            rel = path.relative_to(out)
            if not is_exported(rel):
                continue
            if not path.is_file():
                continue
            mtime = max(path.stat().st_mtime, _ZIP_MIN_EPOCH)
            info = zipfile.ZipInfo(rel.as_posix(), date_time=_time.localtime(mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())
    return zip_path


def _enforce_draft_quota(account_id: Optional[str]) -> None:
    """Server-side trial boundary: blueprint drafts are metered per account.

    Drafting is a paid LLM call that retries once against a fallback model, so
    an unmetered draft endpoint is an unbounded spend path for a free account.
    """
    require_within_limit(account_id, "draft")


@router.get("/{session_id}/product")
def get_product_design(
    session_id: str,
    response: Response,
    principal: Principal = Depends(require_api_key),
) -> Dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    state = _require_session(session_id, principal)
    pd = state.product_design
    yaml_text = None
    if pd.blueprint:
        try:
            yaml_text = blueprint_to_yaml(ProductBlueprint.model_validate(pd.blueprint))
        except Exception:  # noqa: BLE001
            yaml_text = None
    blueprint, plan = _session_product_inputs(state)
    live_generation = generation_with_live_build(
        pd.generation, blueprint=blueprint, plan=plan
    )
    last_error = _clear_sticky_thin_authorship_error(
        session_id, state, live_generation
    )
    return {
        "session_id": session_id,
        "mode": pd.mode,
        "brief": pd.brief,
        "blueprint": pd.blueprint,
        "blueprint_yaml": yaml_text,
        "plan": pd.plan,
        "intake_blueprint": pd.intake_blueprint,
        "blueprint_approved": pd.blueprint_approved,
        "brief_lint": pd.brief_lint,
        "generation": live_generation,
        "last_error": last_error,
    }


def _provenance_from_tree(out) -> dict:
    """factory_commit / blocks_commit / writer_receipt, as the build wrote them.

    Every export shipped these as "unknown" because nothing resolved them;
    they are resolved at converge time now and land in
    docs/provenance/provenance.json. Reading them back keeps the MANIFEST and
    the provenance document from disagreeing.
    """
    import json as _json

    path = Path(out) / "docs" / "provenance" / "provenance.json"
    try:
        doc = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    keys = ("factory_commit", "blocks_commit", "writer_receipt")
    return {k: str(doc.get(k) or "") for k in keys if doc.get(k)}


def _tag_certified_release(state: Any, status: Dict[str, Any]) -> Optional[tuple]:
    """``(tag, sha)`` for a certified export's release tag, or None when the
    platform has no branch of record (cerebrum-builds not armed)."""
    import os

    from app.factory.build.builds_push import builds_token
    from app.factory.build.platform_identity import platform_id_of

    pid = platform_id_of(state.product_design)
    if not pid or not builds_token(os.environ) or status.get("certified") is False:
        return None
    from app.factory.build.platform_branch import builds_remote, release_certified

    try:
        return release_certified(builds_remote(), pid)
    except Exception:  # noqa: BLE001 -- the zip still ships; the tag is retried next export
        logger.exception("release tag for %s failed", pid)
        return None


@router.get("/{session_id}/product/package")
def download_product_package(
    session_id: str,
    as_is: bool = False,
    principal: Principal = Depends(require_api_key),
) -> FileResponse:
    """Export the generated platform as a zip — the factory's deliverable."""
    state = _require_session(session_id, principal)
    _enforce_export_quota(principal.account_id)
    gen = state.product_design.generation
    if not gen or not gen.get("output_dir"):
        raise HTTPException(
            status_code=404,
            detail="no generated product — draft and approve a blueprint first",
        )
    out = Path(gen["output_dir"])
    if not out.is_dir():
        raise HTTPException(
            status_code=404,
            detail="generated product not found on disk — generate again",
        )

    # A runner build is a background job. Zipping mid-build would hand the
    # customer a splice of two writer passes, and zipping a FAILED build
    # would ship an artifact its own gates rejected. Only a succeeded build
    # is downloadable; the template engine has no build ledger and is
    # unaffected.
    from app.factory.build_jobs import build_status

    blueprint, plan = _session_product_inputs(state)
    status = build_status(out, blueprint=blueprint, plan=plan)
    if status["state"] == "building":
        raise HTTPException(
            status_code=409,
            detail=(
                "the platform is still being built "
                f"({status.get('phases_done', 0)}/{status.get('phases_total', 5)} "
                "phases complete) — poll /product/build-status"
            ),
        )
    if as_is and status["state"] != "building":
        # Any terminal state ships as-is on the explicit ask -- including a
        # "succeeded" whose certification blockers (thin authorship, red
        # acceptance, CLI-failed claims) would 409 the normal path below:
        # those ARE gate failures under the owner's rule. Only mid-build
        # stays refused in every mode.
        # Owner's order (2026-09-28): a gate-failed build IS downloadable --
        # on the explicit ask, loudly labeled, never certified. The zip goes
        # through the same is_exported() filter as every export, so the
        # in-house and client-data exclusions hold unchanged. What changes is
        # the paperwork: no MANIFEST.json and no store/acceptance claims; an
        # EXPORTED-AS-IS.md naming the gate that rejected the build instead.
        # "building" stays refused above in every mode -- a splice of two
        # writer passes has no honest as-is to ship.
        from datetime import datetime, timezone

        product_id = gen.get("product_id") or out.name
        note = (
            "# EXPORTED AS-IS — THIS BUILD FAILED ITS GATES\n\n"
            f"Product: {product_id}\n"
            f"Build state: {status['state']}\n"
            f"Gate verdict: {status.get('detail')}\n"
            f"Exported: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
            "This zip was exported on the owner's explicit request AFTER the\n"
            "factory's gates rejected the build. It carries NO certification\n"
            "manifest and makes no store-green, acceptance or pilot claims.\n"
            "Nothing here has been verified beyond what the gate verdict above\n"
            "says. Use it as source material, not as a product.\n"
        )
        (out / "EXPORTED-AS-IS.md").write_text(note, encoding="utf-8")
        # Rule 1: a failed build is still a product. Its MANIFEST names the
        # failing gate/check/finding, k/N, the build level and certified:false
        # -- replacing any certified manifest a refused export left behind.
        from app.factory.build.export_manifest import (
            failed_export_manifest,
            write_export_manifest,
        )

        write_export_manifest(
            out,
            failed_export_manifest(
                status,
                product_id=str(product_id),
                platform_id=getattr(state.product_design, "platform_id", None),
            ),
        )
        archive = zip_generated_product(out, out.parent / f"{out.name}-as-is-export")
        return FileResponse(
            archive,
            filename=f"cerebrumdev-{product_id}-FAILED-GATES-as-is.zip",
            media_type="application/zip",
        )
    if status["state"] == "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                "the build did not pass its gates and will not be shipped: "
                + str(status.get("detail"))
            ),
        )
    if status["state"] == "stalled":
        raise HTTPException(
            status_code=409,
            detail=(
                "the build stalled and will not be shipped as a full-pilot zip: "
                + str(status.get("detail"))
            ),
        )

    from app.factory.build.authorship import thin_store_green_export_blocker
    from app.factory.build.export_manifest import (
        ENGINE_FILE,
        ExportManifestError,
        assert_zip_eligibility,
        build_manifest,
        ci_evidence,
        verify_manifest,
        write_export_manifest,
    )
    from app.factory.build.store_acceptance import acceptance_export_blocker
    from app.factory.build.writer_prompt import PROMPT_VERSION

    # 6.3: a zip exists only after CI is green AND the artifact gate passed.
    # The template engine has no ledger (state "unknown") and its zip carries
    # the prototype marker declaring it unfinished — the gate applies to
    # runner builds, which are the ones claiming a governed product.
    # A refusal here is an answer, not a crash: it used to escape as a bare
    # HTTP 500, so the Floor's Export button failed with nothing to read.
    ci_run = ci_evidence(status)
    if status.get("state") != "unknown":
        try:
            assert_zip_eligibility(status, ci_run)
        except ExportManifestError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    engine_present = (out / ENGINE_FILE).is_file()
    # Tenancy is what the delivered platform DOES, not which kit it came from.
    # The old test looked only for the estate kit's app/steward/tenant_store.py,
    # so every other platform was labelled single_tenant_only -- including ones
    # that had just passed the gate's cross_tenant_404 check. A platform that
    # ships app/tenancy.py and passed that check partitions by tenant.
    acceptance_lines = (status.get("acceptance") or {}).get("lines") or []
    cross_tenant_passed = any(
        isinstance(line, dict)
        and line.get("name") == "cross_tenant_404"
        and line.get("status") == "PASS"
        for line in acceptance_lines
    )
    seam_present = (out / "app" / "steward" / "tenant_store.py").is_file() or (
        (out / "app" / "tenancy.py").is_file() and cross_tenant_passed
    )
    # The prompt version that BUILT this platform, read from the prompt the
    # worker persisted -- not whatever version the server runs at download time.
    built_prompt_version = PROMPT_VERSION
    try:
        import re as _re

        head = (out / "docs" / "writer_prompt.txt").read_text(encoding="utf-8")[:200]
        found = _re.match(r"<!-- ([A-Za-z0-9_.]+) -->", head)
        if found:
            built_prompt_version = found.group(1)
    except OSError:
        pass
    manifest = build_manifest(
        product_id=gen.get("product_id") or out.name,
        tenant_id=status.get("tenant_id"),
        ci_run=ci_run,
        retrieval_mode="vector_rag" if engine_present else "keyword_lexical",
        tenancy_mode=(
            "multi_tenant_partition" if seam_present else "single_tenant_only"
        ),
        embedder=("provider-configured" if engine_present else "none"),
        vector_store=("chroma_tenant_collection" if engine_present else "none"),
        engine_version="retrieval_engine.v1" if engine_present else "none",
        prompt_version=built_prompt_version,
        # A fresh build ships layer 1 (certified kernel definitions);
        # client layers 2-4 are zero until client content lands — honest
        # zeros, never invented counts.
        layer_counts={1: 1} if (out / "app" / "cerebrum_product_kernel" / "formulas").is_dir() else {},
        engine_included=engine_present,
        # Read back out of the tree the build already wrote, rather than
        # recomputed here: docs/provenance/provenance.json is what the gate
        # grades and what the buyer opens, so the MANIFEST must agree with
        # it by construction rather than by coincidence.
        provenance=_provenance_from_tree(out),
        # The level this platform was BUILT to, read from its run's ledger.
        build_level=status.get("build_level"),
        # Gates this build moved to advisory (its brief never defined them),
        # read from the build status -- shipped, never silenced.
        advisory_checks=list(status.get("advisory_checks") or []),
    )
    write_export_manifest(out, manifest)
    tree_contents = {
        p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()
    }
    problems = verify_manifest(manifest, tree_contents)
    if problems:
        raise HTTPException(status_code=500, detail="; ".join(problems))

    thin = thin_store_green_export_blocker(
        status, out, plan=plan, blueprint=blueprint
    )
    if thin:
        raise HTTPException(status_code=409, detail=thin)
    accept = acceptance_export_blocker(status, out)
    if accept:
        raise HTTPException(status_code=409, detail=accept)

    # A certification the gate later withdrew (revoked_certifications.json in
    # cerebrum-builds) is not presented as certified.
    from app.factory.build.n3_store_gate import certification_withdrawn

    withdrawn = certification_withdrawn(out)
    if withdrawn:
        # The gate's verdict now: the run is a failed platform Continue can
        # resume at the Store gate (n3_store_gate.record_certification_withdrawn).
        from app.factory.build.n3_store_gate import record_certification_withdrawn

        record_certification_withdrawn(out, withdrawn)
        raise HTTPException(status_code=409, detail=withdrawn)

    # Every gate held: this export is CERTIFIED. Tag the platform's head
    # release/<platform_id>/<n>; the manifest (and the Store registry entry)
    # point at the tag. A failed build never reaches this line.
    manifest["certified"] = True
    release = _tag_certified_release(state, status)
    if release:
        manifest["release_tag"], manifest["release_sha"] = release
    write_export_manifest(out, manifest)

    archive_base = out.parent / f"{out.name}-export"
    archive = zip_generated_product(out, archive_base)
    product_id = gen.get("product_id") or out.name
    filename = (
        f"cerebrumdev-{product_id}-code-cycle-prototype.zip"
        if status.get("state") == "succeeded" and status.get("pilot_ready") is False
        else f"cerebrumdev-{product_id}.zip"
    )
    return FileResponse(
        archive,
        filename=filename,
        media_type="application/zip",
    )


@router.get("/{session_id}/product/build-status")
def get_build_status(
    session_id: str,
    response: Response,
    principal: Principal = Depends(require_api_key),
) -> Dict[str, Any]:
    """Progress of the background build. Read off the build ledger.

    Cheap and quota-free on purpose: the client polls this while the agent
    writes the platform, and metering a progress read would charge the
    customer for waiting.
    """
    response.headers["Cache-Control"] = "no-store"
    from app.factory.build_jobs import build_status

    state = _require_session(session_id, principal)
    gen = state.product_design.generation
    if not gen or not gen.get("output_dir"):
        return {"ok": True, "build": {"state": "not_started"}}
    blueprint, plan = _session_product_inputs(state)
    return {
        "ok": True,
        "product_id": gen.get("product_id"),
        "build": build_status(
            Path(gen["output_dir"]), blueprint=blueprint, plan=plan
        ),
    }


@router.get("/{session_id}/product/ledger")
def get_build_ledger(
    session_id: str,
    response: Response,
    principal: Principal = Depends(require_api_key),
) -> Dict[str, Any]:
    """The build's whole ledger, event by event, for its owner.

    build-status summarises the ledger; a failure is classified from the
    events themselves (ops.yml ledger-dump). Owner-scoped like every session
    read, and every string is sanitized as build-status sanitizes it: keys
    and auth headers never leave.
    """
    response.headers["Cache-Control"] = "no-store"
    from app.factory.build.ledger import BuildLedger
    from app.factory.build.sanitize import sanitize_for_status

    state = _require_session(session_id, principal)
    gen = state.product_design.generation
    path = Path(gen["output_dir"]) / "build_ledger.jsonl" if gen and gen.get("output_dir") else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="no build ledger for this session")

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return sanitize_for_status(value)
        if isinstance(value, dict):
            return {str(k): clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value

    try:
        events = [clean(e.to_json()) for e in BuildLedger(path).events()]
    except Exception as exc:  # noqa: BLE001 -- a torn ledger is reported, not a 500
        raise HTTPException(status_code=409, detail=f"ledger unreadable: {type(exc).__name__}") from exc
    return {"ok": True, "session_id": session_id, "events": events}


@router.post("/{session_id}/product/coder-control")
def set_coder_control(
    session_id: str,
    body: CoderControlBody,
    principal: Principal = Depends(require_api_key),
) -> Dict[str, Any]:
    """Owner Pause / Stop / Resume for the live coder session.

    Writes ``docs/coder_control.json`` in the product workspace. The
    dispatcher polls it; this is not a role write.
    """
    from app.factory.build.coder_session_status import write_control

    state = _require_session(session_id, principal)
    gen = state.product_design.generation
    if not gen or not gen.get("output_dir"):
        raise HTTPException(status_code=409, detail="no build workspace — start a generate first")
    out = Path(gen["output_dir"])
    if not out.is_dir():
        raise HTTPException(status_code=404, detail="generated product not found on disk")
    control = write_control(out, body.action)
    return {"ok": True, "control": control}


@router.post("/{session_id}/product/mode")
def set_product_mode(
    session_id: str, body: ModeBody, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    state.product_design.mode = body.mode
    update_session(session_id, state)
    return {"ok": True, "mode": body.mode}


@router.get("/{session_id}/product/verticals")
def product_verticals(
    session_id: str, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    """The Floor's vertical picker: what the Store's kits DECLARE they serve,
    and the user's current choice. The user picks one or types their own;
    the Factory never infers it."""
    from app.factory.store_kits import NO_VERTICAL, declared_verticals

    state = _require_session(session_id, principal)
    try:
        options = declared_verticals(resolve_blocks_root())
    except Exception:  # noqa: BLE001 -- no Store: the user can still type one
        options = []
    return {
        "verticals": options,
        "chosen": state.product_design.vertical,
        "default": NO_VERTICAL,
        # The country/currency the user declared (typed, never inferred).
        "country": state.product_design.country,
        "currency": state.product_design.currency,
        # The build level the user chose; None = not chosen (no default).
        "build_level": state.product_design.build_level,
        # The Floor's intake line: declared fields + any pending proposal.
        "intake": intake_state(state.product_design),
    }


@router.post("/{session_id}/product/draft")
def draft_product(
    session_id: str, body: DraftBody, principal: Principal = Depends(require_entitled)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    # Outside the try/except below on purpose: that handler catches bare
    # Exception and re-raises as 400, which would mask the 429.
    _enforce_draft_quota(principal.account_id)
    require_llm_rate(principal, "draft")
    try:
        # The user's vertical: this request's field, else the choice already
        # on the session. Never read out of the brief.
        if body.vertical_hint is not None:
            from app.factory.store_kits import NO_VERTICAL, chosen_vertical

            choice = chosen_vertical(body.vertical_hint)
            state.product_design.vertical = None if choice == NO_VERTICAL else choice
        bp = draft_blueprint_from_brief(
            body.brief, vertical_hint=state.product_design.vertical
        )
        if body.delivery_format in ("zip", "github_repo"):
            bp.delivery_format = body.delivery_format
        state.product_design.brief = body.brief
        state.product_design.blueprint = bp.model_dump(mode="json")
        from app.factory.locale_choice import apply_locale_choice

        # The declared country/currency rides on the blueprint (never read
        # out of the brief); unset fields keep the session's earlier answer.
        apply_locale_choice(state.product_design, body.country, body.currency)
        state.product_design.plan = None
        state.product_design.blueprint_approved = False
        state.product_design.generation = None
        state.product_design.last_error = None
        state.product_design.mode = "product"
        state.config.domain = session_domain_from_blueprint(bp)
        update_session(session_id, state)
        return {
            "ok": True,
            "blueprint": state.product_design.blueprint,
            "yaml": blueprint_to_yaml(bp),
            "source": "golden" if bp.drafting_mode == "golden" else "drafted",
        }
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)


@router.post("/{session_id}/product/plan")
def plan_product(
    session_id: str, principal: Principal = Depends(require_entitled)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="draft a blueprint first")
    require_llm_rate(principal, "plan")
    # Same resolver as the chat flow (env path, then Store clone).
    blocks_root = resolve_blocks_root()
    try:
        from app.factory.locale_choice import sync_blueprint_intake

        sync_blueprint_intake(state.product_design)
        bp = ProductBlueprint.model_validate(state.product_design.blueprint)
        plan = plan_blueprint(bp, blocks_root=blocks_root)
        state.product_design.plan = plan.to_dict()
        state.product_design.last_error = None
        update_session(session_id, state)
        return {"ok": True, "plan": state.product_design.plan}
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)


@router.post("/{session_id}/product/approve")
def approve_blueprint(
    session_id: str, body: ApproveBody, principal: Principal = Depends(require_entitled)
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    if body.blueprint is not None:
        try:
            bp = ProductBlueprint.model_validate(body.blueprint)
            state.product_design.blueprint = bp.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint to approve")
    from app.factory.locale_choice import sync_blueprint_intake

    # The confirmed intake (locale, build level) is ON the blueprint before
    # it is frozen by approval -- never added afterwards.
    sync_blueprint_intake(state.product_design)
    state.product_design.blueprint_approved = bool(body.approve)
    state.config.domain = session_domain_from_blueprint(state.product_design.blueprint)
    update_session(session_id, state)
    return {
        "ok": True,
        "blueprint_approved": state.product_design.blueprint_approved,
    }


@router.post("/{session_id}/product/pilot")
def run_pilot_cycle(
    session_id: str,
    principal: Principal = Depends(require_entitled),
) -> Dict[str, Any]:
    """Reopen TESTER/STORE on the existing workspace for Store-green.

    Same session, hash, and output dir. Does not approve a new blueprint.
    """
    from app.factory.platform_chat_flow import resume_pilot_cycle

    state = _require_session(session_id, principal)
    _refuse_without_build_level(state)
    require_remaining(principal.account_id, "generation")
    require_llm_rate(principal, "generate")
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint")
    try:
        result = resume_pilot_cycle(state, triggered_by="product_pilot")
        if not result.get("already_running") and not result.get("already_complete"):
            _consume_generation_on_start(principal.account_id)
        update_session(session_id, state)
        return {
            "ok": True,
            "cycle": "pilot",
            "generation": state.product_design.generation,
            "summary": result.get("summary"),
            "already_complete": result.get("already_complete"),
            "already_running": result.get("already_running"),
            "pilot_ready": result.get("pilot_ready"),
            "resumed": result.get("resumed"),
        }
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)



def _run_n3_reseed(
    session_id: str,
    state,
    *,
    builds_sha: str,
    builds_branch: str,
    cli_authored_ids: list[str],
    builds_owner: Optional[str] = None,
    builds_repo: Optional[str] = None,
    output_dir: Optional[str] = None,
    principal: Principal,
) -> Dict[str, Any]:
    """Stamp HANDOFF then ingest store-gate. Never calls generate_product."""
    from app.factory.build.n3_store_gate import HandoffReseedError
    from app.factory.platform_chat_flow import has_running_build, reseed_and_ingest_n3

    if has_running_build(state):
        raise HTTPException(
            status_code=409,
            detail="a build is already in progress — poll /product/build-status",
        )
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint")
    if not state.product_design.blueprint_approved:
        raise HTTPException(
            status_code=400, detail="approve blueprint before n3_reseed"
        )
    if output_dir:
        from app.factory.locale_choice import sync_blueprint_intake

        sync_blueprint_intake(state.product_design)
        bp = ProductBlueprint.model_validate(state.product_design.blueprint)
        out = safe_output_dir(output_dir, bp.product_id)
        gen = dict(state.product_design.generation or {})
        gen["output_dir"] = str(out)
        gen.setdefault("product_id", bp.product_id)
        gen.setdefault("engine", "runner")
        state.product_design.generation = gen
    try:
        result = reseed_and_ingest_n3(
            state,
            builds_sha=builds_sha,
            builds_branch=builds_branch,
            cli_authored_ids=cli_authored_ids,
            builds_owner=builds_owner or "bopoadz-del",
            builds_repo=builds_repo or "cerebrum-builds",
            triggered_by="product_n3_reseed",
        )
    except HandoffReseedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state.product_design.last_error = None
    if not result.get("ok"):
        state.product_design.last_error = result.get("summary")
    update_session(session_id, state)
    return {
        "ok": bool(result.get("ok")),
        "n3_reseed": True,
        "n3_ingest": True,
        "summary": result.get("summary"),
        "reseed": result.get("reseed"),
        "n3": result.get("n3"),
        "generation": state.product_design.generation,
        "build": result.get("build"),
    }



@router.post("/{session_id}/product/n3-reseed")
def n3_reseed_product(
    session_id: str,
    body: N3ReseedBody,
    principal: Principal = Depends(require_entitled),
) -> Dict[str, Any]:
    """Safe HANDOFF reseed + store-gate ingest. Does not launch WRITER/BA."""
    state = _require_session(session_id, principal)
    try:
        return _run_n3_reseed(
            session_id,
            state,
            builds_sha=body.builds_sha,
            builds_branch=body.builds_branch,
            cli_authored_ids=list(body.cli_authored_ids),
            builds_owner=body.builds_owner,
            builds_repo=body.builds_repo,
            output_dir=body.output_dir,
            principal=principal,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)


@router.post("/{session_id}/product/generate")
def generate_approved_product(
    session_id: str,
    body: Optional[GenerateBody] = None,
    principal: Principal = Depends(require_entitled),
) -> Dict[str, Any]:
    state = _require_session(session_id, principal)
    body = body or GenerateBody()
    if body.n3_reseed:
        if not body.builds_sha or not body.builds_branch or not body.cli_authored_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    "n3_reseed requires builds_sha, builds_branch, "
                    "and cli_authored_ids"
                ),
            )
        try:
            return _run_n3_reseed(
                session_id,
                state,
                builds_sha=body.builds_sha,
                builds_branch=body.builds_branch,
                cli_authored_ids=list(body.cli_authored_ids),
                builds_owner=body.builds_owner,
                builds_repo=body.builds_repo,
                output_dir=body.output_dir,
                principal=principal,
            )
        except Exception as exc:  # noqa: BLE001
            _raise_product_error(session_id, state, exc)
    require_remaining(principal.account_id, "generation")
    require_llm_rate(principal, "generate")
    if not state.product_design.blueprint_approved:
        raise HTTPException(status_code=400, detail="approve blueprint before generate")
    if not state.product_design.blueprint:
        raise HTTPException(status_code=400, detail="no blueprint")
    from app.factory.platform_chat_flow import has_running_build

    if has_running_build(state):
        raise HTTPException(
            status_code=409,
            detail="a build is already in progress — poll /product/build-status",
        )
    _refuse_without_build_level(state)
    # Same resolver as the chat flow (env path, then Store clone).
    blocks_root = resolve_blocks_root()
    try:
        from app.factory.locale_choice import sync_blueprint_intake

        sync_blueprint_intake(state.product_design)
        bp = ProductBlueprint.model_validate(state.product_design.blueprint)
        from app.factory.platform_chat_flow import _compile_and_lint_approved

        gated = _compile_and_lint_approved(state, bp)
        if not gated["lint"].ok:
            raise HTTPException(
                status_code=400,
                detail="BRIEF_LINT_REJECTED — session never opens: "
                + "; ".join(gated["lint"].errors),
            )
        if not state.product_design.plan:
            state.product_design.plan = plan_blueprint(bp, blocks_root=blocks_root).to_dict()
        # A caller-supplied output_dir is a recursive-delete target inside the
        # generator, so it must stay inside factory_outputs/. None keeps the
        # per-session default.
        # One platform = one workspace of record: a re-run resumes the
        # session's existing workspace, never a sibling (Start over is the
        # only fresh door, and it is a typed Floor action).
        prior = (state.product_design.generation or {}).get("output_dir")
        if body.output_dir:
            out = safe_output_dir(body.output_dir, bp.product_id)
        elif prior and Path(prior).is_dir():
            out = Path(prior)
        else:
            out = _session_output(session_id, bp.product_id)
        from app.factory.build.platform_identity import ensure_platform_id

        result = generate_product(
            bp,
            out,
            blocks_root=blocks_root,
            cycle=body.cycle,
            quota_account_id=principal.account_id,
            tenant_identity=principal.account_id,
            brief=str(state.product_design.brief or "").strip(),
            platform_id=ensure_platform_id(state.product_design),
        )
        if result.get("already_running"):
            raise HTTPException(
                status_code=409,
                detail="a build is already in progress — poll /product/build-status",
            )
        _consume_generation_on_start(principal.account_id)
        state.product_design.generation = {
            "output_dir": result["output_dir"],
            "inputs_hash": result["inputs_hash"],
            "product_id": result["product_id"],
            "canonical_output": result.get("canonical_output"),
            # Without these the client cannot tell a finished template
            # product from a runner build that has only just started, and
            # would go straight to a 409 on download.
            "engine": result.get("engine"),
            "build": result.get("build"),
        }
        state.product_design.last_error = None
        update_session(session_id, state)
        return {
            "ok": True,
            "blueprint": state.product_design.blueprint,
            "plan": state.product_design.plan,
            "generation": state.product_design.generation,
        }
    except Exception as exc:  # noqa: BLE001
        _raise_product_error(session_id, state, exc)
