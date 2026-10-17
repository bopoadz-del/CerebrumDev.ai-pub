"""Workbench coding agent — Kimi Code CLI when enabled, else honest Factory stub.

When KIMI_WORKBENCH_ENABLED=false (default), a labeled deterministic regenerator
applies the approved change inside the sandbox envelope and produces a candidate.
It never calls an LLM and never touches production.

The coder's brief is the Cerebrum Product Delivery Standard: when a change
request carries a ``platform`` block and a ``domain_pack``, the brief is the
full rendered standard (candidate/kimi_prompt.md). Otherwise the legacy
CR-scoped brief is used (candidate/kimi_prompt.json), honestly labeled.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from app.factory import delivery_standard
from app.factory.blueprint import FactoryScenario, ProductBlueprint, load_blueprint
from app.resident_engineer.injection_guard import sanitize_untrusted
from app.workbench.flags import kimi_workbench_enabled
from app.workbench.sandbox import SandboxViolation, WorkbenchSandbox


class AgentError(RuntimeError):
    """Agent failed inside the sandbox."""


class KimiCliError(AgentError):
    """Kimi CLI path failed while KIMI_WORKBENCH_ENABLED=true — fail loudly.

    Never silently substituted by the deterministic stub: a caller who asked
    for the CLI must see the CLI failure, not a stub success envelope.
    """

    def __init__(self, kimi_error: str, *, cli_returncode: Optional[int] = None):
        detail = f"kimi_cli_failed: {kimi_error}"
        if cli_returncode is not None:
            detail += f" (returncode={cli_returncode})"
        super().__init__(detail)
        self.kimi_error = kimi_error
        self.cli_returncode = cli_returncode


def _bump_patch(version: str) -> str:
    parts = str(version or "1.0.0").split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except ValueError:
        parts[-1] = "1"
    return ".".join(parts)


def _load_source_blueprint(product_root: Path, envelope: Dict[str, Any]) -> ProductBlueprint:
    """Prefer product DNA blueprint yaml, else factory blueprint copy."""
    dna_bp = product_root / "product-dna" / "product_blueprint.yaml"
    if dna_bp.is_file():
        return load_blueprint(dna_bp)
    for candidate in (
        product_root / "blueprint.yaml",
        product_root / "product_blueprint.yaml",
        product_root / "factory" / "blueprint.yaml",
    ):
        if candidate.is_file():
            return load_blueprint(candidate)
    # Reconstruct minimal blueprint from envelope request product_id — fail closed
    raise AgentError(f"no blueprint found under {product_root}")


def apply_expansion_to_blueprint(
    blueprint: ProductBlueprint,
    expansion: Dict[str, Any],
) -> ProductBlueprint:
    """Return a MODIFY_PRODUCT blueprint with the requested capability/block added."""
    data = blueprint.model_dump(mode="json")
    data["factory_scenario"] = FactoryScenario.MODIFY_PRODUCT.value
    cap_id = str(expansion.get("capability") or "").strip()
    block_id = expansion.get("block_id")
    if not cap_id:
        raise AgentError("EXPANSION missing capability")
    existing_ids = {c["id"] for c in data.get("capabilities") or []}
    if cap_id in existing_ids:
        # Already present — still mark MODIFY and ensure block listed
        for cap in data["capabilities"]:
            if cap["id"] == cap_id and block_id:
                bids = list(cap.get("block_ids") or [])
                if block_id not in bids:
                    bids.append(block_id)
                    cap["block_ids"] = bids
    else:
        data.setdefault("capabilities", []).append(
            {
                "id": cap_id,
                "description": str(
                    expansion.get("rationale")
                    or f"Workbench EXPANSION: add capability {cap_id}"
                ),
                "block_ids": [block_id] if block_id else [],
                "strategy_hint": "REUSE" if block_id else "GENERATE",
            }
        )
    return ProductBlueprint.model_validate(data)


def apply_upgrade_to_pins(
    pin_versions: Dict[str, str],
    upgrade: Dict[str, Any],
) -> Dict[str, str]:
    component = str(upgrade.get("component") or "")
    to_version = str(upgrade.get("to_version") or "")
    if not component or not to_version:
        raise AgentError("UPGRADE missing component/to_version")
    out = dict(pin_versions)
    out[component] = to_version
    return out


def _standard_brief(envelope: Dict[str, Any]) -> Optional[str]:
    """Render the Cerebrum Product Delivery Standard when the CR carries a
    platform block + domain pack; None means use the legacy CR-scoped brief.

    A CR that carries only one of the two, or a pack the renderer rejects,
    is an error — the coder never gets a partial or silently-downgraded brief.
    """
    request = envelope.get("request") or {}
    platform, domain_pack = request.get("platform"), request.get("domain_pack")
    if platform is None and domain_pack is None:
        return None
    if not isinstance(platform, dict) or not isinstance(domain_pack, dict):
        raise AgentError(
            "CR must carry both 'platform' and 'domain_pack' objects, or neither"
        )
    try:
        brief = delivery_standard.render(platform, domain_pack)
    except ValueError as exc:
        raise AgentError(f"delivery-standard brief incomplete: {exc}") from exc
    return brief + (
        f"\n\n---\nEnvelope: kind={envelope.get('kind')} "
        f"checksum={envelope.get('envelope_checksum')} — candidate changes stay "
        "inside mutable_paths; no deploy; no Store writes.\n"
    )


def run_factory_regenerator_stub(
    sandbox: WorkbenchSandbox,
    *,
    envelope: Dict[str, Any],
    product_root: Path,
) -> Dict[str, Any]:
    """Honest stub: apply approved request to blueprint inside the sandbox."""
    request = envelope.get("request") or {}
    kind = request.get("kind")
    sandbox.log(
        "agent_start",
        backend="factory_regenerator_stub",
        honesty="KIMI_WORKBENCH_ENABLED=false — deterministic stub, not Kimi CLI",
        kind=kind,
    )

    blueprint = _load_source_blueprint(product_root, envelope)
    pin_versions: Dict[str, str] = {}
    # Seed pins from DNA lockfile if present
    lock_path = product_root / "product-dna" / "block_lockfile.json"
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for pin in lock.get("pins") or []:
            bid = pin.get("block_id")
            if bid and pin.get("version"):
                pin_versions[str(bid)] = str(pin["version"])

    summary_parts = []
    next_dna_version = _bump_patch(str(envelope.get("dna_version") or "1.0.0"))

    if kind == "EXPANSION":
        expansion = sanitize_untrusted(request.get("expansion") or {})
        blueprint = apply_expansion_to_blueprint(blueprint, expansion)
        if expansion.get("block_id") and expansion["block_id"] not in pin_versions:
            pin_versions[str(expansion["block_id"])] = "1.0.0"
        summary_parts.append(
            f"EXPANSION: add capability={expansion.get('capability')} "
            f"block_id={expansion.get('block_id')}"
        )
    elif kind == "UPGRADE":
        upgrade = sanitize_untrusted(request.get("upgrade") or {})
        pin_versions = apply_upgrade_to_pins(pin_versions, upgrade)
        data = blueprint.model_dump(mode="json")
        data["factory_scenario"] = FactoryScenario.MODIFY_PRODUCT.value
        blueprint = ProductBlueprint.model_validate(data)
        summary_parts.append(
            f"UPGRADE: {upgrade.get('component')} "
            f"{upgrade.get('from_version')} → {upgrade.get('to_version')}"
        )
    elif kind == "REPAIR":
        repair = sanitize_untrusted(request.get("repair") or {})
        data = blueprint.model_dump(mode="json")
        data["factory_scenario"] = FactoryScenario.REPAIR_PRODUCT.value
        blueprint = ProductBlueprint.model_validate(data)
        # Record repair intent for packager / gates (no silent product mutation)
        sandbox.write_text(
            "candidate/repair_intent.json",
            json.dumps({"repair": repair, "honesty": "restore via packager regenerate"}, indent=2)
            + "\n",
        )
        summary_parts.append(f"REPAIR: target={repair.get('target')}")
    else:
        raise AgentError(f"unsupported kind: {kind}")

    bp_dict = blueprint.model_dump(mode="json")
    bp_yaml = yaml.safe_dump(bp_dict, sort_keys=False, allow_unicode=True)
    sandbox.write_text("blueprint/product_blueprint.yaml", bp_yaml)
    sandbox.write_text(
        "candidate/mutation.json",
        json.dumps(
            {
                "kind": kind,
                "product_dna_version": next_dna_version,
                "pin_versions": pin_versions,
                "blueprint": bp_dict,
                "summary": "; ".join(summary_parts),
                "agent": "factory_regenerator_stub",
                "honesty": "STUB — not Kimi Code CLI; labeled deterministic Factory regenerator",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    # Boundary self-check: refuse escape
    try:
        sandbox.resolve_in_workspace("../outside", write=True)
        escaped = True
    except SandboxViolation:
        escaped = False
    sandbox.log("boundary_self_check", escape_refused=not escaped)

    return {
        "backend": "factory_regenerator_stub",
        "honesty": "STUB — KIMI_WORKBENCH_ENABLED=false",
        "summary": "; ".join(summary_parts) or f"{kind} applied in sandbox",
        "product_dna_version": next_dna_version,
        "pin_versions": pin_versions,
        "blueprint_path": "blueprint/product_blueprint.yaml",
        "mutation_path": "candidate/mutation.json",
    }


def _ensure_mutation_summary(
    sandbox: WorkbenchSandbox,
    envelope: Dict[str, Any],
) -> None:
    """Write a minimal mutation.json if the CLI left none.

    The gates/packager read mutation.json for metadata, but the real blueprint
    diff is computed separately by the candidate builder. This keeps the CLI
    path honest: we record what the CLI was asked to do, not what we hope it did.
    """
    mutation_path = sandbox.workspace / "candidate" / "mutation.json"
    if mutation_path.is_file():
        return
    request = envelope.get("request") or {}
    kind = request.get("kind")
    summary = f"{kind} — materialized by Kimi CLI"
    mutation = {
        "kind": kind,
        "product_dna_version": "1.0.0",
        "pin_versions": {},
        "blueprint": None,
        "summary": summary,
        "agent": "kimi_cli",
        "honesty": "CLI-edited workspace; mutation summary only — real diff computed from blueprint/",
    }
    sandbox.write_text("candidate/mutation.json", json.dumps(mutation, indent=2, sort_keys=True) + "\n")


def run_kimi_cli_agent(
    sandbox: WorkbenchSandbox,
    *,
    envelope: Dict[str, Any],
    product_root: Path,
) -> Dict[str, Any]:
    """Invoke Kimi Code CLI when enabled. Fails loudly if the CLI is unavailable;
    the deterministic stub is only ever used when KIMI_WORKBENCH_ENABLED=false."""
    sandbox.log(
        "agent_start",
        backend="kimi_cli",
        honesty="KIMI_WORKBENCH_ENABLED=true — attempting CLI; key never committed",
    )
    from app.factory.coder import code_cli_command

    cli = code_cli_command()
    model = os.getenv("CEREBRUM_FACTORY_LLM_MODEL", "kimi-k2.7-code")
    # The coder's brief: the full delivery standard when the CR carries a
    # domain pack (kimi_prompt.md); otherwise the legacy CR-scoped brief
    # (kimi_prompt.json), honestly labeled.
    brief_mode = "cr_scoped"
    standard = _standard_brief(envelope)
    if standard is not None:
        brief_mode = "delivery_standard"
        sandbox.write_text("candidate/kimi_prompt.md", standard)
        prompt_arg = "@candidate/kimi_prompt.md"
    else:
        prompt = {
            "instruction": (
                "Produce a candidate change set ONLY inside mutable_paths. "
                "Do not deploy. Do not write to the Store. Stay inside the envelope."
            ),
            "envelope_checksum": envelope.get("envelope_checksum"),
            "kind": envelope.get("kind"),
            "request": envelope.get("request"),
        }
        sandbox.write_text("candidate/kimi_prompt.json", json.dumps(prompt, indent=2) + "\n")
        prompt_arg = "@candidate/kimi_prompt.json"
    try:
        result = sandbox.run_command(
            [cli, "--version"],
            cwd_relative=".",
            timeout=15,
        )
        if result.get("returncode") != 0:
            sandbox.log("kimi_cli_unavailable", detail=result.get("stderr_tail"))
            raise KimiCliError("cli_unavailable", cli_returncode=result.get("returncode"))

        # Preflight passed — run the actual coding session inside the sandbox.
        sandbox.log(
            "kimi_cli_present",
            model=model,
            brief_mode=brief_mode,
            note="CLI detected; invoking real coding session on the brief",
        )
        # --prompt implies auto permission mode; --yolo/--auto are mutually
        # exclusive with it and the real CLI rejects the combination at startup.
        cmd = [
            cli,
            "--prompt",
            prompt_arg,
            "--add-dir",
            ".",
        ]
        # Reserve a little time for post-CLI bookkeeping.
        session_timeout = max(30, sandbox.timeout_seconds - 30)
        cli_result = sandbox.run_command(
            cmd,
            cwd_relative=".",
            timeout=session_timeout,
        )
        if cli_result.get("returncode") != 0:
            sandbox.log(
                "kimi_cli_session_failed",
                returncode=cli_result.get("returncode"),
                stderr_tail=cli_result.get("stderr_tail"),
            )
            raise KimiCliError(
                "cli_session_failed", cli_returncode=cli_result.get("returncode")
            )

        _ensure_mutation_summary(sandbox, envelope)
        return {
            "backend": "kimi_cli",
            "kimi_model": model,
            "brief_mode": brief_mode,
            "honesty": (
                "Kimi CLI invoked on the brief; candidate diff computed from "
                "blueprint/ workspace changes by the Factory packager"
            ),
            "summary": f"{envelope.get('kind')} — Kimi CLI session completed",
            "product_dna_version": "1.0.0",
            "pin_versions": {},
        }
    except FileNotFoundError as exc:
        sandbox.log("kimi_cli_missing")
        raise KimiCliError("cli_not_found") from exc


def run_agent(
    sandbox: WorkbenchSandbox,
    *,
    envelope: Dict[str, Any],
    product_root: Path,
) -> Dict[str, Any]:
    if kimi_workbench_enabled():
        return run_kimi_cli_agent(sandbox, envelope=envelope, product_root=product_root)
    return run_factory_regenerator_stub(sandbox, envelope=envelope, product_root=product_root)
