"""Block taxonomy for CerebrumDev.ai.

The platform always includes a set of core/built-in blocks. Users can optionally
add Fork primitive blocks on top of the domain kit.
"""

from typing import List

BUILTIN_BLOCKS: List[str] = [
    # Runtime / execution
    "orchestrator",
    "smart_orchestrator",
    "async_processor",
    "validation_pipeline",
    # Storage / caching
    "local_drive",
    "storage",
    "cache_manager",
    "memory",
    # RAG / search
    "vector_search",
    "zvec",
    "search",
    "knowledge",
    # LLM / AI
    "llm_enhancer",
    "learning_engine",
    "recommendation_template",
    # Document / vision parsing
    "pdf",
    "ocr",
    "ocr_v2",
    "image",
    "document_engine",
    # Interface / security / monitoring
    "chat",
    "auth",
    "secrets",
    "health_check",
    "monitoring",
    "error_tracking",
]

OPTIONAL_BLOCKS: List[str] = [
    # Drive / communication / utility primitives
    "voice",
    "google_drive",
    "onedrive",
    "android_drive",
    "email",
    "webhook",
    "web",
    "translate",
    "code",
    "capture",
    "jetson_gateway",
    # Domain v2 blocks (one per kit; keep synced with block_store/kits/)
    "agriculture_v2",
    "automotive_v2",
    "aviation_v2",
    "construction_v2",
    "education_v2",
    "finance_v2",
    "hotel_management_v2",
    "hr_v2",
    "insurance_v2",
    "legal_v2",
    "manufacturing_v2",
    "medical_v2",
    "oil_gas_v2",
    "pharma_v2",
    "real_estate_v2",
    "retail_v2",
    "supply_chain_v2",
]


def is_builtin(block_name: str) -> bool:
    return block_name in BUILTIN_BLOCKS


def is_optional(block_name: str) -> bool:
    return block_name in OPTIONAL_BLOCKS


def list_optional_blocks() -> List[str]:
    return list(OPTIONAL_BLOCKS)
