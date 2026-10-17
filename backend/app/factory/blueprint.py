"""product_blueprint.v1 — fail-closed schema for Factory generation."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BlueprintError(ValueError):
    """Invalid product blueprint."""


def connector_slug(text: Any) -> str:
    """The one spelling of a connector id: lowercase snake. The Floor chat,
    the architect draft and the capability declaration all go through it, so
    a capability's connector and the blueprint's placeholder list compare."""
    import re

    return re.sub(r"[^a-z0-9_]+", "_", str(text or "").lower()).strip("_")


class CapabilityStrategyHint(str, Enum):
    REUSE = "REUSE"
    ADAPT = "ADAPT"
    COMPOSE = "COMPOSE"
    GENERATE = "GENERATE"
    STUB = "STUB"
    UNSUPPORTED = "UNSUPPORTED"


class FactoryScenario(str, Enum):
    """Exactly one Factory scenario per blueprint (Store Manager Loop)."""

    CREATE_PRODUCT = "CREATE_PRODUCT"
    MODIFY_PRODUCT = "MODIFY_PRODUCT"
    REPAIR_PRODUCT = "REPAIR_PRODUCT"
    ADAPT_EXISTING_PRODUCT = "ADAPT_EXISTING_PRODUCT"


class CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    block_ids: List[str] = Field(default_factory=list)
    strategy_hint: Optional[CapabilityStrategyHint] = None
    required: bool = True
    #: External systems this capability calls, as connector slugs. One that
    #: is also in ``ProductBlueprint.connectors`` is a DECLARED PLACEHOLDER
    #: (shipped as a ``not_implemented`` stub), so the capability answers
    #: the typed unavailable refusal until it is built -- and every generated
    #: suite expects exactly that. See app.factory.build.placeholder_connectors.
    connectors: List[str] = Field(default_factory=list)

    @field_validator("connectors")
    @classmethod
    def _connector_slugs(cls, v: List[str]) -> List[str]:
        return list(dict.fromkeys(s for s in (connector_slug(c) for c in v) if s))


class ProductBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    product_id: str
    product_name: str
    vertical: str
    summary: str
    capabilities: List[CapabilitySpec]
    ui_modules: List[str] = Field(default_factory=list)
    connectors: List[str] = Field(default_factory=list)
    edge_profile: str = "standard"
    #: A GOLDEN blueprint lists the vertical hints it answers for; the
    #: architect routes a hint by reading these, never a Factory table.
    serves_verticals: List[str] = Field(default_factory=list)
    #: Directory under factory_outputs/ that keeps a stable copy of the last
    #: finished build of this blueprint. Unset: no canonical copy.
    canonical_output: Optional[str] = None
    #: The BUILD LEVEL the user chose on the Floor (a typed field, like the
    #: vertical and the locale): prototype | light | pilot | production
    #: (app.factory.build.build_level). It decides where the run stops on the
    #: CODE -> PRODUCT -> STORE ladder and how strict its top rung is. None
    #: means undeclared -- the Floor refuses to start a build until the user
    #: chooses; it is never inferred from the brief and has no default.
    build_level: Optional[str] = None
    human_authority: bool = True
    factory_scenario: FactoryScenario = FactoryScenario.CREATE_PRODUCT
    # Provenance of the draft itself: "architect_llm" | "golden" (chosen by
    # structural overlap, see golden_match) | "keyword_fallback". The architect fails safe from
    # LLM to deterministic drafting, which is right for availability and
    # wrong to do silently —
    # a user whose LLM credit died must see that templates drafted this.
    drafting_mode: Optional[str] = None
    drafting_note: Optional[str] = None
    #: How the client wants the platform delivered — chosen at request
    #: time, never after the build: "zip" (download in the Floor) or
    #: "github_repo" (pushed to a repo, URL returned on the build).
    delivery_format: str = "zip"
    #: The country / currency the USER declared on the Floor (typed fields,
    #: validated by shape only): {"country": "XX", "currency": "XXX"}.
    #: None when the user declared none -- money is then WITHHELD, never
    #: guessed (money_contract).
    locale: Optional[Dict[str, str]] = None

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_rigor(cls, data: Any) -> Any:
        # ``rigor`` was the build grade before the user chose a level. Its
        # stored value (defaulted to "production") was never the user's
        # choice, so it is dropped on load -- never translated into a level.
        if isinstance(data, dict) and "rigor" in data:
            data = {k: v for k, v in data.items() if k != "rigor"}
        return data

    @field_validator("build_level")
    @classmethod
    def _build_level(cls, v: Optional[str]) -> Optional[str]:
        if v is None or str(v).strip() == "":
            return None
        from app.factory.build.build_level import parse_build_level

        return parse_build_level(v).value

    @field_validator("locale")
    @classmethod
    def _locale(cls, v: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        if v is None:
            return None
        from app.factory.build.money_contract import declared_locale

        shaped = declared_locale(v.get("country"), v.get("currency"))
        if shaped is None:
            raise ValueError("locale needs a 2-letter country and a 3-letter currency code")
        return shaped

    @field_validator("delivery_format")
    @classmethod
    def _delivery(cls, v: str) -> str:
        if v not in ("zip", "github_repo"):
            raise ValueError("delivery_format must be 'zip' or 'github_repo'")
        return v

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, v: str) -> str:
        if v != "product_blueprint.v1":
            raise ValueError(f"unsupported schema_version '{v}'; expected product_blueprint.v1")
        return v

    @field_validator("product_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_-]*", v):
            raise ValueError("product_id must be lowercase kebab/snake")
        return v

    @field_validator("capabilities")
    @classmethod
    def _caps(cls, v: List[CapabilitySpec]) -> List[CapabilitySpec]:
        if not v:
            raise ValueError("capabilities must be non-empty")
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate capability ids")
        return v


def load_blueprint(path: Path | str) -> ProductBlueprint:
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise BlueprintError("blueprint root must be a mapping")
    try:
        return ProductBlueprint.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise BlueprintError(str(exc)) from exc


def blueprint_to_dict(bp: ProductBlueprint) -> Dict[str, Any]:
    return bp.model_dump(mode="json")
