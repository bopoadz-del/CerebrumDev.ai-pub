from pydantic import BaseModel, Field
from typing import Literal, Optional, List, Dict, Any
from datetime import datetime


class AIConfig(BaseModel):
    base_model: str = "Llama-3.2-3B"
    lora_rank: int = 32
    learning_rate: float = 2e-4
    vector_db: str = "ZVec"
    hnsw_preset: str = "balanced"  # fast, balanced, accurate


class SessionConfig(BaseModel):
    domain: str = "construction"
    ai_config: AIConfig = Field(default_factory=AIConfig)


class UploadResult(BaseModel):
    status: str = "pending"  # pending, processing, completed, failed
    progress: float = 0.0
    total_chunks: int = 0
    indexed_collection: Optional[str] = None
    failed_files: List[str] = Field(default_factory=list)
    message: Optional[str] = None


class DeploymentResult(BaseModel):
    status: str = "pending"  # pending, packaging, deploying, live, failed, packaged
    target: str = "cloud"  # cloud, edge
    progress: float = 0.0
    url: Optional[str] = None
    api_key: Optional[str] = None
    service_id: Optional[str] = None
    deploy_id: Optional[str] = None
    message: Optional[str] = None
    package_path: Optional[str] = None


class ProductDesignState(BaseModel):
    """In-session product architecture (Factory Design Product mode)."""

    mode: Literal["kit", "product"] = "kit"
    brief: str = ""
    blueprint: Optional[Dict[str, Any]] = None
    plan: Optional[Dict[str, Any]] = None
    intake_blueprint: Optional[Dict[str, Any]] = None
    blueprint_approved: bool = False
    generation: Optional[Dict[str, Any]] = None
    last_error: Optional[str] = None
    brief_lint: Optional[Dict[str, Any]] = None
    #: What the user has said so far while the Floor chat is still asking
    #: about the platform -- first brief included, verbatim. Folded into the
    #: brief the architect drafts from, then cleared.
    elicitation_turns: List[str] = Field(default_factory=list)
    #: How many times the Floor chat has asked. Capped in code, not by the
    #: model: a chat that can ask forever is a chat that never builds.
    elicitation_rounds: int = 0
    #: The "no ready kit for this" notice is said once per session.
    kit_notice_given: bool = False
    #: The vertical the USER chose on the Floor (picked or typed). The only
    #: source of a product's vertical: None means "product" (no domain kit).
    #: The Factory never infers it from the brief's prose or its blocks.
    vertical: Optional[str] = None
    #: The country (ISO 3166 alpha-2) and currency (ISO 4217) the USER typed
    #: on the Floor, shape-validated only. Copied onto the blueprint's
    #: ``locale``; None means undeclared -- money is then WITHHELD, never
    #: guessed (app.factory.locale_choice / money_contract).
    country: Optional[str] = None
    currency: Optional[str] = None
    #: The BUILD LEVEL the USER chose (prototype | light | pilot | production)
    #: -- typed, copied onto the blueprint's ``build_level``. None means not
    #: chosen yet, and a build cannot start (no default, never inferred).
    build_level: Optional[str] = None
    #: What the Floor chat PROPOSED from the user's answer -- {vertical?,
    #: country?, currency?, build_level?}. A proposal only: it becomes the
    #: typed fields above solely through the typed ``confirm_intake`` action.
    intake_proposal: Optional[Dict[str, str]] = None
    #: Set by the typed ``continue_with_intake`` on a FAILED build: the
    #: intake is editable again, and the next typed ``confirm_intake``
    #: resumes this platform's branch from the new answers.
    intake_reopened: bool = False
    #: The platform's identity (``plt_<16 hex>``), minted ONCE at the first
    #: approval and never derived from a name. ``build/<platform_id>`` on
    #: cerebrum-builds is this platform's one branch of record, forever.
    platform_id: Optional[str] = None


class SessionState(BaseModel):
    session_id: str
    user_id: str
    phase: int = 1
    phase_status: str = "in_progress"
    config: SessionConfig = Field(default_factory=SessionConfig)
    product_design: ProductDesignState = Field(default_factory=ProductDesignState)

    upload: UploadResult = Field(default_factory=UploadResult)
    # Indexed data stored with the session for later packaging
    corpus: Optional[str] = None
    chunks: List[str] = Field(default_factory=list)
    embeddings: List[List[float]] = Field(default_factory=list)
    embedding_meta: Optional[dict] = None
    index_status: Optional[str] = None
    # Phase 3: AI chat + chain generation + rule injection
    chat_history: List[Dict[str, str]] = Field(default_factory=list)
    proposed_chain: Optional[Dict[str, Any]] = None
    extracted_rules: List[str] = Field(default_factory=list)
    chain_approved: bool = False
    rules_injected: bool = False
    container_modified_path: Optional[str] = None
    validation_passed: bool = False
    chain_quality: Optional[Dict[str, Any]] = None
    # Phase 5: deploy / ship
    deployment: DeploymentResult = Field(default_factory=DeploymentResult)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
