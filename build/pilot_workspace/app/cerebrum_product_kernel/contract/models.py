"""Strongly typed models for the generic domain-action contract.

All models use Pydantic v2 and provide *deterministic* serialization: dict/JSON
output orders keys and renders timestamps as ISO-8601 UTC strings, so audit
records and API responses are byte-stable for a given logical value.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Trusted-context keys that a model / caller argument payload must never be able
# to set or override. The runtime strips these from action arguments.
RESERVED_CONTEXT_KEYS = frozenset(
    {
        "user_id",
        "tenant_id",
        "organisation_id",
        "organization_id",
        "project_id",
        "permissions",
        "allowed_domains",
        "corpus_id",
        "knowledge_layer",
        "domain",
        "context",
    }
)


class ActionStatus(str, Enum):
    """Closed set of terminal action statuses."""

    SUCCESS = "success"
    DEPENDENCY_REQUIRED = "dependency_required"
    VALIDATION_ERROR = "validation_error"
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED = "unsupported"
    EXECUTION_ERROR = "execution_error"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class _DeterministicModel(BaseModel):
    """Base model with deterministic dict/JSON serialization."""

    model_config = ConfigDict(extra="forbid")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe dict with sorted keys and ISO timestamps."""
        raw = self.model_dump(mode="json")
        return _sort_deep(raw)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _sort_deep(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _sort_deep(value[k]) for k in sorted(value.keys())}
    if isinstance(value, list):
        return [_sort_deep(v) for v in value]
    return value


class ActionContext(_DeterministicModel):
    """Trusted runtime context resolved by the host from the authenticated
    principal. These values are authoritative and must never be sourced from
    model-generated action arguments.
    """

    user_id: str
    tenant_id: str
    organisation_id: str
    project_id: str
    permissions: List[str] = Field(default_factory=list)
    allowed_domains: List[str] = Field(default_factory=list)
    corpus_id: Optional[str] = None
    knowledge_layer: Optional[str] = None

    @field_validator("permissions", "allowed_domains")
    @classmethod
    def _dedupe(cls, v: List[str]) -> List[str]:
        seen: List[str] = []
        for item in v:
            if item not in seen:
                seen.append(item)
        return seen

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def allows_domain(self, domain: str) -> bool:
        # An empty allow-list denies everything (fail-closed).
        return domain in self.allowed_domains


class ActionDependency(_DeterministicModel):
    """A missing pre-condition that blocks an action from producing output."""

    kind: str
    description: str
    required: bool = True
    hint: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionEvidence(_DeterministicModel):
    """A structured evidence / citation reference attached to a result."""

    source_id: Optional[str] = None
    document_id: Optional[str] = None
    filename: Optional[str] = None
    page: Optional[int] = None
    sheet: Optional[str] = None
    row_start: Optional[int] = None
    row_end: Optional[int] = None
    chunk_id: Optional[str] = None
    excerpt: Optional[str] = None
    score: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionOutcome(_DeterministicModel):
    """The value a handler returns. The runtime wraps this into an
    :class:`ActionResult` with identity, timing and provenance fields.
    """

    status: ActionStatus
    output: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[ActionDependency] = Field(default_factory=list)
    evidence: List[ActionEvidence] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(cls, output: Dict[str, Any], **kwargs: Any) -> "ActionOutcome":
        return cls(status=ActionStatus.SUCCESS, output=output, **kwargs)

    @classmethod
    def dependency(
        cls, dependencies: List[ActionDependency], **kwargs: Any
    ) -> "ActionOutcome":
        return cls(
            status=ActionStatus.DEPENDENCY_REQUIRED,
            dependencies=dependencies,
            **kwargs,
        )


# A handler is an async callable: (context, arguments) -> ActionOutcome
ActionHandler = Callable[[ActionContext, Dict[str, Any]], Any]


class ActionSpec(_DeterministicModel):
    """Declarative specification of a domain action.

    ``action_id`` is namespaced as ``<domain>.<action_name>``. The ``handler``
    is excluded from serialization (it is not part of the public contract) but
    is required for execution.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    action_id: str
    domain: str
    name: str
    description: str
    version: str = "1.0.0"
    kit_version: Optional[str] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Dict[str, Any] = Field(default_factory=dict)
    required_context: List[str] = Field(default_factory=list)
    required_dependencies: List[ActionDependency] = Field(default_factory=list)
    permissions: List[str] = Field(default_factory=list)
    risk_classification: str = "low"
    read_only: bool = True
    confirmation_required: bool = False
    synthesis_instructions: Optional[str] = None
    evaluation_fixtures: List[str] = Field(default_factory=list)
    handler: Optional[ActionHandler] = Field(default=None, exclude=True, repr=False)

    @field_validator("action_id")
    @classmethod
    def _validate_action_id(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", v):
            raise ValueError(
                "action_id must be namespaced as '<domain>.<action_name>' "
                "using lowercase snake_case segments"
            )
        return v

    def model_post_init(self, __context: Any) -> None:  # noqa: D401
        # Enforce that the id namespace matches the declared domain.
        namespace = self.action_id.split(".", 1)[0]
        if namespace != self.domain:
            raise ValueError(
                f"action_id namespace '{namespace}' does not match domain "
                f"'{self.domain}'"
            )

    def public_dict(self) -> Dict[str, Any]:
        """Deterministic dict without the handler (safe for discovery APIs)."""
        return self.to_dict()


class ActionResult(_DeterministicModel):
    """The serialized record of a single action execution."""

    request_id: str
    action_id: str
    kit_version: Optional[str] = None
    status: ActionStatus
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    output: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[ActionDependency] = Field(default_factory=list)
    evidence: List[ActionEvidence] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("started_at", "completed_at")
    @classmethod
    def _ensure_tz(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        # model_dump(mode="json") already renders datetimes; keep explicit ISO
        data["started_at"] = _iso(self.started_at)
        data["completed_at"] = _iso(self.completed_at)
        return _sort_deep(data)
