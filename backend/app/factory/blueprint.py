"""product_blueprint.v1 — fail-closed schema for Factory generation."""

from __future__ import annotations

import json
from enum import Enum, auto
from pathlib import Path
from typing import Annotated, Any, Dict, FrozenSet, List, Optional, Type

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


class SetBy(Enum):
    """Who may set a blueprint field.

    Every ``ProductBlueprint`` field declares exactly one, as
    ``Annotated[<type>, SetBy.X]``. Whether a customer session may carry a
    field is answered by that declaration -- never by a list of field names
    kept somewhere else, and never by which route the blueprint came in on.
    """

    #: The customer's own typed choice on the Floor.
    USER = auto()
    #: Drafted for the customer inside their session.
    ARCHITECT = auto()
    #: Only an operator's own blueprint file. Never a customer session.
    OPERATOR = auto()


#: Whoever acts inside a customer session. A field that anyone else sets never
#: enters a session, whatever carried it to the door.
SESSION_SETTERS: FrozenSet[SetBy] = frozenset({SetBy.USER, SetBy.ARCHITECT})


def field_setters(model: Type[BaseModel]) -> Dict[str, SetBy]:
    """Each field's declared setter, read off the model itself.

    A field with no ``SetBy`` mark -- or with two -- has no answer to "who may
    set this?". Reading the declarations then fails instead of guessing one.
    """
    setters: Dict[str, SetBy] = {}
    for name, info in model.model_fields.items():
        marks = [mark for mark in info.metadata if isinstance(mark, SetBy)]
        if len(marks) != 1:
            raise BlueprintError(
                f"blueprint field '{name}' must declare exactly one SetBy, found {len(marks)}"
            )
        setters[name] = marks[0]
    return setters


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
    #: Store kits the architect picked for this capability, from the kit
    #: index it was shown. A pick draws a kit into the build only for a
    #: block the kit lists and this capability binds (kit_pack.drawn_kits).
    kit_ids: List[str] = Field(default_factory=list)
    strategy_hint: Optional[CapabilityStrategyHint] = None
    required: bool = True
    #: External systems this capability calls, as connector slugs. One that
    #: is also in ``ProductBlueprint.connectors`` is a DECLARED PLACEHOLDER
    #: (shipped as a ``not_implemented`` stub), so the capability answers
    #: the typed unavailable refusal until it is built -- and every generated
    #: suite expects exactly that. See app.factory.build.placeholder_connectors.
    connectors: List[str] = Field(default_factory=list)
    #: Per-capability customer asks, verbatim where the brief gave them (a
    #: clause list, a BOQ line set, a traceability demand). The architect
    #: fills these at drafting time; the writer brief renders them verbatim
    #: so a capability's requirements are never lost in a one-line
    #: description. Empty list: the brief named nothing specific for it.
    requirements: List[str] = Field(default_factory=list)

    @field_validator("connectors")
    @classmethod
    def _connector_slugs(cls, v: List[str]) -> List[str]:
        return list(dict.fromkeys(s for s in (connector_slug(c) for c in v) if s))


class ProductBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Every field says who may set it (``SetBy``). ``customer_settable`` reads
    # these marks; a field added without one stops this module loading.
    schema_version: Annotated[str, SetBy.ARCHITECT]
    product_id: Annotated[str, SetBy.ARCHITECT]
    product_name: Annotated[str, SetBy.ARCHITECT]
    vertical: Annotated[str, SetBy.USER]
    summary: Annotated[str, SetBy.ARCHITECT]
    capabilities: Annotated[List[CapabilitySpec], SetBy.ARCHITECT]
    ui_modules: Annotated[List[str], SetBy.ARCHITECT] = Field(default_factory=list)
    connectors: Annotated[List[str], SetBy.ARCHITECT] = Field(default_factory=list)
    edge_profile: Annotated[str, SetBy.ARCHITECT] = "standard"
    #: A GOLDEN blueprint lists the vertical hints it answers for; the
    #: architect routes a hint by reading these, never a Factory table. It is
    #: the golden's own declaration, so it stays with the golden: operator-only.
    serves_verticals: Annotated[List[str], SetBy.OPERATOR] = Field(default_factory=list)
    #: Directory under factory_outputs/ that keeps a stable copy of the last
    #: finished build of this blueprint. Unset: no canonical copy. It names a
    #: server directory that is replaced after a build, so it is operator-only:
    #: honoured on an operator's own run, never on a customer's
    #: (``build_jobs.clone_canonical``).
    canonical_output: Annotated[Optional[str], SetBy.OPERATOR] = None
    #: The BUILD LEVEL the user chose on the Floor (a typed field, like the
    #: vertical and the locale): prototype | light | pilot | production
    #: (app.factory.build.build_level). It decides where the run stops on the
    #: CODE -> PRODUCT -> STORE ladder and how strict its top rung is. None
    #: means undeclared -- the Floor refuses to start a build until the user
    #: chooses; it is never inferred from the brief and has no default.
    build_level: Annotated[Optional[str], SetBy.USER] = None
    human_authority: Annotated[bool, SetBy.ARCHITECT] = True
    factory_scenario: Annotated[FactoryScenario, SetBy.ARCHITECT] = FactoryScenario.CREATE_PRODUCT
    # Provenance of the draft itself: "architect_llm" | "golden" (chosen by
    # structural overlap, see golden_match) | "keyword_fallback". The architect fails safe from
    # LLM to deterministic drafting, which is right for availability and
    # wrong to do silently —
    # a user whose LLM credit died must see that templates drafted this.
    drafting_mode: Annotated[Optional[str], SetBy.ARCHITECT] = None
    drafting_note: Annotated[Optional[str], SetBy.ARCHITECT] = None
    #: A matching golden surfaced as a labelled accept/decline SUGGESTION —
    #: never substituted for the user's own draft. The base output is always
    #: the user's draft; whenever a golden's declared structure overlaps the
    #: draft above the kernel threshold the match rides here as
    #: {golden_id, golden_name, overlap, drafting_mode, label} for the user to
    #: accept or decline. None below the threshold or when nothing matched.
    golden_suggestion: Annotated[Optional[Dict[str, Any]], SetBy.ARCHITECT] = None
    #: How the client wants the platform delivered — chosen at request
    #: time, never after the build: "zip" (download in the Floor) or
    #: "github_repo" (pushed to a repo, URL returned on the build).
    delivery_format: Annotated[str, SetBy.USER] = "zip"
    #: The country / currency the USER declared on the Floor (typed fields,
    #: validated by shape only): {"country": "XX", "currency": "XXX"}.
    #: None when the user declared none -- money is then WITHHELD, never
    #: guessed (money_contract).
    locale: Annotated[Optional[Dict[str, str]], SetBy.USER] = None

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


#: The fields no customer session may carry, read off the declarations above
#: when this module loads.
OPERATOR_ONLY_FIELDS: FrozenSet[str] = frozenset(
    name
    for name, setter in field_setters(ProductBlueprint).items()
    if setter not in SESSION_SETTERS
)


def customer_settable(blueprint: Any) -> Dict[str, Any]:
    """The part of a blueprint a customer session may carry.

    Called wherever a blueprint enters a session from outside it or from a
    source sessions share: a request body, a golden, a cache entry. Every key
    whose field only an operator may set is left out, so it is never stored on
    the session. Keys the model does not know stay in, for the model itself to
    refuse.
    """
    if isinstance(blueprint, ProductBlueprint):
        blueprint = blueprint_to_dict(blueprint)
    return {
        key: value
        for key, value in dict(blueprint).items()
        if key not in OPERATOR_ONLY_FIELDS
    }


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
