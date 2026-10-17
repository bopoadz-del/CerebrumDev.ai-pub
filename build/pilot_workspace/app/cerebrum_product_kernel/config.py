"""Kernel configuration (env-driven, product-neutral)."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class KernelConfig:
    database_url: str
    embed_backend: str = "hash"
    skip_migrations: bool = False
    llm_provider: str = "fake"

    @classmethod
    def from_env(cls, prefix: str = "PRODUCT_") -> "KernelConfig":
        return cls(
            database_url=os.getenv(f"{prefix}DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or "",
            embed_backend=os.getenv(f"{prefix}EMBED_BACKEND", "hash"),
            skip_migrations=os.getenv(f"{prefix}SKIP_MIGRATIONS", "0") in ("1", "true", "yes"),
            llm_provider=os.getenv(f"{prefix}LLM_PROVIDER", "fake"),
        )
