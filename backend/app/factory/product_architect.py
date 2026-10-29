"""Product Architect — drafts product_blueprint.v1 inside the Factory.

Today Steward may be predefined (golden YAML). The architect can:
- load a checked-in blueprint (deterministic / mock / golden path)
- draft from a brief using the LLM when a factory API key is configured
  (or when ARCHITECT_LLM_DRAFTING_ENABLED is explicitly on)
- fall back to deterministic keyword drafting (always available, no keys)

Architecture is always a validated ProductBlueprint; generation stays fail-closed
via the capability planner + dual registry. LLM drafting is fail-SAFE: any LLM
error, malformed payload, or all-foreign block ids falls back to the keyword
drafter — a draft is always produced, and block ids are always filtered
against the dual registry (never invent blocks).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Final, List, Optional

import yaml

from app.core.llm_config import (
    get_factory_llm_config,
    get_llm_config,
    _is_cursor_chat_host,
    _is_openrouter_base,
)
from app.factory.blueprint import (
    FactoryScenario,
    ProductBlueprint,
    connector_slug,
    load_blueprint,
)
from app.factory.dual_registry import DualRegistryError, dual_registered_ids
from app.factory.dual_registry import certified_ids as _store_certified_ids
from app.factory.generator import ProductGenerator, git_head
from app.factory.paths import factory_repo_root
from app.factory.planner import CapabilityPlanner, ProductPlan

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return factory_repo_root()


def session_domain_from_blueprint(blueprint: Any) -> str:
    """Session ``config.domain`` for a product draft, derived from the draft.

    The domain is DERIVED from the blueprint, never hardwired: prefer
    ``product_id`` (``residential-lettings``), then ``vertical`` with
    underscores folded to hyphens. When the blueprint names no vertical (or
    there is no blueprint yet), fall back to the NEUTRAL sentinel
    (``NO_VERTICAL``) -- never a baked-in customer vertical.
    """
    from app.factory.store_kits import NO_VERTICAL

    if blueprint is None:
        return NO_VERTICAL
    if isinstance(blueprint, dict):
        product_id = str(blueprint.get("product_id") or "").strip()
        vertical = str(blueprint.get("vertical") or "").strip()
    else:
        product_id = str(getattr(blueprint, "product_id", "") or "").strip()
        vertical = str(getattr(blueprint, "vertical", "") or "").strip()
    if product_id:
        return product_id
    if vertical:
        return vertical.replace("_", "-")
    return NO_VERTICAL


# --- LLM drafting (gated, fail-safe) -----------------------------------------

LLM_DRAFTING_ENV = "ARCHITECT_LLM_DRAFTING_ENABLED"


class LlmSoftMiss(Exception):
    """Expected empty / unusable model output — fall back, do not Sentry-error.

    OpenRouter free / Moonshot completions sometimes return ``''`` or ``{}``
    under billing pressure. That is a miss, not a contract bug. Floor chat
    logs these at warning and uses regex routing. Non-empty invalid actions
    stay ``ValueError`` (fail-closed).
    """

# Cost ceilings for the drafting call. The brief is caller-supplied and the
# response was previously unbounded, so a single request could bill an
# arbitrary number of tokens in each direction (twice, because the call
# retries against a fallback model). Both are generous for a real brief and
# env-overridable for deployments that want more headroom.
BRIEF_MAX_CHARS_ENV = "ARCHITECT_BRIEF_MAX_CHARS"
BRIEF_MAX_CHARS_DEFAULT = 8000
LLM_MAX_TOKENS_ENV: Final = "ARCHITECT_LLM_MAX_TOKENS"
LLM_MAX_TOKENS_DEFAULT = 2000

BRIEF_TRUNCATION_MARKER = (
    "\n\n[brief truncated by the server at {limit} characters; "
    "{dropped} characters were not sent to the model]"
)


def llm_drafting_enabled() -> bool:
    """Use the architect LLM when factory credentials exist, unless explicitly off.

    A second flag that defaulted off meant a keyed deployment still shipped
    keyword templates if someone forgot ``ARCHITECT_LLM_DRAFTING_ENABLED``.
    Explicit ``0``/``false``/``off`` still wins. Explicit ``1``/``true`` still
    forces the LLM path (and fail-safes to templates if the call dies).
    Unset means: draft with the LLM whenever a factory API key is configured.
    """
    raw = os.getenv(LLM_DRAFTING_ENV, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    cfg = get_factory_llm_config()
    if cfg.get("error") or cfg.get("mock"):
        return False
    return bool(cfg.get("api_key"))


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive int from the environment, per call, falling back."""
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def brief_max_chars() -> int:
    return _positive_int_env(BRIEF_MAX_CHARS_ENV, BRIEF_MAX_CHARS_DEFAULT)


def llm_max_tokens() -> int:
    return _positive_int_env(LLM_MAX_TOKENS_ENV, LLM_MAX_TOKENS_DEFAULT)


def truncate_brief(brief: str) -> str:
    """Bound the caller-supplied brief, marking the cut explicitly.

    Silent truncation is worse than a bounded prompt: the model would draft
    against text the user cannot see was dropped. The marker is part of the
    prompt so the model knows the brief is incomplete.
    """
    text = brief or ""
    limit = brief_max_chars()
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    return text[:limit] + BRIEF_TRUNCATION_MARKER.format(limit=limit, dropped=dropped)


_LLM_DRAFT_SYSTEM = """You are the Cerebrum product architect. Given a user \
brief for a software platform, draft a product blueprint as JSON.

Return ONLY a JSON object with this shape:
{
  "product_name": "<the name the user gave, or their own words for it>",
  "summary": "<one paragraph: what the product does and who it serves>",
  "capabilities": [
    {
      "id": "<snake_case capability id>",
      "description": "<what this capability does>",
      "block_ids": ["<ids from the AVAILABLE BLOCKS list only>"],
      "strategy_hint": "REUSE",
      "connectors": ["<external systems this capability calls that no AVAILABLE BLOCK supplies>"]
    }
  ]
}

Rules:
- product_name is the customer's, not yours. If they named the platform, use
  that name exactly. If they did not, name it from their own words -- what
  they called the work and who it is for -- and keep it plain. Never invent a
  brand, a product line, or a word the user did not use.
- 3 to 8 capabilities, ordered by importance.
- block_ids may ONLY contain ids from the AVAILABLE BLOCKS list. Never invent ids.
- If no available block fits a capability, use "block_ids": [] and
  "strategy_hint": "GENERATE".
- connectors names the outside systems (a CRM, a DMS, a bank) a capability
  calls that no available block supplies. Each ships as a marked placeholder
  until it is built, and the capability says so; use [] when it calls none.
"""


def _extract_json(text: str) -> Dict[str, Any]:
    """Strip Markdown fences and parse the first JSON object in *text*.

    OpenRouter free models (and some safety filters) return prose such as
    ``User Safety: safe`` instead of JSON. Callers must not ``json.loads``
    the raw completion — this helper raises ``LlmSoftMiss`` for empty
    content / ``{}`` and ``ValueError`` when non-empty output has no object
    so Floor chat / architect fail-safes stay clean.
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.endswith("```"):
        text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    text = text.strip()
    if not text:
        raise LlmSoftMiss("Empty model output")
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output")
    depth = 0
    end = start
    in_string = False
    escape = False
    for i, ch in enumerate(text[start:], start):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if depth != 0:
        raise ValueError("Unbalanced JSON object in model output")
    parsed = json.loads(text[start:end])
    if isinstance(parsed, dict) and not parsed:
        raise LlmSoftMiss("Empty JSON object in model output")
    return parsed


def _llm_json_call(
    messages: List[Dict[str, str]],
    *,
    use_chat_config: bool = False,
) -> Dict[str, Any]:
    """Synchronous JSON call against the configured LLM provider.

    Mirrors core.chain_generator's async callers but stays sync so the
    architect's call sites (routers, chat flow, pipeline) are untouched.
    Raises on any failure — the caller falls back to keyword drafting.

    Floor chat (``use_chat_config=True``) uses ``CEREBRUM_CHAT_LLM_*``.
    Architect draft uses the factory config. Neither may POST to
    ``api.cursor.com/v1/chat/completions``.
    """
    from app.core.model_ladder import (
        NOT_CONFIGURED,
        PAYMENT_REFUSED,
        ModelUnavailable,
        classify,
        rungs_from_config,
        run_ladder,
    )

    cfg = get_llm_config() if use_chat_config else get_factory_llm_config()
    if cfg.get("mock"):
        raise RuntimeError("LLM mock mode — no network call")
    if _is_cursor_chat_host(str(cfg.get("base_url", ""))):
        raise RuntimeError(
            "Refusing api.cursor.com/v1/chat/completions — Cursor has no "
            "public chat-completions API. Floor chat uses CEREBRUM_CHAT_LLM_*."
        )
    provider = cfg.get("provider")
    primary_ok = not cfg.get("error") and (
        provider in ("deepseek", "moonshot", "kimi", "cursor", "openrouter")
        or (not provider and cfg.get("api_key") and cfg.get("base_url"))
    )
    if cfg.get("error") and os.getenv("LLM_PROVIDER", "").strip():
        # A provider asked for BY NAME whose credentials are missing is an
        # error, never an invitation to substitute another vendor (the same
        # rule the coder applies).
        raise ModelUnavailable(NOT_CONFIGURED, str(cfg["error"]))
    # The ladder: the configured primary, then the cross-provider fallback
    # leg. Each rung uses its OWN base_url and key (core.model_ladder).
    rungs = rungs_from_config(cfg if primary_ok else None)
    if not rungs:
        raise ModelUnavailable(
            NOT_CONFIGURED, str(cfg.get("error") or "No LLM provider configured")
        )

    from app.factory.llm_watchdog import call_timeout_s, post_with_deadline

    def _post(rung: Any, m: str) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if rung.api_key:
            headers["Authorization"] = f"Bearer {rung.api_key}"
        if _is_openrouter_base(rung.base_url):
            headers["HTTP-Referer"] = "https://cerebrumdev.ai"
            headers["X-Title"] = "CerebrumDev Floor"
        payload = {
            "model": m,
            "messages": messages,
            # Hard ceiling on the completion. Without it a single draft
            # can bill the model's full context window, and this call
            # retries once against the fallback model.
            "max_tokens": llm_max_tokens(),
        }
        if not _is_openrouter_base(rung.base_url):
            payload["response_format"] = {"type": "json_object"}
        # Omit temperature unless explicitly configured: reasoning models
        # (kimi-k2.x) reject any explicit temperature other than 1.
        if rung.temperature is not None:
            payload["temperature"] = rung.temperature
        resp = post_with_deadline(
            f"{rung.base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
            timeout=call_timeout_s(),
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _extract_json(content)

    def _rung(rung: Any) -> Dict[str, Any]:
        try:
            return _post(rung, rung.model)
        except Exception as exc:
            # A payment refusal is the ACCOUNT, not the model: another model
            # on the same endpoint would be refused the same way.
            kind, _status = classify(exc)
            fallback = rung.fallback_model
            if kind == PAYMENT_REFUSED or not fallback or fallback == rung.model:
                raise
            return _post(rung, fallback)

    return run_ladder(rungs, _rung)


def _slug(text: str, default: str = "product") -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", (text or "").lower())[:48].strip("-")
    return slug or default


def _snake_slug(text: str, default: str = "product") -> str:
    """Normalize a brief or LLM vertical into lowercase snake_case."""
    normalized = (text or "").lower().replace(" ", "_").replace("-", "_")
    slug = re.sub(r"[^a-z0-9_]+", "_", normalized).strip("_")[:48]
    return slug or default


def _blueprint_from_llm_payload(
    data: Dict[str, Any],
    brief: str,
    vertical_hint: Optional[str],
    dual_ids: List[str],
) -> ProductBlueprint:
    """Validate + sanitize an LLM draft payload into a ProductBlueprint.

    Fail-closed on structure: malformed payloads raise (caller falls back).
    Fail-safe on content: block ids are filtered against the dual registry.
    """
    if not isinstance(data, dict):
        raise ValueError("LLM draft is not a JSON object")

    dual = set(dual_ids)
    raw_caps = data.get("capabilities")
    if not isinstance(raw_caps, list) or not raw_caps:
        raise ValueError("LLM draft has no capabilities")

    caps = []
    for item in raw_caps[:8]:
        if not isinstance(item, dict):
            continue
        cap_id = _slug(str(item.get("id", "")), default="")
        if not cap_id:
            continue
        block_ids = [
            b for b in item.get("block_ids", []) if isinstance(b, str) and b in dual
        ]
        # A connector a block supplies is that block, not a placeholder.
        connectors = [
            s
            for s in (connector_slug(c) for c in (item.get("connectors") or []) if isinstance(c, str))
            if s and s not in dual
        ]
        caps.append(
            {
                "id": cap_id.replace("-", "_"),
                "description": str(item.get("description", ""))[:300]
                or f"Capability {cap_id}",
                "block_ids": block_ids,
                "strategy_hint": "REUSE" if block_ids else "GENERATE",
                "connectors": connectors,
            }
        )
    if not caps:
        raise ValueError("LLM draft produced no usable capabilities")

    # The vertical is the USER's choice (the Floor's typed field), never the
    # model's reading of the brief: a "vertical" key in the payload is ignored.
    from app.factory.store_kits import chosen_vertical

    vertical = chosen_vertical(vertical_hint)
    product_name = str(data.get("product_name", "")).strip()[:120] or vertical.replace(
        "_", " "
    ).title()

    raw = {
        "schema_version": "product_blueprint.v1",
        "product_id": _slug(vertical),
        "product_name": product_name,
        "vertical": vertical,
        "summary": str(data.get("summary", "")).strip()[:500]
        or brief.strip()[:500]
        or f"Factory-drafted {vertical} product",
        "factory_scenario": FactoryScenario.CREATE_PRODUCT.value,
        "capabilities": caps,
        "ui_modules": ["command_center", "operational_chat", "resident_engineer"],
        # Every connector a capability calls that no block supplies is a
        # declared placeholder (an honest not_implemented stub).
        "connectors": list(dict.fromkeys(c for cap in caps for c in cap["connectors"])),
        "edge_profile": "standard",
        "human_authority": True,
    }
    return ProductBlueprint.model_validate(raw)



def _certified_ids() -> set:
    """Blocks the Store has certified. An unreadable record means no claim."""
    try:
        return _store_certified_ids()
    except Exception:  # noqa: BLE001 -- absent evidence must not block drafting
        return set()


def _draft_with_llm(
    brief: str,
    *,
    vertical_hint: Optional[str] = None,
) -> ProductBlueprint:
    """Draft a blueprint via the configured LLM. Raises on any failure."""
    dual = sorted(dual_registered_ids())
    # Mark the ones the Store has actually proved. Every block.json claims
    # trust_tier "platform", so without this the architect cannot tell a block
    # that survived a control-delete from one nobody has ever exercised. The
    # note matters as much as the mark: an unproven block is still the right
    # choice when it is the one that fits.
    proven = _certified_ids()
    block_list = (
        "\n".join(f"- {b}" + ("  [certified]" if b in proven else "") for b in dual)
        or "- (none registered)"
    )
    if proven:
        block_list += (
            "\n(A [certified] block is one whose tests go red when its entry "
            "method is gutted -- the Store has proved it does what it claims. "
            "Prefer one when it fits the capability; the rest are unproven, "
            "not unusable.)"
        )
    # The brief is caller-supplied and was previously sent whole.
    bounded_brief = truncate_brief(brief)
    messages = [
        {
            "role": "system",
            "content": _LLM_DRAFT_SYSTEM + f"\nAVAILABLE BLOCKS:\n{block_list}\n",
        },
        {"role": "user", "content": bounded_brief},
    ]
    data = _llm_json_call(messages)
    return _blueprint_from_llm_payload(data, bounded_brief, vertical_hint, dual)


# --- Deterministic keyword drafting (offline / canned demo path) --------------

# --- Public drafting API ------------------------------------------------------


def draft_blueprint_from_brief(
    brief: str,
    *,
    vertical_hint: Optional[str] = None,
    use_llm: Optional[bool] = None,
) -> ProductBlueprint:
    """Draft a ProductBlueprint and stamp the honest inventory declaration.

    The base output is ALWAYS the user's own draft (from
    ``_draft_blueprint_from_brief_inner``) — a golden is never substituted
    for it. This wrapper stamps ``drafting_note`` with the factory inventory
    verdict when the vertical is not on the declared-ready list — the client
    must see "the Store has no domain kit for this" before approving, never
    after.

    A matching golden is OFFERED, not swapped in. A golden is chosen by
    STRUCTURE only (``golden_match``: the draft's capability ids, vertical and
    hint against each golden's declared structure) and, whenever its overlap
    clears the kernel threshold (``golden_match.min_overlap``), is ALWAYS
    attached as a labelled accept/decline SUGGESTION on
    ``blueprint.golden_suggestion`` — the user's draft stays the base. Below
    the threshold (or on a tie / no match) the suggestion is None. There is no
    on/off flag: a structural match that clears the threshold is always offered.
    """
    bp = _draft_blueprint_from_brief_inner(
        brief,
        vertical_hint=vertical_hint,
        use_llm=use_llm,
    )
    suggestion = _golden_suggestion_for_draft(bp, vertical_hint)
    if suggestion is not None:
        bp.golden_suggestion = suggestion
    from app.factory.inventory import inventory_drafting_note

    note = inventory_drafting_note(str(getattr(bp, "vertical", "") or ""))
    if note:
        existing = str(bp.drafting_note or "")
        bp.drafting_note = (existing + "; " if existing else "") + note
    return bp


def _best_golden_match(
    draft: ProductBlueprint, vertical_hint: Optional[str] = None
) -> Optional[tuple]:
    """(Golden, overlap) whose declared structure best overlaps the draft's.

    Only goldens that declare the USER's chosen vertical (their own
    ``serves_verticals``) are eligible, so a golden can never hand a product
    a vertical the user did not pick. No choice, no golden. The scoring is a
    pure structural rule (Jaccard over capability ids), never an LLM call.
    """
    from app.factory.golden_match import (
        best_golden,
        draft_structure,
        eligible_for,
        goldens,
    )
    from app.factory.store_kits import NO_VERTICAL, chosen_vertical

    choice = chosen_vertical(vertical_hint)
    if choice == NO_VERTICAL:
        return None

    store_root = None
    try:
        from app.factory.blocks_source import resolve_blocks_root

        store_root = resolve_blocks_root()
    except Exception:  # noqa: BLE001 -- no Store: score on blueprints alone
        store_root = None
    return best_golden(
        draft_structure(draft, choice),
        eligible_for(choice, goldens(_repo_root() / "blueprints", store_root)),
    )


def _golden_suggestion_for_draft(
    draft: ProductBlueprint, vertical_hint: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """A labelled accept/decline suggestion for the best-overlapping golden.

    Returns the OFFER, never a replacement blueprint: {golden_id, golden_name,
    overlap, drafting_mode, label}. The caller attaches it to the user's own
    draft (``golden_suggestion``); the draft stays the base output.
    """
    match = _best_golden_match(draft, vertical_hint)
    if match is None:
        return None
    golden, score = match
    golden_bp = load_blueprint(golden.path)
    return {
        "golden_id": golden_bp.product_id,
        "golden_name": golden.name,
        "overlap": round(float(score), 4),
        "drafting_mode": "golden",
        "label": "suggested golden match — accept/decline",
    }


def _golden_for_draft(
    draft: ProductBlueprint, vertical_hint: Optional[str] = None
) -> Optional[ProductBlueprint]:
    """The golden whose declared structure best overlaps the draft's, if any.

    This loads the matching golden as a standalone blueprint — the resolution
    a user's ACCEPT of the offered suggestion uses. It is NOT part of the
    drafting path: ``draft_blueprint_from_brief`` never substitutes a golden
    for the user's draft, it only offers one (``_golden_suggestion_for_draft``).

    Only goldens that declare the USER's chosen vertical (their own
    ``serves_verticals``) are eligible, so a golden can never hand a product
    a vertical the user did not pick. No choice, no golden.
    """
    match = _best_golden_match(draft, vertical_hint)
    if match is None:
        return None
    golden, score = match
    bp = load_blueprint(golden.path)
    bp.drafting_mode = "golden"
    note = f"golden {golden.name}: structure overlap {score:.2f}"
    bp.drafting_note = f"{draft.drafting_note}; {note}" if draft.drafting_note else note
    return bp


def _draft_blueprint_from_brief_inner(
    brief: str,
    *,
    vertical_hint: Optional[str] = None,
    use_llm: Optional[bool] = None,
) -> ProductBlueprint:
    """Draft a ProductBlueprint from a user brief.

    1. LLM drafting when a factory API key is configured, or when
       ARCHITECT_LLM_DRAFTING_ENABLED / ``use_llm=True`` forces it --
       fail-safe: any error falls through to (2).
    2. Deterministic drafting (always works, no keys needed).

    Golden selection is not here: it compares the DRAFT's structure with
    each golden's (``_golden_for_draft``), never the brief's words.
    """
    text = (brief or "").lower()
    if use_llm is None:
        use_llm = llm_drafting_enabled()
    fallback_note = "LLM drafting disabled" if not use_llm else None
    if use_llm:
        try:
            bp = _draft_with_llm(brief, vertical_hint=vertical_hint)
            bp.drafting_mode = "architect_llm"
            return bp
        except Exception as exc:
            # Falling back is right for availability; hiding it is not. The
            # note travels on the blueprint so the chat/UI can say templates
            # drafted this, instead of a dead LLM key looking identical to a
            # working architect.
            logger.warning("LLM drafting failed, falling back: %s", exc)
            fallback_note = f"LLM drafting failed ({type(exc).__name__}); deterministic fallback used"

    dual = sorted(dual_registered_ids())
    # Blocks the brief actually mentions become REUSE capabilities; every
    # block that DECLARES itself governance (capability_class) is always
    # added -- governance is cross-cutting -- so the demo blueprint never
    # ships governance-less. Which block that is, the Store says.
    mentioned = [b for b in dual if b.replace("_", " ") in text or b in text]
    from app.factory.store_kits import GOVERNANCE, capability_classes

    for bid, cls in sorted(capability_classes(dual).items()):
        if cls == GOVERNANCE and bid not in mentioned:
            mentioned.append(bid)

    # The vertical is a structured field (the Floor's vertical_hint), never
    # parsed out of the brief's prose. Without one the draft is generic and
    # golden routing (_golden_for_draft) decides on structure alone.
    from app.factory.store_kits import chosen_vertical

    vertical = chosen_vertical(vertical_hint)
    product_id = re.sub(r"[^a-z0-9-]+", "-", vertical)[:48].strip("-") or "product"
    product_name = (
        vertical.replace("_", " ").replace("-", " ").title() + " Platform"
    ).strip()
    caps: List[Dict[str, Any]] = [
        {
            "id": f"{vertical.replace('-', '_')}_core",
            "description": f"Core {vertical.replace('_', ' ')} workflows and data model",
            "block_ids": [],
            "strategy_hint": "GENERATE",
        }
    ]
    for bid in mentioned[:7]:
        caps.append(
            {
                "id": bid,
                "description": f"{bid.replace('_', ' ').title()} capability (reused from the block store)",
                "block_ids": [bid],
                "strategy_hint": "REUSE",
            }
        )

    raw = {
        "schema_version": "product_blueprint.v1",
        "product_id": product_id,
        "product_name": product_name,
        "vertical": vertical,
        "summary": brief.strip()[:500] or f"Factory-drafted {vertical} product",
        "factory_scenario": FactoryScenario.CREATE_PRODUCT.value,
        "capabilities": caps,
        "ui_modules": ["command_center", "operational_chat", "resident_engineer"],
        "connectors": [],
        "edge_profile": "standard",
        "human_authority": True,
        "drafting_mode": "keyword_fallback",
        "drafting_note": fallback_note,
    }
    return ProductBlueprint.model_validate(raw)


def plan_blueprint(
    blueprint: ProductBlueprint,
    *,
    blocks_root: Optional[Path] = None,
) -> ProductPlan:
    return CapabilityPlanner(blocks_root).plan(blueprint)


def generate_product(
    blueprint: ProductBlueprint,
    output_dir: Path | str,
    *,
    blocks_root: Optional[Path] = None,
    cycle: Optional[str] = None,
    quota_account_id: Optional[str] = None,
    tenant_identity: Optional[str] = None,
    brief: str = "",
    platform_id: Optional[str] = None,
    start_over: bool = False,
    intake_changed: bool = False,
) -> Dict[str, Any]:
    """Build a product. The role runner is the default engine.

    Every production door funnels through here, which is why the cutover
    lives here rather than in four routers. With the runner engine the build
    is a background job (minutes, agent-written) and the return carries a
    ``build`` status the caller polls; with the template engine it is the
    old synchronous composition. ``FACTORY_BUILD_ENGINE=template`` reverts.
    """
    blocks = Path(blocks_root) if blocks_root else None
    if blocks is None:
        # The SAME resolver every router uses: explicit path, then a Store
        # clone. Reading only the env vars here meant a caller that passed no
        # blocks_root (the CLI, the demo flows) silently vendored the factory's
        # own mirror -- which contains real Store shims and therefore cannot
        # produce a standalone platform.
        from app.factory.blocks_source import resolve_blocks_root

        blocks = resolve_blocks_root()

    # Contract compliance, checked once, here. There are seven call sites of
    # this function across four modules; gating there would be seven
    # wirings and a silent bypass for the eighth. This is the door every
    # production path already funnels through, so the gate is structural
    # rather than remembered.
    #
    # The plan is computed and discarded: both engines plan again from the
    # blueprint. That duplication is deliberate -- threading a plan through
    # start_runner_build would add a parameter that could carry an ungated
    # plan, which is the shape of bypass this gate exists to close.
    from app.factory.compliance_gate import assert_compliant, load_trust_tiers

    # The tiers are read here rather than inside the gate so the gate stays a
    # pure function of the plan. load_trust_tiers() returns None when the
    # shelf is unreadable, and the trust check then abstains -- it will not
    # invent a refusal it cannot substantiate, nor pass a plan it never
    # checked.
    assert_compliant(
        CapabilityPlanner(blocks).plan(blueprint),
        blueprint=blueprint,
        trust_tiers=load_trust_tiers(),
    )

    from app.factory.build_jobs import RUNNER, build_engine, start_runner_build

    if build_engine() == RUNNER:
        return start_runner_build(
            blueprint,
            output_dir,
            blocks_root=blocks,
            cycle=cycle,
            quota_account_id=quota_account_id,
            tenant_identity=tenant_identity,
            brief=str(brief or "").strip(),
            platform_id=platform_id,
            start_over=start_over,
            intake_changed=intake_changed,
        )

    factory_root = _repo_root()
    gen = ProductGenerator(
        blueprint,
        blocks_root=blocks,
        factory_commit=git_head(factory_root),
        blocks_commit=git_head(blocks) if blocks else "unknown",
    )
    result = gen.generate(output_dir)
    from app.factory.build_jobs import clone_canonical

    try:
        canonical = clone_canonical(blueprint, output_dir)
        if canonical is not None:
            result["canonical_output"] = str(canonical)
    except Exception:  # noqa: BLE001
        logger.exception("canonical clone failed after template generate")
    return result


def architect_pipeline(
    brief: str,
    output_dir: Path | str,
    *,
    vertical_hint: Optional[str] = None,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Brief → blueprint → plan → generate. Fail closed on UNSUPPORTED."""
    try:
        bp = draft_blueprint_from_brief(brief, vertical_hint=vertical_hint)
        plan = plan_blueprint(bp, blocks_root=blocks_root)
        result = generate_product(bp, output_dir, blocks_root=blocks_root)
        return {
            "ok": True,
            "blueprint": bp.model_dump(mode="json"),
            "plan": plan.to_dict(),
            "generation": {
                "output_dir": result["output_dir"],
                "inputs_hash": result["inputs_hash"],
                "product_id": result["product_id"],
            },
        }
    except DualRegistryError as exc:
        return {"ok": False, "error": str(exc)}


def blueprint_to_yaml(bp: ProductBlueprint) -> str:
    return yaml.safe_dump(bp.model_dump(mode="json"), sort_keys=False)
