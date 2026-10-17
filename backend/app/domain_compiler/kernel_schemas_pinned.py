# PINNED COPY of Cerebrum-Blocks app/reasoning_kernel/schemas.py
# Source: bopoadz-del/Cerebrum-Blocks, branch feat/cerebrum-reasoning-kernel @ f9d61f60
# The Domain Pack standard lives in the kernel; the compiler validates against
# this pinned snapshot so validation works without a store checkout. Prefer the
# live checkout when present (CEREBRUM_BLOCKS_PATH); refresh this copy when the
# kernel schema changes and re-run tests/compiler.
"""Domain Pack schemas â€” the formal, versioned contracts (mission Phase 2).

Every Domain Pack artifact validates against these Pydantic models before
the kernel will load it. A schema violation is a load refusal, never a
silent default. ``extra="forbid"`` everywhere: an unknown field means the
pack was written for a different standard version.

Certification states (mission Phase 4): a formula/rule is authoritative in
production mode only when its pack declares ``certification: domain_approved``.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Certification(str, Enum):
    CANDIDATE = "candidate"
    TECHNICALLY_VERIFIED = "technically_verified"
    DOMAIN_REVIEW_REQUIRED = "domain_review_required"
    DOMAIN_APPROVED = "domain_approved"
    DEPRECATED = "deprecated"


class PackManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain_id: str = Field(..., pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    name: str = Field(..., min_length=1)
    description: str = ""
    certification: Certification = Certification.CANDIDATE
    effective_date: Optional[date] = None
    review_date: Optional[date] = None
    donor_repo: Optional[str] = None
    donor_commit: Optional[str] = None


class FormulaSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    formula_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    domain: str
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    description: str = ""
    expression: Optional[str] = None
    inputs: List[Dict[str, Any]] = Field(default_factory=list)
    output: Dict[str, Any] = Field(default_factory=dict)
    units: Dict[str, str] = Field(default_factory=dict)
    currency: Optional[str] = None
    precision: Optional[int] = Field(default=None, ge=0, le=15)
    rounding: str = "half_even"
    assumptions: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    edge_cases: List[str] = Field(default_factory=list)
    unsupported_conditions: List[str] = Field(default_factory=list)
    authority_source: str = ""
    effective_date: Optional[date] = None
    review_date: Optional[date] = None
    implementation: str = ""
    verification_oracle: str = ""
    certification: Certification = Certification.CANDIDATE
    provenance: Dict[str, str] = Field(default_factory=dict)


class RuleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    decision: Dict[str, Any] = Field(default_factory=dict)
    severity: str = "info"
    exceptions: List[Dict[str, Any]] = Field(default_factory=list)
    precedence: int = 0
    conflicts: List[str] = Field(default_factory=list)
    authority_source: str = ""
    evidence_requirements: List[str] = Field(default_factory=list)
    effective_date: Optional[date] = None
    review_owner: str = ""
    refusal_behavior: str = "refuse"
    certification: Certification = Certification.CANDIDATE
    provenance: Dict[str, str] = Field(default_factory=dict)


class DecisionTableSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table_id: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    inputs: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    default_outcome: Dict[str, Any] = Field(default_factory=dict)
    authority_source: str = ""
    certification: Certification = Certification.CANDIDATE
    provenance: Dict[str, str] = Field(default_factory=dict)


class WorkflowSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    states: List[str] = Field(default_factory=list)
    initial_state: str = ""
    terminal_states: List[str] = Field(default_factory=list)
    transitions: List[Dict[str, Any]] = Field(default_factory=list)
    permitted_actors: Dict[str, List[str]] = Field(default_factory=dict)
    guards: Dict[str, List[str]] = Field(default_factory=dict)
    evidence_required: Dict[str, List[str]] = Field(default_factory=dict)
    approval_required: Dict[str, bool] = Field(default_factory=dict)
    immutable_states: List[str] = Field(default_factory=list)
    reopening_rules: Dict[str, Any] = Field(default_factory=dict)
    certification: Certification = Certification.CANDIDATE
    provenance: Dict[str, str] = Field(default_factory=dict)


class ApprovalPolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(..., min_length=1)
    version: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    risk_tier: str = "low"
    requester_roles: List[str] = Field(default_factory=list)
    approver_roles: List[str] = Field(default_factory=list)
    minimum_approvals: int = Field(default=1, ge=1)
    thresholds: Dict[str, Any] = Field(default_factory=dict)
    self_approval: str = "prohibited"
    segregation_of_duties: List[List[str]] = Field(default_factory=list)
    payload_binding: str = "required"
    expiry_seconds: Optional[int] = None
    replay_prevention: str = "required"
    emergency_override: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[str] = Field(default_factory=list)
    audit_required: bool = True
    certification: Certification = Certification.CANDIDATE
    provenance: Dict[str, str] = Field(default_factory=dict)


class DomainPack(BaseModel):
    """The assembled pack: manifest + artifacts + fixtures + tests refs."""

    model_config = ConfigDict(extra="forbid")

    manifest: PackManifest
    ontology: Dict[str, Any] = Field(default_factory=dict)
    entities: Dict[str, Any] = Field(default_factory=dict)
    permissions: Dict[str, Any] = Field(default_factory=dict)
    evidence_policy: Dict[str, Any] = Field(default_factory=dict)
    authority_sources: Dict[str, Any] = Field(default_factory=dict)
    refusal_policy: Dict[str, Any] = Field(default_factory=dict)
    formulas: List[FormulaSpec] = Field(default_factory=list)
    rules: List[RuleSpec] = Field(default_factory=list)
    decision_tables: List[DecisionTableSpec] = Field(default_factory=list)
    workflows: List[WorkflowSpec] = Field(default_factory=list)
    approvals: List[ApprovalPolicySpec] = Field(default_factory=list)
    actions: Dict[str, Any] = Field(default_factory=dict)
    verification_oracles: Dict[str, Any] = Field(default_factory=dict)
    fixtures: Dict[str, Any] = Field(default_factory=dict)
    tests: Dict[str, Any] = Field(default_factory=dict)

    @property
    def domain_approved_formulas(self) -> List[FormulaSpec]:
        return [f for f in self.formulas if f.certification is Certification.DOMAIN_APPROVED]

