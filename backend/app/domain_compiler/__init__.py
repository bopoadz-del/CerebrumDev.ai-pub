"""Domain Intelligence Compiler (mission Phase 4).

Scout a donor repository -> generate candidate Domain Pack artifacts with
exact provenance -> validate against the kernel's real schemas -> package,
publish, install.

Rule: the compiler never certifies. Every artifact it emits is
``candidate``; promotion to technically_verified / domain_approved happens
only through the store gate.
"""

from .generator import CandidatePackGenerator
from .publisher import PublishRefusedError, install, package, publish
from .scout import Discovery, DonorScout, ScoutReport
from .validator import SchemaUnavailableError, validate_pack

__all__ = [
    "CandidatePackGenerator",
    "Discovery",
    "DonorScout",
    "PublishRefusedError",
    "SchemaUnavailableError",
    "ScoutReport",
    "install",
    "package",
    "publish",
    "validate_pack",
]
